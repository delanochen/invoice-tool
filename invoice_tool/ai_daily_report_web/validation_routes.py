"""Business routes with explicit current-app resolvers; no reverse import."""


def register_ai_validation_routes(app, *,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__build_review_center_preview,
    _cb_resolve_can_edit_ai_daily_report_draft,
    _cb_resolve_can_view_ai_daily_report_draft,
    _cb_resolve_datetime,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_json,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_request,
    _cb_resolve_require_ai_daily_report_csrf,
    _cb_resolve_timezone,
):
    @_cb_resolve_login_required()
    def ai_daily_report_draft_preview(draft_id):
        """Get Preview Aggregation for a Draft (read-only).

    Does NOT call Google Routes, scan photos, or call Vision.
    Only reads saved Draft data and aggregates for display.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        # Authorization check
        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权访问此 Draft"}), 403

        preview = _cb_resolve__build_review_center_preview()(draft_row)

        return _cb_resolve_jsonify()({"ok": True, "preview": preview})


    @_cb_resolve_login_required()
    def ai_daily_report_draft_validation(draft_id):
        """Get ValidationResult for a Draft (Phase 7).

    READ-ONLY: runs ValidationEngine (pure, no DB writes, no external API,
    no filesystem scan). Uses ValidationContextBuilder for authoritative DB data.

    Permission: can_view_ai_daily_report_draft (finance read-only allowed).
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权访问此 Draft"}), 403

        draft_data = {}
        try:
            draft_data = _cb_resolve_json().loads(draft_row["draft_data"] or "{}")
        except (_cb_resolve_json().JSONDecodeError, TypeError):
            draft_data = {"verification_required": True, "verification_fields": ["draft_data_corrupted"]}

        from ai_daily_report.validation_engine import ValidationEngine, ValidationContextBuilder
        context_builder = ValidationContextBuilder(_cb_resolve_db()())
        context = context_builder.build(draft_data)
        engine = ValidationEngine()
        result = engine.validate(
            draft_data=draft_data,
            context=context,
            draft_version=draft_row.get("draft_version", 1),
        )

        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "draft_version": draft_row.get("draft_version", 1),
            "status": draft_row.get("status"),
            "validation": result.to_dict(),
        })


    @_cb_resolve_login_required()
    def ai_daily_report_draft_acknowledge(draft_id):
        """Acknowledge a WARNING issue (Phase 7).

    Constraints:
    - CSRF required
    - draft_version optimistic locking (stale → 409)
    - issue_key must exist in current validation
    - Only WARNING severity can be acknowledged (ERROR/INFO rejected)
    - acknowledgement_required must be true
    - Finance/read-only: POST denied
    - confirmed/cancelled/saved drafts: rejected by can_edit check

    Body: { issue_key, draft_version }
    Returns updated ValidationResult.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        # can_edit enforces: status=='draft', role in {admin, manager, employee with access}
        # finance is read-only, confirmed/cancelled/saved are read-only
        if not _cb_resolve_can_edit_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权修改此 Draft（需 draft 状态且有编辑权限）"}), 403

        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            issue_key = data.get("issue_key", "")
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        if not issue_key:
            return _cb_resolve_jsonify()({"ok": False, "error": "缺少 issue_key"}), 400

        # Parse draft data
        draft = svc.parse_draft_data(draft_row)
        draft_data = draft.model_dump()

        # Run validation to find the issue
        from ai_daily_report.validation_engine import (
            ValidationEngine, ValidationContextBuilder,
            add_acknowledgement, SEVERITY_WARNING,
        )
        context_builder = ValidationContextBuilder(_cb_resolve_db()())
        context = context_builder.build(draft_data)
        engine = ValidationEngine()
        result = engine.validate(
            draft_data=draft_data,
            context=context,
            draft_version=draft_row.get("draft_version", 1),
        )

        # Find the issue by key
        target_issue = None
        for issue in result.issues:
            if issue.issue_key == issue_key:
                target_issue = issue
                break

        if target_issue is None:
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": "issue_key 不存在",
                "issue_key": issue_key,
            }), 404

        # Only WARNING can be acknowledged
        if target_issue.severity != SEVERITY_WARNING:
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": f"只能确认 WARNING 级别的问题（当前: {target_issue.severity}）",
                "issue_key": issue_key,
                "severity": target_issue.severity,
            }), 400

        if not target_issue.acknowledgement_required:
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": "该问题不需要确认",
                "issue_key": issue_key,
            }), 400

        # Optimistic locking
        current_version = draft_row.get("draft_version", 1)
        if expected_version is not None and expected_version != current_version:
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": "Draft 版本冲突，请刷新后重试",
                "expected_version": expected_version,
                "current_version": current_version,
            }), 409

        # Add acknowledgement (mutates draft_data in place)
        user = _cb_resolve_g().user
        if hasattr(user, "keys"):
            user = dict(user)
        user_id = user.get("id", "")

        now_iso = _cb_resolve_datetime().now(_cb_resolve_timezone().utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        add_acknowledgement(
            draft_data=draft_data,
            issue_key=issue_key,
            rule_id=target_issue.rule_id,
            issue_fingerprint=target_issue.issue_fingerprint,
            validation_fingerprint=result.validation_fingerprint,
            draft_version=current_version,
            user_id=user_id,
            now_iso=now_iso,
        )

        # Save updated draft
        from ai_daily_report.schemas import DailyReportDraft
        updated_draft = DailyReportDraft(**draft_data)
        svc.save_draft(draft_id, updated_draft, expected_version=expected_version)
        _cb_resolve_db()().commit()

        # Re-run validation to return updated result
        updated_row = svc.get_draft(draft_id)
        updated_data = {}
        try:
            updated_data = _cb_resolve_json().loads(updated_row["draft_data"] or "{}")
        except (_cb_resolve_json().JSONDecodeError, TypeError):
            updated_data = {}
        context2 = context_builder.build(updated_data)
        result2 = engine.validate(
            draft_data=updated_data,
            context=context2,
            draft_version=updated_row.get("draft_version", 1),
        )

        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "draft_version": updated_row.get("draft_version", 1),
            "issue_key": issue_key,
            "acknowledged_by": user_id,
            "acknowledged_at": now_iso,
            "validation": result2.to_dict(),
        })


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/preview', endpoint='ai_daily_report_draft_preview', view_func=ai_daily_report_draft_preview, methods=['GET'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/validation', endpoint='ai_daily_report_draft_validation', view_func=ai_daily_report_draft_validation, methods=['GET'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/acknowledge', endpoint='ai_daily_report_draft_acknowledge', view_func=ai_daily_report_draft_acknowledge, methods=['POST'])
    return {'ai_daily_report_draft_preview': ai_daily_report_draft_preview, 'ai_daily_report_draft_validation': ai_daily_report_draft_validation, 'ai_daily_report_draft_acknowledge': ai_daily_report_draft_acknowledge}
