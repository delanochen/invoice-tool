"""Legacy-compatible dashboard_projects helpers; explicit runtime dependencies."""

def available_projects_for_access(*, client_filter_clause, db):
    access_clause, access_params = client_filter_clause("invoices")
    rows = db().execute(
        f"""
        select distinct projects.id, projects.name
        from projects
        join invoice_items on invoice_items.project_id = projects.id
        join invoices on invoices.id = invoice_items.invoice_id
        where invoices.status = 'completed' and {access_clause}
        order by projects.name
        """,
        access_params,
    ).fetchall()
    if rows:
        return rows
    return db().execute(
        "select id, name from projects where project_type = 'invoice' and is_active = 1 order by name"
    ).fetchall()


def dashboard_project_options(*, available_projects_for_access, project_color):
    return [
        {"id": row["id"], "name": row["name"], "color": project_color(row["id"])}
        for row in available_projects_for_access()
    ]


def report_project_options(*, client_filter_clause, db, is_external_user):
    if is_external_user():
        access_clause, access_params = client_filter_clause("invoices")
        return db().execute(
            f"""
            select distinct projects.id, projects.name
            from projects
            join invoice_items on invoice_items.project_id = projects.id
            join invoices on invoices.id = invoice_items.invoice_id
            where invoices.status != 'void' and {access_clause}
            order by projects.name
            """,
            access_params,
        ).fetchall()
    return db().execute(
        """
        select id, name from projects where project_type = 'invoice' and is_active = 1
        union
        select distinct projects.id, projects.name
        from projects
        join invoice_items on invoice_items.project_id = projects.id
        join invoices on invoices.id = invoice_items.invoice_id
        where invoices.status != 'void'
        order by name
        """
    ).fetchall()
