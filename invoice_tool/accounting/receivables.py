"""Accounts-receivable aging report."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")
BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "days_90_plus")


def _money(value):
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


class ReceivableAgingService:
    def __init__(self, connection):
        self.connection = connection

    def report(self, *, as_of=None, customer_id=None):
        as_of_date = date.fromisoformat(str(as_of)) if as_of else date.today()
        params = [as_of_date.isoformat(), as_of_date.isoformat(), as_of_date.isoformat()]
        customer_clause = ""
        if customer_id:
            customer_clause = " and i.client_id=?"
            params.append(int(customer_id))
        rows = self.connection.execute(
            "select i.id,i.invoice_number,i.client_id,i.issue_date,i.due_date,c.name customer_name,"
            "coalesce((select sum(ii.amount*(1+ii.tax_rate/100.0)) from invoice_items ii "
            "where ii.invoice_id=i.id),0) original_total,"
            "coalesce((select sum(cor.revenue_delta+cor.sales_tax_delta) "
            "from invoice_accounting_corrections cor "
            "join vouchers cv on cv.id=cor.voucher_id "
            "left join vouchers rv on rv.id=cor.reversal_voucher_id "
            "where cor.invoice_id=i.id and cv.accounting_date<=? "
            "and (rv.id is null or rv.accounting_date>?)),0) correction_total,"
            "coalesce((select sum(s.amount) from voucher_settlement_lines s "
            "join vouchers sv on sv.id=s.voucher_id where s.target_type='receivable' "
            "and s.target_id=i.id and s.status='active' and sv.accounting_date<=?),0) settled_total "
            "from invoices i join clients c on c.id=i.client_id "
            "where i.status='completed' and i.currency='USD' "
            "and exists(select 1 from posting_events pe join vouchers pv on pv.id=pe.voucher_id "
            "where pe.source_type='invoice' and pe.source_id=i.id "
            "and pe.event_type='invoice.confirmed' and pv.accounting_date<=?)" +
            customer_clause + " order by c.name,i.due_date,i.invoice_number",
            [*params[:3], as_of_date.isoformat(), *params[3:]],
        ).fetchall()
        aging_rows = []
        totals = {bucket: Decimal("0.00") for bucket in BUCKETS}
        customer_totals = {}
        for row in rows:
            outstanding = _money(
                Decimal(str(row["original_total"]))
                + Decimal(str(row["correction_total"]))
                - Decimal(str(row["settled_total"]))
            )
            if outstanding <= 0:
                continue
            due = date.fromisoformat(str(row["due_date"] or row["issue_date"]))
            days_overdue = (as_of_date - due).days
            if days_overdue <= 0:
                bucket = "current"
            elif days_overdue <= 30:
                bucket = "days_1_30"
            elif days_overdue <= 60:
                bucket = "days_31_60"
            elif days_overdue <= 90:
                bucket = "days_61_90"
            else:
                bucket = "days_90_plus"
            values = {name: Decimal("0.00") for name in BUCKETS}
            values[bucket] = outstanding
            totals[bucket] += outstanding
            customer = customer_totals.setdefault(
                int(row["client_id"]),
                {"customer_id": int(row["client_id"]), "customer_name": row["customer_name"],
                 **{name: Decimal("0.00") for name in BUCKETS}, "total": Decimal("0.00")},
            )
            customer[bucket] += outstanding
            customer["total"] += outstanding
            aging_rows.append({
                "invoice_id": int(row["id"]), "invoice_number": row["invoice_number"],
                "customer_id": int(row["client_id"]), "customer_name": row["customer_name"],
                "issue_date": row["issue_date"], "due_date": row["due_date"],
                "days_overdue": max(days_overdue, 0), "outstanding": outstanding,
                "bucket": bucket, **values,
            })
        grand_total = sum(totals.values(), Decimal("0.00"))
        return {
            "as_of": as_of_date.isoformat(), "rows": aging_rows,
            "customers": list(customer_totals.values()), "totals": totals,
            "grand_total": grand_total,
        }
