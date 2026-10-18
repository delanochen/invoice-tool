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


def register_draft_prepare_routes(app, *,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__auto_prepare_draft_media,
    _cb_resolve__build_review_center_preview,
    _cb_resolve_db,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_require_ai_daily_report_csrf,
):
    @_cb_resolve_login_required()
    def ai_daily_report_auto_prepare(draft_id):
        """One-click auto pipeline: discover photos -> confirm timeline ->
    classify photos (auto-select safety/construction photos) ->
    recalculate mileage.

    Idempotent and best-effort: already-finished steps are skipped, manual
    user overrides (times, photo selections) are preserved, and failures
    degrade gracefully so the caller can retry or fix data manually.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        steps = _cb_resolve__auto_prepare_draft_media()(draft_id)
        _cb_resolve_db()().commit()

        draft_row = svc.get_draft(draft_id)
        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "steps": steps,
            "draft_version": draft_row["draft_version"],
            # v0.1.254: must be the aggregated Review-Center preview (same shape as
            # GET /draft/<id>/preview). The flat DailyReportService.build_preview()
            # carries no status/draft_version, so the review page wiped its action
            # buttons right after this call re-rendered ("按钮一闪而过").
            "preview": _cb_resolve__build_review_center_preview()(draft_row),
        })


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/auto-prepare', endpoint='ai_daily_report_auto_prepare', view_func=ai_daily_report_auto_prepare, methods=['POST'])
    return {'ai_daily_report_auto_prepare': ai_daily_report_auto_prepare}
