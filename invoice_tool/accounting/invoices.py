"""Customer invoice recognition posting."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .posting import EventKey, PostingLine, PostingService


CENT = Decimal("0.01")


class InvoiceRecognitionError(RuntimeError):
    code = "invoice_recognition_error"


class InvoiceVoidError(RuntimeError):
    code = "invoice_void_error"


@dataclass(frozen=True)
class InvoiceRecognitionResult:
    event_id: int
    voucher_id: int
    created: bool
    receivable: Decimal
    revenue: Decimal
    sales_tax: Decimal


@dataclass(frozen=True)
class InvoiceVoidResult:
    event_id: int
    voucher_id: int
    original_voucher_id: int
    created: bool


@dataclass(frozen=True)
class InvoiceCorrectionResult:
    correction_id: int
    correction_no: int
    event_id: int
    voucher_id: int
    receivable_delta: Decimal


@dataclass(frozen=True)
class InvoiceCorrectionReversalResult:
    correction_id: int
    event_id: int
    voucher_id: int
    original_voucher_id: int


def _money(value):
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


class InvoiceRecognitionService:
    def __init__(self, connection):
        self.connection = connection
        self.posting = PostingService(connection)

    def recognize(self, invoice_id, *, source_version=1, accounting_date=None,
                  actor_id=None, idempotency_key=None):
        if int(source_version) < 1:
            raise ValueError("source_version must be positive")
        invoice = self.connection.execute(
            "select id,invoice_number,client_id,issue_date,currency,status "
            "from invoices where id=?", (invoice_id,),
        ).fetchone()
        if not invoice or invoice["status"] != "completed":
            raise InvoiceRecognitionError("only a completed invoice can be recognized")
        if invoice["currency"] != "USD":
            raise InvoiceRecognitionError("only USD invoices can be recognized")
        items = self.connection.execute(
            "select id,project_id,description,amount,tax_rate from invoice_items "
            "where invoice_id=? order by id", (invoice_id,),
        ).fetchall()
        if not items:
            raise InvoiceRecognitionError("invoice has no recognition lines")

        snapshot_items = []
        revenue = Decimal("0.00")
        sales_tax = Decimal("0.00")
        for item in items:
            base = _money(item["amount"])
            if base < 0:
                raise InvoiceRecognitionError("invoice line amount cannot be negative")
            rate = Decimal(str(item["tax_rate"] or 0))
            if rate < 0:
                raise InvoiceRecognitionError("invoice tax rate cannot be negative")
            tax = (base * rate / Decimal("100")).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            revenue += base
            sales_tax += tax
            snapshot_items.append({
                "id": int(item["id"]),
                "project_id": int(item["project_id"]),
                "description": item["description"],
                "amount": format(base, ".2f"),
                "tax_rate": format(rate, "f"),
                "tax": format(tax, ".2f"),
            })
        receivable = revenue + sales_tax
        if receivable <= 0:
            raise InvoiceRecognitionError("invoice total must be positive")
        posting_date = date.fromisoformat(
            str(accounting_date or invoice["issue_date"])
        ).isoformat()
        lines = [PostingLine(
            "1100", debit=receivable, source_line_type="invoice",
            source_line_id=invoice_id, memo=invoice["invoice_number"],
        ), PostingLine(
            "4000", credit=revenue, source_line_type="invoice",
            source_line_id=invoice_id, memo=invoice["invoice_number"],
        )]
        if sales_tax:
            lines.append(PostingLine(
                "2100", credit=sales_tax, source_line_type="invoice",
                source_line_id=invoice_id, memo=invoice["invoice_number"],
            ))
        snapshot = {
            "invoice_id": invoice_id,
            "invoice_number": invoice["invoice_number"],
            "customer_id": int(invoice["client_id"]),
            "currency": "USD",
            "source_version": int(source_version),
            "items": snapshot_items,
            "revenue": format(revenue, ".2f"),
            "sales_tax": format(sales_tax, ".2f"),
            "receivable": format(receivable, ".2f"),
        }
        result = self.posting.post_event(
            key=EventKey("invoice", invoice_id, int(source_version),
                         "invoice.confirmed"),
            business_anchor_type="invoice", business_anchor_id=invoice_id,
            business_date=invoice["issue_date"], accounting_date=posting_date,
            lines=lines, description=f"Invoice {invoice['invoice_number']} confirmed",
            source_snapshot=snapshot, actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
        return InvoiceRecognitionResult(
            result.event_id, result.voucher_id, result.created,
            receivable, revenue, sales_tax,
        )

    def void(self, invoice_id, *, accounting_date, reason_code,
             actor_id=None, idempotency_key=None):
        if not str(reason_code or "").strip():
            raise ValueError("reason_code is required")
        invoice = self.connection.execute(
            "select id,invoice_number,status,paid_at from invoices where id=?",
            (invoice_id,),
        ).fetchone()
        if not invoice:
            raise InvoiceVoidError("invoice not found")
        original = self.connection.execute(
            "select e.id as event_id,e.source_version,e.voucher_id,v.status,v.business_date,"
            "v.source_snapshot from posting_events e join vouchers v on v.id=e.voucher_id "
            "where e.source_type='invoice' and e.source_id=? "
            "and e.event_type='invoice.confirmed' order by e.source_version desc limit 1",
            (invoice_id,),
        ).fetchone()
        if not original:
            raise InvoiceVoidError("invoice has no recognition voucher")
        if invoice["paid_at"]:
            raise InvoiceVoidError("paid invoice must be refunded before voiding")
        active_settlement = self.connection.execute(
            "select 1 from voucher_settlement_lines where target_type='receivable' "
            "and target_id=? and status='active' limit 1", (invoice_id,),
        ).fetchone()
        if active_settlement:
            raise InvoiceVoidError("invoice has active settlements")
        original_lines = self.connection.execute(
            "select a.account_code,e.debit,e.credit,e.source_line_type,e.source_line_id,e.memo "
            "from voucher_entries e join accounts a on a.id=e.account_id "
            "where e.voucher_id=? order by e.line_no", (original["voucher_id"],),
        ).fetchall()
        if not original_lines:
            raise InvoiceVoidError("recognition voucher has no entries")
        reversing_lines = tuple(PostingLine(
            row["account_code"], debit=row["credit"], credit=row["debit"],
            source_line_type=row["source_line_type"],
            source_line_id=row["source_line_id"], memo=row["memo"],
        ) for row in original_lines)
        snapshot = {
            "invoice_id": invoice_id,
            "invoice_number": invoice["invoice_number"],
            "original_event_id": int(original["event_id"]),
            "original_voucher_id": int(original["voucher_id"]),
            "original_source_snapshot": original["source_snapshot"],
            "reason_code": reason_code.strip(),
        }
        result = self.posting.post_event(
            key=EventKey("invoice", invoice_id, int(original["source_version"]),
                         "invoice.voided", 1),
            business_anchor_type="invoice", business_anchor_id=invoice_id,
            business_date=original["business_date"], accounting_date=accounting_date,
            lines=reversing_lines,
            description=f"Void invoice {invoice['invoice_number']}",
            source_snapshot=snapshot, actor_id=actor_id,
            idempotency_key=idempotency_key, reversal_of=original["voucher_id"],
            reason_code=reason_code.strip(),
        )
        if original["status"] == "posted":
            self.posting.mark_reversed(
                int(original["voucher_id"]), actor_id=actor_id,
                reason_code=reason_code.strip(),
            )
        self.connection.execute(
            "update invoices set status='void' where id=?", (invoice_id,)
        )
        return InvoiceVoidResult(
            result.event_id, result.voucher_id, int(original["voucher_id"]), result.created
        )

    def correct(self, invoice_id, *, revenue_delta=0, sales_tax_delta=0,
                accounting_date, reason_code, actor_id=None):
        revenue_delta = _money(revenue_delta)
        sales_tax_delta = _money(sales_tax_delta)
        receivable_delta = revenue_delta + sales_tax_delta
        if revenue_delta == 0 and sales_tax_delta == 0:
            raise InvoiceRecognitionError("correction amount cannot be zero")
        if not str(reason_code or "").strip():
            raise ValueError("reason_code is required")
        accounting_date = date.fromisoformat(str(accounting_date)).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute("SAVEPOINT invoice_correction")
        try:
            self.connection.execute(
                "select pg_advisory_xact_lock(hashtextextended(?,0))",
                (f"invoice-correction:{invoice_id}",),
            )
            invoice = self.connection.execute(
                "select id,invoice_number,status,currency from invoices where id=?",
                (invoice_id,),
            ).fetchone()
            recognition = self.connection.execute(
                "select 1 from posting_events where source_type='invoice' and source_id=? "
                "and event_type='invoice.confirmed' limit 1", (invoice_id,),
            ).fetchone()
            if (not invoice or invoice["status"] != "completed" or
                    invoice["currency"] != "USD" or not recognition):
                raise InvoiceRecognitionError("invoice is not an active recognized receivable")
            if self.connection.execute(
                "select 1 from voucher_settlement_lines where target_type='receivable' "
                "and target_id=? and status='active' limit 1", (invoice_id,),
            ).fetchone():
                raise InvoiceRecognitionError(
                    "invoice with active settlements must be unallocated before correction"
                )
            base_total = Decimal(str(self.connection.execute(
                "select coalesce(sum(amount*(1+tax_rate/100.0)),0) "
                "from invoice_items where invoice_id=?", (invoice_id,),
            ).fetchone()[0])).quantize(CENT, rounding=ROUND_HALF_UP)
            prior_delta = Decimal(str(self.connection.execute(
                "select coalesce(sum(revenue_delta+sales_tax_delta),0) "
                "from invoice_accounting_corrections where invoice_id=? and status='posted'",
                (invoice_id,),
            ).fetchone()[0])).quantize(CENT)
            if base_total + prior_delta + receivable_delta <= 0:
                raise InvoiceRecognitionError("correction would make receivable non-positive")
            correction_no = int(self.connection.execute(
                "select coalesce(max(correction_no),0)+1 "
                "from invoice_accounting_corrections where invoice_id=?",
                (invoice_id,),
            ).fetchone()[0])
            cursor = self.connection.execute(
                "insert into invoice_accounting_corrections(invoice_id,correction_no,"
                "accounting_date,revenue_delta,sales_tax_delta,reason_code,created_by) "
                "values(?,?,?,?,?,?,?)",
                (invoice_id, correction_no, accounting_date, str(revenue_delta),
                 str(sales_tax_delta), reason_code.strip(), actor_id),
            )
            correction_id = int(cursor.lastrowid)
            lines = []
            if receivable_delta:
                lines.append(PostingLine(
                    "1100", debit=receivable_delta if receivable_delta > 0 else 0,
                    credit=-receivable_delta if receivable_delta < 0 else 0,
                    source_line_type="invoice_correction", source_line_id=correction_id,
                ))
            if revenue_delta:
                lines.append(PostingLine(
                    "4000", credit=revenue_delta if revenue_delta > 0 else 0,
                    debit=-revenue_delta if revenue_delta < 0 else 0,
                    source_line_type="invoice_correction", source_line_id=correction_id,
                ))
            if sales_tax_delta:
                lines.append(PostingLine(
                    "2100", credit=sales_tax_delta if sales_tax_delta > 0 else 0,
                    debit=-sales_tax_delta if sales_tax_delta < 0 else 0,
                    source_line_type="invoice_correction", source_line_id=correction_id,
                ))
            result = self.posting.post_event(
                key=EventKey("invoice_correction", correction_id, 1,
                             "invoice.corrected", correction_no),
                business_anchor_type="invoice_correction",
                business_anchor_id=correction_id,
                business_date=accounting_date, accounting_date=accounting_date,
                lines=tuple(lines),
                description=f"Invoice {invoice['invoice_number']} correction {correction_no}",
                source_snapshot={
                    "invoice_id": invoice_id, "correction_no": correction_no,
                    "revenue_delta": format(revenue_delta, ".2f"),
                    "sales_tax_delta": format(sales_tax_delta, ".2f"),
                    "receivable_delta": format(receivable_delta, ".2f"),
                    "reason_code": reason_code.strip(),
                },
                actor_id=actor_id, reason_code=reason_code.strip(),
            )
            self.connection.execute(
                "update invoice_accounting_corrections set posting_event_id=?,voucher_id=? "
                "where id=?", (result.event_id, result.voucher_id, correction_id),
            )
            self.connection.execute("RELEASE SAVEPOINT invoice_correction")
            return InvoiceCorrectionResult(
                correction_id, correction_no, result.event_id,
                result.voucher_id, receivable_delta,
            )
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT invoice_correction")
            self.connection.execute("RELEASE SAVEPOINT invoice_correction")
            raise

    def reverse_correction(self, correction_id, *, accounting_date, reason_code,
                           actor_id=None):
        if not str(reason_code or "").strip():
            raise ValueError("reason_code is required")
        accounting_date = date.fromisoformat(str(accounting_date)).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute("SAVEPOINT invoice_correction_reversal")
        try:
            self.connection.execute(
                "select pg_advisory_xact_lock(hashtextextended(?,0))",
                (f"invoice-correction-reversal:{correction_id}",),
            )
            correction = self.connection.execute(
                "select c.*,i.invoice_number,i.status as invoice_status "
                "from invoice_accounting_corrections c "
                "join invoices i on i.id=c.invoice_id where c.id=?",
                (correction_id,),
            ).fetchone()
            if not correction or correction["status"] != "posted":
                raise InvoiceRecognitionError("only a posted correction can be reversed")
            if correction["invoice_status"] != "completed":
                raise InvoiceRecognitionError("invoice is not active")
            if self.connection.execute(
                "select 1 from voucher_settlement_lines where target_type='receivable' "
                "and target_id=? and status='active' limit 1",
                (correction["invoice_id"],),
            ).fetchone():
                raise InvoiceRecognitionError(
                    "invoice with active settlements must be unallocated before reversal"
                )
            original_entries = self.connection.execute(
                "select a.account_code,e.debit,e.credit,e.source_line_type,e.source_line_id "
                "from voucher_entries e join accounts a on a.id=e.account_id "
                "where e.voucher_id=? order by e.line_no",
                (correction["voucher_id"],),
            ).fetchall()
            if not original_entries:
                raise InvoiceRecognitionError("correction voucher has no entries")
            lines = tuple(PostingLine(
                row["account_code"], debit=row["credit"], credit=row["debit"],
                source_line_type="invoice_correction_reversal",
                source_line_id=correction_id,
            ) for row in original_entries)
            result = self.posting.post_event(
                key=EventKey("invoice_correction", correction_id, 1,
                             "invoice.correction_reversed", 1),
                business_anchor_type="invoice_correction",
                business_anchor_id=correction_id,
                business_date=accounting_date, accounting_date=accounting_date,
                lines=lines,
                description=(f"Reverse invoice {correction['invoice_number']} "
                             f"correction {correction['correction_no']}"),
                source_snapshot={
                    "invoice_id": int(correction["invoice_id"]),
                    "correction_id": correction_id,
                    "correction_no": int(correction["correction_no"]),
                    "original_voucher_id": int(correction["voucher_id"]),
                    "reason_code": reason_code.strip(),
                },
                actor_id=actor_id, reversal_of=correction["voucher_id"],
                reason_code=reason_code.strip(),
            )
            self.posting.mark_reversed(
                int(correction["voucher_id"]), actor_id=actor_id,
                reason_code=reason_code.strip(),
            )
            self.connection.execute(
                "update invoice_accounting_corrections set status='reversed',"
                "reversal_event_id=?,reversal_voucher_id=?,reversed_by=?,"
                "reversed_at=CURRENT_TIMESTAMP::text,reversal_reason=? where id=?",
                (result.event_id, result.voucher_id, actor_id,
                 reason_code.strip(), correction_id),
            )
            self.connection.execute("RELEASE SAVEPOINT invoice_correction_reversal")
            return InvoiceCorrectionReversalResult(
                correction_id, result.event_id, result.voucher_id,
                int(correction["voucher_id"]),
            )
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT invoice_correction_reversal")
            self.connection.execute("RELEASE SAVEPOINT invoice_correction_reversal")
            raise
