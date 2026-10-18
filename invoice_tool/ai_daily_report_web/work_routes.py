"""Business routes with explicit current-app resolvers; no reverse import."""


def register_ai_work_routes(app, *,
    _cb_resolve_DATA_DIR,
    _cb_resolve_DraftVersionConflict,
    _cb_resolve_EmployeeResolutionService,
    _cb_resolve_GoogleRoutesService,
    _cb_resolve_MileageService,
    _cb_resolve_TravelService,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve_can_edit_ai_daily_report_draft,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_get_google_routes_api_key,
    _cb_resolve_get_google_static_maps_api_key,
    _cb_resolve_is_internal_user,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_os,
    _cb_resolve_request,
    _cb_resolve_require_ai_daily_report_csrf,
    _cb_resolve_require_service_order,
):
    @_cb_resolve_login_required()
    def ai_daily_report_recalculate_mileage(draft_id):
        """Recalculate mileage for self_drive workers and (re)generate evidence.

    Reuses the same server-side Phase 2/3A/3B logic as the chat action:
    - Phase 2: destination from service_order.site_address + travel field verification
    - Phase 3A: Google Routes mileage via MileageService
    - Phase 3B: Static Maps evidence via MileageEvidenceService (persisted to Draft evidence_records)

    Requires CSRF + optimistic locking. Does NOT touch formal reports.
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

        expected_version = None
        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            if data.get("draft_version") is not None:
                expected_version = int(data["draft_version"])
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        try:
            draft = svc.parse_draft_data(draft_row)

            # Phase 2: destination from service_order site_address
            try:
                order = _cb_resolve_require_service_order()(draft.service_order_id)
            except Exception:
                return _cb_resolve_jsonify()({"ok": False, "error": "工单不存在"}), 404
            site_address = (order["site_address"] or "") if order else ""
            emp_resolver = _cb_resolve_EmployeeResolutionService()(_cb_resolve_db()(), _cb_resolve_g().user["id"], _cb_resolve_g().user["name"])
            travel_svc = _cb_resolve_TravelService()(_cb_resolve_db()(), emp_resolver)
            travel_svc.set_destination_for_all(draft.workers, site_address)

            # Verify travel fields first (same gate as chat Phase 2)
            missing, travel_messages = travel_svc.verify_travel_fields(
                draft.workers, bool(site_address.strip())
            )
            if missing:
                draft.verification_required = True
                draft.verification_fields = list(set(draft.verification_fields) | set(missing))
                svc.save_draft(draft_id, draft, expected_version=expected_version)
                _cb_resolve_db()().commit()
                updated_row = svc.get_draft(draft_id)
                return _cb_resolve_jsonify()({
                    "ok": False,
                    "error": "缺少必要出行信息，无法计算里程",
                    "missing": sorted(set(missing)),
                    "draft_version": updated_row["draft_version"],
                }), 422

            routes_api_key = _cb_resolve_get_google_routes_api_key()()
            if not routes_api_key:
                for w in draft.workers:
                    if w.transportation == "self_drive" and w.route_status != "success":
                        w.route_status = "verification_required"
                        w.route_error = "Google Routes API key not configured"
                svc.save_draft(draft_id, draft, expected_version=expected_version)
                _cb_resolve_db()().commit()
                updated_row = svc.get_draft(draft_id)
                return _cb_resolve_jsonify()({
                    "ok": False,
                    "error": "服务器未配置 Google Routes API key，无法计算里程",
                    "draft_version": updated_row["draft_version"],
                }), 422

            from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService

            # Phase 3A: mileage via Google Routes
            routes_svc = _cb_resolve_GoogleRoutesService()(routes_api_key)
            mileage_svc = _cb_resolve_MileageService()(routes_svc)
            # Explicitly invalidate cached routes first: this endpoint is a manual
            # "recalculate" request, so the MileageService cache guard must not
            # short-circuit with stale origin/overnight data.
            for w in draft.workers:
                if w.transportation == "self_drive":
                    _cb_resolve_TravelService().invalidate_worker_route(w)
            mileage_svc.calculate_for_all(draft.workers)

            # Phase 3B: evidence via Static Maps (persist to Draft evidence_records)
            static_maps_key = _cb_resolve_get_google_static_maps_api_key()()
            if static_maps_key:
                evidence_svc = MileageEvidenceService(
                    GoogleStaticMapsService(static_maps_key),
                    _cb_resolve_os().path.join(_cb_resolve_DATA_DIR(), "ai-daily-report-drafts"),
                )
                for w in draft.workers:
                    if w.transportation == "self_drive" and w.route_status == "success":
                        record = evidence_svc.generate_evidence(
                            w,
                            draft_id=draft_id,
                            service_order_id=draft.service_order_id,
                            report_date=draft.report_date,
                            generated_by=_cb_resolve_g().user["id"],
                            draft_evidence_records=draft.evidence_records,
                        )
                        draft.evidence_records = MileageEvidenceService.upsert_evidence_record(
                            draft.evidence_records, record
                        )
                        w.mileage_evidence_id = record.evidence_id
                        w.mileage_evidence_path = record.file_relative_path
                        w.mileage_verification_required = record.evidence_status == "verification_required"

            svc.save_draft(draft_id, draft, expected_version=expected_version)
            _cb_resolve_db()().commit()
            updated_row = svc.get_draft(draft_id)

            workers_out = []
            for w in draft.workers:
                if w.transportation == "self_drive":
                    workers_out.append({
                        "user_id": w.user_id,
                        "name": w.name,
                        "route_status": w.route_status,
                        "one_way_miles": w.one_way_miles,
                        "reported_miles": w.reported_miles,
                        "mileage_evidence_id": w.mileage_evidence_id,
                        "route_error": w.route_error,
                    })
            return _cb_resolve_jsonify()({
                "ok": True,
                "draft_version": updated_row["draft_version"],
                "message": "里程计算完成",
                "workers": workers_out,
            })
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409


    @_cb_resolve_login_required()
    def ai_daily_report_update_arrival_departure(draft_id):
        """Update arrival/departure times (user override).

    Sets arrival_time_source/departure_time_source = user_input.
    Requires CSRF + optimistic locking.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_edit_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权修改此 Draft"}), 403

        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            arrival_time = data.get("arrival_time")
            departure_time = data.get("departure_time")
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        try:
            draft = svc.parse_draft_data(draft_row)
            if arrival_time is not None:
                draft.arrival_time = arrival_time
                draft.arrival_time_source = "user_input"
            if departure_time is not None:
                draft.departure_time = departure_time
                draft.departure_time_source = "user_input"

            svc.save_draft(draft_id, draft, expected_version=expected_version)
            _cb_resolve_db()().commit()
            updated_row = svc.get_draft(draft_id)
            return _cb_resolve_jsonify()({"ok": True, "draft_version": updated_row["draft_version"]})
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409


    @_cb_resolve_login_required()
    def ai_daily_report_update_work_item(draft_id):
        """Update work items (add/update/remove).

    Requires CSRF + optimistic locking.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        _cb_resolve_require_ai_daily_report_csrf()()

        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404

        if not _cb_resolve_can_edit_ai_daily_report_draft()(_cb_resolve_g().user, draft_row):
            return _cb_resolve_jsonify()({"ok": False, "error": "无权修改此 Draft"}), 403

        try:
            data = _cb_resolve_request().get_json(silent=True) or {}
            action = data.get("action", "replace")  # add/update/remove/replace
            work_items = data.get("work_items", [])
            expected_version = data.get("draft_version")
            if expected_version is not None:
                expected_version = int(expected_version)
        except (ValueError, TypeError):
            return _cb_resolve_jsonify()({"ok": False, "error": "无效请求"}), 400

        try:
            draft = svc.parse_draft_data(draft_row)

            if action == "replace":
                draft.work_items = work_items
            elif action == "add":
                draft.work_items.extend(work_items)
            elif action == "update":
                for item in work_items:
                    for i, existing in enumerate(draft.work_items):
                        if existing.get("equipment") == item.get("equipment"):
                            draft.work_items[i] = item
                            break
            elif action == "remove":
                remove_equipments = {item.get("equipment") for item in work_items}
                draft.work_items = [w for w in draft.work_items if w.get("equipment") not in remove_equipments]

            svc.save_draft(draft_id, draft, expected_version=expected_version)
            _cb_resolve_db()().commit()
            updated_row = svc.get_draft(draft_id)
            return _cb_resolve_jsonify()({"ok": True, "draft_version": updated_row["draft_version"], "work_items_count": len(draft.work_items)})
        except _cb_resolve_DraftVersionConflict():
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 版本冲突，请刷新后重试"}), 409


    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/recalculate-mileage', endpoint='ai_daily_report_recalculate_mileage', view_func=ai_daily_report_recalculate_mileage, methods=['POST'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/update-arrival-departure', endpoint='ai_daily_report_update_arrival_departure', view_func=ai_daily_report_update_arrival_departure, methods=['POST'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/update-work-item', endpoint='ai_daily_report_update_work_item', view_func=ai_daily_report_update_work_item, methods=['POST'])
    return {'ai_daily_report_recalculate_mileage': ai_daily_report_recalculate_mileage, 'ai_daily_report_update_arrival_departure': ai_daily_report_update_arrival_departure, 'ai_daily_report_update_work_item': ai_daily_report_update_work_item}
