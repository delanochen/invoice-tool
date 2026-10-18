"""Business routes with explicit current-app resolvers; no reverse import."""


def register_invoice_list_routes(app, *,
    _cb_resolve_STATUS_LABELS,
    _cb_resolve_abort,
    _cb_resolve_can_view_invoices,
    _cb_resolve_client_filter_clause,
    _cb_resolve_db,
    _cb_resolve_invoice_totals,
    _cb_resolve_login_required,
    _cb_resolve_render_template,
    _cb_resolve_request,
):
    @_cb_resolve_login_required()
    def invoices():
        if not _cb_resolve_can_view_invoices()():
            _cb_resolve_abort()(403)
        paid_status = _cb_resolve_request().args.get("paid_status", "")
        work_order_status = _cb_resolve_request().args.get("work_order_status", "")
        if work_order_status not in {"", "open", "closed"}:
            work_order_status = ""
        date_from = _cb_resolve_request().args.get("date_from", "")
        date_to = _cb_resolve_request().args.get("date_to", "")
        access_clause, access_params = _cb_resolve_client_filter_clause()("invoices")
        clauses = [access_clause]
        params = list(access_params)
        order_ids = list(dict.fromkeys(value for value in _cb_resolve_request().args.getlist("order_id") if value))
        sites = list(dict.fromkeys(value for value in _cb_resolve_request().args.getlist("site") if value))
        options = _cb_resolve_db()().execute(f"select distinct service_orders.id, service_orders.order_number, service_orders.client_name from invoices join service_orders on service_orders.id=invoices.service_order_id join clients on clients.id=invoices.client_id where {access_clause} order by service_orders.order_number desc", access_params).fetchall()
        for column, values in [("service_orders.id", order_ids), ("service_orders.client_name", sites)]:
            if values:
                clauses.append(f"{column} in ({','.join('?' for _ in values)})")
                params.extend(values)
        if work_order_status:
            clauses.append("service_orders.status = ?")
            params.append(work_order_status)
        if paid_status == "paid":
            clauses.append("invoices.paid_at is not null")
        elif paid_status == "unpaid":
            clauses.append("invoices.status = 'completed' and invoices.paid_at is null")
        if date_from:
            clauses.append("invoices.issue_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("invoices.issue_date <= ?")
            params.append(date_to)
        rows = _cb_resolve_db()().execute(
            f"""
        select invoices.*, clients.name as client_name, clients.short_name as client_short_name,
               clients.client_number, users.name as creator_name,
               service_orders.order_number as service_order_number,
               service_orders.status as service_order_status,
               service_orders.client_order_number as service_client_order_number
        from invoices
        join clients on clients.id = invoices.client_id
        left join users on users.id = invoices.created_by
        left join service_orders on service_orders.id = invoices.service_order_id
        where {" and ".join(clauses)}
        order by invoices.issue_date desc, invoices.id desc
        """,
            params,
        ).fetchall()
        totals = {row["id"]: _cb_resolve_invoice_totals()(row["id"]) for row in rows}
        summary_totals = {}
        for row in rows:
            currency = row["currency"] or "USD"
            summary_totals[currency] = summary_totals.get(currency, 0) + totals[row["id"]]["total"]
        summary_totals = dict(sorted(summary_totals.items()))
        return _cb_resolve_render_template()(
            "invoices.html",
            invoices=rows,
            totals=totals,
            summary_totals=summary_totals,
            labels=_cb_resolve_STATUS_LABELS(),
            order_ids=order_ids, sites=sites, order_options=options,
            site_options=[{"client_name": name} for name in sorted({row["client_name"] for row in options if row["client_name"]})],
            paid_status=paid_status,
            work_order_status=work_order_status,
            date_from=date_from,
            date_to=date_to,
        )


    app.add_url_rule('/invoices', endpoint='invoices', view_func=invoices, methods=['GET'])
    return {'invoices': invoices}
