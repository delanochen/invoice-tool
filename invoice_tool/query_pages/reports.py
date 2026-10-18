"""Business routes with explicit current-app resolvers; no reverse import."""


def register_report_query_page_routes(app, *,
    _cb_resolve_abort,
    _cb_resolve_current_language,
    _cb_resolve_db,
    _cb_resolve_has_action_permission,
    _cb_resolve_is_internal_user,
    _cb_resolve_login_required,
    _cb_resolve_render_template,
    _cb_resolve_request,
    _cb_resolve_service_order_access_filters,
):
    @_cb_resolve_login_required()
    def service_report_query():
        # View access is driven by the permission matrix (工作日报 > 查看) instead of
        # an internal-only gate, so external accounts granted 查看 can use this page.
        if not (_cb_resolve_is_internal_user()() or _cb_resolve_has_action_permission()("service_reports", "view")):
            _cb_resolve_abort()(403)
        q = _cb_resolve_request().args.get("q", "").strip()
        date_from = _cb_resolve_request().args.get("date_from", "")
        date_to = _cb_resolve_request().args.get("date_to", "")
        order_ids = list(dict.fromkeys(_cb_resolve_request().args.getlist("order_id")))
        sites = list(dict.fromkeys(_cb_resolve_request().args.getlist("site")))
        worker_ids = list(dict.fromkeys(_cb_resolve_request().args.getlist("worker_id")))
        order_ids = [value for value in order_ids if value]
        sites = [value for value in sites if value]
        worker_ids = [value for value in worker_ids if value]
        clauses, params = _cb_resolve_service_order_access_filters()("service_orders")
        if not clauses:
            clauses.append("1 = 1")
        for column, values in [("service_orders.id", order_ids), ("service_orders.client_name", sites)]:
            if values:
                clauses.append(f"{column} in ({','.join('?' for _ in values)})")
                params.extend(values)
        if worker_ids:
            clauses.append(f"exists (select 1 from service_report_workers matched_worker where matched_worker.report_id = service_reports.id and matched_worker.user_id in ({','.join('?' for _ in worker_ids)}))")
            params.extend(worker_ids)
        if q:
            clauses.append(
                "(service_orders.order_number like ? or service_orders.client_name like ? or coalesce(owners.name, buyers.owner) like ? or service_reports.cabinet_number like ?)"
            )
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
        if date_from:
            clauses.append("coalesce(service_reports.actual_work_date, service_reports.report_date) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("coalesce(service_reports.actual_work_date, service_reports.report_date) <= ?")
            params.append(date_to)
        rows = _cb_resolve_db()().execute(
            f"""
        select service_reports.*, service_orders.order_number, service_orders.client_name,
               coalesce(owners.name, buyers.owner) as buyer_owner,
               coalesce(country_local.name, country_zh.name, service_orders.country_code) as country_name,
               coalesce(country_local.region_name, country_zh.region_name, service_orders.region_code) as region_name,
               group_concat(users.name, ', ') as worker_names
        from service_reports
        join service_orders on service_orders.id = service_reports.service_order_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join owners on owners.id = buyers.owner_id
        left join country_translations country_local
          on country_local.country_code = service_orders.country_code and country_local.language_code = ?
        left join country_translations country_zh
          on country_zh.country_code = service_orders.country_code and country_zh.language_code = 'zh-CN'
        left join service_report_workers on service_report_workers.report_id = service_reports.id
        left join users on users.id = service_report_workers.user_id
        where {" and ".join(clauses)}
        group by service_reports.id, service_orders.id, buyers.id, owners.id, country_local.name, country_zh.name, country_local.region_name, country_zh.region_name
        order by coalesce(service_reports.actual_work_date, service_reports.report_date) desc, service_reports.id desc
        """,
            [_cb_resolve_current_language()(), *params],
        ).fetchall()
        external_view = not _cb_resolve_is_internal_user()()
        if external_view:
            # Scope the filter dropdowns to the external account's visible orders
            # so the options themselves don't leak other clients' data.
            opt_clauses, opt_params = _cb_resolve_service_order_access_filters()("o")
            option_where = " and ".join(opt_clauses) if opt_clauses else "1 = 1"
            order_options = _cb_resolve_db()().execute(
                f"select distinct o.id, o.order_number from service_orders o join service_reports r on r.service_order_id=o.id where {option_where} order by o.order_number desc",
                opt_params,
            ).fetchall()
            site_options = _cb_resolve_db()().execute(
                f"select distinct o.client_name from service_orders o join service_reports r on r.service_order_id=o.id where o.client_name != '' and {option_where} order by o.client_name",
                opt_params,
            ).fetchall()
            worker_options = _cb_resolve_db()().execute(
                f"""
            select distinct u.id, u.name from users u
            join service_report_workers w on w.user_id=u.id
            join service_reports r on r.id=w.report_id
            join service_orders o on o.id = r.service_order_id
            where {option_where} order by u.name, u.id
            """,
                opt_params,
            ).fetchall()
        else:
            order_options = _cb_resolve_db()().execute("select distinct o.id, o.order_number from service_orders o join service_reports r on r.service_order_id=o.id order by o.order_number desc").fetchall()
            site_options = _cb_resolve_db()().execute("select distinct o.client_name from service_orders o join service_reports r on r.service_order_id=o.id where o.client_name != '' order by o.client_name").fetchall()
            worker_options = _cb_resolve_db()().execute("select distinct u.id, u.name from users u join service_report_workers w on w.user_id=u.id join service_reports r on r.id=w.report_id order by u.name, u.id").fetchall()
        return _cb_resolve_render_template()(
            "service_report_query.html",
            rows=rows,
            q=q,
            date_from=date_from,
            date_to=date_to,
            order_ids=order_ids, sites=sites, worker_ids=worker_ids,
            order_options=order_options,
            site_options=site_options,
            worker_options=worker_options,
            external_view=external_view,
        )


    app.add_url_rule('/reports/service-reports', endpoint='service_report_query', view_func=service_report_query, methods=['GET'])
    return {'service_report_query': service_report_query}
