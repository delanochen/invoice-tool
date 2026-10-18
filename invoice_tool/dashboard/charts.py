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


def monthly_project_chart(selected_project_ids, *, client_filter_clause, db, project_color):
    access_clause, access_params = client_filter_clause("invoices")
    clauses = ["invoices.status = 'completed'", access_clause]
    params = list(access_params)
    if selected_project_ids:
        placeholders = ",".join("?" for _ in selected_project_ids)
        clauses.append(f"invoice_items.project_id in ({placeholders})")
        params.extend(selected_project_ids)
    rows = db().execute(
        f"""
        select substr(invoices.issue_date, 1, 7) as month, projects.id as project_id,
               projects.name as project_name, sum(invoice_items.amount + invoice_items.amount * invoice_items.tax_rate / 100) as total
        from invoice_items
        join invoices on invoices.id = invoice_items.invoice_id
        join projects on projects.id = invoice_items.project_id
        where {" and ".join(clauses)}
        group by month, projects.id, projects.name
        order by month asc, projects.name asc
        """,
        params,
    ).fetchall()
    buckets = {}
    project_names = {}
    for row in rows:
        month = row["month"]
        amount = float(row["total"] or 0)
        project_id = row["project_id"]
        project_names[project_id] = row["project_name"]
        buckets.setdefault(month, {"month": month, "total": 0, "segments": []})
        buckets[month]["total"] += amount
        buckets[month]["segments"].append(
            {"project_id": project_id, "name": row["project_name"], "value": amount, "color": project_color(project_id)}
        )
    if not buckets:
        return []
    max_total = max(bucket["total"] for bucket in buckets.values()) or 1
    for bucket in buckets.values():
        bucket["height"] = round(bucket["total"] / max_total * 100)
        for segment in bucket["segments"]:
            segment["height"] = round(segment["value"] / bucket["total"] * 100) if bucket["total"] else 0
    return list(buckets.values())
