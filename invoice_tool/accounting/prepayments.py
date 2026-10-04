"""Apply customer prepayments to confirmed receivables."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .posting import EventKey, PostingLine, PostingService, SettlementLine


CENT = Decimal("0.01")


class PrepaymentApplicationError(RuntimeError):
    code = "prepayment_application_error"


@dataclass(frozen=True)
class PrepaymentApplicationResult:
    application_id: int
    event_id: int
    voucher_id: int
    occurrence_no: int


def _money(value):
    amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError("amount must be positive")
    return amount


class CustomerPrepaymentService:
    def __init__(self, connection):
        self.connection = connection
        self.posting = PostingService(connection)

    def apply(self, *, prepayment_id, invoice_id, amount, accounting_date,
              actor_id=None, idempotency_key=None):
        amount = _money(amount)
        accounting_date = date.fromisoformat(str(accounting_date)).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute("SAVEPOINT prepayment_application")
        try:
            for lock_key in sorted((f"prepayment:{prepayment_id}",
                                    f"receipt:invoice:{invoice_id}")):
                self.connection.execute(
                    "select pg_advisory_xact_lock(hashtextextended(?,0))", (lock_key,)
                )
            prepayment = self.connection.execute(
                "select p.*,r.receipt_date from customer_prepayments p "
                "join customer_receipts r on r.id=p.receipt_id where p.id=?",
                (prepayment_id,),
            ).fetchone()
            invoice = self.connection.execute(
                "select i.id,i.client_id,i.currency,i.status,"
                "coalesce(sum(ii.amount * (1 + ii.tax_rate / 100.0)),0) as total "
                "from invoices i left join invoice_items ii on ii.invoice_id=i.id "
                "where i.id=? group by i.id", (invoice_id,),
            ).fetchone()
            if not prepayment or prepayment["status"] != "open":
                raise PrepaymentApplicationError("prepayment is not open")
            if (not invoice or invoice["status"] != "completed" or
                    invoice["currency"] != "USD"):
                raise PrepaymentApplicationError("invoice is not a confirmed USD receivable")
            if int(prepayment["customer_id"]) != int(invoice["client_id"]):
                raise PrepaymentApplicationError("prepayment and invoice customers differ")

            prepayment_limit = Decimal(str(prepayment["original_amount"])).quantize(CENT)
            invoice_limit = Decimal(str(invoice["total"])).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            correction_delta = self.connection.execute(
                "select coalesce(sum(revenue_delta+sales_tax_delta),0) "
                "from invoice_accounting_corrections where invoice_id=? and status='posted'",
                (invoice_id,),
            ).fetchone()[0]
            invoice_limit += Decimal(str(correction_delta)).quantize(CENT)
            used_prepayment = Decimal(str(self.connection.execute(
                "select coalesce(sum(amount),0) from customer_prepayment_applications "
                "where prepayment_id=? and status='active'", (prepayment_id,),
            ).fetchone()[0])).quantize(CENT)
            used_receivable = Decimal(str(self.connection.execute(
                "select coalesce(sum(amount),0) from voucher_settlement_lines "
                "where target_type='receivable' and target_id=? and status='active'",
                (invoice_id,),
            ).fetchone()[0])).quantize(CENT)
            if used_prepayment + amount > prepayment_limit:
                raise PrepaymentApplicationError("application exceeds prepayment balance")
            if used_receivable + amount > invoice_limit:
                raise PrepaymentApplicationError("application exceeds receivable balance")

            occurrence = int(self.connection.execute(
                "select coalesce(max(occurrence_no),0)+1 "
                "from customer_prepayment_applications "
                "where prepayment_id=? and invoice_id=?",
                (prepayment_id, invoice_id),
            ).fetchone()[0])
            cursor = self.connection.execute(
                "insert into customer_prepayment_applications(prepayment_id,invoice_id,"
                "amount,currency,occurrence_no,status,created_by) "
                "values(?,?,?,'USD',?,'active',?)",
                (prepayment_id, invoice_id, str(amount), occurrence, actor_id),
            )
            application_id = int(cursor.lastrowid)
            snapshot = {
                "application_id": application_id,
                "prepayment_id": prepayment_id,
                "invoice_id": invoice_id,
                "amount": format(amount, ".2f"),
                "occurrence_no": occurrence,
            }
            event = self.posting.post_event(
                key=EventKey("prepayment_application", application_id, 1,
                             "prepayment.applied", occurrence),
                business_anchor_type="prepayment_application",
                business_anchor_id=application_id,
                business_date=accounting_date, accounting_date=accounting_date,
                lines=(
                    PostingLine("2200", debit=amount,
                                source_line_type="customer_prepayment",
                                source_line_id=prepayment_id),
                    PostingLine("1100", credit=amount, source_line_type="invoice",
                                source_line_id=invoice_id),
                ),
                settlements=(
                    SettlementLine(1, "prepayment", prepayment_id, amount),
                    SettlementLine(2, "receivable", invoice_id, amount),
                ),
                settlement_limits={
                    ("prepayment", prepayment_id): prepayment_limit,
                    ("receivable", invoice_id): invoice_limit,
                },
                description=f"Apply customer prepayment {prepayment_id}",
                source_snapshot=snapshot, actor_id=actor_id,
                idempotency_key=idempotency_key,
            )
            self.connection.execute(
                "update customer_prepayment_applications set posting_event_id=?,voucher_id=? "
                "where id=?", (event.event_id, event.voucher_id, application_id),
            )
            if used_prepayment + amount == prepayment_limit:
                self.connection.execute(
                    "update customer_prepayments set status='applied' where id=?",
                    (prepayment_id,),
                )
            self.connection.execute("RELEASE SAVEPOINT prepayment_application")
            return PrepaymentApplicationResult(
                application_id, event.event_id, event.voucher_id, occurrence
            )
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT prepayment_application")
            self.connection.execute("RELEASE SAVEPOINT prepayment_application")
            raise
