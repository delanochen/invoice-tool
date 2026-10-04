"""Customer receipt allocation and prepayment posting."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .posting import EventKey, PostingLine, PostingService, SettlementLine


CENT = Decimal("0.01")


class ReceiptError(RuntimeError):
    code = "receipt_error"


class ReceiptConflict(ReceiptError):
    code = "receipt_conflict"


class UnallocatedReceipt(ReceiptError):
    code = "unallocated_receipt"


class InvoiceNotReceivable(ReceiptError):
    code = "invoice_not_receivable"


@dataclass(frozen=True)
class ReceiptAllocation:
    invoice_id: int
    amount: object


@dataclass(frozen=True)
class ReceiptResult:
    receipt_id: int
    event_id: int
    voucher_id: int
    created: bool


def _money(value):
    amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError("amount must be positive")
    return amount


class CustomerReceiptService:
    def __init__(self, connection):
        self.connection = connection
        self.posting = PostingService(connection)

    def _invoice(self, invoice_id, customer_id):
        self.connection.execute(
            "select pg_advisory_xact_lock(hashtextextended(?,0))",
            (f"receipt:invoice:{invoice_id}",),
        )
        row = self.connection.execute(
            "select i.id,i.client_id,i.currency,i.status,"
            "coalesce(sum(ii.amount * (1 + ii.tax_rate / 100.0)),0) as total "
            "from invoices i left join invoice_items ii on ii.invoice_id=i.id "
            "where i.id=? group by i.id", (invoice_id,),
        ).fetchone()
        if (not row or int(row["client_id"]) != customer_id or
                row["status"] != "completed" or row["currency"] != "USD"):
            raise InvoiceNotReceivable("invoice is not a confirmed USD receivable")
        total = Decimal(str(row["total"])).quantize(CENT, rounding=ROUND_HALF_UP)
        correction_delta = self.connection.execute(
            "select coalesce(sum(revenue_delta+sales_tax_delta),0) "
            "from invoice_accounting_corrections where invoice_id=? and status='posted'",
            (invoice_id,),
        ).fetchone()[0]
        total += Decimal(str(correction_delta)).quantize(CENT)
        allocated = self.connection.execute(
            "select coalesce(sum(amount),0) from receipt_allocations "
            "where invoice_id=? and status='active' and redirected_to is null",
            (invoice_id,),
        ).fetchone()[0]
        return total, Decimal(str(allocated)).quantize(CENT, rounding=ROUND_HALF_UP)

    def receive(self, *, receipt_no, customer_id, receipt_date, amount,
                bank_account_id, allocations=(), prepayment_reason="",
                actor_id=None, idempotency_key=None):
        amount = _money(amount)
        receipt_date = date.fromisoformat(str(receipt_date)).isoformat()
        normalized = []
        seen = set()
        for item in allocations:
            if item.invoice_id in seen:
                raise ValueError("an invoice may appear only once per receipt")
            seen.add(item.invoice_id)
            normalized.append((int(item.invoice_id), _money(item.amount)))
        allocated_total = sum((item[1] for item in normalized), Decimal("0"))
        if allocated_total > amount:
            raise ReceiptError("allocations exceed receipt amount")
        prepayment = amount - allocated_total
        if prepayment and not str(prepayment_reason or "").strip():
            raise UnallocatedReceipt("unallocated receipt requires a prepayment reason")

        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute("SAVEPOINT customer_receipt")
        try:
            existing = self.connection.execute(
                "select * from customer_receipts where customer_id=? and receipt_no=?",
                (customer_id, receipt_no),
            ).fetchone()
            if existing:
                existing_allocations = self.connection.execute(
                    "select invoice_id,amount from receipt_allocations "
                    "where receipt_id=? and status='active' and redirected_to is null "
                    "order by invoice_id", (existing["id"],),
                ).fetchall()
                actual_allocations = [
                    (int(row[0]), Decimal(str(row[1])).quantize(CENT))
                    for row in existing_allocations
                ]
                desired_allocations = sorted(normalized)
                existing_prepayment = self.connection.execute(
                    "select original_amount,reason_code from customer_prepayments "
                    "where receipt_id=? and status='open'", (existing["id"],),
                ).fetchone()
                actual_prepayment = (
                    Decimal(str(existing_prepayment[0])).quantize(CENT)
                    if existing_prepayment else Decimal("0.00")
                )
                actual_reason = existing_prepayment[1] if existing_prepayment else ""
                same = (
                    str(existing["receipt_date"]) == receipt_date and
                    Decimal(str(existing["amount"])) == amount and
                    int(existing["bank_account_id"]) == bank_account_id and
                    existing["currency"] == "USD" and
                    actual_allocations == desired_allocations and
                    actual_prepayment == prepayment and
                    actual_reason == str(prepayment_reason or "").strip()
                )
                if not same or not existing["posting_event_id"]:
                    raise ReceiptConflict("receipt number already belongs to different facts")
                self.connection.execute("RELEASE SAVEPOINT customer_receipt")
                return ReceiptResult(
                    int(existing["id"]), int(existing["posting_event_id"]),
                    int(existing["voucher_id"]), False,
                )

            bank = self.connection.execute(
                "select id from bank_accounts where id=? and is_active=1 and currency='USD'",
                (bank_account_id,),
            ).fetchone()
            if not bank:
                raise ReceiptError("active USD bank account is required")

            invoice_limits = {}
            for invoice_id, allocation_amount in normalized:
                total, occupied = self._invoice(invoice_id, customer_id)
                if occupied + allocation_amount > total:
                    raise ReceiptError("allocation exceeds invoice remaining balance")
                invoice_limits[("receivable", invoice_id)] = total

            cursor = self.connection.execute(
                "insert into customer_receipts(receipt_no,customer_id,receipt_date,amount,"
                "currency,bank_account_id,reason_code,created_by) values(?,?,?,?,?,?,?,?)",
                (receipt_no, customer_id, receipt_date, str(amount), "USD",
                 bank_account_id, str(prepayment_reason or "").strip(), actor_id),
            )
            receipt_id = int(cursor.lastrowid)
            lines = [PostingLine("1000", debit=amount, source_line_type="receipt",
                                 source_line_id=receipt_id, memo=receipt_no)]
            settlements = []
            for line_no, (invoice_id, allocation_amount) in enumerate(normalized, 2):
                lines.append(PostingLine(
                    "1100", credit=allocation_amount, source_line_type="invoice",
                    source_line_id=invoice_id, memo=receipt_no,
                ))
                settlements.append(SettlementLine(
                    line_no, "receivable", invoice_id, allocation_amount,
                ))
            if prepayment:
                lines.append(PostingLine(
                    "2200", credit=prepayment, source_line_type="customer",
                    source_line_id=customer_id, memo=prepayment_reason,
                ))
            snapshot = {
                "receipt_no": receipt_no,
                "customer_id": customer_id,
                "amount": format(amount, ".2f"),
                "allocations": [
                    {"invoice_id": invoice_id, "amount": format(value, ".2f")}
                    for invoice_id, value in normalized
                ],
                "prepayment": format(prepayment, ".2f"),
                "prepayment_reason": str(prepayment_reason or "").strip(),
            }
            event = self.posting.post_event(
                key=EventKey("customer_receipt", receipt_id, 1, "customer.received"),
                business_anchor_type="customer_receipt", business_anchor_id=receipt_id,
                business_date=receipt_date, accounting_date=receipt_date, lines=lines,
                description=f"Customer receipt {receipt_no}", source_snapshot=snapshot,
                actor_id=actor_id, idempotency_key=idempotency_key,
                settlements=settlements, settlement_limits=invoice_limits,
            )
            for invoice_id, allocation_amount in normalized:
                self.connection.execute(
                    "insert into receipt_allocations(receipt_id,invoice_id,amount,currency,"
                    "source_event_id) values(?,?,?,'USD',?)",
                    (receipt_id, invoice_id, str(allocation_amount), event.event_id),
                )
            if prepayment:
                self.connection.execute(
                    "insert into customer_prepayments(customer_id,receipt_id,original_amount,"
                    "currency,reason_code,status,posting_event_id,voucher_id) "
                    "values(?,?,?,'USD',?,'open',?,?)",
                    (customer_id, receipt_id, str(prepayment), prepayment_reason.strip(),
                     event.event_id, event.voucher_id),
                )
            self.connection.execute(
                "update customer_receipts set posting_event_id=?,voucher_id=? where id=?",
                (event.event_id, event.voucher_id, receipt_id),
            )
            self.connection.execute("RELEASE SAVEPOINT customer_receipt")
            return ReceiptResult(receipt_id, event.event_id, event.voucher_id, True)
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT customer_receipt")
            self.connection.execute("RELEASE SAVEPOINT customer_receipt")
            raise
