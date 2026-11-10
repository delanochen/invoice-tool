"""Business routes with explicit current-app resolvers; no reverse import."""
from invoice_tool.invoices import order_links as _invoice_order_links


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
        if work_order_status not in {"", "not_started", "open", "settling", "closed"}:
            work_order_status = ""
        date_from = _cb_resolve_request().args.get("date_from", "")
        date_to = _cb_resolve_request().args.get("date_to", "")
        access_clause, access_params = _cb_resolve_client_filter_clause()("invoices")
        clauses = [access_clause]
        params = list(access_params)
        order_ids = list(dict.fromkeys(value for value in _cb_resolve_request().args.getlist("order_id") if value))
        sites = list(dict.fromkeys(value for value in _cb_resolve_request().args.getlist("site") if value))
        options = _cb_resolve_db()().execute(f"select distinct service_orders.id, service_orders.order_number, service_orders.client_name from invoices join service_orders on service_orders.id=invoices.service_order_id join clients on clients.id=invoices.client_id where {access_clause} order by service_orders.order_number desc", access_params).fetchall()
        # 按工单筛发票：一张发票可能覆盖多个工单，只看旧列会漏掉"非主工单"的那几张。
        if order_ids:
            marks = ",".join("?" for _ in order_ids)
            if _invoice_order_links.supports_invoice_orders(_cb_resolve_db()()):
                clauses.append(
                    "(invoices.service_order_id in (%s) or invoices.id in (select io_f.invoice_id"
                    " from invoice_orders io_f where io_f.service_order_id in (%s)"
                    " and io_f.released_at is null))" % (marks, marks)
                )
                params.extend(order_ids)
                params.extend(order_ids)
            else:
                clauses.append("invoices.service_order_id in (%s)" % marks)
                params.extend(order_ids)
        if sites:
            clauses.append("service_orders.client_name in (%s)" % ",".join("?" for _ in sites))
            params.extend(sites)
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
        total_ids = [row["id"] for row in rows]
        # 含已释放的关联：作废过的合并发票也要能显示它当时覆盖的工单。
        linked_orders = _invoice_order_links.orders_for_invoices(total_ids, db=_cb_resolve_db(), include_released=True)
        totals = {row["id"]: _cb_resolve_invoice_totals()(row["id"]) for row in rows}
        summary_totals = {}
        for row in rows:
            currency = row["currency"] or "USD"
            summary_totals[currency] = summary_totals.get(currency, 0) + totals[row["id"]]["total"]
        summary_totals = dict(sorted(summary_totals.items()))
        return _cb_resolve_render_template()(
            "invoices.html",
            invoices=rows,
            invoice_linked_orders=linked_orders,
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


    @_cb_resolve_login_required()
    def invoice_merge():
        """合并开票选单页：列同客户、已审核未开票的工单结算，勾选后进发票表单。"""
        if not _cb_resolve_can_view_invoices()():
            _cb_resolve_abort()(403)
        connection = _cb_resolve_db()()
        rows = connection.execute(
            """
            select cr.id as reimbursement_id, cr.service_order_id, cr.total_amount, cr.status,
                   cr.created_at, so.order_number, so.client_order_number, so.client_name,
                   clients.name as client_display
            from customer_reimbursements cr
            join service_orders so on so.id = cr.service_order_id
            left join clients on clients.id = so.client_id
            where cr.status = 'approved' and cr.invoice_id is null
              and so.status != 'closed'
            order by coalesce(clients.name, so.client_name), so.order_number
            """
        ).fetchall()
        candidates = []
        for row in rows:
            if _invoice_order_links.invoice_ids_for_service_order(
                row["service_order_id"], db=_cb_resolve_db()
            ):
                continue
            candidates.append(row)
        groups = {}
        for row in candidates:
            key = row["client_display"] or row["client_name"] or "-"
            groups.setdefault(key, []).append(row)
        return _cb_resolve_render_template()(
            "invoice_merge.html",
            groups=groups,
            candidates=candidates,
            labels=_cb_resolve_STATUS_LABELS(),
        )


    app.add_url_rule('/invoices', endpoint='invoices', view_func=invoices, methods=['GET'])
    app.add_url_rule('/invoices/merge', endpoint='invoice_merge', view_func=invoice_merge, methods=['GET'])
    return {'invoices': invoices, 'invoice_merge': invoice_merge}


def register_invoice_query_page_routes(app, *,
    _cb_resolve_STATUS_LABELS,
    _cb_resolve_abort,
    _cb_resolve_can_view_invoices,
    _cb_resolve_client_filter_clause,
    _cb_resolve_db,
    _cb_resolve_login_required,
    _cb_resolve_render_template,
    _cb_resolve_report_location_filters,
    _cb_resolve_report_project_options,
    _cb_resolve_request,
):
    @_cb_resolve_login_required()
    def invoice_query():
        if not _cb_resolve_can_view_invoices()():
            _cb_resolve_abort()(403)
        client_q = _cb_resolve_request().args.get("client", "").strip()
        short_q = _cb_resolve_request().args.get("short_name", "").strip()
        date_from = _cb_resolve_request().args.get("date_from", "")
        date_to = _cb_resolve_request().args.get("date_to", "")
        work_order_status = _cb_resolve_request().args.get("work_order_status", "")
        if work_order_status not in {"", "not_started", "open", "settling", "closed"}:
            work_order_status = ""
        region_code, country_code, countries, regions, location_clauses, location_params = _cb_resolve_report_location_filters()()
        available_statuses = set(_cb_resolve_STATUS_LABELS().keys())
        selected_statuses = [value for value in _cb_resolve_request().args.getlist("status") if value in available_statuses]
        selected_paid_statuses = [value for value in _cb_resolve_request().args.getlist("paid_status") if value in {"paid", "unpaid"}]
        project_options = _cb_resolve_report_project_options()()
        available_project_ids = {str(project["id"]) for project in project_options}
        selected_project_ids = [value for value in _cb_resolve_request().args.getlist("project_id") if value in available_project_ids]
        effective_project_ids = selected_project_ids or [str(project["id"]) for project in project_options]
        access_clause, access_params = _cb_resolve_client_filter_clause()("invoices")
        clauses = [access_clause]
        params = list(access_params)
        clauses.extend(location_clauses)
        params.extend(location_params)
        if work_order_status:
            clauses.append("service_orders.status = ?")
            params.append(work_order_status)
        if selected_statuses:
            placeholders = ",".join("?" for _ in selected_statuses)
            clauses.append(f"invoices.status in ({placeholders})")
            params.extend(selected_statuses)
        else:
            clauses.append("invoices.status != 'void'")
        if selected_paid_statuses and len(selected_paid_statuses) < 2:
            if selected_paid_statuses[0] == "paid":
                clauses.append("invoices.paid_at is not null")
            elif selected_paid_statuses[0] == "unpaid":
                clauses.append("invoices.status = 'completed' and invoices.paid_at is null")
        if client_q:
            clauses.append("(clients.name like ? or clients.client_number like ?)")
            params.extend([f"%{client_q}%", f"%{client_q}%"])
        if short_q:
            clauses.append("clients.short_name like ?")
            params.append(f"%{short_q}%")
        if date_from:
            clauses.append("invoices.issue_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("invoices.issue_date <= ?")
            params.append(date_to)
        if effective_project_ids:
            placeholders = ",".join("?" for _ in effective_project_ids)
            clauses.append(f"invoice_items.project_id in ({placeholders})")
            params.extend(effective_project_ids)
        rows = _cb_resolve_db()().execute(
            f"""
        select invoices.id, invoices.invoice_number, invoices.issue_date, invoices.currency, invoices.status, invoices.paid_at,
               clients.client_number, clients.name as client_name, clients.short_name,
               invoice_items.description as project_name, invoice_items.amount, invoice_items.tax_rate,
               service_orders.status as service_order_status,
               service_orders.client_order_number as service_client_order_number
        from invoice_items
        join invoices on invoices.id = invoice_items.invoice_id
        join clients on clients.id = invoices.client_id
        left join service_orders on service_orders.id = invoices.service_order_id
        where {" and ".join(clauses)}
        order by invoices.issue_date desc, invoices.id desc, invoice_items.id asc
        """,
            params,
        ).fetchall()
        report_rows = []
        subtotal = tax_total = grand_total = 0
        for row in rows:
            amount = float(row["amount"] or 0)
            tax = amount * float(row["tax_rate"] or 0) / 100
            line_total = amount + tax
            subtotal += amount
            tax_total += tax
            grand_total += line_total
            report_rows.append({"row": row, "tax": tax, "line_total": line_total})
        return _cb_resolve_render_template()(
            "reports.html",
            rows=report_rows,
            project_options=project_options,
            selected_project_ids=effective_project_ids,
            client_q=client_q,
            short_q=short_q,
            date_from=date_from,
            date_to=date_to,
            work_order_status=work_order_status,
            selected_statuses=selected_statuses or [value for value in _cb_resolve_STATUS_LABELS() if value != "void"],
            selected_paid_statuses=selected_paid_statuses or ["paid", "unpaid"],
            subtotal=subtotal,
            tax_total=tax_total,
            grand_total=grand_total,
            labels=_cb_resolve_STATUS_LABELS(),
            countries=countries,
            regions=regions,
            region_code=region_code,
            country_code=country_code,
        )


    app.add_url_rule('/reports/invoices', endpoint='invoice_query', view_func=invoice_query, methods=['GET'])
    return {'invoice_query': invoice_query}
