"""Business routes with explicit current-app resolvers; no reverse import."""


def register_photos_discover_routes(app, *,
    _cb_resolve_DraftVersionConflict,
    _cb_resolve_PhotoDiscoveryService,
    _cb_resolve_PhotoMetadataService,
    _cb_resolve_SHARED_PHOTOS_DIR,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve_db,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_request,
    _cb_resolve_service_order_photo_folder,
):
    @_cb_resolve_login_required()
    def ai_daily_report_discover_photos(draft_id):
        """Discover original site photos for draft and compute arrival/departure candidates.

    Phase 4: Only generates candidates. Does NOT overwrite formal arrival/departure.
    Photos come from server directory: <root>/<SO-XXXXXX>/pictures/YYYY-MM-DD/
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404
        if draft_row["status"] not in {"draft", "confirmed"}:
            return _cb_resolve_jsonify()({"ok": False, "error": f"Draft 状态为 {draft_row['status']}，无法发现照片"}), 400

        # Get expected_version for optimistic locking (optional)
        expected_version = None
        try:
            data = _cb_resolve_request().get_json(silent=True)
            if data and "draft_version" in data:
                expected_version = int(data["draft_version"])
        except Exception:
            pass

        # Initialize photo services
        photo_discovery = _cb_resolve_PhotoDiscoveryService()(
            shared_photos_root=_cb_resolve_SHARED_PHOTOS_DIR(),
            service_order_photo_folder_func=_cb_resolve_service_order_photo_folder(),
        )
        photo_metadata = _cb_resolve_PhotoMetadataService()(shared_photos_root=_cb_resolve_SHARED_PHOTOS_DIR())

        # Field-work manual photo_type is authoritative when present (equipment /
        # arrival / departure / safety). Inherit it into discovered photo candidates.
        try:
            _fp_rows = _cb_resolve_db()().execute("select relative_path, photo_type from field_photos").fetchall()
            _fp_map = {r["relative_path"]: (r["photo_type"] or "") for r in _fp_rows}
        except Exception:
            _fp_map = {}

        def _photo_type_lookup(relative_path: str):
            value = _fp_map.get(relative_path)
            return value or None

        try:
            result = svc.discover_photos_for_draft(
                draft_id=draft_id,
                photo_discovery_service=photo_discovery,
                photo_metadata_service=photo_metadata,
                expected_version=expected_version,
                photo_type_lookup=_photo_type_lookup,
            )
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409

        if result["status"] == "failed":
            error = result.get("error", "照片发现失败")
            if "version" in error:
                return _cb_resolve_jsonify()({"ok": False, "error": error}), 409
            return _cb_resolve_jsonify()({"ok": False, "error": error}), 500

        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()({"ok": True, **result})


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/discover-photos', endpoint='ai_daily_report_discover_photos', view_func=ai_daily_report_discover_photos, methods=['POST'])
    return {'ai_daily_report_discover_photos': ai_daily_report_discover_photos}


def register_photo_timeline_routes(app, *,
    _cb_resolve_DraftVersionConflict,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve_db,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_request,
):
    @_cb_resolve_login_required()
    def ai_daily_report_confirm_photo_timeline(draft_id):
        """Confirm photo timeline and apply candidates to formal arrival/departure.

    User confirms "photo times are OK" -> apply candidates.
    If timeline is suspicious/insufficient/verification_required, requires force=true.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        # Get force flag and expected_version
        force = False
        expected_version = None
        try:
            data = _cb_resolve_request().get_json(silent=True)
            if data:
                force = bool(data.get("force", False))
                if "draft_version" in data:
                    expected_version = int(data["draft_version"])
        except Exception:
            pass

        try:
            result = svc.confirm_photo_timeline(
                draft_id=draft_id,
                expected_version=expected_version,
                force=force,
            )
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409

        if not result.get("ok"):
            return _cb_resolve_jsonify()(result), 400

        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()(result)


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/confirm-photo-timeline', endpoint='ai_daily_report_confirm_photo_timeline', view_func=ai_daily_report_confirm_photo_timeline, methods=['POST'])
    return {'ai_daily_report_confirm_photo_timeline': ai_daily_report_confirm_photo_timeline}


def register_photos_classify_routes(app, *,
    _cb_resolve_DraftVersionConflict,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__make_photo_classification_service,
    _cb_resolve_db,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_request,
    _cb_resolve_vision_settings,
):
    @_cb_resolve_login_required()
    def ai_daily_report_classify_photos(draft_id):
        """Classify all photos in draft using Vision API (Phase 5)."""
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        expected_version = None
        try:
            data = _cb_resolve_request().get_json(silent=True)
            if data and "draft_version" in data:
                expected_version = int(data["draft_version"])
        except Exception:
            pass

        settings = _cb_resolve_vision_settings()()
        photo_svc = _cb_resolve__make_photo_classification_service()()
        try:
            result = svc.classify_draft_photos(
                draft_id=draft_id,
                photo_classification_service=photo_svc,
                analysis_model=settings["model"],
                expected_version=expected_version,
            )
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409

        if not result.get("ok"):
            return _cb_resolve_jsonify()(result), 400

        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()(result)


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/classify-photos', endpoint='ai_daily_report_classify_photos', view_func=ai_daily_report_classify_photos, methods=['POST'])
    return {'ai_daily_report_classify_photos': ai_daily_report_classify_photos}


def register_safety_photo_routes(app, *,
    _cb_resolve_DraftVersionConflict,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__make_photo_classification_service,
    _cb_resolve_db,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_request,
):
    @_cb_resolve_login_required()
    def ai_daily_report_change_safety_photo(draft_id):
        """User manually changes safety photo (Phase 5)."""
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403
        svc = _cb_resolve__ai_daily_report_service()()
        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            photo_id = data.get("photo_id", "")
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
        except Exception:
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        if not photo_id:
            return _cb_resolve_jsonify()({"ok": False, "error": "缺少 photo_id"}), 400

        photo_svc = _cb_resolve__make_photo_classification_service()()
        try:
            result = svc.change_safety_photo(draft_id, photo_id, photo_svc, expected_version)
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突"}), 409
        if not result.get("ok"):
            return _cb_resolve_jsonify()(result), 400

        _cb_resolve_db()().commit()
        return _cb_resolve_jsonify()(result)


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/change-safety-photo', endpoint='ai_daily_report_change_safety_photo', view_func=ai_daily_report_change_safety_photo, methods=['POST'])
    return {'ai_daily_report_change_safety_photo': ai_daily_report_change_safety_photo}
