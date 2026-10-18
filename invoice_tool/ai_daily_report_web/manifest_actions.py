"""Business routes with explicit current-app resolvers; no reverse import."""


def register_manifest_delete_routes(app, *,
    _cb_resolve_ManifestError,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__attachment_manifest_service,
    _cb_resolve__manifest_error_response,
    _cb_resolve_can_prepare_attachment_manifest,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_require_ai_daily_report_csrf,
):
    @_cb_resolve_login_required()
    def ai_daily_report_manifest_cancel(draft_id, manifest_id):
        """Cancel a (non-ready) Manifest: mark cancelled + delete its staging only.

    Original Work Order photos / Phase 3B evidence / formal attachments are
    never touched. Requires CSRF + can_prepare (confirmed, non-read-only).
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_prepare_attachment_manifest()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权操作附件清单"}), 403

        try:
            manifest_svc = _cb_resolve__attachment_manifest_service()()
            manifest = manifest_svc.cancel(draft_id, manifest_id)
        except _cb_resolve_ManifestError() as exc:
            return _cb_resolve__manifest_error_response()(exc, default_status=400)

        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()({"ok": True, "manifest": manifest})


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/manifest/<manifest_id>', endpoint='ai_daily_report_manifest_cancel', view_func=ai_daily_report_manifest_cancel, methods=['DELETE'])
    return {'ai_daily_report_manifest_cancel': ai_daily_report_manifest_cancel}
