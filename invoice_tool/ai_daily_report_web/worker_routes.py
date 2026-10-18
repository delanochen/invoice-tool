"""Business routes with explicit current-app resolvers; no reverse import."""


def register_ai_workers_routes(app, *,
    _cb_resolve_DraftVersionConflict,
    _cb_resolve_TravelService,
    _cb_resolve_WorkerTravel,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__fold_staff_query,
    _cb_resolve_can_edit_ai_daily_report_draft,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_normalize_trip_type,
    _cb_resolve_request,
    _cb_resolve_require_ai_daily_report_csrf,
):
    @_cb_resolve_login_required()
    def ai_daily_report_update_worker(draft_id):
        """Update worker travel info (origin, trip_type, transportation).

    Triggers route invalidation (clears old mileage/evidence).
    Requires CSRF token + optimistic locking.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_edit_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权修改此 Draft（需 draft 状态且有编辑权限）"}), 403

        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            user_id = data.get("user_id")
            origin = data.get("origin")
            origin_source = data.get("origin_source", "user_input")
            # trip_type: round_trip / one_way（默认往返）；旧的 overnight_stay 仅做兼容映射
            trip_type = _cb_resolve_normalize_trip_type()(
                data.get("trip_type") if data.get("trip_type") not in (None, "") else data.get("overnight_stay")
            )
            transportation = data.get("transportation", "self_drive")
            # 工作内容（可选）：人工填写或从设备维修清单读取，不参与路程计算
            work_description = data.get("work_description")
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        if not user_id:
            return _cb_resolve_jsonify()({"ok": False, "error": "缺少 user_id"}), 400

        try:
            draft = svc.parse_draft_data(draft_row)
            # Find and update worker
            updated = False
            route_invalidated = False
            for w in draft.workers:
                if w.user_id == user_id:
                    # 只有路程相关字段真的变了才让里程失效：工作内容（work_description）
                    # 与路线无关，单独保存工作内容不该逼用户重算里程。
                    route_related = False
                    if origin is not None and w.origin != origin:
                        w.origin = origin
                        w.origin_source = origin_source
                        w.origin_confirmed = True if origin_source == "user_input" else w.origin_confirmed
                        route_related = True
                    if trip_type and w.trip_type != trip_type:
                        w.trip_type = trip_type
                        route_related = True
                    if transportation is not None and w.transportation != transportation:
                        w.transportation = transportation
                        route_related = True
                    if work_description is not None:
                        w.work_description = work_description
                    updated = True
                    if route_related:
                        # Invalidate route (clears old mileage/evidence) - static method
                        _cb_resolve_TravelService().invalidate_worker_route(w)
                        route_invalidated = True
                    break

            if not updated:
                return _cb_resolve_jsonify()({"ok": False, "error": "工作人员不在此 Draft 中"}), 404

            svc.save_draft(draft_id, draft, expected_version=expected_version)
            _cb_resolve_db()().commit()
            # Get updated draft_version from database
            updated_row = svc.get_draft(draft_id)
            message = ("工作人员信息已更新，里程已失效需重新计算" if route_invalidated
                       else "工作人员信息已更新")
            return _cb_resolve_jsonify()({"ok": True, "draft_version": updated_row["draft_version"], "message": message})
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409


    @_cb_resolve_login_required()
    def ai_daily_report_staff_search():
        """Search active staff to add as Draft workers.

    Read-only. Worker-eligible roles match WORKER_ELIGIBLE_ROLES
    (v0.1.255: includes finance / external_manager / external_employee per
    the 2026-09-16 product decision that daily reports often record work
    performed by external staff). Matching folds case and diacritics.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        q = (_cb_resolve_request().args.get("q") or "").strip()
        if not q:
            return _cb_resolve_jsonify()({"ok": True, "staff": [], "hint": None})

        needle = _cb_resolve__fold_staff_query()(q)
        rows = _cb_resolve_db()().execute(
            "SELECT id, name, email, role FROM users "
        "WHERE is_active = 1 AND role IN ('admin', 'manager', 'finance', 'employee', 'external_manager', 'external_employee') "
        "ORDER BY name",
        ).fetchall()
        staff = [
            {"id": r["id"], "name": r["name"], "email": r["email"], "role": r["role"]}
            for r in rows
            if needle in _cb_resolve__fold_staff_query()(r["name"]) or needle in _cb_resolve__fold_staff_query()(r["email"])
        ][:20]

        hint = None
        if not staff:
            near = _cb_resolve_db()().execute(
                "SELECT id FROM users WHERE name LIKE ? OR email LIKE ? LIMIT 1",
                (f"%{q}%", f"%{q}%"),
            ).fetchone()
            if near:
                hint = "找到相近的用户，但其账号已停用或角色不可加入日报。"
            else:
                hint = "系统中没有该人员的账号。外部人员请先在用户管理中创建账号后再添加。"

        return _cb_resolve_jsonify()({"ok": True, "staff": staff, "hint": hint})


    @_cb_resolve_login_required()
    def ai_daily_report_add_worker(draft_id):
        """Add an internal staff member as a Draft worker.

    Requires CSRF + optimistic locking. Only allowed on draft status.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_edit_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权修改此 Draft（需 draft 状态且有编辑权限）"}), 403

        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            user_id = int(data.get("user_id"))
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        user = _cb_resolve_db()().execute(
            "SELECT id, name, email, role FROM users WHERE id = ? AND is_active = 1", (user_id,)
        ).fetchone()
        if not user:
            return _cb_resolve_jsonify()({"ok": False, "error": "用户不存在"}), 404
        # v0.1.255: align with WORKER_ELIGIBLE_ROLES — external staff may be
        # added to daily reports (2026-09-16 product decision).
        from ai_daily_report.employee_resolution import WORKER_ELIGIBLE_ROLES
        if user["role"] not in WORKER_ELIGIBLE_ROLES:
            return _cb_resolve_jsonify()({"ok": False, "error": "该用户角色不可加入日报工作人员"}), 400

        try:
            draft = svc.parse_draft_data(draft_row)
            if any(w.user_id == user_id for w in draft.workers):
                return _cb_resolve_jsonify()({"ok": False, "error": "该工作人员已在 Draft 中"}), 400
            draft.workers.append(
                _cb_resolve_WorkerTravel()(user_id=user_id, name=user["name"], transportation="self_drive")
            )
            svc.save_draft(draft_id, draft, expected_version=expected_version)
            _cb_resolve_db()().commit()
            updated_row = svc.get_draft(draft_id)
            return _cb_resolve_jsonify()({"ok": True, "draft_version": updated_row["draft_version"], "message": f"已添加 {user['name']}"})
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409


    @_cb_resolve_login_required()
    def ai_daily_report_remove_worker(draft_id):
        """Remove a worker from the Draft.

    Requires CSRF + optimistic locking. Only allowed on draft status.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_edit_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权修改此 Draft（需 draft 状态且有编辑权限）"}), 403

        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            user_id = int(data.get("user_id"))
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        try:
            draft = svc.parse_draft_data(draft_row)
            removed = False
            for i, w in enumerate(draft.workers):
                if w.user_id == user_id:
                    draft.workers.pop(i)
                    removed = True
                    break
            if not removed:
                return _cb_resolve_jsonify()({"ok": False, "error": "工作人员不在此 Draft 中"}), 404
            svc.save_draft(draft_id, draft, expected_version=expected_version)
            _cb_resolve_db()().commit()
            updated_row = svc.get_draft(draft_id)
            return _cb_resolve_jsonify()({"ok": True, "draft_version": updated_row["draft_version"], "message": "已移除工作人员"})
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/update-worker', endpoint='ai_daily_report_update_worker', view_func=ai_daily_report_update_worker, methods=['POST'])
    app.add_url_rule('/api/ai/daily-report/staff', endpoint='ai_daily_report_staff_search', view_func=ai_daily_report_staff_search, methods=['GET'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/add-worker', endpoint='ai_daily_report_add_worker', view_func=ai_daily_report_add_worker, methods=['POST'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/remove-worker', endpoint='ai_daily_report_remove_worker', view_func=ai_daily_report_remove_worker, methods=['POST'])
    return {'ai_daily_report_update_worker': ai_daily_report_update_worker, 'ai_daily_report_staff_search': ai_daily_report_staff_search, 'ai_daily_report_add_worker': ai_daily_report_add_worker, 'ai_daily_report_remove_worker': ai_daily_report_remove_worker}
