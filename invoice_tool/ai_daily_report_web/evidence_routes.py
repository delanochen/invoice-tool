"""Business routes with explicit current-app resolvers; no reverse import."""


def register_ai_evidence_routes(app, *,
    _cb_resolve_DATA_DIR,
    _cb_resolve_FormalSaveCommitConflictError,
    _cb_resolve_FormalSaveComplianceError,
    _cb_resolve_FormalSaveIntegrityError,
    _cb_resolve_FormalSaveStaleError,
    _cb_resolve_FormalSaveStateError,
    _cb_resolve_FormalSaveValidationBlockedError,
    _cb_resolve_ManifestError,
    _cb_resolve_ManifestNotFoundError,
    _cb_resolve_Path,
    _cb_resolve_SHARED_PHOTOS_DIR,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__attachment_manifest_service,
    _cb_resolve__manifest_error_response,
    _cb_resolve__new_formal_save_service,
    _cb_resolve__run_phase7_validation,
    _cb_resolve_abort,
    _cb_resolve_can_formal_save_draft,
    _cb_resolve_can_prepare_attachment_manifest,
    _cb_resolve_can_view_ai_daily_report_draft,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_json,
    _cb_resolve_jsonify,
    _cb_resolve_log_action,
    _cb_resolve_login_required,
    _cb_resolve_now,
    _cb_resolve_request,
    _cb_resolve_require_ai_daily_report_csrf,
    _cb_resolve_send_file,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def ai_daily_report_draft_photo_preview(draft_id, photo_id):
        """Secure photo preview for Draft.

    Does NOT accept client-supplied path. Uses draft_id + photo_id to resolve.
    Validates: user permission, draft ownership, path confinement, hash.
    """
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            _cb_resolve_abort()(404)

        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            _cb_resolve_abort()(403)

        # Find photo in draft's photo_candidates
        draft_data = {}
        try:
            draft_data = _cb_resolve_json().loads(draft_row["draft_data"] or "{}")
        except (_cb_resolve_json().JSONDecodeError, TypeError):
            _cb_resolve_abort()(404)

        photo_ref = None
        for p in draft_data.get("photo_candidates", []):
            if p.get("photo_id") == photo_id or p.get("photo_hash") == photo_id:
                photo_ref = p
                break

        if not photo_ref:
            _cb_resolve_abort()(404)

        # Resolve path safely
        relative_path = photo_ref.get("relative_path", "")
        if not relative_path or _cb_resolve_Path()(relative_path).is_absolute() or ".." in _cb_resolve_Path()(relative_path).parts:
            _cb_resolve_abort()(400)

        try:
            full_path = (_cb_resolve_Path()(_cb_resolve_SHARED_PHOTOS_DIR()) / relative_path).resolve()
            if not str(full_path).startswith(str(_cb_resolve_Path()(_cb_resolve_SHARED_PHOTOS_DIR()).resolve())):
                _cb_resolve_abort()(400)
            if not full_path.exists():
                _cb_resolve_abort()(404)
        except OSError:
            _cb_resolve_abort()(400)

        return _cb_resolve_send_file()(str(full_path), as_attachment=False, conditional=True)


    @_cb_resolve_login_required()
    def ai_daily_report_draft_evidence_preview(draft_id, evidence_id):
        """Secure Mileage Evidence preview for Draft.

    Does NOT accept client-supplied path. Uses draft_id + evidence_id to resolve.
    Validates: user permission, draft ownership, path confinement, hash.
    """
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            _cb_resolve_abort()(404)

        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            _cb_resolve_abort()(403)

        # Find evidence in draft's evidence_records
        draft_data = {}
        try:
            draft_data = _cb_resolve_json().loads(draft_row["draft_data"] or "{}")
        except (_cb_resolve_json().JSONDecodeError, TypeError):
            _cb_resolve_abort()(404)

        evidence = None
        for e in draft_data.get("evidence_records", []):
            if e.get("evidence_id") == evidence_id:
                evidence = e
                break

        if not evidence:
            _cb_resolve_abort()(404)

        # Resolve path safely (Draft evidence is stored in DATA_DIR/ai-daily-report-drafts/)
        relative_path = evidence.get("file_relative_path", "")
        if not relative_path:
            _cb_resolve_abort()(404)

        # Evidence path should be under Draft evidence directory.
        # Persisted records may store either a bare filename (relative to the
        # draft mileage dir) or a full DATA_DIR-relative path; normalize both.
        expected_prefix = f"ai-daily-report-drafts/{draft_id}/mileage/"
        if relative_path.startswith("ai-daily-report-drafts/"):
            if not relative_path.startswith(expected_prefix):
                _cb_resolve_abort()(400)
            resolved_rel = relative_path
        else:
            if "/" in relative_path or "\\" in relative_path or ".." in _cb_resolve_Path()(relative_path).parts:
                _cb_resolve_abort()(400)
            resolved_rel = expected_prefix + relative_path

        if _cb_resolve_Path()(resolved_rel).is_absolute() or ".." in _cb_resolve_Path()(resolved_rel).parts:
            _cb_resolve_abort()(400)

        try:
            full_path = (_cb_resolve_Path()(_cb_resolve_DATA_DIR()) / resolved_rel).resolve()
            if not str(full_path).startswith(str(_cb_resolve_Path()(_cb_resolve_DATA_DIR()).resolve())):
                _cb_resolve_abort()(400)
            if not full_path.exists():
                _cb_resolve_abort()(404)
        except OSError:
            _cb_resolve_abort()(400)

        return _cb_resolve_send_file()(str(full_path), as_attachment=False, conditional=True)


    @_cb_resolve_login_required()
    def ai_daily_report_draft_prepare_attachments(draft_id):
        """Prepare (or reuse) a ready Attachment Manifest for a confirmed Draft.

    Phase 8 state machine: Draft -> Validation -> Confirm -> Prepare -> Phase 9.
    - Only status == 'confirmed' (can_prepare_attachment_manifest)
    - CSRF required
    - Server-side Phase 7 validation gate (can_proceed == False -> 422)
    - draft_version optimistic locking (stale -> 409)
    - Idempotent: identical fingerprint reuses the existing ready manifest.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_prepare_attachment_manifest()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权准备附件（需 confirmed 状态且非只读权限）"}), 403

        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        if expected_version is not None and expected_version != int(draft_row.get("draft_version", 1)):
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试。", "code": "version_conflict"}), 409

        validation = _cb_resolve__run_phase7_validation()(draft_row)
        if not validation.can_proceed:
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": "Phase 7 校验未通过（存在未解决的 ERROR），无法准备附件清单。",
                "code": "validation_cannot_proceed",
                "validation": validation.to_dict(),
            }), 422

        try:
            manifest_svc = _cb_resolve__attachment_manifest_service()()
            manifest = manifest_svc.prepare(draft_row, validation)
        except _cb_resolve_ManifestError() as exc:
            return _cb_resolve__manifest_error_response()(exc)

        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()({"ok": True, "draft_id": draft_id, "manifest": manifest})


    def ai_daily_report_draft_formal_save(draft_id):
        """Formal Save: deterministically commit a confirmed Draft + ready Manifest.

    Phase 9 state machine: Draft -> Validation -> Confirm -> Prepare -> Formal Save.
    - Only status == 'confirmed' (can_formal_save_draft)
    - CSRF required
    - Server-side Phase 7 validation gate (can_proceed == False -> 422)
    - Exactly-once: a Draft maps to at most one formal service_report
      (200 returns the existing report; 201 first creation)
    - Client may only submit draft_version + manifest_id (optimistic lock).
    """
        if not _cb_resolve_g().user:
            return _cb_resolve_jsonify()({"ok": False, "error": "未登录"}), 401
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权访问此 Draft"}), 403

        # State machine: only confirmed (or already-saved) drafts may hit this.
        if draft_row.get("status") not in {"confirmed", "saved"}:
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": "Draft 状态不允许正式保存。",
                "code": "draft_state_error",
            }), 409

        # Exactly-once: an already-saved Draft returns its existing formal report
        # (200), even if the client retries after a lost response.
        if draft_row.get("saved_report_id"):
            report_id = int(draft_row["saved_report_id"])
            return _cb_resolve_jsonify()({
                "ok": True,
                "status": "already_committed",
                "service_report_id": report_id,
                "report_url": _cb_resolve_url_for()("edit_service_report", report_id=report_id),
            }), 200

        if not _cb_resolve_can_formal_save_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权正式保存（需 confirmed 状态且非只读权限）"}), 403

        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
            client_manifest_id = data.get("manifest_id")
            if client_manifest_id is not None:
                client_manifest_id = str(client_manifest_id)
            # v0.1.252: explicit flag to pass an INCOMPLETE report to the order.
            force_incomplete = bool(data.get("force_incomplete"))
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        validation = _cb_resolve__run_phase7_validation()(draft_row)
        manifest_svc = _cb_resolve__attachment_manifest_service()()
        # v0.1.252: force path only applies when validation is blocking — otherwise
        # the full manifest chain (with attachments) is always preferred.
        incomplete_pass = bool(force_incomplete and not validation.can_proceed)
        try:
            formal_svc = _cb_resolve__new_formal_save_service()(int(_cb_resolve_g().user["id"]))
            if incomplete_pass:
                result = formal_svc.run_incomplete(draft_row, expected_version)
            else:
                result = formal_svc.run(
                    draft_row, expected_version, client_manifest_id, validation, manifest_svc
                )
        except (_cb_resolve_FormalSaveStaleError(), _cb_resolve_FormalSaveStateError(), _cb_resolve_FormalSaveCommitConflictError()) as exc:
            return _cb_resolve_jsonify()({"ok": False, "error": exc.message, "code": exc.code}), 409
        except (_cb_resolve_FormalSaveIntegrityError(), _cb_resolve_FormalSaveValidationBlockedError(), _cb_resolve_FormalSaveComplianceError()) as exc:
            return _cb_resolve_jsonify()({"ok": False, "error": exc.message, "code": exc.code}), 422
        except _cb_resolve_ManifestError() as exc:
            return _cb_resolve__manifest_error_response()(exc)

        status_code = 201 if result["status"] == "created" else 200
        payload = {
            "ok": True,
            "status": result["status"],
            "service_report_id": result["service_report_id"],
            "report_url": _cb_resolve_url_for()("edit_service_report", report_id=result["service_report_id"]),
        }
        if incomplete_pass:
            payload["incomplete"] = True
        return _cb_resolve_jsonify()(payload), status_code


    def ai_daily_report_manifest_compliance_review(draft_id, manifest_id):
        """Human compliance review of a Google mileage evidence source (Phase 8 extension).

    Minimal design (sealed Phase 9 STEP 5): only admin/manager may mark a
    compliance_review_required source as reviewed. Phase 8/9 flows never mark
    reviewed automatically. External roles / finance keep current boundaries.
    """
        if not _cb_resolve_g().user:
            return _cb_resolve_jsonify()({"ok": False, "error": "未登录"}), 401
        if hasattr(_cb_resolve_g().user, "keys"):
            _cb_resolve_g().user = dict(_cb_resolve_g().user)
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        role = _cb_resolve_g().user.get("role", "")
        if role not in {"admin", "manager"}:
            return _cb_resolve_jsonify()({"ok": False, "error": "仅管理员/经理可执行合规审查"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404
        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权访问此 Draft"}), 403

        # Compliance review only makes sense on a confirmed Draft: a reopened or
        # already-saved Draft must not let an old manifest review be misapplied
        # (GATE 6 / Ready->Reopen invariant).
        if draft_row.get("status") != "confirmed":
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 状态不允许合规审查。", "code": "draft_state_error"}), 409

        manifest_svc = _cb_resolve__attachment_manifest_service()()
        dbc = _cb_resolve_db()()
        manifest = dbc.execute(
            "select * from ai_daily_report_attachment_manifests where manifest_id = ? and draft_id = ?",
            (manifest_id, draft_id),
        ).fetchone()
        if not manifest:
            return _cb_resolve_jsonify()({"ok": False, "error": "Manifest 不存在或不属于该 Draft"}), 404

        sources = dbc.execute(
            """
        select source_id, compliance_status as old_status, provider
        from ai_daily_report_manifest_sources
        where manifest_id = ? and compliance_review_required = 1 and compliance_status != 'reviewed'
        """,
            (manifest_id,),
        ).fetchall()
        if not sources:
            return _cb_resolve_jsonify()({"ok": False, "error": "没有待审查的合规项"}), 400
        reviewed_at = _cb_resolve_now()()
        for src in sources:
            dbc.execute(
                """
            update ai_daily_report_manifest_sources
            set compliance_status = 'reviewed',
                compliance_reviewed_by = ?,
                compliance_reviewed_at = ?
            where source_id = ?
            """,
                (_cb_resolve_g().user.get("id"), reviewed_at, src["source_id"]),
            )
        dbc.execute(
            "update ai_daily_report_attachment_manifests set updated_at = ? where manifest_id = ?",
            (reviewed_at, manifest_id),
        )
        dbc.commit()
        _cb_resolve_log_action()(
            "update", "ai_daily_report_manifest", manifest_id, "",
            f"合规审查通过 {len(sources)} 项（{sources[0]['provider']}），旧状态 {sources[0]['old_status']} -> reviewed，"
        f"draft_version={manifest['draft_version']} validation_fingerprint={manifest['validation_fingerprint']}",
        )
        return _cb_resolve_jsonify()({"ok": True, "reviewed": len(sources), "reviewed_at": reviewed_at})


    @_cb_resolve_login_required()
    def ai_daily_report_draft_manifest(draft_id):
        """GET Attachment Manifest state (read-only).

    Returns current_applicable_manifest: the ready manifest matching the
    current draft_version + validation_fingerprint. Old stale/cancelled/failed
    manifests are returned only in history (include_history=1) and are never
    presented as current.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权访问此 Draft"}), 403

        include_history = _cb_resolve_request().args.get("include_history") == "1"
        validation = _cb_resolve__run_phase7_validation()(draft_row)
        manifest_svc = _cb_resolve__attachment_manifest_service()()
        current = manifest_svc.get_current_manifest(draft_row, validation)
        history = manifest_svc.get_manifest_history(draft_id) if include_history else []

        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "draft_version": draft_row.get("draft_version", 1),
            "status": draft_row.get("status"),
            "validation_fingerprint": validation.validation_fingerprint,
            "current": current,
            "history": history,
        })


    @_cb_resolve_login_required()
    def ai_daily_report_manifest_asset_preview(draft_id, manifest_id, asset_id):
        """Secure prepared asset preview (Phase 8).

    Accepts ONLY draft_id + manifest_id + asset_id (no ?path=). Server resolves
    via DB, then enforces DATA_DIR confinement.
    """
        if not _cb_resolve_is_internal_user()():
            _cb_resolve_abort()(403)

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            _cb_resolve_abort()(404)

        if not _cb_resolve_can_view_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            _cb_resolve_abort()(403)

        try:
            manifest_svc = _cb_resolve__attachment_manifest_service()()
            full_path, content_type = manifest_svc.get_asset(draft_id, manifest_id, asset_id)
        except _cb_resolve_ManifestNotFoundError():
            _cb_resolve_abort()(404)
        except _cb_resolve_ManifestError():
            _cb_resolve_abort()(400)

        return _cb_resolve_send_file()(str(full_path), mimetype=content_type, as_attachment=False, conditional=True)


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/photo/<photo_id>', endpoint='ai_daily_report_draft_photo_preview', view_func=ai_daily_report_draft_photo_preview, methods=['GET'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/evidence/<evidence_id>', endpoint='ai_daily_report_draft_evidence_preview', view_func=ai_daily_report_draft_evidence_preview, methods=['GET'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/prepare-attachments', endpoint='ai_daily_report_draft_prepare_attachments', view_func=ai_daily_report_draft_prepare_attachments, methods=['POST'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/formal-save', endpoint='ai_daily_report_draft_formal_save', view_func=ai_daily_report_draft_formal_save, methods=['POST'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/manifest/<manifest_id>/compliance-review', endpoint='ai_daily_report_manifest_compliance_review', view_func=ai_daily_report_manifest_compliance_review, methods=['POST'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/manifest', endpoint='ai_daily_report_draft_manifest', view_func=ai_daily_report_draft_manifest, methods=['GET'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/manifest/<manifest_id>/asset/<asset_id>', endpoint='ai_daily_report_manifest_asset_preview', view_func=ai_daily_report_manifest_asset_preview, methods=['GET'])
    return {'ai_daily_report_draft_photo_preview': ai_daily_report_draft_photo_preview, 'ai_daily_report_draft_evidence_preview': ai_daily_report_draft_evidence_preview, 'ai_daily_report_draft_prepare_attachments': ai_daily_report_draft_prepare_attachments, 'ai_daily_report_draft_formal_save': ai_daily_report_draft_formal_save, 'ai_daily_report_manifest_compliance_review': ai_daily_report_manifest_compliance_review, 'ai_daily_report_draft_manifest': ai_daily_report_draft_manifest, 'ai_daily_report_manifest_asset_preview': ai_daily_report_manifest_asset_preview}
