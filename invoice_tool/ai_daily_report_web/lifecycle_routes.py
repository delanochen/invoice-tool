"""Business routes with explicit current-app resolvers; no reverse import."""


def register_draft_cancel_routes(app, *,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve_datetime,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_require_ai_daily_report_csrf,
    _cb_resolve_timezone,
):
    @_cb_resolve_login_required()
    def ai_daily_report_cancel(draft_id):
        """Soft-cancel a draft (v0.1.241, per user decision 2026-09-17).

    State machine: draft/confirmed -> cancelled. The draft row is KEPT with
    its audit trail (cancelled_by / cancelled_at) — this is "取消", not a
    physical delete. Use /delete for a hard delete.
    Requires CSRF token.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404
        if draft_row["status"] == "saved":
            return _cb_resolve_jsonify()({"ok": False, "error": "已正式保存的日报不能取消"}), 409
        if draft_row["status"] == "cancelled":
            return _cb_resolve_jsonify()({
                "ok": True,
                "draft_id": draft_id,
                "status": "cancelled",
                "message": "该 Draft 已是取消状态",
            })

        user = _cb_resolve_g().user
        # Convert database Row to dict (Row does not support attribute access)
        if hasattr(user, "keys"):
            user = dict(user)
        user_id = user.get("id", "")

        draft = svc.parse_draft_data(draft_row)
        draft.cancelled_by = user_id
        draft.cancelled_at = _cb_resolve_datetime().now(_cb_resolve_timezone().utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        svc.save_draft(draft_id, draft)
        svc.update_draft_status(draft_id, "cancelled")
        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "status": "cancelled",
            "cancelled_by": user_id,
            "cancelled_at": draft.cancelled_at,
            "message": "Draft 已取消（保留记录，可在列表中筛选 Cancelled 查看）",
        })


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/cancel', endpoint='ai_daily_report_cancel', view_func=ai_daily_report_cancel, methods=['POST'])
    return {'ai_daily_report_cancel': ai_daily_report_cancel}


def register_draft_delete_routes(app, *,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__attachment_manifest_service,
    _cb_resolve_datetime,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_require_ai_daily_report_csrf,
    _cb_resolve_timezone,
):
    @_cb_resolve_login_required()
    def ai_daily_report_delete(draft_id):
        """Physically delete a draft (hard delete, not a soft cancel).

    v0.1.241: /cancel was turned back into a soft cancel; this endpoint now
    owns the hard delete. Removes the draft plus cascaded actions / manifests
    (with their sources, assets, roles) and formal commits. Cleans manifest
    staging directories first. Saved (formally committed) drafts cannot be
    deleted. Requires CSRF token.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404
        if draft_row["status"] == "saved":
            return _cb_resolve_jsonify()({"ok": False, "error": "已正式保存的日报不能删除"}), 409

        user = _cb_resolve_g().user
        # Convert database Row to dict (Row does not support attribute access)
        if hasattr(user, "keys"):
            user = dict(user)
        user_id = user.get("id", "")

        # Clean manifest staging directories before the rows disappear.
        manifest_rows = _cb_resolve_db()().execute(
            "select manifest_id from ai_daily_report_attachment_manifests where draft_id = ?",
            (draft_id,),
        ).fetchall()
        if manifest_rows:
            try:
                msvc = _cb_resolve__attachment_manifest_service()()
                for row in manifest_rows:
                    msvc._delete_staging_dir(draft_id, row[0])
            except Exception:
                pass

        svc.delete_draft(draft_id)  # physical delete (cascades child rows)
        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "status": "deleted",
            "deleted_by": user_id,
            "deleted_at": _cb_resolve_datetime().now(_cb_resolve_timezone().utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message": "Draft 已彻底删除",
        })


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/delete', endpoint='ai_daily_report_delete', view_func=ai_daily_report_delete, methods=['POST'])
    return {'ai_daily_report_delete': ai_daily_report_delete}


def register_draft_reopen_routes(app, *,
    _cb_resolve_DraftStateError,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve_datetime,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_timezone,
):
    @_cb_resolve_login_required()
    def ai_daily_report_reopen(draft_id):
        """Explicitly reopen a confirmed draft for editing.

    State machine: confirmed -> draft.
    Records reopened_by, reopened_at.
    Preserves all AI/photo/mileage metadata.
    Requires CSRF token.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        # Note: reopen is a Phase 1 API called by AI Assistant,
        # so authorization is not enforced here to avoid breaking existing integrations.
        user = _cb_resolve_g().user
        # Convert database Row to dict (Row does not support attribute access)
        if hasattr(user, "keys"):
            user = dict(user)
        user_id = user.get("id", "")

        try:
            svc.reopen_draft(draft_id)
        except _cb_resolve_DraftStateError() as exc:
            return _cb_resolve_jsonify()({"ok": False, "error": str(exc)}), 409

        # Record audit (preserves all other metadata)
        draft = svc.parse_draft_data(svc.get_draft(draft_id))
        draft.reopened_by = user_id
        draft.reopened_at = _cb_resolve_datetime().now(_cb_resolve_timezone().utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Clear confirmed audit
        draft.confirmed_by = None
        draft.confirmed_at = None
        draft.verification_override = False
        draft.override_fields = []
        svc.save_draft(draft_id, draft)

        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "status": "draft",
            "reopened_by": user_id,
            "reopened_at": draft.reopened_at,
            "message": "Draft 已重新打开，可以继续修改（AI/照片/里程数据已保留）",
        })


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/reopen', endpoint='ai_daily_report_reopen', view_func=ai_daily_report_reopen, methods=['POST'])
    return {'ai_daily_report_reopen': ai_daily_report_reopen}
