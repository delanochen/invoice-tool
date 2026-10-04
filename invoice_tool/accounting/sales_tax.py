"""Sales-tax liability roll-forward for CPA reconciliation."""

from decimal import Decimal


EVENT_LABELS = {
    "invoice.confirmed": "Invoice tax accrued",
    "invoice.voided": "Invoice tax reversed",
    "invoice.corrected": "Tax correction",
    "invoice.correction_reversed": "Tax correction reversed",
    "opening.established": "Opening balance",
}


class SalesTaxReportService:
    def __init__(self, connection):
        self.connection = connection

    def report(self, *, date_from=None, date_to=None):
        rows = self.connection.execute(
            "select v.id voucher_id,v.voucher_number,v.accounting_date,v.description,"
            "pe.event_type,coalesce(i.invoice_number,ci.invoice_number,oi.invoice_number,'') "
            "reference,coalesce(c.name,cc.name,oc.name,'') customer_name,"
            "sum(e.credit-e.debit) amount "
            "from vouchers v join posting_events pe on pe.voucher_id=v.id "
            "join voucher_entries e on e.voucher_id=v.id "
            "join accounts a on a.id=e.account_id and a.account_code='2100' "
            "left join invoices i on pe.source_type='invoice' and i.id=pe.source_id "
            "left join clients c on c.id=i.client_id "
            "left join invoice_accounting_corrections cor on pe.source_type='invoice_correction' "
            "and cor.id=pe.source_id left join invoices ci on ci.id=cor.invoice_id "
            "left join clients cc on cc.id=ci.client_id "
            "left join accounting_opening_lines ol on ol.voucher_entry_id=e.id "
            "and ol.origin_type in ('invoice','sales_tax') "
            "left join invoices oi on ol.origin_type='invoice' and oi.id=ol.origin_id "
            "left join clients oc on oc.id=oi.client_id "
            "where v.status in ('posted','reversed') "
            "group by v.id,pe.event_type,i.invoice_number,ci.invoice_number,oi.invoice_number,"
            "c.name,cc.name,oc.name order by v.accounting_date,v.id",
        ).fetchall()
        opening = Decimal("0")
        movement = Decimal("0")
        transactions = []
        for row in rows:
            amount = Decimal(str(row["amount"]))
            accounting_date = str(row["accounting_date"])
            if date_from and accounting_date < str(date_from):
                opening += amount
                continue
            if date_to and accounting_date > str(date_to):
                continue
            movement += amount
            transactions.append({
                "voucher_id": int(row["voucher_id"]),
                "voucher_number": row["voucher_number"],
                "accounting_date": accounting_date,
                "event_type": row["event_type"],
                "event_label": EVENT_LABELS.get(row["event_type"], row["event_type"]),
                "reference": row["reference"], "customer_name": row["customer_name"],
                "description": row["description"],
                "increase": amount if amount > 0 else Decimal("0"),
                "decrease": -amount if amount < 0 else Decimal("0"),
                "amount": amount, "running": opening + movement,
            })
        increases = sum((row["increase"] for row in transactions), Decimal("0"))
        decreases = sum((row["decrease"] for row in transactions), Decimal("0"))
        return {
            "date_from": date_from, "date_to": date_to,
            "opening_balance": opening, "rows": transactions,
            "increases": increases, "decreases": decreases,
            "net_movement": increases - decreases,
            "closing_balance": opening + movement,
        }
