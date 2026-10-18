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
