"""Business routes with explicit current-app resolvers; no reverse import."""


def register_order_query_page_routes(app, *,
    _cb_resolve_abort,
    _cb_resolve_current_language,
    _cb_resolve_db,
    _cb_resolve_is_internal_user,
    _cb_resolve_login_required,
    _cb_resolve_render_template,
    _cb_resolve_request,
):
    @_cb_resolve_login_required()
    def service_order_query():
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)
        q = _cb_resolve_request().args.get("q", "").strip()
        order_number = _cb_resolve_request().args.get("order_number", "").strip()
        site_name = _cb_resolve_request().args.get("site_name", "").strip()
        date_from = _cb_resolve_request().args.get("date_from", "").strip()
        date_to = _cb_resolve_request().args.get("date_to", "").strip()
        status = _cb_resolve_request().args.get("status", "")
        clauses = ["1 = 1"]
        params = []
        if q:
            clauses.append(
                "(service_orders.order_number like ? or service_orders.client_name like ? or coalesce(owners.name, buyers.owner) like ? or service_orders.client_order_number like ?)"
            )
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
        if order_number:
            clauses.append("service_orders.order_number like ?")
            params.append(f"%{order_number}%")
        if site_name:
            clauses.append("service_orders.client_name like ?")
            params.append(f"%{site_name}%")
        if date_from:
            clauses.append("service_orders.start_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("service_orders.start_date <= ?")
            params.append(date_to)
        if status:
            clauses.append("service_orders.status = ?")
            params.append(status)
        rows = _cb_resolve_db()().execute(
            f"""
        select service_orders.*,
               work_order_types.name as work_order_type_name,
               coalesce(owners.name, buyers.owner) as buyer_owner,
               coalesce(order_manufacturers.name, buyers.equipment_manufacturer) as buyer_equipment_manufacturer,
               coalesce(country_local.name, country_zh.name, service_orders.country_code) as country_name,
               coalesce(country_local.region_name, country_zh.region_name, service_orders.region_code) as region_name,
               count(distinct service_reports.id) as report_count,
               count(distinct invoices.id) as invoice_count,
               count(distinct case when invoices.paid_at is not null then invoices.id end) as paid_invoice_count,
               coalesce(sum(case when expenses.status = 'approved' then expenses.amount else 0 end), 0) as approved_expense_total
        from service_orders
        left join work_order_types on work_order_types.id = service_orders.work_order_type_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join manufacturers as order_manufacturers on order_manufacturers.id = service_orders.manufacturer_id
        left join owners on owners.id = buyers.owner_id
        left join country_translations country_local
          on country_local.country_code = service_orders.country_code and country_local.language_code = ?
        left join country_translations country_zh
          on country_zh.country_code = service_orders.country_code and country_zh.language_code = 'zh-CN'
        left join service_reports on service_reports.service_order_id = service_orders.id
        left join invoices on invoices.service_order_id = service_orders.id and invoices.status != 'void'
        left join expenses on expenses.service_order_id = service_orders.id
        where {" and ".join(clauses)}
        group by service_orders.id, work_order_types.id, owners.id, buyers.id, order_manufacturers.id, country_local.name, country_zh.name, country_local.region_name, country_zh.region_name
        order by service_orders.created_at desc, service_orders.id desc
        """,
            [_cb_resolve_current_language()(), *params],
        ).fetchall()
        return _cb_resolve_render_template()(
            "service_order_query.html",
            orders=rows,
            q=q,
            order_number=order_number,
            site_name=site_name,
            date_from=date_from,
            date_to=date_to,
            status=status,
        )


    app.add_url_rule('/reports/service-orders', endpoint='service_order_query', view_func=service_order_query, methods=['GET'])
    return {'service_order_query': service_order_query}
