"""AI Daily Report - Preview Aggregation Service (Phase 6)

Aggregates all Draft data (workers, mileage, timeline, photos, classification, work items)
into a single read-only Preview model for the Review Center UI.

CRITICAL PRINCIPLES:
- READ-ONLY: does NOT modify Draft, does NOT call Google Routes, does NOT scan photos, does NOT call Vision
- All mutations go through existing DailyReportService / TravelService / etc.
- Provenance/source is explicitly marked for each field (ai_generated, user_input, employee_default, etc.)
- AI model names come from Draft metadata, not hardcoded
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from .schemas import collect_safety_photos
from trip_policy import normalize_trip_type

logger = logging.getLogger(__name__)


class PreviewAggregationService:
    """Aggregates Draft data into a Preview model for Review Center UI."""

    def __init__(self, db_connection, shared_photos_root: str):
        self.db = db_connection
        self.root = Path(shared_photos_root).resolve()

    def build_preview(self, draft_row: Dict[str, Any]) -> Dict[str, Any]:
        """Build complete Preview model from a draft row.

        Args:
            draft_row: database row from ai_daily_report_drafts

        Returns:
            Dict with all aggregated preview data
        """
        draft_data = self._safe_parse_json(draft_row.get("draft_data", "{}"))

        # Basic info
        service_order = self._get_service_order(draft_row.get("service_order_id"))
        basic_info = self._build_basic_info(draft_row, draft_data, service_order)

        # Workers & Travel
        workers = self._build_workers(draft_data)

        # Timeline
        timeline = self._build_timeline(draft_data)

        # Safety Photo
        safety_photo = self._build_safety_photo(draft_data)

        # Service Photos
        service_photos = self._build_service_photos(draft_data)

        # Work Items
        work_items = self._build_work_items(draft_data)

        # Verification Checklist
        verification = self._build_verification_checklist(draft_data, workers, timeline, safety_photo, service_photos, work_items)

        # AI Metadata / Provenance
        ai_metadata = self._build_ai_metadata(draft_data, draft_row)

        # Audit
        audit = self._build_audit(draft_data)

        # Phase 7: Validation Result (read-only, pure computation)
        validation_result = self._build_validation_result(draft_row, draft_data)

        return {
            "draft_id": draft_row.get("id"),
            "status": draft_row.get("status"),
            "draft_version": draft_row.get("draft_version", 1),
            "basic_info": basic_info,
            "workers": workers,
            "timeline": timeline,
            "safety_photo": safety_photo,
            "service_photos": service_photos,
            "work_items": work_items,
            "verification_checklist": verification,
            "validation_result": validation_result,
            "ai_metadata": ai_metadata,
            "audit": audit,
        }

    # ─── Basic Info ────────────────────────────────────────────────────────

    def _build_basic_info(self, draft_row, draft_data, service_order) -> Dict[str, Any]:
        return {
            "report_date": draft_data.get("report_date") or draft_row.get("report_date"),
            "service_order_id": draft_row.get("service_order_id"),
            "order_number": service_order.get("order_number") if service_order else None,
            "site_name": service_order.get("site_name") if service_order else None,
            "site_address": draft_data.get("destination") or (service_order.get("site_address") if service_order else None),
            "site_address_source": ("draft" if draft_data.get("destination") else ("service_order" if service_order else None)),
            "created_by": self._get_user_name(draft_row.get("created_by")),
            "created_by_id": draft_row.get("created_by"),
            "created_at": draft_row.get("created_at"),
            "updated_at": draft_row.get("updated_at"),
        }

    # ─── Workers & Travel ──────────────────────────────────────────────────

    def _build_workers(self, draft_data) -> List[Dict[str, Any]]:
        workers = []
        for w in draft_data.get("workers", []):
            user_id = w.get("user_id")
            worker = {
                "user_id": user_id,
                "name": w.get("name") or self._get_user_name(user_id),
                "transportation": w.get("transportation", "self_drive"),
                "origin": w.get("origin"),
                "origin_source": w.get("origin_source"),
                "origin_confirmed": w.get("origin_confirmed", False),
                "destination": w.get("destination"),
                "destination_source": w.get("destination_source", "service_order"),
                "trip_type": normalize_trip_type(w.get("trip_type")),
                "one_way_miles": w.get("one_way_miles"),
                "reported_miles": w.get("reported_miles"),
                "route_status": w.get("route_status", "not_calculated"),
                "route_provider": w.get("route_provider"),
                "route_query_time": w.get("route_query_time"),
                "mileage_evidence_id": w.get("mileage_evidence_id"),
                # 工作内容（人工填写或从设备维修清单读取的「位置号 + 铭牌号 + 备注」）
                "work_description": w.get("work_description") or "",
                "provenance": self._determine_worker_provenance(w),
            }
            workers.append(worker)
        return workers

    def _determine_worker_provenance(self, worker: Dict) -> str:
        """Determine provenance of worker travel data."""
        if worker.get("origin_source") == "user_input":
            return "user_input"
        if worker.get("origin_source") == "employee_default":
            return "employee_default"
        if worker.get("route_status") == "success":
            return "ai_generated"
        return "unknown"

    # ─── Timeline ──────────────────────────────────────────────────────────

    def _build_timeline(self, draft_data) -> Dict[str, Any]:
        return {
            "arrival_time": draft_data.get("arrival_time"),
            "departure_time": draft_data.get("departure_time"),
            "arrival_time_source": draft_data.get("arrival_time_source"),
            "departure_time_source": draft_data.get("departure_time_source"),
            "arrival_candidate_photo_id": self._get_photo_id_from_candidate(draft_data.get("arrival_candidate")),
            "departure_candidate_photo_id": self._get_photo_id_from_candidate(draft_data.get("departure_candidate")),
            "arrival_photo_ref": draft_data.get("arrival_photo_ref"),
            "departure_photo_ref": draft_data.get("departure_photo_ref"),
            "photo_timeline_status": draft_data.get("photo_timeline_status", "not_scanned"),
            "photo_set_fingerprint": draft_data.get("photo_set_fingerprint"),
            "photo_timeline_generated_at": draft_data.get("photo_timeline_generated_at"),
            "total_photos": len(draft_data.get("photo_candidates", [])),
            "photo_candidates": [
                {
                    "photo_id": p.get("photo_id"),
                    "relative_path": p.get("relative_path"),
                    "classification": p.get("classification"),
                    "manual_classification": p.get("manual_classification"),
                    "capture_time": p.get("capture_time"),
                    "user_modified_time": p.get("user_modified_time"),
                }
                for p in draft_data.get("photo_candidates", [])
                if isinstance(p, dict) and p.get("photo_id")
            ],
            "provenance": "photo_timeline" if draft_data.get("arrival_time_source") == "photo_timeline_confirmed" else (draft_data.get("arrival_time_source") or "unknown"),
        }

    def _get_photo_id_from_candidate(self, candidate) -> Optional[str]:
        if not candidate:
            return None
        if isinstance(candidate, dict):
            return candidate.get("photo_id")
        return None

    # ─── Safety Photo ──────────────────────────────────────────────────────

    def _build_safety_photo(self, draft_data) -> Dict[str, Any]:
        selected = collect_safety_photos(draft_data)
        candidates = draft_data.get("safety_photo_candidates", [])
        return {
            "selected_count": len(selected),
            "selected_photo_ids": [s.get("photo_id") for s in selected if isinstance(s, dict)],
            "selected_photos": [
                {
                    "photo_id": s.get("photo_id"),
                    "confidence": s.get("confidence"),
                    "sub_category": s.get("sub_category"),
                    "selected_source": s.get("selected_source"),
                } for s in selected if isinstance(s, dict)
            ],
            "selected_photo_id": selected[0].get("photo_id") if selected else None,
            "selected_source": draft_data.get("selected_safety_photo_source") or (selected[0].get("selected_source") if selected else None),
            "confidence": selected[0].get("confidence") if selected else None,
            "sub_category": selected[0].get("sub_category") if selected else None,
            "candidates_count": len(candidates),
            "candidates": [{"photo_id": c.get("photo_id"), "confidence": c.get("confidence")} for c in candidates if isinstance(c, dict)],
            "provenance": "ai_selected" if draft_data.get("selected_safety_photo_source") == "ai_selected" else "user_selected" if selected else "none",
        }

    # ─── Service Photos ────────────────────────────────────────────────────

    def _build_service_photos(self, draft_data) -> Dict[str, Any]:
        selected = draft_data.get("selected_service_photos", [])
        candidates = draft_data.get("service_photo_candidates", [])
        return {
            "selected_count": len(selected),
            "max_allowed": 10,
            "selected_photo_ids": [s.get("photo_id") for s in selected if isinstance(s, dict)],
            "selected": [
                {
                    "photo_id": s.get("photo_id"),
                    "sub_category": s.get("sub_category"),
                    "confidence": s.get("confidence"),
                    "selected_source": s.get("selected_source"),
                }
                for s in selected if isinstance(s, dict)
            ],
            "candidates_count": len(candidates),
            "user_selected_ids": draft_data.get("user_selected_service_photo_ids", []),
            "user_removed_ids": draft_data.get("user_removed_service_photo_ids", []),
            "provenance": "mixed" if draft_data.get("user_selected_service_photo_ids") else "ai_selected",
        }

    # ─── Work Items ────────────────────────────────────────────────────────

    def _build_work_items(self, draft_data) -> List[Dict[str, Any]]:
        items = []
        for item in draft_data.get("work_items", []):
            items.append({
                "equipment": item.get("equipment"),
                "action": item.get("action"),
                "fuse_number": item.get("fuse_number"),
                "description": item.get("description"),
                "provenance": item.get("provenance", "ai_generated"),
            })
        return items

    # ─── Verification Checklist ────────────────────────────────────────────

    def _build_verification_checklist(self, draft_data, workers, timeline, safety_photo, service_photos, work_items) -> List[Dict[str, Any]]:
        checklist = []

        # Workers
        if workers:
            # 行程类型有默认值（往返），不再像旧 overnight_stay 那样参与「是否待确认」判定。
            all_origin_confirmed = all(w.get("origin_confirmed") for w in workers if w.get("transportation") == "self_drive")
            if all_origin_confirmed:
                checklist.append({"field": "workers", "status": "ready", "message": f"{len(workers)} 名工作人员，出行信息完整"})
            else:
                checklist.append({"field": "workers", "status": "warning", "message": "部分工作人员出行信息待确认"})
        else:
            checklist.append({"field": "workers", "status": "missing", "message": "未添加工作人员"})

        # Mileage
        self_drive_workers = [w for w in workers if w.get("transportation") == "self_drive"]
        if self_drive_workers:
            all_routes_success = all(w.get("route_status") == "success" for w in self_drive_workers)
            if all_routes_success:
                checklist.append({"field": "mileage", "status": "ready", "message": f"{len(self_drive_workers)} 名自驾人员里程已计算"})
            else:
                checklist.append({"field": "mileage", "status": "warning", "message": "部分人员里程未计算"})
        else:
            checklist.append({"field": "mileage", "status": "ready", "message": "无自驾人员"})

        # Timeline
        if timeline.get("photo_timeline_status") == "ready":
            checklist.append({"field": "timeline", "status": "ready", "message": f"到达 {timeline.get('arrival_time')} / 离场 {timeline.get('departure_time')}"})
        elif timeline.get("photo_timeline_status") in ("suspicious", "verification_required"):
            checklist.append({"field": "timeline", "status": "warning", "message": "照片时间线需要确认"})
        elif timeline.get("photo_timeline_status") in ("no_photos", "insufficient_photos"):
            checklist.append({"field": "timeline", "status": "missing", "message": "照片数量不足"})
        else:
            checklist.append({"field": "timeline", "status": "warning", "message": "未扫描照片时间线"})

        # Safety Photo
        if safety_photo.get("selected_photo_id"):
            checklist.append({"field": "safety_photo", "status": "ready", "message": "已选择安全自检照片"})
        else:
            checklist.append({"field": "safety_photo", "status": "missing", "message": "未选择安全自检照片"})

        # Service Photos
        if service_photos.get("selected_count", 0) > 0:
            checklist.append({"field": "service_photos", "status": "ready", "message": f"已选择 {service_photos['selected_count']} 张施工照片"})
        else:
            checklist.append({"field": "service_photos", "status": "missing", "message": "未选择施工照片"})

        # Work Items
        if work_items:
            checklist.append({"field": "work_items", "status": "ready", "message": f"{len(work_items)} 项施工内容"})
        else:
            checklist.append({"field": "work_items", "status": "missing", "message": "未填写施工内容"})

        return checklist

    # ─── AI Metadata / Provenance ──────────────────────────────────────────

    def _build_ai_metadata(self, draft_data, draft_row) -> Dict[str, Any]:
        ai_generated = draft_data.get("ai_generated", False)
        ai_model = draft_data.get("ai_model")  # Text AI model (from Draft metadata, not hardcoded)
        vision_model = draft_data.get("vision_model")  # Vision model (separate field)
        vision_analysis_version = draft_data.get("photo_classification_version")

        # Determine which fields are AI generated vs user modified
        fields_ai = []
        fields_user = []

        if draft_data.get("workers"):
            has_user_origin = any(w.get("origin_source") == "user_input" for w in draft_data["workers"])
            if has_user_origin:
                fields_user.append("workers.origin")
            else:
                fields_ai.append("workers")

        if draft_data.get("arrival_time_source") == "user_input":
            fields_user.append("arrival_time")
        elif draft_data.get("arrival_time_source") == "photo_timeline_confirmed":
            fields_ai.append("arrival_time")

        if draft_data.get("selected_safety_photo_source") == "user_selected":
            fields_user.append("safety_photo")
        else:
            fields_ai.append("safety_photo")

        if draft_data.get("user_selected_service_photo_ids"):
            fields_user.append("service_photos")
        else:
            fields_ai.append("service_photos")

        verification_fields = draft_data.get("verification_fields", [])
        if isinstance(verification_fields, list):
            # v0.1.236: 施工内容是描述文字而非表格化字段（用户 2026-09-17 决策），
            # work_items.* 不再作为待确认字段展示/弹窗（含存量草稿已有的标记）。
            # v0.1.241: 再同步清理已确认的出行字段（origin/overnight）存量标记，
            # 避免「以下字段仍需要确认」弹窗反复出现。
            from .travel_service import reconcile_travel_verification_fields
            verification_fields = reconcile_travel_verification_fields(
                draft_data.get("workers", []), verification_fields
            )
            verification_fields = [
                f for f in verification_fields
                if not str(f).lower().startswith("work_items.")
            ]

        return {
            "ai_generated": ai_generated,
            "ai_model": ai_model,  # Text AI model (e.g. deepseek-v4-flash)
            "vision_model": vision_model,  # Vision model (separate, e.g. deepseek-flash)
            "vision_analysis_version": vision_analysis_version,
            "ai_created_at": draft_data.get("ai_created_at") or draft_row.get("created_at"),
            "fields_ai_generated": fields_ai,
            "fields_user_modified": fields_user,
            "verification_required_fields": verification_fields,
            "has_verification_required": len(verification_fields) > 0,
        }

    # ─── Audit ─────────────────────────────────────────────────────────────

    def _build_audit(self, draft_data) -> Dict[str, Any]:
        return {
            "confirmed_by": draft_data.get("confirmed_by"),
            "confirmed_at": draft_data.get("confirmed_at"),
            "verification_override": draft_data.get("verification_override", False),
            "override_fields": draft_data.get("override_fields", []),
            "reopened_by": draft_data.get("reopened_by"),
            "reopened_at": draft_data.get("reopened_at"),
            "cancelled_by": draft_data.get("cancelled_by"),
            "cancelled_at": draft_data.get("cancelled_at"),
        }

    # ─── Phase 7: Validation Result ───────────────────────────────────────

    def _build_validation_result(self, draft_row: Dict[str, Any], draft_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build ValidationResult for the Review Center.

        READ-ONLY: ValidationEngine is pure (no DB, no filesystem, no network).
        ValidationContextBuilder only does SELECT queries for authoritative data.
        No writes, no external calls, no photo scanning.
        """
        try:
            from .validation_engine import ValidationEngine, ValidationContextBuilder
            context_builder = ValidationContextBuilder(self.db)
            context = context_builder.build(draft_data)
            engine = ValidationEngine()
            result = engine.validate(
                draft_data=draft_data,
                context=context,
                draft_version=draft_row.get("draft_version", 1),
            )
            return result.to_dict()
        except Exception as exc:
            logger.warning("Preview validation failed for draft %s: %s", draft_row.get("id"), exc)
            return {
                "engine_version": "7.0.0",
                "is_valid": False,
                "can_proceed": False,
                "error_count": 1,
                "warning_count": 0,
                "info_count": 0,
                "total_count": 1,
                "draft_version": draft_row.get("draft_version", 1),
                "validation_fingerprint": "",
                "issues": [{
                    "rule_id": "DRFT-001",
                    "severity": "error",
                    "category": "DRFT",
                    "message": f"Validation engine error: {exc}",
                    "subject_type": "draft",
                    "subject_id": "validation_error",
                    "issue_key": "DRFT-001:draft:validation_error",
                    "issue_fingerprint": "",
                    "blocking": True,
                    "acknowledgement_required": False,
                    "acknowledged": False,
                }],
                "errors": [],
                "warnings": [],
                "infos": [],
            }

    # ─── Helpers ───────────────────────────────────────────────────────────

    def _safe_parse_json(self, raw: str) -> Dict[str, Any]:
        """Safely parse JSON, returning empty dict on failure."""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _get_service_order(self, order_id: Optional[int]) -> Optional[Dict]:
        if not order_id:
            return None
        try:
            row = self.db.execute(
                "select id, order_number, client_name, site_address from service_orders where id = ?",
                (order_id,),
            ).fetchone()
            if not row:
                return None
            out = dict(row)
            # service_orders has no site_name column; the business "site" for a
            # service order is its client. Keep the key so the preview shape is
            # unchanged for the frontend.
            out["site_name"] = row["client_name"] or ""
            return out
        except Exception:
            return None

    def _get_user_name(self, user_id: Optional[int]) -> Optional[str]:
        if not user_id:
            return None
        try:
            row = self.db.execute("select name from users where id = ?", (user_id,)).fetchone()
            return row["name"] if row else None
        except Exception:
            return None
