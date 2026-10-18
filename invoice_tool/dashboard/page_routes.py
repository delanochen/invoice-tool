"""Business routes with explicit current-app resolvers; no reverse import."""


def register_workspace_pages_routes(app, *,
    _cb_resolve_login_required,
    _cb_resolve_redirect,
    _cb_resolve_render_template,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def workspace():
        return _cb_resolve_render_template()("workspace.html")


    @_cb_resolve_login_required()
    def report_center():
        return _cb_resolve_redirect()(_cb_resolve_url_for()("service_order_query"))


    app.add_url_rule('/workspace', endpoint='workspace', view_func=workspace, methods=['GET'])
    app.add_url_rule('/reports', endpoint='report_center', view_func=report_center, methods=['GET'])
    return {'workspace': workspace, 'report_center': report_center}


def register_dashboard_page_routes(app, *,
    _cb_resolve_STATUS_LABELS,
    _cb_resolve_can_view_invoices,
    _cb_resolve_client_filter_clause,
    _cb_resolve_dashboard_project_options,
    _cb_resolve_db,
    _cb_resolve_get_metrics,
    _cb_resolve_is_external_user,
    _cb_resolve_login_required,
    _cb_resolve_monthly_paid_chart,
    _cb_resolve_monthly_project_chart,
    _cb_resolve_redirect,
    _cb_resolve_render_template,
    _cb_resolve_request,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def dashboard():
        if not _cb_resolve_can_view_invoices()() or _cb_resolve_is_external_user()():
            return _cb_resolve_redirect()(_cb_resolve_url_for()("service_orders"))
        metrics = _cb_resolve_get_metrics()()
        selected_project_ids = _cb_resolve_request().args.getlist("project_id")
        project_options = _cb_resolve_dashboard_project_options()()
        project_filter_submitted = "project_filter" in _cb_resolve_request().args
        available_project_ids = {str(project["id"]) for project in project_options}
        selected_project_ids = [project_id for project_id in selected_project_ids if project_id in available_project_ids]
        if not project_filter_submitted and not selected_project_ids:
            selected_project_ids = [str(project["id"]) for project in project_options]
        chart = _cb_resolve_monthly_project_chart()(selected_project_ids)
        paid_chart = _cb_resolve_monthly_paid_chart()()
        access_clause, access_params = _cb_resolve_client_filter_clause()("invoices")
        recent = _cb_resolve_db()().execute(
            f"""
        select invoices.*, clients.name as client_name
        from invoices join clients on clients.id = invoices.client_id
        where invoices.status = 'completed' and {access_clause}
        order by invoices.created_at desc limit 8
        """,
            access_params,
        ).fetchall()
        return _cb_resolve_render_template()(
            "dashboard.html",
            metrics=metrics,
            chart=chart,
            paid_chart=paid_chart,
            recent=recent,
            labels=_cb_resolve_STATUS_LABELS(),
            project_options=project_options,
            selected_project_ids=selected_project_ids,
        )


    app.add_url_rule('/', endpoint='dashboard', view_func=dashboard, methods=['GET'])
    return {'dashboard': dashboard}
