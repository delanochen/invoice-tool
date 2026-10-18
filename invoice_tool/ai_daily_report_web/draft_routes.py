"""Business routes with explicit current-app resolvers; no reverse import."""


def register_draft_get_routes(app, *,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
):
    @_cb_resolve_login_required()
    def ai_daily_report_get_draft(draft_id):
        """Get draft preview."""
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404
        draft = svc.parse_draft_data(draft_row)
        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "status": draft_row["status"],
            "preview": svc.build_preview(draft),
        })


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>', endpoint='ai_daily_report_get_draft', view_func=ai_daily_report_get_draft, methods=['GET'])
    return {'ai_daily_report_get_draft': ai_daily_report_get_draft}
