"""AI Daily Report - DailyReportService (Phase 1)

Manages Draft CRUD and Action execution.
CRITICAL: Never writes to service_reports until user explicitly Confirms.
All writes go to ai_daily_report_drafts only.

State machine:
    draft -> confirmed   (confirm)
    draft -> cancelled   (cancel)
    confirmed -> draft   (reopen, explicit)
    confirmed -> saved   (Phase 9, formal report creation)
    confirmed -> cancelled (if business allows)
Illegal transitions are rejected. Once confirmed, ordinary update actions
are blocked until explicit reopen.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .schemas import (
    AIAction,
    DailyReportDraft,
    WorkerTravel,
    WorkItem,
    PhotoRef,
)
from .action_validator import generate_action_id
from .travel_service import TravelService

logger = logging.getLogger(__name__)

# Valid state transitions
VALID_TRANSITIONS = {
    "draft": {"confirmed", "cancelled"},
    "confirmed": {"draft", "saved", "cancelled"},
    "saved": set(),
    "cancelled": set(),
}

# Intents allowed even when draft is confirmed (before reopen)
CONFIRMED_ALLOWED_INTENTS = {"clarify"}

# Conversation context limits (Phase 2)
MAX_CONVERSATION_ROUNDS = 6
MAX_MESSAGE_LENGTH = 2000
MAX_SUMMARY_LENGTH = 1500


class ConversationTooLongError(Exception):
    """Raised when conversation exceeds max rounds."""


class DraftStateError(Exception):
    """Raised when an operation violates the draft state machine."""


class DraftVersionConflict(Exception):
    """Raised when optimistic locking detects a concurrent update."""


class DailyReportService:
    """Draft lifecycle and action execution.

    Usage:
        svc = DailyReportService(db_connection, now_fn, user_id, user_name)
        draft_row = svc.get_or_create_draft(service_order_id, report_date)
        draft = svc.parse_draft_data(draft_row)
        draft, msg = svc.execute_action(draft, action)
        svc.save_draft(draft_id, draft, expected_version=draft_row['draft_version'])
    """

    def __init__(self, db_conn, now_fn, user_id: int, user_name: str):
        self.db = db_conn
        self.now = now_fn
        self.user_id = user_id
        self.user_name = user_name

    # ─── State Machine ────────────────────────────────────────────────────

    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        return target in VALID_TRANSITIONS.get(current, set())

    @staticmethod
    def can_execute_action(status: str, intent: str) -> bool:
        """Check if an action intent can be executed in the current status."""
        if status == "draft":
            return True
        if status == "confirmed":
            return intent in CONFIRMED_ALLOWED_INTENTS
        return False  # saved/cancelled

    # ─── Draft CRUD ────────────────────────────────────────────────────────

    def get_draft(self, draft_id: int) -> Optional[Dict[str, Any]]:
        row = self.db.execute(
            "select * from ai_daily_report_drafts where id = ?", (draft_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_active_draft(
        self, service_order_id: int, report_date: str
    ) -> Optional[Dict[str, Any]]:
        """Get the latest draft for this order+date that is not saved/cancelled."""
        row = self.db.execute(
            """
            select * from ai_daily_report_drafts
            where service_order_id = ? and report_date = ? and status in ('draft', 'confirmed')
            order by updated_at desc, id desc
            limit 1
            """,
            (service_order_id, report_date),
        ).fetchone()
        return dict(row) if row else None

    def create_draft(
        self,
        service_order_id: int,
        report_date: str,
        site_address: Optional[str] = None,
        initial_workers: Optional[List[Dict[str, Any]]] = None,
        initial_work_items: Optional[List[Dict[str, Any]]] = None,
        ai_model: str = "",
    ) -> Dict[str, Any]:
        """Create a new draft. Does NOT touch service_reports."""
        draft = DailyReportDraft(
            service_order_id=service_order_id,
            report_date=report_date,
            site_address=site_address,
            workers=[WorkerTravel(**w) for w in (initial_workers or [])],
            work_items=[WorkItem(**w) for w in (initial_work_items or [])],
            ai_metadata={"model": ai_model, "created_by": "ai"},
        )
        now = self.now()
        cursor = self.db.execute(
            """
            insert into ai_daily_report_drafts (
                service_order_id, report_date, draft_data, status,
                ai_model, ai_confidence, created_by, created_at, updated_at
            ) values (?, ?, ?, 'draft', ?, ?, ?, ?, ?)
            """,
            (
                service_order_id,
                report_date,
                draft.model_dump_json(),
                ai_model,
                None,
                self.user_id,
                now,
                now,
            ),
        )
        draft_id = cursor.lastrowid
        return self.get_draft(draft_id)

    def parse_draft_data(self, draft_row: Dict[str, Any]) -> DailyReportDraft:
        """Parse draft_data JSON with safe error handling.

        If JSON is corrupted, returns a safe fallback draft with
        verification_required=True and verification_fields=["draft_data_corrupted"].

        CRITICAL: A corrupted fallback draft MUST NOT proceed to:
        - Confirm (blocked by verification_required check in confirm API)
        - Mileage calculation (Phase 2 must check verification_required)
        - Formal report submission (Phase 9 must check verification_required)
        Human intervention or draft regeneration is required.
        """
        raw = draft_row.get("draft_data") or "{}"
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("draft_data is not a JSON object")
            return DailyReportDraft(**data)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Corrupted draft_data for draft %s: %s", draft_row.get("id"), exc)
            return DailyReportDraft(
                service_order_id=draft_row.get("service_order_id", 0),
                report_date=draft_row.get("report_date", ""),
                verification_required=True,
                verification_fields=["draft_data_corrupted"],
                ai_metadata={"parse_error": str(exc)},
            )

    def save_draft(
        self, draft_id: int, draft: DailyReportDraft, expected_version: Optional[int] = None
    ) -> Dict[str, Any]:
        """Persist draft data with optimistic locking.

        Args:
            draft_id: draft ID
            draft: updated draft object
            expected_version: if provided, only saves if current draft_version matches.
                              Raises DraftVersionConflict on mismatch.

        Returns updated draft row.
        """
        now = self.now()
        verification_fields = json.dumps(
            draft.verification_fields, ensure_ascii=False
        )
        if expected_version is not None:
            cursor = self.db.execute(
                """
                update ai_daily_report_drafts
                set draft_data = ?, verification_required = ?, verification_fields = ?,
                    updated_at = ?, draft_version = draft_version + 1
                where id = ? and draft_version = ?
                """,
                (
                    draft.model_dump_json(),
                    1 if draft.verification_required else 0,
                    verification_fields,
                    now,
                    draft_id,
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                raise DraftVersionConflict(
                    f"Draft {draft_id} was modified by another request (version conflict)."
                )
        else:
            self.db.execute(
                """
                update ai_daily_report_drafts
                set draft_data = ?, verification_required = ?, verification_fields = ?,
                    updated_at = ?, draft_version = draft_version + 1
                where id = ?
                """,
                (
                    draft.model_dump_json(),
                    1 if draft.verification_required else 0,
                    verification_fields,
                    now,
                    draft_id,
                ),
            )
        return self.get_draft(draft_id)

    def update_draft_status(self, draft_id: int, status: str) -> None:
        """Transition draft status with state machine validation."""
        if status not in {"draft", "confirmed", "saved", "cancelled"}:
            raise ValueError(f"Invalid draft status: {status}")
        current = self.db.execute(
            "select status from ai_daily_report_drafts where id = ?", (draft_id,)
        ).fetchone()
        if not current:
            raise ValueError(f"Draft {draft_id} not found")
        if not self.can_transition(current["status"], status):
            raise DraftStateError(
                f"Illegal state transition: {current['status']} -> {status}"
            )
        self.db.execute(
            "update ai_daily_report_drafts set status = ?, updated_at = ? where id = ?",
            (status, self.now(), draft_id),
        )

    # ─── Photo Discovery (Phase 4 Final) ─────────────────────────────────

    def discover_photos_for_draft(
        self,
        draft_id: int,
        photo_discovery_service,
        photo_metadata_service,
        expected_version: Optional[int] = None,
        photo_type_lookup: Optional[Callable[[str], Optional[str]]] = None,
    ) -> Dict[str, Any]:
        """Discover original site photos for a draft and compute arrival/departure candidates.

        Phase 4 Final:
        - Only generates candidates. Does NOT overwrite formal arrival/departure.
        - State machine: only draft status allows scan. confirmed/cancelled prohibited.
        - Optimistic locking: expected_version checked.
        - Manual time protection: arrival_time_source=user_input is never overwritten.
        - Photo set fingerprint: same set -> reuse metadata, no rescan needed.
        - Timeline status: ready / no_photos / insufficient_photos / suspicious / verification_required / failed

        Args:
            draft_id: Draft ID.
            photo_discovery_service: PhotoDiscoveryService instance.
            photo_metadata_service: PhotoMetadataService instance.
            expected_version: if provided, only saves if current draft_version matches.

        Returns:
            Dict with status, photo_count, timeline_status, candidates, verification_fields.
        """
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"status": "failed", "error": "draft_not_found"}

        # State machine: only draft status allows scan
        if draft_row["status"] == "confirmed":
            return {"status": "failed", "error": "draft_confirmed_reopen_required"}
        if draft_row["status"] in ("cancelled", "saved"):
            return {"status": "failed", "error": f"draft_status_{draft_row['status']}_cannot_scan"}

        draft = self.parse_draft_data(draft_row)
        if draft.verification_required and "draft_data_corrupted" in draft.verification_fields:
            return {"status": "failed", "error": "draft_data_corrupted"}

        # Get order_number from service_orders
        order_row = self.db.execute(
            "select order_number from service_orders where id = ?",
            (draft.service_order_id,),
        ).fetchone()
        if not order_row:
            draft.photo_discovery_status = "failed"
            draft.photo_discovery_error = "service_order_not_found"
            draft.photo_timeline_status = "failed"
            self.save_draft(draft_id, draft, expected_version=expected_version)
            return {"status": "failed", "error": "service_order_not_found"}

        order_number = order_row["order_number"]

        # Discover photos (inherit field-work photo_type when lookup provided)
        photos, status, error = photo_discovery_service.discover_photos(
            order_number=order_number,
            report_date=draft.report_date,
            photo_type_lookup=photo_type_lookup,
        )

        if status == "failed":
            draft.photo_discovery_status = "failed"
            draft.photo_discovery_error = error
            draft.photo_timeline_status = "failed"
            draft.photo_candidates = []
            draft.arrival_candidate = None
            draft.departure_candidate = None
            self.save_draft(draft_id, draft, expected_version=expected_version)
            return {"status": "failed", "error": error}

        if status == "no_photos":
            draft.photo_discovery_status = "no_photos"
            draft.photo_discovery_error = None
            draft.photo_timeline_status = "no_photos"
            draft.photo_set_fingerprint = None
            draft.photo_timeline_generated_at = self.now()
            draft.photo_candidates = []
            draft.arrival_candidate = None
            draft.departure_candidate = None
            # Mark verification required for no photos
            if "no_photos" not in draft.verification_fields:
                draft.verification_fields.append("no_photos")
                draft.verification_required = True
            self.save_draft(draft_id, draft, expected_version=expected_version)
            return {"status": "no_photos", "photo_count": 0, "timeline_status": "no_photos"}

        # Compute photo set fingerprint
        new_fingerprint = photo_discovery_service.compute_photo_set_fingerprint(photos)

        # Idempotency: if fingerprint unchanged and we already have enriched photos, reuse
        if (draft.photo_set_fingerprint == new_fingerprint
                and draft.photo_candidates
                and all(p.capture_time for p in draft.photo_candidates)):
            # Reuse existing enriched photos, just recompute candidates
            photos = draft.photo_candidates
            arrival, departure, timeline_status, verification_fields = (
                photo_metadata_service.compute_arrival_departure_candidates(photos)
            )
        else:
            # Enrich photos with capture time
            photos = photo_metadata_service.enrich_all_photos(photos, draft.report_date)
            # Compute arrival/departure candidates (returns 4-tuple now)
            arrival, departure, timeline_status, verification_fields = (
                photo_metadata_service.compute_arrival_departure_candidates(photos)
            )

        # Manual time protection: NEVER overwrite user-input arrival/departure
        # Candidates can update, but formal arrival_time/departure_time stay as-is
        # if they were manually set (arrival_time_source == "user_input")

        # Update draft (candidates only, NOT formal arrival/departure)
        draft.photo_candidates = photos
        draft.arrival_candidate = arrival
        draft.departure_candidate = departure
        draft.arrival_candidate_source = "photo_timeline" if arrival else None
        draft.departure_candidate_source = "photo_timeline" if departure else None
        draft.photo_discovery_status = "discovered"
        draft.photo_discovery_error = None
        draft.photo_set_fingerprint = new_fingerprint
        draft.photo_timeline_generated_at = self.now()
        draft.photo_timeline_status = timeline_status

        # Update verification fields
        for vf in verification_fields:
            if vf not in draft.verification_fields:
                draft.verification_fields.append(vf)
        if timeline_status in ("suspicious", "verification_required", "insufficient_photos"):
            draft.verification_required = True

        self.save_draft(draft_id, draft, expected_version=expected_version)

        return {
            "status": "discovered",
            "photo_count": len(photos),
            "timeline_status": timeline_status,
            "arrival_time": arrival.capture_time if arrival else None,
            "departure_time": departure.capture_time if departure else None,
            "arrival_photo_id": arrival.photo_id if arrival else None,
            "departure_photo_id": departure.photo_id if departure else None,
            "verification_fields": verification_fields,
            "photo_set_fingerprint": new_fingerprint,
        }

    def confirm_photo_timeline(
        self,
        draft_id: int,
        expected_version: Optional[int] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Confirm photo timeline and apply candidates to formal arrival/departure.

        User confirms "photo times are OK" -> apply candidates to formal fields.
        Sets arrival_time_source = photo_timeline_confirmed.
        Preserves arrival_photo_ref / departure_photo_ref for audit.

        If timeline is suspicious/insufficient_photos/verification_required:
        - Cannot confirm silently unless force=True (explicit override)
        - Returns error requiring user to resolve issues or explicitly override.

        Manual time protection: if arrival_time_source == "user_input", we do NOT
        overwrite unless the user explicitly confirms (this method IS the explicit confirm).
        """
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"ok": False, "error": "draft_not_found"}

        # State machine
        if draft_row["status"] == "cancelled":
            return {"ok": False, "error": "draft_cancelled_cannot_confirm"}

        draft = self.parse_draft_data(draft_row)

        # Check timeline status
        if draft.photo_timeline_status in ("suspicious", "insufficient_photos", "verification_required"):
            if not force:
                return {
                    "ok": False,
                    "error": f"timeline_{draft.photo_timeline_status}_requires_override",
                    "timeline_status": draft.photo_timeline_status,
                    "verification_fields": draft.verification_fields,
                }

        if draft.photo_timeline_status in ("no_photos", "failed", "not_scanned"):
            return {"ok": False, "error": f"timeline_{draft.photo_timeline_status}_cannot_confirm"}

        # Apply arrival candidate
        if draft.arrival_candidate and draft.arrival_candidate.capture_time:
            draft.arrival_time = draft.arrival_candidate.capture_time
            draft.arrival_time_source = "photo_timeline_confirmed"
            draft.arrival_photo_ref = draft.arrival_candidate.photo_id
            draft.arrival_photo = draft.arrival_candidate

        # Apply departure candidate
        if draft.departure_candidate and draft.departure_candidate.capture_time:
            draft.departure_time = draft.departure_candidate.capture_time
            draft.departure_time_source = "photo_timeline_confirmed"
            draft.departure_photo_ref = draft.departure_candidate.photo_id
            draft.departure_photo = draft.departure_candidate

        self.save_draft(draft_id, draft, expected_version=expected_version)

        return {
            "ok": True,
            "arrival_time": draft.arrival_time,
            "departure_time": draft.departure_time,
            "arrival_time_source": draft.arrival_time_source,
            "departure_time_source": draft.departure_time_source,
        }

    # ─── Photo Classification (Phase 5) ──────────────────────────────────

    _PHOTO_ID_RE = re.compile(r"^[0-9a-fA-F]{64}$")
    _PHOTO_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def _validate_photo_id(self, photo_id: str) -> bool:
        return bool(self._PHOTO_ID_RE.fullmatch(photo_id or ""))

    def auto_select_service_photos(
        self,
        draft_id: int,
        max_photos: int = 10,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Auto-select service photos ONLY from photos already classified as equipment.

        Time window [arrival_time, departure_time] is used ONLY to filter/sort,
        never to classify. unknown / general / legacy photos are never promoted
        to equipment by time. Never overwrites an existing manual classification.
        No Vision calls.
        """
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"ok": False, "error": "draft_not_found"}
        draft = self.parse_draft_data(draft_row)
        photos = draft.photo_candidates
        if not photos:
            return {"ok": False, "error": "no_photo_candidates_run_phase4_first"}

        # Only photos already explicitly classified as equipment qualify.
        eligible = [p for p in photos if p.classification == "equipment"]
        if not eligible:
            return {"ok": False, "error": "no_equipment_photos_please_mark_equipment_first"}

        def eff_time(p):
            return p.user_modified_time or p.capture_time or ""

        arrival = draft.arrival_time or ""
        departure = draft.departure_time or ""

        def in_window(t):
            if not t:
                return False
            if arrival and t < arrival:
                return False
            if departure and t > departure:
                return False
            return True

        windowed = [p for p in eligible if in_window(eff_time(p))]
        pool = windowed if windowed else eligible
        pool.sort(key=eff_time)
        selected = pool[:max_photos]

        from .schemas import PhotoAnalysis
        analyses = []
        for p in selected:
            analyses.append(PhotoAnalysis(
                photo_id=p.photo_id,
                photo_path=p.relative_path,
                photo_hash=p.photo_hash,
                classification="equipment",
                confidence=1.0,
                sub_category=None,
                capture_time=p.capture_time,
                capture_time_source=p.capture_time_source,
                selected_source="auto_selected",
            ))
        # Merge without overwriting existing user-selected service photos.
        existing_ids = {a.photo_id for a in draft.selected_service_photos}
        for a in analyses:
            if a.photo_id not in existing_ids:
                draft.selected_service_photos.append(a)
        draft.photo_classification_status = "classified"
        draft.photo_classification_generated_at = self.now()
        self.save_draft(draft_id, draft, expected_version=expected_version)
        updated = self.get_draft(draft_id)
        return {
            "ok": True,
            "selected_count": len(analyses),
            "selected_photo_ids": [a.photo_id for a in analyses],
            "draft_version": updated["draft_version"],
        }

    def mark_photo_classification(
        self,
        draft_id: int,
        photo_id: str,
        classification: str,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Set a photo's single business role: equipment / arrival / departure / safety.

        Roles are mutually exclusive. Switching roles removes the photo from its
        previous role collection/refs (selected_service_photos, arrival_photo_ref,
        departure_photo_ref, selected_safety_photo). Already-confirmed formal times
        are preserved; only photo refs are cleaned. No Vision calls.
        """
        if classification not in {"arrival", "departure", "safety", "equipment"}:
            return {"ok": False, "error": "invalid_classification"}
        if not self._validate_photo_id(photo_id):
            return {"ok": False, "error": "invalid_photo_id"}
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"ok": False, "error": "draft_not_found"}
        draft = self.parse_draft_data(draft_row)
        photo = next((p for p in draft.photo_candidates if p.photo_id == photo_id), None)
        if not photo:
            return {"ok": False, "error": "photo_not_found"}

        old_role = photo.manual_classification
        if old_role not in ("equipment", "arrival", "departure", "safety"):
            old_role = photo.classification if photo.classification in ("equipment", "arrival", "departure", "safety") else None

        # ---- clean up previous role ----
        if old_role == "equipment":
            draft.selected_service_photos = [a for a in draft.selected_service_photos if a.photo_id != photo_id]
        if old_role == "arrival" and draft.arrival_photo_ref == photo_id:
            draft.arrival_photo_ref = None
            draft.arrival_photo = None
            # Preserve the user-confirmed time value; a photo-sourced marker is no
            # longer valid without its photo ref, so demote to the existing
            # manual source (never leave photo_marked dangling without a ref).
            if draft.arrival_time and draft.arrival_time_source in ("photo", "photo_timeline_confirmed", "photo_marked"):
                draft.arrival_time_source = "manual"
        if old_role == "departure" and draft.departure_photo_ref == photo_id:
            draft.departure_photo_ref = None
            draft.departure_photo = None
            if draft.departure_time and draft.departure_time_source in ("photo", "photo_timeline_confirmed", "photo_marked"):
                draft.departure_time_source = "manual"
        if old_role == "safety":
            if draft.selected_safety_photo is not None and draft.selected_safety_photo.photo_id == photo_id:
                draft.selected_safety_photo = None
                draft.safety_photo = None

        # ---- apply new role ----
        photo.manual_classification = classification
        photo.classification = classification
        eff = photo.user_modified_time or photo.capture_time

        if classification == "equipment":
            from .schemas import PhotoAnalysis
            existing = {a.photo_id for a in draft.selected_service_photos}
            if photo.photo_id not in existing:
                draft.selected_service_photos.append(PhotoAnalysis(
                    photo_id=photo.photo_id,
                    photo_path=photo.relative_path,
                    photo_hash=photo.photo_hash,
                    classification="equipment",
                    confidence=1.0,
                    sub_category=None,
                    capture_time=photo.capture_time,
                    capture_time_source=photo.capture_time_source,
                    selected_source="user_selected",
                ))
            draft.photo_classification_status = "classified"
        elif classification == "arrival":
            draft.arrival_time = eff
            draft.arrival_time_source = "photo_marked"
            draft.arrival_photo_ref = photo.photo_id
            draft.arrival_photo = photo
        elif classification == "departure":
            draft.departure_time = eff
            draft.departure_time_source = "photo_marked"
            draft.departure_photo_ref = photo.photo_id
            draft.departure_photo = photo
        elif classification == "safety":
            from .schemas import PhotoAnalysis
            draft.selected_safety_photo = PhotoAnalysis(
                photo_id=photo.photo_id,
                photo_path=photo.relative_path,
                photo_hash=photo.photo_hash,
                classification="safety_person",
                confidence=1.0,
                sub_category=None,
                capture_time=photo.capture_time,
                capture_time_source=photo.capture_time_source,
                selected_source="user_selected",
            )
            draft.selected_safety_photo_source = "user_selected"
            draft.safety_photo = photo

        self.save_draft(draft_id, draft, expected_version=expected_version)
        updated = self.get_draft(draft_id)
        return {
            "ok": True,
            "photo_id": photo_id,
            "classification": classification,
            "draft_version": updated["draft_version"],
        }

    def update_photo_time(
        self,
        draft_id: int,
        photo_id: str,
        time_str: str,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Set a user-modified time on a photo (server-validated).

        Original capture_time is never overwritten; user time is stored as
        user_modified_time. If the photo is currently marked as arrival/departure,
        the formal arrival_time / departure_time is updated immediately.
        Invalid format / non-report-date times are rejected (422).
        """
        if not self._validate_photo_id(photo_id):
            return {"ok": False, "error": "invalid_photo_id"}
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"ok": False, "error": "draft_not_found"}
        draft = self.parse_draft_data(draft_row)
        photo = next((p for p in draft.photo_candidates if p.photo_id == photo_id), None)
        if not photo:
            return {"ok": False, "error": "photo_not_found"}

        if time_str:
            if not self._PHOTO_TIME_RE.fullmatch(time_str):
                return {"ok": False, "error": "invalid_time_format"}
            if not time_str.startswith(draft.report_date + "T"):
                return {"ok": False, "error": "time_must_match_report_date"}
            try:
                datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                return {"ok": False, "error": "invalid_time_value"}

        photo.user_modified_time = time_str or None
        if photo.manual_classification == "arrival":
            draft.arrival_time = photo.user_modified_time or photo.capture_time
            draft.arrival_time_source = "photo_marked"
        elif photo.manual_classification == "departure":
            draft.departure_time = photo.user_modified_time or photo.capture_time
            draft.departure_time_source = "photo_marked"
        self.save_draft(draft_id, draft, expected_version=expected_version)
        updated = self.get_draft(draft_id)
        return {"ok": True, "draft_version": updated["draft_version"]}

    def classify_draft_photos(
        self,
        draft_id: int,
        photo_classification_service,
        analysis_model: str = "",
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Classify all photos in a draft using Vision API (Phase 5).

        Uses ai_photo_analysis cache (cross-restart, key=photo_hash+model+version).
        User overrides are persisted and NOT overwritten by re-analysis.
        Equipment ID from Vision does NOT modify Draft.work_items.

        Args:
            draft_id: Draft ID.
            photo_classification_service: PhotoClassificationService instance.
            analysis_model: Vision model name (from config).
            expected_version: optimistic locking version.

        Returns:
            Dict with status, counts, selected safety/service photos.
        """
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"ok": False, "error": "draft_not_found"}

        # State machine
        if draft_row["status"] == "cancelled":
            return {"ok": False, "error": "draft_cancelled_cannot_classify"}
        if draft_row["status"] == "confirmed":
            return {"ok": False, "error": "draft_confirmed_reopen_required"}
        if draft_row["status"] == "saved":
            return {"ok": False, "error": "draft_saved_cannot_modify"}

        draft = self.parse_draft_data(draft_row)
        if draft.verification_required and "draft_data_corrupted" in draft.verification_fields:
            return {"ok": False, "error": "draft_data_corrupted"}

        if not draft.photo_candidates:
            return {"ok": False, "error": "no_photo_candidates_run_phase4_first"}

        # Valid photo IDs from current draft (for cross-draft protection)
        valid_photo_ids = {p.photo_id for p in draft.photo_candidates}

        # User override sets (persisted in draft)
        user_selected_ids = set(draft.user_selected_service_photo_ids)
        user_removed_ids = set(draft.user_removed_service_photo_ids)

        # Classify all photos
        analysis_results, status, metadata = photo_classification_service.classify_draft_photos(
            photo_candidates=draft.photo_candidates,
            analysis_model=analysis_model,
            user_selected_service_photo_ids=user_selected_ids,
            user_removed_service_photo_ids=user_removed_ids,
        )

        # Select safety photo (preserve user selection)
        selected_safety, safety_candidates = photo_classification_service.select_safety_photo(
            analysis_results=analysis_results,
            existing_selected=draft.selected_safety_photo,
        )

        # Select service photos (preserve user selections, exclude user-removed)
        selected_service, selection_error = photo_classification_service.select_service_photos(
            analysis_results=analysis_results,
            existing_selected=draft.selected_service_photos,
            user_selected_ids=user_selected_ids,
            user_removed_ids=user_removed_ids,
        )

        if selection_error == "max_service_photos_exceeded":
            return {"ok": False, "error": "max_service_photos_exceeded",
                    "message": f"用户手动选择超过 {photo_classification_service.max_service_photos} 张，请先移除部分照片"}

        # Update draft
        draft.photo_analysis_results = analysis_results
        draft.safety_photo_candidates = safety_candidates
        draft.selected_safety_photo = selected_safety
        draft.selected_safety_photo_source = selected_safety.selected_source if selected_safety else None
        draft.service_photo_candidates = [a for a in analysis_results if a.classification == "equipment"]
        draft.selected_service_photos = selected_service
        # Persist user override IDs (don't overwrite)
        draft.user_selected_service_photo_ids = list(user_selected_ids)
        draft.user_removed_service_photo_ids = list(user_removed_ids)
        draft.photo_classification_status = status
        draft.photo_classification_generated_at = self.now()
        draft.photo_classification_version = 1

        self.save_draft(draft_id, draft, expected_version=expected_version)

        return {
            "ok": True,
            "status": status,
            "total_photos": len(analysis_results),
            "safety_count": len(safety_candidates),
            "equipment_count": len([a for a in analysis_results if a.classification == "equipment"]),
            "selected_safety": selected_safety.photo_id if selected_safety else None,
            "selected_service_count": len(selected_service),
            "verification_required": any(a.verification_required for a in analysis_results),
            "metadata": metadata,
        }

    def change_safety_photo(
        self,
        draft_id: int,
        photo_id: str,
        photo_classification_service,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """User manually changes safety photo.

        Validates photo_id exists in current draft's photo_candidates.
        """
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"ok": False, "error": "draft_not_found"}
        if draft_row["status"] == "cancelled":
            return {"ok": False, "error": "draft_cancelled"}
        if draft_row["status"] in ("confirmed", "saved"):
            return {"ok": False, "error": "draft_confirmed_reopen_required"}

        draft = self.parse_draft_data(draft_row)
        valid_photo_ids = {p.photo_id for p in draft.photo_candidates}

        selected, error = photo_classification_service.change_safety_photo(
            photo_id=photo_id,
            analysis_results=draft.photo_analysis_results,
            valid_photo_ids=valid_photo_ids,
        )
        if error:
            return {"ok": False, "error": error}

        draft.selected_safety_photo = selected
        draft.selected_safety_photo_source = "user_selected"
        self.save_draft(draft_id, draft, expected_version=expected_version)
        return {"ok": True, "selected_safety": photo_id}

    def add_service_photo(
        self,
        draft_id: int,
        photo_id: str,
        photo_classification_service,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """User manually adds a service photo.

        Validates photo_id exists in current draft's photo_candidates.
        Persists user_selected_service_photo_ids.
        """
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"ok": False, "error": "draft_not_found"}
        if draft_row["status"] == "cancelled":
            return {"ok": False, "error": "draft_cancelled"}
        if draft_row["status"] in ("confirmed", "saved"):
            return {"ok": False, "error": "draft_confirmed_reopen_required"}

        draft = self.parse_draft_data(draft_row)
        valid_photo_ids = {p.photo_id for p in draft.photo_candidates}

        current = list(draft.selected_service_photos)
        updated, error = photo_classification_service.add_service_photo(
            photo_id=photo_id,
            analysis_results=draft.photo_analysis_results,
            current_selected=current,
            valid_photo_ids=valid_photo_ids,
        )
        if error:
            return {"ok": False, "error": error}

        draft.selected_service_photos = updated
        # Persist user selection
        if photo_id not in draft.user_selected_service_photo_ids:
            draft.user_selected_service_photo_ids.append(photo_id)
        # Remove from user_removed if present
        draft.user_removed_service_photo_ids = [
            x for x in draft.user_removed_service_photo_ids if x != photo_id
        ]
        self.save_draft(draft_id, draft, expected_version=expected_version)
        return {"ok": True, "selected_count": len(updated)}

    def remove_service_photo(
        self,
        draft_id: int,
        photo_id: str,
        photo_classification_service,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """User manually removes a service photo.

        Persists user_removed_service_photo_ids so AI re-analysis does NOT re-add.
        """
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return {"ok": False, "error": "draft_not_found"}
        if draft_row["status"] == "cancelled":
            return {"ok": False, "error": "draft_cancelled"}
        if draft_row["status"] in ("confirmed", "saved"):
            return {"ok": False, "error": "draft_confirmed_reopen_required"}

        draft = self.parse_draft_data(draft_row)
        current = list(draft.selected_service_photos)
        updated = photo_classification_service.remove_service_photo(
            photo_id=photo_id,
            current_selected=current,
        )
        draft.selected_service_photos = updated
        # Persist user removal (so AI does not re-add)
        if photo_id not in draft.user_removed_service_photo_ids:
            draft.user_removed_service_photo_ids.append(photo_id)
        # Remove from user_selected if present
        draft.user_selected_service_photo_ids = [
            x for x in draft.user_selected_service_photo_ids if x != photo_id
        ]
        self.save_draft(draft_id, draft, expected_version=expected_version)
        return {"ok": True, "selected_count": len(updated)}

    def reopen_draft(self, draft_id: int) -> None:
        """Explicitly reopen a confirmed draft for editing."""
        self.update_draft_status(draft_id, "draft")

    def delete_draft(self, draft_id: int) -> None:
        """Soft-delete by marking cancelled. Also keeps audit trail."""
        self.update_draft_status(draft_id, "cancelled")

    # ─── Action Audit / Idempotency ───────────────────────────────────────

    def record_action(
        self,
        draft_id: int,
        action_id: str,
        action: AIAction,
        result: str = "ok",
        error: Optional[str] = None,
    ) -> bool:
        """Record an action execution. Returns False if duplicate (idempotency)."""
        existing = self.db.execute(
            "select id from ai_daily_report_actions where draft_id = ? and action_id = ?",
            (draft_id, action_id),
        ).fetchone()
        if existing:
            return False  # duplicate
        self.db.execute(
            """
            insert into ai_daily_report_actions (
                draft_id, action_id, action_version, intent, action_payload,
                ai_generated, executed_at, executed_by, result
            ) values (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                draft_id,
                action_id,
                action.action_version,
                action.intent,
                action.model_dump_json(),
                self.now(),
                self.user_id,
                error or result,
            ),
        )
        return True

    def get_executed_action_ids(self, draft_id: int) -> set:
        rows = self.db.execute(
            "select action_id from ai_daily_report_actions where draft_id = ?",
            (draft_id,),
        ).fetchall()
        return {r["action_id"] for r in rows}

    # ─── Action Execution ─────────────────────────────────────────────────

    def execute_action(
        self, draft: DailyReportDraft, action: AIAction,
        resolved_workers: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[DailyReportDraft, str]:
        """Execute an action on the draft. Returns (updated_draft, message).

        Args:
            resolved_workers: pre-resolved workers with user_id (Phase 2).
                Passed through to update_worker for user_id-based matching.

        Phase 1 implemented intents:
        - create_daily_report: handled by caller (creates draft first)
        - update_daily_report: update service_description, dates
        - update_worker: add or update a worker
        - add_work_item: append a work item
        - update_work_item: update a work item by equipment
        - remove_work_item: remove a work item by equipment
        - add_waiting_time: set waiting_hours and waiting_reason
        - update_arrival_time: set arrival_time
        - update_departure_time: set departure_time
        - clarify: no-op, just return clarification question
        """
        intent = action.intent

        if intent in ("create_daily_report", "update_daily_report"):
            # create_daily_report: the caller creates an empty draft first, then
            # this branch applies everything the model returned. Previously this
            # intent fell through to the "not implemented" tail, silently
            # dropping workers/work_items (draft stayed empty).
            if action.work_items is not None:
                draft.work_items = [
                    WorkItem(
                        equipment=wi.equipment,
                        action=wi.action,
                        fuse_number=wi.fuse_number,
                        description=wi.description or "",
                    )
                    for wi in action.work_items
                ]
            if action.workers:
                draft, _ = self._apply_update_worker(draft, action, resolved_workers=resolved_workers)
            if action.overnight_stay is not None:
                for w in draft.workers:
                    w.overnight_stay = action.overnight_stay
            if action.arrival_time:
                draft.arrival_time = action.arrival_time
                draft.arrival_time_source = "manual"
            if action.departure_time:
                draft.departure_time = action.departure_time
                draft.departure_time_source = "manual"
            if action.waiting_hours is not None:
                draft.waiting_hours = action.waiting_hours
            if action.waiting_reason:
                draft.waiting_reason = action.waiting_reason
            return draft, "日报已创建" if intent == "create_daily_report" else "日报已更新"

        if intent == "update_worker":
            return self._apply_update_worker(draft, action, resolved_workers=resolved_workers)

        if intent == "add_work_item":
            return self._apply_add_work_item(draft, action)

        if intent == "update_work_item":
            return self._apply_update_work_item(draft, action)

        if intent == "remove_work_item":
            return self._apply_remove_work_item(draft, action)

        if intent == "add_waiting_time":
            if action.waiting_hours is not None:
                draft.waiting_hours = action.waiting_hours
            if action.waiting_reason:
                draft.waiting_reason = action.waiting_reason
            return draft, f"已添加等待时间 {action.waiting_hours} 小时"

        if intent == "update_arrival_time":
            if action.arrival_time:
                draft.arrival_time = action.arrival_time
                draft.arrival_time_source = "manual"
                return draft, f"到达时间已更新为 {action.arrival_time}"
            return draft, "到达时间未变更"

        if intent == "update_departure_time":
            if action.departure_time:
                draft.departure_time = action.departure_time
                draft.departure_time_source = "manual"
                return draft, f"离场时间已更新为 {action.departure_time}"
            return draft, "离场时间未变更"

        if intent == "clarify":
            msg = action.clarification_question or "需要更多信息"
            return draft, msg

        if intent == "recalculate_mileage":
            # Invalidate all existing routes so MileageService will re-fetch
            for w in draft.workers:
                TravelService.invalidate_worker_route(w)
            # Actual Google Routes call happens in app.py via MileageService
            return draft, "正在重新计算里程..."

        # Photo/mileage intents are Phase 2+
        return draft, f"操作 {intent} 将在后续阶段实现"

    def _apply_update_worker(
        self, draft: DailyReportDraft, action: AIAction,
        resolved_workers: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[DailyReportDraft, str]:
        """Add or update workers. Phase 2: user_id-based matching (primary).

        Args:
            resolved_workers: pre-resolved workers with user_id (from EmployeeResolutionService).
                If provided, uses user_id for matching. If None, falls back to name matching.
        """
        messages = []

        if resolved_workers:
            # Phase 2: user_id-based matching
            for rw in resolved_workers:
                user_id = rw.get("user_id")
                if not user_id:
                    continue
                existing = None
                for w in draft.workers:
                    if w.user_id == user_id:
                        existing = w
                        break

                if existing:
                    if rw.get("origin"):
                        existing.origin = rw["origin"]
                        existing.origin_source = "user_input"
                        existing.origin_confirmed = True
                        TravelService.invalidate_worker_route(existing)
                    if rw.get("transportation") and rw["transportation"] != "self_drive":
                        existing.transportation = rw["transportation"]
                        TravelService.invalidate_worker_route(existing)
                    if rw.get("overnight_stay") is not None:
                        existing.overnight_stay = rw["overnight_stay"]
                        TravelService.invalidate_worker_route(existing)
                    messages.append(f"已更新 {rw.get('name', user_id)} 的信息")
                else:
                    draft.workers.append(WorkerTravel(
                        user_id=user_id,
                        name=rw.get("name", ""),
                        transportation=rw.get("transportation", "self_drive"),
                        origin=rw.get("origin"),
                        origin_source="user_input" if rw.get("origin") else None,
                        origin_confirmed=bool(rw.get("origin")),
                        overnight_stay=rw.get("overnight_stay"),
                    ))
                    messages.append(f"已添加工作人员 {rw.get('name', user_id)}")
        elif action.workers:
            # Phase 1 fallback: name-based matching (deprecated, will be removed)
            for wi in action.workers:
                existing = None
                for w in draft.workers:
                    if w.name == wi.name:
                        existing = w
                        break
                if existing:
                    if wi.origin is not None:
                        existing.origin = wi.origin
                        existing.origin_source = "user_input"
                        existing.origin_confirmed = True
                        TravelService.invalidate_worker_route(existing)
                    if wi.transportation != "self_drive":
                        existing.transportation = wi.transportation
                        TravelService.invalidate_worker_route(existing)
                    messages.append(f"已更新 {wi.name} 的信息")
                else:
                    draft.workers.append(WorkerTravel(
                        user_id=0,  # unresolved - should not happen in Phase 2
                        name=wi.name,
                        transportation=wi.transportation,
                        origin=wi.origin,
                    ))
                    messages.append(f"已添加工作人员 {wi.name}")

        # Apply overnight_stay global shortcut
        if action.overnight_stay is not None:
            for w in draft.workers:
                w.overnight_stay = action.overnight_stay
                TravelService.invalidate_worker_route(w)
            messages.append(
                f"所有人员已设置为{'住宿' if action.overnight_stay else '不住宿'}"
            )

        return draft, "；".join(messages) if messages else "未变更"

    def _apply_add_work_item(
        self, draft: DailyReportDraft, action: AIAction
    ) -> Tuple[DailyReportDraft, str]:
        if not action.work_items:
            return draft, "未指定施工项"
        for wi in action.work_items:
            draft.work_items.append(
                WorkItem(
                    equipment=wi.equipment,
                    action=wi.action,
                    fuse_number=wi.fuse_number,
                    description=wi.description or "",
                )
            )
        return draft, f"已添加 {len(action.work_items)} 项施工内容"

    def _apply_update_work_item(
        self, draft: DailyReportDraft, action: AIAction
    ) -> Tuple[DailyReportDraft, str]:
        if not action.work_items:
            return draft, "未指定施工项"
        updated = 0
        for wi in action.work_items:
            for existing in draft.work_items:
                if wi.equipment and existing.equipment == wi.equipment:
                    if wi.action:
                        existing.action = wi.action
                    if wi.fuse_number is not None:
                        existing.fuse_number = wi.fuse_number
                    if wi.description:
                        existing.description = wi.description
                    updated += 1
                    break
        return draft, f"已更新 {updated} 项施工内容" if updated else "未找到匹配的施工项"

    def _apply_remove_work_item(
        self, draft: DailyReportDraft, action: AIAction
    ) -> Tuple[DailyReportDraft, str]:
        if not action.work_items:
            return draft, "未指定要删除的施工项"
        removed = 0
        for wi in action.work_items:
            before = len(draft.work_items)
            draft.work_items = [
                w for w in draft.work_items
                if not (wi.equipment and w.equipment == wi.equipment)
            ]
            removed += before - len(draft.work_items)
        return draft, f"已删除 {removed} 项施工内容"

    # ─── Preview ──────────────────────────────────────────────────────────

    def build_preview(self, draft: DailyReportDraft) -> Dict[str, Any]:
        """Build a preview dict for the frontend."""
        return {
            "service_order_id": draft.service_order_id,
            "report_date": draft.report_date,
            "site_address": draft.site_address,
            "workers": [w.model_dump() for w in draft.workers],
            "work_items": [wi.model_dump() for wi in draft.work_items],
            "arrival_time": draft.arrival_time,
            "departure_time": draft.departure_time,
            "arrival_time_source": draft.arrival_time_source,
            "departure_time_source": draft.departure_time_source,
            "safety_photo": draft.safety_photo.model_dump() if draft.safety_photo else None,
            "safety_photo_verification_required": draft.safety_photo_verification_required,
            "service_photos": [p.model_dump() for p in draft.service_photos],
            "service_description": draft.service_description,
            "waiting_hours": draft.waiting_hours,
            "waiting_reason": draft.waiting_reason,
            "verification_required": draft.verification_required,
            "verification_fields": draft.verification_fields,
        }

    def build_draft_summary(self, draft: DailyReportDraft) -> str:
        """Build a short text summary for DeepSeek context (token-efficient)."""
        parts = [f"日期={draft.report_date}"]
        if draft.workers:
            worker_strs = []
            for w in draft.workers:
                overnight = "住宿" if w.overnight_stay else ("不住宿" if w.overnight_stay is False else "未确认住宿")
                origin = w.origin or "出发地未知"
                worker_strs.append(f"{w.name}({origin},{overnight})")
            parts.append(f"人员={','.join(worker_strs)}")
        if draft.work_items:
            item_strs = []
            for wi in draft.work_items:
                eq = wi.equipment or "未知设备"
                act = wi.action or ""
                item_strs.append(f"{eq}:{act}")
            parts.append(f"施工={','.join(item_strs)}")
        if draft.arrival_time:
            parts.append(f"到达={draft.arrival_time}")
        if draft.departure_time:
            parts.append(f"离场={draft.departure_time}")
        summary = "; ".join(parts)
        # Truncate summary to max length
        if len(summary) > MAX_SUMMARY_LENGTH:
            summary = summary[:MAX_SUMMARY_LENGTH] + "..."
        return summary

    # ─── Conversation Context (Phase 2) ──────────────────────────────────

    @staticmethod
    def truncate_message(message: str) -> str:
        """Truncate user message to max length. Returns truncated version."""
        if len(message) > MAX_MESSAGE_LENGTH:
            return message[:MAX_MESSAGE_LENGTH] + "...(truncated)"
        return message

    def load_conversation(self, draft_row: Dict[str, Any]) -> List[Dict[str, str]]:
        """Load conversation context from draft row. Safe JSON parsing."""
        raw = draft_row.get("conversation_context") or "[]"
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data[-MAX_CONVERSATION_ROUNDS:]
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("Corrupted conversation_context for draft %s", draft_row.get("id"))
        return []

    def add_conversation_message(
        self, draft_id: int, role: str, content: str
    ) -> List[Dict[str, str]]:
        """Add a message to conversation context, enforcing max rounds.

        Returns the updated conversation list. Does NOT save to DB; caller saves.
        """
        draft_row = self.get_draft(draft_id)
        if not draft_row:
            return []
        conv = self.load_conversation(draft_row)
        conv.append({"role": role, "content": content[:MAX_MESSAGE_LENGTH]})
        # Keep only last N rounds
        if len(conv) > MAX_CONVERSATION_ROUNDS:
            conv = conv[-MAX_CONVERSATION_ROUNDS:]
        # Save back
        self.db.execute(
            "update ai_daily_report_drafts set conversation_context = ? where id = ?",
            (json.dumps(conv, ensure_ascii=False), draft_id),
        )
        return conv

    def get_context_for_deepseek(
        self, draft: DailyReportDraft, draft_row: Dict[str, Any]
    ) -> str:
        """Get context to send to DeepSeek.

        IMPORTANT: Does NOT send full conversation history.
        Sends only: current draft summary + last user message (if any).
        This keeps token usage low and avoids sending sensitive old data.
        """
        summary = self.build_draft_summary(draft)
        conv = self.load_conversation(draft_row)
        # Only include the most recent user message, not full history
        last_user = ""
        for msg in reversed(conv):
            if msg.get("role") == "user":
                last_user = msg.get("content", "")
                break

        context = f"当前Draft摘要: {summary}"
        if last_user:
            context += f"\n最近用户输入: {last_user[:500]}"
        return context
