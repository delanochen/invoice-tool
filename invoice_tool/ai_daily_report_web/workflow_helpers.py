"""Legacy-compatible ai_workflow helpers; explicit runtime dependencies."""

def reset_ai_drafts_for_deleted_service_report(connection, report_id, *, now):
    """Make AI drafts reusable after their generated formal report is deleted."""
    rows = connection.execute(
        """
        select id from ai_daily_report_drafts where saved_report_id = ?
        union
        select draft_id from ai_daily_report_formal_commits where service_report_id = ?
        """,
        (report_id, report_id),
    ).fetchall()
    draft_ids = [int(row[0]) for row in rows]
    if not draft_ids:
        return 0
    placeholders = ",".join("?" for _ in draft_ids)
    connection.execute(
        f"delete from ai_daily_report_formal_commits where draft_id in ({placeholders})",
        draft_ids,
    )
    connection.execute(
        f"""
        update ai_daily_report_drafts
        set status = 'draft', saved_report_id = null,
            draft_version = draft_version + 1, updated_at = ?
        where id in ({placeholders})
        """,
        [now(), *draft_ids],
    )
    return len(draft_ids)


def _auto_prepare_draft_media(draft_id: int, *, DATA_DIR, EmployeeResolutionService, PhotoDiscoveryService, PhotoMetadataService, SHARED_PHOTOS_DIR, TravelService, _ai_daily_report_service, _make_photo_classification_service, db, g, get_google_routes_api_key, get_google_static_maps_api_key, os, require_service_order, service_order_photo_folder, vision_settings) -> list:
    """Best-effort auto pipeline for a draft — no manual clicks required.

    Runs, in order (each step skipped when already done, degrades gracefully):
      1. discover_photos: scan site photos and build timeline candidates
         (only when the timeline has not been scanned yet).
      2. confirm_timeline: apply arrival/departure photo candidates
         (force-confirmed; SKIPPED when the user set times manually via
         user_input/manual sources, so human edits are never overwritten).
      3. classify_photos: Vision-classify photos and auto-select safety +
         construction (service) photos (only when not classified yet and
         Vision is enabled; user selections/removals are preserved).
      4. recalculate_mileage: for self_drive workers lacking a successful
         route — compute mileage via Google Routes and generate Static Maps
         evidence records.

    Returns a list of {"step", "ok", ...} summaries; never raises.
    """
    steps = []
    svc = _ai_daily_report_service()

    def _load_draft():
        row = svc.get_draft(draft_id)
        return svc.parse_draft_data(row) if row else None

    def _run(step_name, fn):
        try:
            info = fn() or {}
            db().commit()
            steps.append({"step": step_name, "ok": True, **info})
        except Exception as exc:  # auto pipeline must never break the caller
            try:
                db().rollback()
            except Exception:
                pass
            steps.append({"step": step_name, "ok": False, "error": str(exc)})

    draft_row = svc.get_draft(draft_id)
    if not draft_row or draft_row["status"] not in {"draft", "confirmed"}:
        return steps
    draft = _load_draft()

    # 1) Discover photos (only if not scanned yet)
    if draft.photo_timeline_status in (None, "", "not_scanned", "failed"):
        def _discover():
            photo_discovery = PhotoDiscoveryService(
                shared_photos_root=SHARED_PHOTOS_DIR,
                service_order_photo_folder_func=service_order_photo_folder,
            )
            photo_metadata = PhotoMetadataService(shared_photos_root=SHARED_PHOTOS_DIR)
            try:
                fp_rows = db().execute(
                    "select relative_path, photo_type from field_photos"
                ).fetchall()
                fp_map = {r["relative_path"]: (r["photo_type"] or "") for r in fp_rows}
            except Exception:
                fp_map = {}
            result = svc.discover_photos_for_draft(
                draft_id=draft_id,
                photo_discovery_service=photo_discovery,
                photo_metadata_service=photo_metadata,
                photo_type_lookup=lambda p: fp_map.get(p) or None,
            )
            return {"photo_count": result.get("photo_count")}
        _run("discover_photos", _discover)
        draft = _load_draft()

    # 2) Confirm photo timeline (skip when user set times manually)
    manual_times = draft.arrival_time_source in ("user_input", "manual") or (
        draft.departure_time_source in ("user_input", "manual")
    )
    if (
        not manual_times
        and draft.arrival_time_source != "photo_timeline_confirmed"
        and draft.photo_timeline_status
        not in (None, "", "not_scanned", "no_photos", "failed")
    ):
        _run("confirm_timeline", lambda: svc.confirm_photo_timeline(draft_id, force=True))

    # 3) Vision classify + auto-select safety/construction photos
    draft_row = svc.get_draft(draft_id)
    draft = _load_draft()
    vision_cfg = vision_settings()
    if (
        draft_row["status"] == "draft"
        and vision_cfg.get("enabled")
        and draft.photo_candidates
        and not draft.photo_analysis_results
    ):
        def _classify():
            photo_svc = _make_photo_classification_service()
            result = svc.classify_draft_photos(
                draft_id=draft_id,
                photo_classification_service=photo_svc,
                analysis_model=vision_cfg.get("model", ""),
            )
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "classification_failed"))
            return {
                "selected_service_count": result.get("selected_service_count"),
                "selected_safety": result.get("selected_safety"),
            }
        _run("classify_photos", _classify)

    # 4) Recalculate mileage for self_drive workers without a successful route
    draft = _load_draft()
    if any(
        w.transportation == "self_drive" and w.route_status != "success"
        for w in draft.workers
    ):
        def _mileage():
            try:
                order = require_service_order(draft.service_order_id)
            except Exception:
                return {"skipped": "order_not_found"}
            site_address = (order["site_address"] or "") if order else ""
            emp_resolver = EmployeeResolutionService(db(), g.user["id"], g.user["name"])
            travel_svc = TravelService(db(), emp_resolver)
            travel_svc.set_destination_for_all(draft.workers, site_address)
            missing, _msgs = travel_svc.verify_travel_fields(
                draft.workers, bool(site_address.strip())
            )
            routes_api_key = get_google_routes_api_key()
            if missing:
                return {"skipped": "missing_travel_fields"}
            if not routes_api_key:
                return {"skipped": "no_routes_api_key"}

            from ai_daily_report import (
                GoogleRoutesService,
                MileageService,
                GoogleStaticMapsService,
                MileageEvidenceService,
            )
            routes_svc = GoogleRoutesService(routes_api_key)
            mileage_svc = MileageService(routes_svc)
            for w in draft.workers:
                if w.transportation == "self_drive" and w.route_status != "success":
                    TravelService.invalidate_worker_route(w)
            mileage_svc.calculate_for_all(draft.workers)

            static_maps_key = get_google_static_maps_api_key()
            if static_maps_key:
                evidence_svc = MileageEvidenceService(
                    GoogleStaticMapsService(static_maps_key),
                    os.path.join(DATA_DIR, "ai-daily-report-drafts"),
                )
                for w in draft.workers:
                    if w.transportation == "self_drive" and w.route_status == "success":
                        record = evidence_svc.generate_evidence(
                            w,
                            draft_id=draft_id,
                            service_order_id=draft.service_order_id,
                            report_date=draft.report_date,
                            generated_by=g.user["id"],
                            draft_evidence_records=draft.evidence_records,
                        )
                        draft.evidence_records = MileageEvidenceService.upsert_evidence_record(
                            draft.evidence_records, record
                        )
                        w.mileage_evidence_id = record.evidence_id
                        w.mileage_evidence_path = record.file_relative_path
                        w.mileage_verification_required = (
                            record.evidence_status == "verification_required"
                        )
            svc.save_draft(draft_id, draft)
            return {"calculated": True}
        _run("recalculate_mileage", _mileage)

    return steps


def _build_review_center_preview(draft_row, *, SHARED_PHOTOS_DIR, _attachment_manifest_service, _formal_save_preview_summary, _run_phase7_validation, db):
    """Aggregated read-only preview for the Review Center detail page.

    Every response the review page assigns wholesale to ``currentPreview``
    must use this same shape (status / draft_version / basic_info / timeline /
    validation_result / ...). The flat DailyReportService.build_preview() is a
    chat-flow payload and must NOT be used here (v0.1.254: auto-prepare used
    to return it, which wiped the action buttons after re-render).
    """
    from ai_daily_report import PreviewAggregationService
    preview_svc = PreviewAggregationService(db(), SHARED_PHOTOS_DIR)
    preview = preview_svc.build_preview(draft_row)

    # Phase 8: Attachment Preparation summary (READ-ONLY; never materializes)
    validation = _run_phase7_validation(draft_row)
    manifest_svc = _attachment_manifest_service()
    preview["attachment_preparation"] = manifest_svc.summary_for_preview(draft_row, validation)

    # Phase 9: Formal Save status (READ-ONLY)
    preview["formal_save"] = _formal_save_preview_summary(draft_row)
    return preview


def _formal_save_preview_summary(draft_row, *, can_formal_save_draft, db, g):
    """Read-only Phase 9 status aggregation for the Review Center preview."""
    draft_id = int(draft_row["id"])
    commit = db().execute(
        """
        select status, service_report_id, draft_version, committed_at, failure_code
        from ai_daily_report_formal_commits
        where draft_id = ? order by id desc limit 1
        """,
        (draft_id,),
    ).fetchone()
    saved_report_id = draft_row.get("saved_report_id")
    summary = {
        "allowed": can_formal_save_draft(g.user, draft_row),
        "status": "not_saved",
        "service_report_id": saved_report_id,
    }
    if saved_report_id:
        summary["status"] = "saved"
    if commit:
        summary["commit_status"] = commit["status"]
        summary["commit_report_id"] = commit["service_report_id"]
        summary["committed_at"] = commit["committed_at"]
        summary["failure_code"] = commit["failure_code"]
        if commit["status"] == "committed" and not saved_report_id:
            summary["status"] = "saved"
            summary["service_report_id"] = commit["service_report_id"]
        elif commit["status"] in ("committing", "failed"):
            summary["status"] = "saved" if saved_report_id else "commit_" + commit["status"]
    return summary


def _run_phase7_validation(draft_row, *, db, json):
    """Server-side Phase 7 Validation rerun (read-only).

    Phase 8 MUST NOT trust any frontend can_proceed flag; this is the only
    gate for manifest preparation.
    """
    draft_data = {}
    try:
        draft_data = json.loads(draft_row["draft_data"] or "{}")
    except (json.JSONDecodeError, TypeError):
        draft_data = {"verification_required": True, "verification_fields": ["draft_data_corrupted"]}
    from ai_daily_report.validation_engine import ValidationEngine, ValidationContextBuilder
    context = ValidationContextBuilder(db()).build(draft_data)
    return ValidationEngine().validate(
        draft_data=draft_data,
        context=context,
        draft_version=draft_row.get("draft_version", 1),
    )


def _manifest_error_response(exc, default_status=422, *, ManifestError, ManifestIntegrityError, ManifestNotFoundError, ManifestStaleError, ManifestValidationBlockedError, jsonify):
    if isinstance(exc, ManifestValidationBlockedError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 422
    if isinstance(exc, ManifestIntegrityError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 422
    if isinstance(exc, ManifestStaleError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 409
    if isinstance(exc, ManifestNotFoundError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 404
    if isinstance(exc, ManifestError):
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), default_status
    return jsonify({"ok": False, "error": "附件准备失败"}), 500


def _fold_staff_query(text: str) -> str:
    """Casefold + strip diacritics for tolerant staff matching.

    SQL equality does not provide the required Unicode folding, so searching
    "Antonio" would not match a stored "Ant\u00f3nio". Fold both sides.
    """
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
