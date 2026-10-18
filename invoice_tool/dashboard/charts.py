"""Legacy-compatible dashboard_monthly helpers; explicit runtime dependencies."""

def monthly_chart(*, client_filter_clause, db, invoice_totals):
    access_clause, access_params = client_filter_clause("invoices")
    rows = db().execute(
        f"select id, issue_date from invoices where status = 'completed' and {access_clause} order by issue_date asc",
        access_params,
    ).fetchall()
    buckets = {}
    for row in rows:
        month = row["issue_date"][:7]
        buckets[month] = buckets.get(month, 0) + invoice_totals(row["id"])["total"]
    if not buckets:
        return []
    max_value = max(buckets.values()) or 1
    return [{"month": month, "value": value, "height": round(value / max_value * 100)} for month, value in buckets.items()]
