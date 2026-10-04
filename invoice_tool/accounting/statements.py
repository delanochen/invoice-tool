"""Customer accounts-receivable statements from posted GL movements."""

from decimal import Decimal


EVENT_LABELS = {
    "invoice.confirmed": "Invoice",
    "invoice.voided": "Invoice void",
    "invoice.corrected": "Invoice correction",
    "invoice.correction_reversed": "Correction reversal",
    "customer.received": "Customer receipt",
    "prepayment.applied": "Prepayment applied",
    "opening.established": "Opening balance",
}


class CustomerStatementService:
    def __init__(self, connection):
        self.connection = connection

    def statement(self, customer_id, *, date_from=None, date_to=None):
        customer = self.connection.execute(
            "select id,name,client_number from clients where id=?", (customer_id,)
        ).fetchone()
        if not customer:
            raise ValueError("customer not found")
        rows = self.connection.execute(
            "select v.id voucher_id,v.voucher_number,v.accounting_date,v.description,"
            "pe.event_type,coalesce(i.invoice_number,ci.invoice_number,ai.invoice_number,"
            "oi.invoice_number,'') reference,sum(e.debit-e.credit) amount "
            "from vouchers v join posting_events pe on pe.voucher_id=v.id "
            "join voucher_entries e on e.voucher_id=v.id "
            "join accounts a on a.id=e.account_id and a.account_code='1100' "
            "left join invoices i on pe.source_type='invoice' and i.id=pe.source_id "
            "left join invoice_accounting_corrections cor on pe.source_type='invoice_correction' "
            "and cor.id=pe.source_id left join invoices ci on ci.id=cor.invoice_id "
            "left join customer_receipts r on pe.source_type='customer_receipt' "
            "and r.id=pe.source_id left join customer_prepayment_applications pa "
            "on pe.source_type='prepayment_application' and pa.id=pe.source_id "
            "left join invoices ai on ai.id=pa.invoice_id "
            "left join accounting_opening_lines ol on ol.voucher_entry_id=e.id "
            "and ol.origin_type in ('invoice','receivable') "
            "left join invoices oi on oi.id=ol.origin_id "
            "where v.status in ('posted','reversed') and coalesce(i.client_id,ci.client_id,"
            "r.customer_id,ai.client_id,oi.client_id)=? "
            "group by v.id,pe.event_type,i.invoice_number,ci.invoice_number,"
            "ai.invoice_number,oi.invoice_number order by v.accounting_date,v.id",
            (customer_id,),
        ).fetchall()
        opening = Decimal("0")
        transactions = []
        running = Decimal("0")
        for row in rows:
            amount = Decimal(str(row["amount"]))
            accounting_date = str(row["accounting_date"])
            if date_from and accounting_date < str(date_from):
                opening += amount
                continue
            if date_to and accounting_date > str(date_to):
                continue
            running += amount
            transactions.append({
                "voucher_id": int(row["voucher_id"]),
                "voucher_number": row["voucher_number"],
                "accounting_date": accounting_date,
                "event_type": row["event_type"],
                "event_label": EVENT_LABELS.get(row["event_type"], row["event_type"]),
                "reference": row["reference"], "description": row["description"],
                "charge": amount if amount > 0 else Decimal("0"),
                "credit": -amount if amount < 0 else Decimal("0"),
                "amount": amount, "running": opening + running,
            })
        return {
            "customer": customer, "date_from": date_from, "date_to": date_to,
            "opening_balance": opening, "rows": transactions,
            "charges": sum((row["charge"] for row in transactions), Decimal("0")),
            "credits": sum((row["credit"] for row in transactions), Decimal("0")),
            "closing_balance": opening + running,
        }
