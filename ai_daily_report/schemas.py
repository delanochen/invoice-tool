"""AI Daily Report - Pydantic Schemas (Phase 1)"""
from __future__ import annotations

import re
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator

ACTION_VERSION = 1

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")


# ─── Action Schema (DeepSeek output) ───────────────────────────────────────

class WorkerInput(BaseModel):
    name: str
    transportation: Literal[
        "self_drive", "carpool", "passenger", "flight", "rental_car", "other"
    ] = "self_drive"
    origin: Optional[str] = None  # None = unknown, must ask; never guess


class WorkItemInput(BaseModel):
    equipment: Optional[str] = None
    action: Optional[str] = None  # replace_fuse / repair / inspect / install / other
    fuse_number: Optional[int] = None
    description: Optional[str] = None


class AIAction(BaseModel):
    """Strict schema for DeepSeek JSON output. All unknown intents rejected."""
    action_version: int = Field(..., ge=1, le=ACTION_VERSION)
    intent: Literal[
        "create_daily_report",
        "update_daily_report",
        "update_worker",
        "add_work_item",
        "update_work_item",
        "remove_work_item",
        "add_waiting_time",
        "update_arrival_time",
        "update_departure_time",
        "change_safety_photo",
        "add_service_photo",
        "remove_service_photo",
        "recalculate_mileage",
        "submit_daily_report",
        "clarify",
    ]
    # Date: only set when user explicitly mentions a date; backend injects default
    date: Optional[str] = None
    workers: Optional[List[WorkerInput]] = None
    work_items: Optional[List[WorkItemInput]] = None
    overnight_stay: Optional[bool] = None  # global shortcut; per-person in worker_overrides
    worker_overrides: Optional[List[Dict[str, Any]]] = None
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None
    waiting_hours: Optional[float] = None
    waiting_reason: Optional[str] = None
    # Photo operations use stable hash, NOT index
    photo_hash: Optional[str] = None
    clarification_required: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    clarification_question: Optional[str] = None

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not DATE_PATTERN.match(v):
            raise ValueError(f"date must be YYYY-MM-DD, got {v!r}")
        return v

    @field_validator("arrival_time", "departure_time")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not TIME_PATTERN.match(v):
            raise ValueError(f"time must be HH:MM, got {v!r}")
        return v

    @field_validator("waiting_hours")
    @classmethod
    def validate_waiting(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("waiting_hours cannot be negative")
        return v


# ─── Draft Schema (stored in ai_daily_report_drafts.draft_data) ────────────

class WorkerTravel(BaseModel):
    """Per-worker travel data. overnight_stay is per-person.

    Phase 2: user_id is the primary identity; name is display only.
    origin_source tracks where origin came from; employee_default is NOT confirmed.
    Phase 3A: route fields from Google Routes API.
    """
    user_id: int
    name: str
    transportation: Literal[
        "self_drive", "carpool", "passenger", "flight", "rental_car", "other"
    ] = "self_drive"
    origin: Optional[str] = None
    origin_source: Optional[str] = None  # user_input / employee_default / draft_existing
    origin_confirmed: bool = False
    origin_normalized: Optional[str] = None  # Google-formatted address (do NOT overwrite origin)
    destination: Optional[str] = None
    destination_source: Optional[str] = None  # service_order
    destination_normalized: Optional[str] = None
    overnight_stay: Optional[bool] = None  # None = unconfirmed
    # Route results (Phase 3A)
    route_distance_meters: Optional[float] = None  # raw Google distance, always meters
    one_way_miles: Optional[float] = None  # meters / 1609.344
    reported_miles: Optional[float] = None  # one_way * 2 (no overnight) or one_way (overnight)
    route_duration_seconds: Optional[int] = None
    route_polyline: Optional[str] = None
    route_provider: Optional[str] = None  # "google_routes"
    route_query_time: Optional[str] = None  # UTC ISO8601 with Z suffix
    route_status: Optional[Literal["not_calculated", "success", "verification_required", "failed"]] = None
    route_error: Optional[str] = None
    mileage_evidence_id: Optional[str] = None  # evidence_id from Draft evidence_records (Phase 3B)
    mileage_evidence_path: Optional[str] = None
    mileage_verification_required: bool = False


class WorkItem(BaseModel):
    equipment: Optional[str] = None
    action: Optional[str] = None
    fuse_number: Optional[int] = None
    description: str = ""


class PhotoRef(BaseModel):
    """Stable reference to a photo. Uses full hash + relative path, not absolute path."""
    photo_id: str = ""  # stable backend identity = full SHA256 (UI may truncate for display)
    photo_hash: str = ""  # full SHA256 of file content
    relative_path: str = ""  # relative to shared-photos root, e.g. SO-123/pictures/2026-09-14/xxx.jpg
    classification: str = "unknown"
    confidence: float = 0.0
    capture_time: Optional[str] = None  # ISO8601 local time, e.g. 2026-09-14T08:13:42
    capture_time_source: Optional[str] = None  # exif_original / exif_digitized / exif_datetime / filename / file_mtime / unknown
    capture_timezone: Optional[str] = None  # e.g. America/Chicago
    capture_timezone_source: Optional[str] = None  # site_timezone / business_timezone / exif_offset
    file_modified_time: Optional[str] = None
    mime: Optional[str] = None
    source: str = "server_original"
    time_verification_required: bool = False
    # Timeline eligibility (Phase 4 Final)
    timeline_eligible: bool = False  # only eligible photos can be auto arrival/departure
    timeline_exclusion_reason: Optional[str] = None  # date_mismatch / mtime_only / invalid_timestamp / future_timestamp / missing_timestamp / metadata_unsupported
    metadata_unsupported: bool = False  # format cannot be parsed (e.g. HEIC without plugin)
    # 拍照功能 (photo management): user-set photo time and manual classification
    user_modified_time: Optional[str] = None  # user-set photo time, ISO8601; takes priority over capture_time
    manual_classification: Optional[str] = None  # arrival / departure / safety (manual tag for non-equipment photos)


class DailyReportDraft(BaseModel):
    service_order_id: int
    report_date: str
    site_address: Optional[str] = None
    workers: List[WorkerTravel] = Field(default_factory=list)
    work_items: List[WorkItem] = Field(default_factory=list)
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None
    arrival_time_source: Optional[str] = None  # photo / manual / None
    departure_time_source: Optional[str] = None
    arrival_photo: Optional[PhotoRef] = None
    departure_photo: Optional[PhotoRef] = None
    safety_photo: Optional[PhotoRef] = None
    safety_photo_verification_required: bool = False
    service_photos: List[PhotoRef] = Field(default_factory=list)
    service_description: str = ""
    waiting_hours: float = 0.0
    waiting_reason: str = ""
    verification_required: bool = False
    verification_fields: List[str] = Field(default_factory=list)
    ai_metadata: Dict[str, Any] = Field(default_factory=dict)
    # Phase 3B: persisted evidence metadata (restart-safe idempotency)
    evidence_records: List[Dict[str, Any]] = Field(default_factory=list)
    # Phase 4: photo candidates (discovered from server, NOT yet applied to arrival/departure)
    photo_candidates: List[PhotoRef] = Field(default_factory=list)
    arrival_candidate: Optional[PhotoRef] = None
    departure_candidate: Optional[PhotoRef] = None
    arrival_candidate_source: Optional[str] = None  # photo_timeline
    departure_candidate_source: Optional[str] = None  # photo_timeline
    photo_discovery_status: str = "not_discovered"  # not_discovered / discovered / no_photos / failed
    photo_discovery_error: Optional[str] = None
    # Phase 4 Final: photo set fingerprint and timeline status
    photo_set_fingerprint: Optional[str] = None  # SHA256(sorted(photo sha256 list))
    photo_timeline_generated_at: Optional[str] = None  # UTC ISO8601
    photo_timeline_status: str = "not_scanned"  # not_scanned / ready / no_photos / insufficient_photos / suspicious / verification_required / failed
    # Confirmed photo refs (after user confirms timeline)
    arrival_photo_ref: Optional[str] = None  # stable photo_id of confirmed arrival photo
    departure_photo_ref: Optional[str] = None  # stable photo_id of confirmed departure photo
    # Phase 5: Vision classification results
    photo_analysis_results: List[PhotoAnalysis] = Field(default_factory=list)
    safety_photo_candidates: List[PhotoAnalysis] = Field(default_factory=list)
    selected_safety_photo: Optional[PhotoAnalysis] = None
    selected_safety_photo_source: Optional[str] = None  # ai_selected / user_selected
    service_photo_candidates: List[PhotoAnalysis] = Field(default_factory=list)
    selected_service_photos: List[PhotoAnalysis] = Field(default_factory=list)
    # User override persistence (cross-restart)
    user_selected_service_photo_ids: List[str] = Field(default_factory=list)
    user_removed_service_photo_ids: List[str] = Field(default_factory=list)
    photo_classification_status: str = "not_classified"  # not_classified / classified / disabled / failed
    photo_classification_generated_at: Optional[str] = None
    photo_classification_version: int = 1
    # Phase 6: Confirm/Cancel/Reopen audit fields
    confirmed_by: Optional[int] = None
    confirmed_at: Optional[str] = None
    verification_override: bool = False
    override_fields: List[str] = Field(default_factory=list)
    reopened_by: Optional[int] = None
    reopened_at: Optional[str] = None
    cancelled_by: Optional[int] = None
    cancelled_at: Optional[str] = None
    # Phase 7: Warning acknowledgement records (persisted in draft_data)
    warning_acknowledgements: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("report_date")
    @classmethod
    def validate_report_date(cls, v: str) -> str:
        if not DATE_PATTERN.match(v):
            raise ValueError(f"report_date must be YYYY-MM-DD, got {v!r}")
        return v


# ─── Mileage Evidence Schema (Phase 3B) ────────────────────────────────────

EVIDENCE_VERSION = 1

EVIDENCE_STATUSES = Literal[
    "not_generated", "generating", "ready", "stale", "failed", "verification_required"
]


class MileageEvidenceRecord(BaseModel):
    """Metadata for a generated Mileage Evidence (Draft stage only).

    This is NOT a formal service_report_attachment. It lives in the AI Draft
    temporary area until Phase 9 Confirm & Save copies it to the formal system.

    Google API Key is NEVER stored here.
    """
    evidence_id: str  # UUID hex
    draft_id: int
    service_order_id: int
    report_date: str
    worker_user_id: int
    worker_name: str = ""
    # Route data (snapshot from Phase 3A, never re-queried)
    route_provider: str = "google_routes"
    route_query_time: Optional[str] = None
    route_distance_meters: Optional[float] = None
    one_way_miles: Optional[float] = None
    reported_miles: Optional[float] = None
    overnight_stay: Optional[bool] = None
    route_fingerprint: str = ""  # SHA256 of route identity fields
    # Evidence file
    evidence_version: int = EVIDENCE_VERSION
    evidence_status: EVIDENCE_STATUSES = "not_generated"
    generated_at: Optional[str] = None  # UTC ISO8601 Z
    generated_by: Optional[int] = None  # user_id
    file_relative_path: Optional[str] = None  # relative to draft evidence dir
    file_sha256: Optional[str] = None
    error: Optional[str] = None


# ─── Photo Analysis Schema (for Phase 4+, defined here for stability) ──────

# Photo classification sub-categories (Phase 5)
PHOTO_SUB_CATEGORIES = Literal[
    "front_standing_worker",  # safety
    "equipment_overview", "nameplate", "fault_location",
    "before_repair", "during_disassembly", "replacement", "fuse_wiring",
    "after_repair", "startup", "final_state",
    "other", "unknown",
]


class PhotoAnalysis(BaseModel):
    """Vision analysis result for a single photo (Phase 5)."""
    photo_id: str = ""  # stable identity = full SHA256
    photo_path: str = ""  # relative path (for cache key, not sent to Vision)
    photo_hash: str = ""
    analysis_model: str = ""  # from config, e.g. DEEPSEEK_VISION_MODEL
    analysis_version: int = 1
    classification: Literal["safety_person", "equipment", "other", "unknown"] = "unknown"
    sub_category: Optional[PHOTO_SUB_CATEGORIES] = None
    confidence: float = 0.0
    description: Optional[str] = None
    equipment_id: Optional[str] = None
    equipment_id_confidence: float = 0.0
    capture_time: Optional[str] = None
    capture_time_source: Optional[str] = None
    is_original_field_photo: bool = True
    perceptual_hash: Optional[str] = None
    # Verification
    verification_required: bool = False
    verification_reason: Optional[str] = None
    # Status
    analysis_status: str = "success"  # success / failed / disabled / error
    analyzed_at: Optional[str] = None
    # Selection source (ai_selected / user_selected)
    selected_source: Optional[str] = None


# ─── Mileage Evidence Schema (for Phase 3+) ────────────────────────────────

class MileageEvidence(BaseModel):
    employee_id: int
    employee_name: str
    report_date: str
    origin_address: str
    destination_address: str
    one_way_miles: float
    reported_miles: float
    overnight_stay: bool
    route_provider: str = "google_routes"
    route_query_time: str
    route_reference: Optional[str] = None
    route_polyline: Optional[str] = None
    evidence_image_path: Optional[str] = None
    evidence_image_hash: Optional[str] = None
