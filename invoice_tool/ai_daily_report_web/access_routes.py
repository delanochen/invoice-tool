"""Business routes with explicit current-app resolvers; no reverse import."""


def register_draft_csrf_routes(app, *,
    _cb_resolve_ai_daily_report_csrf_token,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
):
    @_cb_resolve_login_required()
    def ai_daily_report_csrf():
        """Get CSRF token for Review Center mutation APIs."""
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        return _cb_resolve_jsonify()({"ok": True, "csrf_token": _cb_resolve_ai_daily_report_csrf_token()()})


    app.add_url_rule('/api/ai/daily-report/csrf', endpoint='ai_daily_report_csrf', view_func=ai_daily_report_csrf, methods=['GET'])
    return {'ai_daily_report_csrf': ai_daily_report_csrf}


def register_draft_orders_routes(app, *,
    _cb_resolve_db,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_request,
    _cb_resolve_service_order_access_filters,
):
    @_cb_resolve_login_required()
    def ai_daily_report_service_orders_options():
        """JSON options for the 'New AI Daily Report' order picker.

    Internal users only. Applies the same client-scope filters as the rest of
    the app (service_order_access_filters), so the picker never leaks orders
    the user could not otherwise see.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        q = _cb_resolve_request().args.get("q", "").strip()
        try:
            limit = min(int(_cb_resolve_request().args.get("limit", "20") or 20), 50)
        except (TypeError, ValueError):
            limit = 20
        clauses, params = _cb_resolve_service_order_access_filters()("service_orders")
        clauses.append("service_orders.status != 'closed'")
        if q:
            clauses.append(
                "(service_orders.order_number like ? or service_orders.client_name like ? "
            "or service_orders.client_order_number like ? or coalesce(service_orders.site_address, '') like ?)"
            )
            like = f"%{q}%"
            params.extend([like, like, like, like])
        rows = _cb_resolve_db()().execute(
            f"select service_orders.id, service_orders.order_number, service_orders.client_name, "
        f"service_orders.site_address "
        f"from service_orders where {' and '.join(clauses)} "
        f"order by service_orders.order_number limit ?",
            params + [limit],
        ).fetchall()
        return _cb_resolve_jsonify()({"ok": True, "orders": [dict(r) for r in rows]})


    app.add_url_rule('/api/ai/daily-report/service-orders', endpoint='ai_daily_report_service_orders_options', view_func=ai_daily_report_service_orders_options, methods=['GET'])
    return {'ai_daily_report_service_orders_options': ai_daily_report_service_orders_options}
