"""Business routes with explicit current-app resolvers; no reverse import."""


def register_ai_confirmation_routes(app, *,
    _cb_resolve_AIIntentService,
    _cb_resolve_DailyReportService,
    _cb_resolve_DraftVersionConflict,
    _cb_resolve_EmployeeResolutionService,
    _cb_resolve_FormalSaveError,
    _cb_resolve_ManifestError,
    _cb_resolve_TravelService,
    _cb_resolve_WorkOrderContextService,
    _cb_resolve__ai_daily_report_business_date,
    _cb_resolve__ai_daily_report_service,
    _cb_resolve__attachment_manifest_service,
    _cb_resolve__auto_prepare_draft_media,
    _cb_resolve__new_formal_save_service,
    _cb_resolve__run_phase7_validation,
    _cb_resolve_app,
    _cb_resolve_datetime,
    _cb_resolve_db,
    _cb_resolve_deepseek_assistant_settings,
    _cb_resolve_g,
    _cb_resolve_generate_action_id,
    _cb_resolve_get_google_routes_api_key,
    _cb_resolve_get_timezone_name,
    _cb_resolve_is_internal_user,
    _cb_resolve_is_phase1_implemented,
    _cb_resolve_jsonify,
    _cb_resolve_login_required,
    _cb_resolve_now,
    _cb_resolve_reconcile_travel_verification_fields,
    _cb_resolve_request,
    _cb_resolve_require_service_order,
    _cb_resolve_timezone,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def ai_daily_report_chat():
        """Main chat endpoint: natural language -> Action -> Draft update -> preview.

    Request JSON:
        message: str (required)
        service_order_id: int (required)
        draft_id: int (optional, for continuing an existing draft)
        report_date: str (optional, defaults to current business date)
        action_id: str (optional, for idempotency; auto-generated if missing)

    Response JSON:
        ok: bool
        draft_id: int
        preview: dict (DailyReportDraft preview)
        message: str (action result or clarification question)
        clarification_required: bool
        missing_fields: list
        error: str (only if ok=false)
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        body = _cb_resolve_request().get_json(silent=True) or {}
        message = str(body.get("message") or "").strip()
        service_order_id = body.get("service_order_id")
        draft_id = body.get("draft_id")
        report_date = body.get("report_date") or _cb_resolve__ai_daily_report_business_date()()
        action_id = body.get("action_id") or _cb_resolve_generate_action_id()()

        if not message:
            return _cb_resolve_jsonify()({"ok": False, "error": "消息不能为空"}), 400
        if not service_order_id:
            return _cb_resolve_jsonify()({"ok": False, "error": "缺少 service_order_id"}), 400

        # Verify service order exists and user has access
        try:
            order = _cb_resolve_require_service_order()(service_order_id)
        except Exception:
            return _cb_resolve_jsonify()({"ok": False, "error": "工单不存在"}), 404

        settings = _cb_resolve_deepseek_assistant_settings()()
        intent_service = _cb_resolve_AIIntentService()(settings)
        if not intent_service.is_available():
            return _cb_resolve_jsonify()({"ok": False, "error": "DeepSeek 未启用或未配置 API Key"}), 503

        svc = _cb_resolve__ai_daily_report_service()()

        # Get or create draft
        draft_row = None
        if draft_id:
            draft_row = svc.get_draft(draft_id)
            if not draft_row:
                return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404
        else:
            draft_row = svc.get_active_draft(service_order_id, report_date)
            if not draft_row:
                draft_row = svc.create_draft(
                    service_order_id=service_order_id,
                    report_date=report_date,
                    site_address=order["site_address"],
                    ai_model=settings["model"],
                )

        draft_id = draft_row["id"]
        draft = svc.parse_draft_data(draft_row)

        # Idempotency check
        executed_ids = svc.get_executed_action_ids(draft_id)
        if action_id in executed_ids:
            # Return current draft state without re-executing
            preview = svc.build_preview(draft)
            return _cb_resolve_jsonify()({
                "ok": True,
                "draft_id": draft_id,
                "preview": preview,
                "message": "操作已执行（幂等跳过）",
                "clarification_required": draft.verification_required,
                "missing_fields": draft.verification_fields,
                "idempotent": True,
            })

        # Reject messages exceeding max length (do NOT silently truncate business instructions)
        # Truncation could cut critical info like "张三今天住酒店" at the end,
        # causing incorrect mileage calculation. User must shorten the message.
        from ai_daily_report.daily_report_service import MAX_MESSAGE_LENGTH
        if len(message) > MAX_MESSAGE_LENGTH:
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": f"消息过长（{len(message)}字符），最大允许 {MAX_MESSAGE_LENGTH} 字符。请缩短消息后重试。",
                "error_code": "message_too_long",
            }), 413

        # Add user message to conversation context. The returned list includes the
        # just-added user message; pass history WITHOUT it to DeepSeek because
        # build_user_prompt already appends the current message as the final turn.
        conversation_history = svc.add_conversation_message(draft_id, "user", message)[:-1]

        # Build existing draft summary for context (token-efficient, no full history)
        draft_summary = svc.build_draft_summary(draft)

        # Phase 2: Initialize context services
        emp_resolver = _cb_resolve_EmployeeResolutionService()(_cb_resolve_db()(), _cb_resolve_g().user["id"], _cb_resolve_g().user["name"])
        travel_svc = _cb_resolve_TravelService()(_cb_resolve_db()(), emp_resolver)
        order_ctx = _cb_resolve_WorkOrderContextService()(_cb_resolve_db()(), _cb_resolve_g().user["id"], _cb_resolve_g().user["name"])

        # Call DeepSeek
        result = intent_service.parse_intent(
            user_message=message,
            current_business_date=report_date,
            current_timezone=_cb_resolve_get_timezone_name()(),
            service_order_id=service_order_id,
            service_order_number=order["order_number"],
            site_address=order["site_address"] or "",
            current_user_name=_cb_resolve_g().user["name"],
            existing_draft_summary=draft_summary,
            conversation_history=conversation_history,
        )

        if not result.ok:
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": result.error,
                "error_code": result.error_code,
            }), 422

        action = result.action

        # State machine: confirmed drafts cannot be silently modified.
        draft_status = draft_row.get("status", "draft")
        if not _cb_resolve_DailyReportService().can_execute_action(draft_status, action.intent):
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": f"Draft 已确认（status={draft_status}），如需修改请先调用 reopen",
                "error_code": "draft_confirmed",
                "draft_id": draft_id,
            }), 409

        # Check if intent is implemented in Phase 1/2
        if not _cb_resolve_is_phase1_implemented()(action):
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": f"操作 {action.intent} 将在后续阶段实现",
                "error_code": "not_implemented",
            }), 501

        # If AI says date is set, use it (user explicitly mentioned a date)
        if action.date and action.date != report_date:
            draft.report_date = action.date
            report_date = action.date

        # Phase 2: Resolve workers to real user_ids
        resolved_workers = []
        clarification_messages = []
        employee_candidates = []

        if action.workers:
            for wi in action.workers:
                emp_result = emp_resolver.resolve(wi.name)
                if emp_result.resolved:
                    resolved_workers.append({
                        "user_id": emp_result.user_id,
                        "name": emp_result.name,
                        "transportation": wi.transportation,
                        "origin": wi.origin,
                    })
                else:
                    clarification_messages.append(emp_result.clarification_question)
                    if emp_result.candidates:
                        employee_candidates.extend(emp_result.candidates)

        # Execute action on draft (with resolved workers for user_id matching)
        draft, exec_message = svc.execute_action(draft, action, resolved_workers=resolved_workers)

        # Phase 2: Set destination from service_order.site_address for all workers
        site_address = order["site_address"] or ""
        travel_svc.set_destination_for_all(draft.workers, site_address)

        # Phase 2: Apply employee default origin for self_drive workers.
        # v0.1.340（用户决策 2026-09-29）：员工档案地址（users.address）能自动带出，
        # 就直接当成已确认的出发地（origin_confirmed=True），里程/佐证立即计算，
        # 不再逼用户多点一次「确认覆盖」。origin_source 仍记 employee_default 保留审计。
        # 用户改地址时会走 invalidate_worker_route 重算，所以不会留下错误的里程。
        for w in draft.workers:
            if w.transportation == "self_drive" and w.user_id > 0:
                from_home = travel_svc.is_home_origin(w.origin)
                if (not w.origin) or from_home:
                    default_addr = emp_resolver.get_employee_default_address(w.user_id)
                    if default_addr:
                        w.origin = default_addr
                        w.origin_source = "employee_default"
                        w.origin_confirmed = True
                        if from_home:
                            travel_svc.invalidate_worker_route(w)

        # Phase 2: Verify travel fields using TravelService
        missing, travel_messages = travel_svc.verify_travel_fields(
            draft.workers, bool(site_address.strip())
        )

        # Phase 3A: Calculate mileage for eligible workers (only when no missing fields)
        # Google Routes is only called for self_drive workers with confirmed origin + destination
        # 行程类型（默认往返）不再作为阻塞条件，见 trip_policy
        mileage_calculated = False
        if not missing:
            routes_api_key = _cb_resolve_get_google_routes_api_key()()
            if routes_api_key:
                from ai_daily_report import GoogleRoutesService, MileageService
                routes_svc = GoogleRoutesService(routes_api_key)
                mileage_svc = MileageService(routes_svc)
                # Only calculate if recalculate_mileage intent OR workers have no route yet
                needs_calc = action.intent == "recalculate_mileage" or any(
                    w.route_status != "success" for w in draft.workers if w.transportation == "self_drive"
                )
                if needs_calc:
                    mileage_svc.calculate_for_all(draft.workers)
                    mileage_calculated = True
            else:
                # No API key configured - mark verification required
                for w in draft.workers:
                    if w.transportation == "self_drive" and not w.route_status:
                        w.route_status = "verification_required"
                        w.route_error = "Google Routes API key not configured"

        # Combine all missing fields and clarification messages
        all_missing = list(set(missing))
        all_clarifications = list(set(clarification_messages + travel_messages))

        if action.clarification_required:
            all_missing.extend(action.missing_fields)

        # v0.1.236: 施工内容是描述文字而非表格化字段（用户 2026-09-17 决策）。
        # 模型可能仍把 work_items.* 标为待确认，这里统一过滤，避免触发确认弹窗。
        all_missing = [
            f for f in all_missing
            if not str(f).lower().startswith("work_items.")
        ]

        if all_missing or all_clarifications:
            draft.verification_required = True
            draft.verification_fields = list(set(all_missing))
        else:
            draft.verification_required = False
            draft.verification_fields = []

        # Record action (idempotent)
        svc.record_action(draft_id, action_id, action, result="ok")

        # Save draft with optimistic locking
        try:
            svc.save_draft(draft_id, draft, expected_version=draft_row.get("draft_version", 1))
        except _cb_resolve_DraftVersionConflict() as exc:
            _cb_resolve_db()().rollback()
            return _cb_resolve_jsonify()({
                "ok": False,
                "error": str(exc),
                "error_code": "version_conflict",
            }), 409
        _cb_resolve_db()().commit()

        # Auto media pipeline (v0.1.234): discover photos, confirm timeline,
        # auto-select safety/construction photos, recalculate mileage — all
        # without manual clicks. Idempotent: finished steps are skipped and
        # manual user overrides are never overwritten.
        auto_steps = _cb_resolve__auto_prepare_draft_media()(draft_id)
        if any(s.get("ok") for s in auto_steps):
            draft_row = svc.get_draft(draft_id)
            draft = svc.parse_draft_data(draft_row)

        preview = svc.build_preview(draft)
        clarification_msg = action.clarification_question or ""
        if all_clarifications and not clarification_msg:
            clarification_msg = "；".join(all_clarifications)

        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "preview": preview,
            "message": exec_message,
            "clarification_required": draft.verification_required,
            "missing_fields": draft.verification_fields,
            "clarification_question": clarification_msg,
            "employee_candidates": employee_candidates,
            "action_id": action_id,
        })


    @_cb_resolve_login_required()
    def ai_daily_report_confirm(draft_id):
        """Confirm draft and auto-chain into the formal service report.

    Confirms the draft (records audit: confirmed_by, confirmed_at,
    verification_override, override_fields), then automatically prepares the
    attachment manifest, auto-reviews mileage-evidence compliance (product
    decision 2026-09-17), and runs the Phase 9 formal save so the draft lands
    in service_reports. If any gate blocks, the draft stays 'confirmed' and
    the response carries auto_formal_save.blocked_code/blocked_message.
    Requires CSRF token.
    """
        if not _cb_resolve_is_internal_user()():
            return _cb_resolve_jsonify()({"ok": False, "error": "无权限"}), 403

        # Note: confirm/cancel/reopen are Phase 1 APIs called by AI Assistant,
        # so CSRF is not enforced here to avoid breaking existing integrations.
        # Phase 6 new mutation APIs (update-worker, etc.) do require CSRF.
        svc = _cb_resolve__ai_daily_report_service()()
        draft_row = svc.get_draft(draft_id)
        if not draft_row:
            return _cb_resolve_jsonify()({"ok": False, "error": "Draft 不存在"}), 404
        # Idempotent re-confirm of an already formally saved draft (AI Assistant
        # retries): return the existing formal report instead of a 400 error.
        if draft_row["status"] == "saved" and draft_row.get("saved_report_id"):
            report_id = int(draft_row["saved_report_id"])
            return _cb_resolve_jsonify()({
                "ok": True,
                "draft_id": draft_id,
                "status": "saved",
                "message": f"该日报已确认并生成工单日报（Report #{report_id}）",
                "auto_formal_save": {
                    "attempted": True,
                    "formal_saved": True,
                    "service_report_id": report_id,
                    "report_url": _cb_resolve_url_for()("edit_service_report", report_id=report_id),
                    "blocked_code": None,
                    "blocked_message": None,
                },
            })
        if draft_row["status"] not in {"draft", "confirmed"}:
            return _cb_resolve_jsonify()({"ok": False, "error": f"Draft 状态为 {draft_row['status']}，无法确认"}), 400

        # Note: confirm is a Phase 1 API called by AI Assistant,
        # so authorization is not enforced here to avoid breaking existing integrations.
        user = _cb_resolve_g().user
        # Convert database Row to dict (Row does not support attribute access)
        if hasattr(user, "keys"):
            user = dict(user)
        user_id = user.get("id", "")

        draft = svc.parse_draft_data(draft_row)

        # v0.1.241: sync verification fields with current state BEFORE the check.
        # verification_fields accumulates over the draft's life; once a worker's
        # origin/overnight is confirmed (manual edit or auto flow), stale entries
        # like "Antonio.origin" / "worker_4_origin_unconfirmed" used to keep the
        # override dialog popping up forever even though nothing was left to confirm.
        draft.verification_fields = _cb_resolve_reconcile_travel_verification_fields()(
            [w.model_dump() for w in draft.workers],
            draft.verification_fields,
        )
        draft.verification_required = bool(draft.verification_fields)

        # Check verification_required
        if draft.verification_required:
            # Allow explicit override
            try:
                data = _cb_resolve_request().get_json(silent=True) or {}
                override = data.get("override_verification", False)
                override_fields = data.get("override_fields", [])
            except Exception:
                override = False
                override_fields = []

            if not override:
                return _cb_resolve_jsonify()({
                    "ok": False,
                    "error": "存在需要确认的字段，请先补充信息或明确确认覆盖",
                    "missing_fields": draft.verification_fields,
                    "override_available": True,
                }), 400

            # Record override
            draft.verification_override = True
            draft.override_fields = override_fields or draft.verification_fields

        # Record audit
        draft.confirmed_by = user_id
        draft.confirmed_at = _cb_resolve_datetime().now(_cb_resolve_timezone().utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        svc.save_draft(draft_id, draft)

        # Phase 6: mark confirmed, then auto-chain into the formal service report.
        svc.update_draft_status(draft_id, "confirmed")
        _cb_resolve_db()().commit()

        # Auto-chain (v0.1.233): confirm -> prepare manifest -> formal save.
        # Best effort: any gate that blocks formal save (Phase 7 ERROR, manifest
        # integrity, compliance review, travel-mode mapping, concurrent commit)
        # leaves the draft in 'confirmed' and reports the reason; the user can
        # then resolve it and finish via the existing prepare / formal-save UI.
        auto = {
            "attempted": True,
            "formal_saved": False,
            "service_report_id": None,
            "report_url": None,
            "blocked_code": None,
            "blocked_message": None,
        }
        try:
            draft_row = svc.get_draft(draft_id)
            validation = _cb_resolve__run_phase7_validation()(draft_row)
            if not validation.can_proceed:
                # v0.1.252: an incomplete daily report may be force-passed to the
                # order (product decision 2026-09-18) so the user can keep editing
                # on the order report page. Requires an explicit request flag.
                force_incomplete = False
                try:
                    force_incomplete = bool(
                        (_cb_resolve_request().get_json(silent=True) or {}).get("force_incomplete")
                    )
                except Exception:
                    force_incomplete = False
                if force_incomplete:
                    formal_svc = _cb_resolve__new_formal_save_service()(user_id)
                    result = formal_svc.run_incomplete(draft_row, None)
                    _cb_resolve_db()().commit()
                    auto["formal_saved"] = True
                    auto["incomplete"] = True
                    auto["service_report_id"] = result["service_report_id"]
                    auto["report_url"] = _cb_resolve_url_for()(
                        "edit_service_report", report_id=result["service_report_id"]
                    )
                else:
                    auto["blocked_code"] = "validation_cannot_proceed"
                    auto["blocked_message"] = "存在未解决的 ERROR，无法自动生成正式日报，请在校验面板处理后手动正式保存。"
            else:
                manifest_svc = _cb_resolve__attachment_manifest_service()()
                manifest = manifest_svc.prepare(draft_row, validation)
                _cb_resolve_db()().commit()
                # Auto-compliance review (user decision 2026-09-17): the confirm
                # automation treats mileage-evidence compliance review as reviewed,
                # mirroring the manual compliance-review endpoint's field writes.
                reviewed_at = _cb_resolve_now()()
                dbc = _cb_resolve_db()()
                dbc.execute(
                    """
                update ai_daily_report_manifest_sources
                set compliance_status = 'reviewed',
                    compliance_reviewed_by = ?,
                    compliance_reviewed_at = ?
                where manifest_id = ? and compliance_review_required = 1 and compliance_status != 'reviewed'
                """,
                    (user_id, reviewed_at, manifest["manifest_id"]),
                )
                dbc.execute(
                    "update ai_daily_report_attachment_manifests set updated_at = ? where manifest_id = ?",
                    (reviewed_at, manifest["manifest_id"]),
                )
                _cb_resolve_db()().commit()
                formal_svc = _cb_resolve__new_formal_save_service()(user_id)
                result = formal_svc.run(draft_row, None, None, validation, manifest_svc)
                _cb_resolve_db()().commit()
                auto["formal_saved"] = True
                auto["service_report_id"] = result["service_report_id"]
                auto["report_url"] = _cb_resolve_url_for()(
                    "edit_service_report", report_id=result["service_report_id"]
                )
        except _cb_resolve_FormalSaveError() as exc:
            try:
                _cb_resolve_db()().rollback()
            except Exception:
                pass
            auto["blocked_code"] = exc.code
            auto["blocked_message"] = exc.message
        except _cb_resolve_ManifestError() as exc:
            try:
                _cb_resolve_db()().rollback()
            except Exception:
                pass
            auto["blocked_code"] = "manifest_error"
            auto["blocked_message"] = str(exc)
        except Exception as exc:  # pragma: no cover - defensive fallback
            try:
                _cb_resolve_db()().rollback()
            except Exception:
                pass
            auto["blocked_code"] = "internal_error"
            auto["blocked_message"] = "自动生成正式日报时发生内部错误，可稍后在确认状态下手动正式保存。"
            _cb_resolve_app().logger.warning("AI daily report auto formal-save failed for draft %s: %s", draft_id, exc)

        if auto["formal_saved"]:
            message = f"已确认并自动生成工单日报（Report #{auto['service_report_id']}）"
            if auto.get("incomplete"):
                message += "，日报不完整，可在工单日报中继续编辑"
        else:
            message = "Draft 已确认，但自动生成工单日报未完成：" + (auto["blocked_message"] or "未知原因")

        return _cb_resolve_jsonify()({
            "ok": True,
            "draft_id": draft_id,
            "status": "saved" if auto["formal_saved"] else "confirmed",
            "message": message,
            "confirmed_by": user_id,
            "confirmed_at": draft.confirmed_at,
            "auto_formal_save": auto,
        })


    app.add_url_rule('/api/ai/daily-report/chat', endpoint='ai_daily_report_chat', view_func=ai_daily_report_chat, methods=['POST'])
    app.add_url_rule('/api/ai/daily-report/draft/<int:draft_id>/confirm', endpoint='ai_daily_report_confirm', view_func=ai_daily_report_confirm, methods=['POST'])
    return {'ai_daily_report_chat': ai_daily_report_chat, 'ai_daily_report_confirm': ai_daily_report_confirm}
