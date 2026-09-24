"""AI Daily Report - Validation Engine (Phase 7)

Pure, deterministic validation of a DailyReportDraft against 20 rule categories.

ARCHITECTURE:
- ValidationContextBuilder: queries DB for authoritative data (service_order, workers).
  This is the ONLY component allowed to touch the database.
- ValidationEngine: pure function. Takes (draft_dict, context, acknowledgements)
  and returns ValidationResult. NO DB, NO filesystem, NO network.
- ValidationIssue: single finding with stable issue_key and issue_fingerprint.
- ValidationResult: aggregate with is_valid / can_proceed / fingerprints.

BOUNDARY LOCK (Phase 7):
- Does NOT write service_reports / service_report_workers / service_report_attachments.
- Does NOT call DeepSeek / Vision / Google Routes / Google Static Maps.
- Does NOT scan photo directories or re-read evidence files.
- Does NOT compute live file SHA256.
- Does NOT change Phase 6 Confirm semantics.

SEVERITY SEMANTICS:
- ERROR: must fix real data. blocking=True. Cannot be acknowledged/overridden.
- WARNING: can be acknowledged. blocking=False.
- INFO: display only. blocking=False.
- is_valid = (error_count == 0)
- can_proceed = (unresolved blocking ERROR count == 0)  → always equals is_valid in v1
"""
from __future__ import annotations

import hashlib
import json
from .schemas import collect_safety_photos
from trip_policy import normalize_trip_type, trip_label, trip_multiplier
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENGINE_VERSION = "7.0.0"

# Severities
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# Subject types
SUBJECT_DRAFT = "draft"
SUBJECT_WORKER = "worker"
SUBJECT_WORK_ITEM = "work_item"
SUBJECT_PHOTO = "photo"
SUBJECT_EVIDENCE = "evidence"
SUBJECT_SERVICE_ORDER = "service_order"

# Verification field → canonical rule mapping (AIVR central mapping)
# Known fields are handled by specific business rules; unknown fields get a safe WARNING.
VERIFICATION_FIELD_RULE_MAP: Dict[str, str] = {
    "draft_data_corrupted": "DRFT-001",
    "no_photos": "PTML-004",
    "origin": "ORIG-002",
    "origin_unconfirmed": "ORIG-002",
    "mileage": "MILE-006",
    "mileage_verification_required": "MILE-006",
    "safety_photo": "SAFE-002",
    "timeline": "PTML-003",
    "photo_timeline": "PTML-003",
    "arrival_time": "TIME-006",
    "departure_time": "TIME-006",
    "service_photo": "SVCF-003",
    "equipment_id": "VISN-004",
}

# Mileage threshold for MILE-003 (reported miles above this triggers WARNING)
MILEAGE_WARNING_THRESHOLD = 500.0

# Max service photos (matches Phase 5)
MAX_SERVICE_PHOTOS = 10


# ─── Fingerprint Utilities ──────────────────────────────────────────────────

def _canonical_json(obj: Any) -> str:
    """Stable JSON serialization: sort_keys, no whitespace, no generated_at."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_draft_data_hash(draft_data: Dict[str, Any]) -> str:
    """Hash of draft content excluding volatile/audit fields.

    Excludes: confirmed_at, cancelled_at, reopened_at, ai_metadata timestamps,
    photo_timeline_generated_at, photo_classification_generated_at,
    warning_acknowledgements (handled separately), and any *_at fields.
    """
    excluded_keys = {
        "confirmed_by", "confirmed_at", "cancelled_by", "cancelled_at",
        "reopened_by", "reopened_at", "photo_timeline_generated_at",
        "photo_classification_generated_at", "warning_acknowledgements",
    }
    filtered = {k: v for k, v in draft_data.items() if k not in excluded_keys}
    return _sha256_hex(_canonical_json(filtered))


def compute_context_hash(context: Dict[str, Any]) -> str:
    """Hash of authoritative context data."""
    return _sha256_hex(_canonical_json(context))


def compute_issue_fingerprint(
    rule_id: str,
    subject_type: str,
    subject_id: str,
    relevant_values: Dict[str, Any],
) -> str:
    """Stable fingerprint for an issue's relevant data.

    When relevant_values change, the fingerprint changes and any old
    acknowledgement for this issue_key becomes stale.
    """
    payload = {
        "rule_id": rule_id,
        "subject_type": subject_type,
        "subject_id": str(subject_id),
        "relevant_values": relevant_values,
    }
    return _sha256_hex(_canonical_json(payload))


def make_issue_key(rule_id: str, subject_type: str, subject_id: Any) -> str:
    """Build stable issue_key: rule_id:subject_type:subject_id."""
    return f"{rule_id}:{subject_type}:{subject_id}"


# ─── Models ─────────────────────────────────────────────────────────────────

class ValidationIssue:
    """A single validation finding.

    issue_key uniquely identifies (rule + subject), so the same rule applied
    to different workers produces different issues.
    issue_fingerprint changes when the underlying relevant data changes,
    which auto-invalidates stale acknowledgements.
    """

    def __init__(
        self,
        rule_id: str,
        severity: str,
        message: str,
        subject_type: str = SUBJECT_DRAFT,
        subject_id: Any = "",
        relevant_values: Optional[Dict[str, Any]] = None,
        category: str = "",
        blocking: Optional[bool] = None,
        acknowledgement_required: Optional[bool] = None,
    ):
        self.rule_id = rule_id
        self.severity = severity
        self.message = message
        self.subject_type = subject_type
        self.subject_id = str(subject_id) if subject_id != "" else ""
        self.category = category or rule_id.split("-")[0]
        self.relevant_values = relevant_values or {}

        # blocking: ERROR=True, WARNING/INFO=False
        if blocking is not None:
            self.blocking = blocking
        else:
            self.blocking = (severity == SEVERITY_ERROR)

        # acknowledgement_required: WARNING=True, ERROR/INFO=False
        if acknowledgement_required is not None:
            self.acknowledgement_required = acknowledgement_required
        else:
            self.acknowledgement_required = (severity == SEVERITY_WARNING)

        self.issue_key = make_issue_key(rule_id, subject_type, self.subject_id or "global")
        self.issue_fingerprint = compute_issue_fingerprint(
            rule_id, subject_type, self.subject_id or "global", self.relevant_values
        )
        # Acknowledgement state (filled by engine)
        self.acknowledged = False
        self.acknowledged_by: Optional[int] = None
        self.acknowledged_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "issue_key": self.issue_key,
            "issue_fingerprint": self.issue_fingerprint,
            "blocking": self.blocking,
            "acknowledgement_required": self.acknowledgement_required,
            "acknowledged": self.acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
        }


class ValidationResult:
    """Aggregate validation result for a draft."""

    def __init__(
        self,
        issues: List[ValidationIssue],
        draft_data_hash: str = "",
        context_hash: str = "",
        draft_version: int = 0,
    ):
        # Deduplicate by issue_key (first occurrence wins — specific rules
        # are registered before AIVR fallback, so canonical issue is kept)
        seen: set = set()
        self.issues: List[ValidationIssue] = []
        for issue in issues:
            if issue.issue_key in seen:
                continue
            seen.add(issue.issue_key)
            self.issues.append(issue)

        self.errors = [i for i in self.issues if i.severity == SEVERITY_ERROR]
        self.warnings = [i for i in self.issues if i.severity == SEVERITY_WARNING]
        self.infos = [i for i in self.issues if i.severity == SEVERITY_INFO]

        self.error_count = len(self.errors)
        self.warning_count = len(self.warnings)
        self.info_count = len(self.infos)

        # v1 semantics: all ERRORs are blocking, WARNING/INFO are not
        self.is_valid = (self.error_count == 0)
        unresolved_blocking = [i for i in self.errors if i.blocking and not i.acknowledged]
        self.can_proceed = (len(unresolved_blocking) == 0)
        # Acknowledgement can never remove an ERROR, so can_proceed == is_valid in v1
        # (kept as separate field for Phase 8 forward-compatibility)

        self.draft_data_hash = draft_data_hash
        self.context_hash = context_hash
        self.draft_version = draft_version
        self.validation_fingerprint = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        """validation_fingerprint = SHA256(engine_version, draft_version,
        draft_data_hash, context_hash, sorted issue keys+fingerprints).

        Does NOT include generated_at. Does NOT include acknowledgement UI state
        (acknowledged flags are derived from ack records, not part of issue identity).
        """
        issue_identity = sorted(
            [(i.issue_key, i.issue_fingerprint) for i in self.issues]
        )
        payload = {
            "engine_version": ENGINE_VERSION,
            "draft_version": self.draft_version,
            "draft_data_hash": self.draft_data_hash,
            "context_hash": self.context_hash,
            "issues": issue_identity,
        }
        return _sha256_hex(_canonical_json(payload))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine_version": ENGINE_VERSION,
            "is_valid": self.is_valid,
            "can_proceed": self.can_proceed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "total_count": len(self.issues),
            "draft_version": self.draft_version,
            "draft_data_hash": self.draft_data_hash,
            "context_hash": self.context_hash,
            "validation_fingerprint": self.validation_fingerprint,
            "issues": [i.to_dict() for i in self.issues],
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
            "infos": [i.to_dict() for i in self.infos],
        }


# ─── Validation Context ─────────────────────────────────────────────────────

class ValidationContext:
    """Authoritative data from DB, built by ValidationContextBuilder.

    The engine itself never queries the DB — it only reads this context.
    Normal business states (e.g. service_order deleted) are expressed as
    context fields, not as exceptions.
    """

    def __init__(
        self,
        service_order_exists: bool = False,
        service_order: Optional[Dict[str, Any]] = None,
        workers: Optional[Dict[int, Dict[str, Any]]] = None,
        site_address: str = "",
        site_name: str = "",
        order_number: str = "",
    ):
        self.service_order_exists = service_order_exists
        self.service_order = service_order or {}
        self.workers = workers or {}
        self.site_address = site_address
        self.site_name = site_name
        self.order_number = order_number

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_order_exists": self.service_order_exists,
            "service_order": self.service_order,
            "workers": self.workers,
            "site_address": self.site_address,
            "site_name": self.site_name,
            "order_number": self.order_number,
        }

    def worker_exists(self, user_id: int) -> bool:
        return user_id in self.workers

    def worker_is_active(self, user_id: int) -> bool:
        w = self.workers.get(user_id)
        if not w:
            return False
        return bool(w.get("is_active", True))


class ValidationContextBuilder:
    """Builds ValidationContext by querying the database.

    This is the ONLY Phase 7 component allowed to query the DB.
    The engine receives the resulting context and is pure.
    """

    def __init__(self, db_connection):
        self.db = db_connection

    def build(self, draft_data: Dict[str, Any]) -> ValidationContext:
        """Build authoritative context for a draft.

        Never raises on normal business states (missing order, inactive worker).
        Only raises on true system errors (DB unavailable).
        """
        service_order_id = draft_data.get("service_order_id")
        ctx = ValidationContext()

        # Service order
        if service_order_id:
            try:
                row = self.db.execute(
                    "select id, order_number, client_name, site_address, status "
                    "from service_orders where id = ?",
                    (service_order_id,),
                ).fetchone()
                if row:
                    so = dict(row)
                    ctx.service_order_exists = True
                    ctx.service_order = so
                    ctx.site_address = so.get("site_address", "") or ""
                    ctx.order_number = so.get("order_number", "") or ""
            except Exception as exc:
                logger.error("ValidationContextBuilder: service_order query failed: %s", exc)
                raise

        # Workers (collect all user_ids from draft)
        worker_ids = set()
        for w in draft_data.get("workers", []):
            uid = w.get("user_id")
            if uid:
                worker_ids.add(uid)

        if worker_ids:
            try:
                placeholders = ",".join("?" * len(worker_ids))
                rows = self.db.execute(
                    f"select id, name, role, is_active from users where id in ({placeholders})",
                    tuple(worker_ids),
                ).fetchall()
                for row in rows:
                    r = dict(row)
                    ctx.workers[r["id"]] = {
                        "name": r.get("name", ""),
                        "role": r.get("role", ""),
                        "is_active": bool(r.get("is_active", 1)),
                    }
            except Exception as exc:
                logger.error("ValidationContextBuilder: workers query failed: %s", exc)
                raise

        return ctx


# ─── Acknowledgement Helpers ────────────────────────────────────────────────

def get_acknowledgements(draft_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read warning_acknowledgements from draft_data."""
    acks = draft_data.get("warning_acknowledgements", [])
    if not isinstance(acks, list):
        return []
    return acks


def set_acknowledgements(draft_data: Dict[str, Any], acks: List[Dict[str, Any]]) -> None:
    """Write warning_acknowledgements to draft_data (mutates in place)."""
    draft_data["warning_acknowledgements"] = acks


def add_acknowledgement(
    draft_data: Dict[str, Any],
    issue_key: str,
    rule_id: str,
    issue_fingerprint: str,
    validation_fingerprint: str,
    draft_version: int,
    user_id: int,
    now_iso: str,
) -> List[Dict[str, Any]]:
    """Add or update an acknowledgement record. Returns the updated ack list."""
    acks = get_acknowledgements(draft_data)
    # Remove any existing ack for same issue_key (replace with fresh one)
    acks = [a for a in acks if a.get("issue_key") != issue_key]
    acks.append({
        "issue_key": issue_key,
        "rule_id": rule_id,
        "acknowledged_by": user_id,
        "acknowledged_at": now_iso,
        "draft_version_at_ack": draft_version,
        "validation_fingerprint_at_ack": validation_fingerprint,
        "issue_fingerprint_at_ack": issue_fingerprint,
    })
    set_acknowledgements(draft_data, acks)
    return acks


def is_acknowledgement_valid(ack: Dict[str, Any], issue: ValidationIssue) -> bool:
    """Check if an acknowledgement record is still valid for a current issue.

    Validity requires:
    1. issue_key matches
    2. issue_fingerprint matches (relevant data hasn't changed)
    """
    if ack.get("issue_key") != issue.issue_key:
        return False
    if ack.get("issue_fingerprint_at_ack") != issue.issue_fingerprint:
        return False
    return True


# ─── Address Normalization ──────────────────────────────────────────────────

def normalize_address(value: Optional[str]) -> str:
    """Deterministic local address normalization.

    Uses the same logic as app.normalized_address:
    collapse whitespace + casefold. No Google geocoding.
    """
    if not value:
        return ""
    return " ".join(str(value).strip().split()).casefold()


def addresses_equal(a: Optional[str], b: Optional[str]) -> bool:
    """Compare two addresses after deterministic normalization."""
    return normalize_address(a) == normalize_address(b)


# ─── Validation Engine ──────────────────────────────────────────────────────

class ValidationEngine:
    """Pure validation engine. NO DB, NO filesystem, NO network.

    Usage:
        engine = ValidationEngine()
        result = engine.validate(draft_data_dict, context, draft_version)
    """

    def __init__(self):
        pass

    def validate(
        self,
        draft_data: Dict[str, Any],
        context: ValidationContext,
        draft_version: int = 0,
    ) -> ValidationResult:
        """Run all validation rules and return ValidationResult.

        Args:
            draft_data: parsed draft_data dict (from ai_daily_report_drafts.draft_data)
            context: authoritative ValidationContext (built by ValidationContextBuilder)
            draft_version: current draft_version from DB row (for fingerprint)

        Returns:
            ValidationResult with all issues, deduplicated, with acknowledgements applied.
        """
        if not isinstance(draft_data, dict):
            draft_data = {}

        issues: List[ValidationIssue] = []

        # Run all rule categories (order matters for dedup: specific rules first,
        # AIVR fallback last so it doesn't duplicate canonical issues)
        issues.extend(self._check_draft_state(draft_data))
        issues.extend(self._check_service_order(draft_data, context))
        issues.extend(self._check_workers(draft_data, context))
        issues.extend(self._check_transportation(draft_data))
        issues.extend(self._check_origin_destination(draft_data, context))
        issues.extend(self._check_route_status(draft_data))
        issues.extend(self._check_mileage(draft_data))
        issues.extend(self._check_evidence(draft_data))
        issues.extend(self._check_time(draft_data))
        issues.extend(self._check_photo_timeline(draft_data))
        issues.extend(self._check_safety_photo(draft_data))
        issues.extend(self._check_service_photos(draft_data))
        issues.extend(self._check_work_items(draft_data))
        issues.extend(self._check_service_description(draft_data))
        issues.extend(self._check_waiting_time(draft_data))
        issues.extend(self._check_vision(draft_data))
        issues.extend(self._check_provenance(draft_data, draft_version))
        # AIVR central mapping MUST run last (only for fields not covered by specific rules)
        issues.extend(self._check_ai_verification(draft_data, issues))
        # USRO (acknowledgement audit) runs after acks are applied
        # (handled below)

        # Apply acknowledgements
        acks = get_acknowledgements(draft_data)
        ack_issues_keys = {a.get("issue_key") for a in acks}
        for issue in issues:
            if issue.severity != SEVERITY_WARNING:
                continue
            for ack in acks:
                if is_acknowledgement_valid(ack, issue):
                    issue.acknowledged = True
                    issue.acknowledged_by = ack.get("acknowledged_by")
                    issue.acknowledged_at = ack.get("acknowledged_at")
                    break

        # USRO: stale / orphaned acknowledgements (INFO only)
        issues.extend(self._check_user_acknowledgements(draft_data, issues))

        # Compute hashes
        draft_hash = compute_draft_data_hash(draft_data)
        ctx_hash = compute_context_hash(context.to_dict())

        return ValidationResult(
            issues=issues,
            draft_data_hash=draft_hash,
            context_hash=ctx_hash,
            draft_version=draft_version,
        )

    # ─── 1. Draft State (DRFT) ───────────────────────────────────────────

    def _check_draft_state(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        # DRFT-001: draft_data corrupted
        if d.get("verification_required") and "draft_data_corrupted" in d.get("verification_fields", []):
            issues.append(ValidationIssue(
                "DRFT-001", SEVERITY_ERROR,
                "Draft 数据已损坏，需要重新生成",
                subject_type=SUBJECT_DRAFT, subject_id="corrupted",
                relevant_values={"parse_error": d.get("ai_metadata", {}).get("parse_error", "")},
            ))
        # DRFT-002: invalid report_date
        rd = d.get("report_date", "")
        if not rd or len(rd) != 10 or rd[4] != "-" or rd[7] != "-":
            issues.append(ValidationIssue(
                "DRFT-002", SEVERITY_ERROR,
                f"报告日期无效: {rd!r}",
                subject_type=SUBJECT_DRAFT, subject_id="report_date",
                relevant_values={"report_date": rd},
            ))
        # DRFT-003: draft status (INFO — just display, not blocking)
        # Status comes from DB row, not draft_data; handled at API level.
        # DRFT-004: completely empty draft
        workers = d.get("workers", [])
        work_items = d.get("work_items", [])
        desc = (d.get("service_description") or "").strip()
        if not workers and not work_items and not desc:
            issues.append(ValidationIssue(
                "DRFT-004", SEVERITY_ERROR,
                "Draft 为空：无工作人员、无施工内容、无服务描述",
                subject_type=SUBJECT_DRAFT, subject_id="empty",
                relevant_values={"has_workers": bool(workers), "has_work_items": bool(work_items), "has_description": bool(desc)},
            ))
        return issues

    # ─── 2. Service Order / Site (SRVC) ──────────────────────────────────

    def _check_service_order(self, d: Dict[str, Any], ctx: ValidationContext) -> List[ValidationIssue]:
        issues = []
        so_id = d.get("service_order_id")
        # SRVC-001: service order not found in DB
        if not ctx.service_order_exists:
            issues.append(ValidationIssue(
                "SRVC-001", SEVERITY_ERROR,
                f"服务单 ID {so_id} 不存在或已删除",
                subject_type=SUBJECT_SERVICE_ORDER, subject_id=str(so_id),
                relevant_values={"service_order_id": so_id},
            ))
        else:
            # SRVC-002: authoritative site_address missing
            if not ctx.site_address:
                issues.append(ValidationIssue(
                    "SRVC-002", SEVERITY_WARNING,
                    "服务单缺少站点地址",
                    subject_type=SUBJECT_SERVICE_ORDER, subject_id=str(so_id),
                    relevant_values={"site_address": ctx.site_address},
                ))
        return issues

    # ─── 3. Workers / Participants (WRKR) ────────────────────────────────

    def _check_workers(self, d: Dict[str, Any], ctx: ValidationContext) -> List[ValidationIssue]:
        issues = []
        workers = d.get("workers", [])
        # WRKR-001: no workers
        if not workers:
            issues.append(ValidationIssue(
                "WRKR-001", SEVERITY_ERROR,
                "未添加工作人员",
                subject_type=SUBJECT_DRAFT, subject_id="workers",
                relevant_values={"worker_count": 0},
            ))
            return issues  # no workers to check individually

        # WRKR-004: duplicate user_id
        seen_ids: set = set()
        for w in workers:
            uid = w.get("user_id")
            if uid and uid in seen_ids:
                issues.append(ValidationIssue(
                    "WRKR-004", SEVERITY_ERROR,
                    f"工作人员 ID {uid} 重复出现",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"user_id": uid, "name": w.get("name", "")},
                ))
            seen_ids.add(uid)

        for w in workers:
            uid = w.get("user_id")
            name = w.get("name", "")
            sid = str(uid) if uid else name
            # WRKR-002: worker not found in DB
            if uid and not ctx.worker_exists(uid):
                issues.append(ValidationIssue(
                    "WRKR-002", SEVERITY_ERROR,
                    f"工作人员 {name} (ID={uid}) 在系统中不存在",
                    subject_type=SUBJECT_WORKER, subject_id=sid,
                    relevant_values={"user_id": uid, "name": name},
                ))
            # WRKR-003: worker inactive
            elif uid and ctx.worker_exists(uid) and not ctx.worker_is_active(uid):
                issues.append(ValidationIssue(
                    "WRKR-003", SEVERITY_ERROR,
                    f"工作人员 {name} (ID={uid}) 已停用",
                    subject_type=SUBJECT_WORKER, subject_id=sid,
                    relevant_values={"user_id": uid, "name": name, "is_active": False},
                ))
        return issues

    # ─── 4. Transportation (TRSP) ────────────────────────────────────────

    def _check_transportation(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        valid_transport = {"self_drive", "carpool", "passenger", "flight", "rental_car", "other"}
        for w in d.get("workers", []):
            uid = w.get("user_id") or w.get("name", "unknown")
            t = w.get("transportation", "self_drive")
            # TRSP-001: invalid transportation
            if t not in valid_transport:
                issues.append(ValidationIssue(
                    "TRSP-001", SEVERITY_ERROR,
                    f"工作人员 {w.get('name', uid)} 的出行方式无效: {t!r}",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"transportation": t},
                ))
        return issues

    # ─── 5. Origin / Destination (ORIG) ──────────────────────────────────

    def _check_origin_destination(self, d: Dict[str, Any], ctx: ValidationContext) -> List[ValidationIssue]:
        issues = []
        for w in d.get("workers", []):
            uid = w.get("user_id") or w.get("name", "unknown")
            name = w.get("name", str(uid))
            t = w.get("transportation", "self_drive")
            if t != "self_drive":
                continue  # origin/destination rules only apply to self_drive

            # ORIG-001: origin missing
            if not w.get("origin"):
                issues.append(ValidationIssue(
                    "ORIG-001", SEVERITY_ERROR,
                    f"{name} 缺少出发地",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"origin": w.get("origin"), "transportation": t},
                ))
            # ORIG-002: origin not confirmed (employee_default)
            elif not w.get("origin_confirmed", False):
                issues.append(ValidationIssue(
                    "ORIG-002", SEVERITY_WARNING,
                    f"{name} 的出发地来自员工默认值，尚未确认",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"origin": w.get("origin"), "origin_source": w.get("origin_source", "")},
                ))

            # ORIG-004: destination missing
            dest = w.get("destination")
            if not dest:
                issues.append(ValidationIssue(
                    "ORIG-004", SEVERITY_ERROR,
                    f"{name} 缺少目的地",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"destination": dest},
                ))
            # ORIG-003: destination differs from authoritative site_address
            elif ctx.service_order_exists and ctx.site_address:
                if not addresses_equal(dest, ctx.site_address):
                    issues.append(ValidationIssue(
                        "ORIG-003", SEVERITY_ERROR,
                        f"{name} 的目的地与服务单地址不一致",
                        subject_type=SUBJECT_WORKER, subject_id=str(uid),
                        relevant_values={
                            "draft_destination": dest,
                            "authoritative_site_address": ctx.site_address,
                        },
                    ))
        return issues

    # ─── 6. Overnight Stay (OVRN) ────────────────────────────────────────
    # REMOVED (v0.1.273): OVRN-001「未确认是否住宿」不再是一条校验规则。
    # 行程类型 trip_type 有默认值（往返），不再需要用户确认住宿与否；
    # 里程口径是否自洽改由 MILE-005 按 trip_type 校验。

    # ─── 7. Route Status (ROUT) ──────────────────────────────────────────

    def _check_route_status(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        for w in d.get("workers", []):
            if w.get("transportation") != "self_drive":
                continue
            uid = w.get("user_id") or w.get("name", "unknown")
            name = w.get("name", str(uid))
            rs = w.get("route_status", "not_calculated")
            # ROUT-003: not calculated
            if rs == "not_calculated":
                issues.append(ValidationIssue(
                    "ROUT-003", SEVERITY_WARNING,
                    f"{name} 的里程路线尚未计算",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"route_status": rs},
                ))
            # ROUT-002: verification_required
            elif rs == "verification_required":
                issues.append(ValidationIssue(
                    "ROUT-002", SEVERITY_WARNING,
                    f"{name} 的里程路线需要确认",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"route_status": rs, "route_error": w.get("route_error", "")},
                ))
            # ROUT-001: failed (WARNING — not ERROR; mileage rule determines blocking)
            elif rs == "failed":
                issues.append(ValidationIssue(
                    "ROUT-001", SEVERITY_WARNING,
                    f"{name} 的里程路线计算失败: {w.get('route_error', 'unknown')}",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"route_status": rs, "route_error": w.get("route_error", "")},
                ))
            # ROUT-004: success but distance is 0
            elif rs == "success" and w.get("route_distance_meters") == 0:
                issues.append(ValidationIssue(
                    "ROUT-004", SEVERITY_WARNING,
                    f"{name} 的路线距离为 0 米",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"route_distance_meters": 0},
                ))
        return issues

    # ─── 8. Mileage (MILE) ───────────────────────────────────────────────

    def _check_mileage(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        for w in d.get("workers", []):
            if w.get("transportation") != "self_drive":
                continue
            uid = w.get("user_id") or w.get("name", "unknown")
            name = w.get("name", str(uid))
            reported = w.get("reported_miles")
            one_way = w.get("one_way_miles")
            rs = w.get("route_status", "not_calculated")
            trip_type = normalize_trip_type(w.get("trip_type"))

            # MILE-001: no reported_miles and route not successful → ERROR
            if reported is None and rs != "success":
                issues.append(ValidationIssue(
                    "MILE-001", SEVERITY_ERROR,
                    f"{name} 缺少有效里程数据（路线状态: {rs}）",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"reported_miles": reported, "route_status": rs},
                ))
            # MILE-002: reported_miles <= 0
            elif reported is not None and reported <= 0:
                issues.append(ValidationIssue(
                    "MILE-002", SEVERITY_ERROR,
                    f"{name} 的报告里程无效: {reported}",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"reported_miles": reported},
                ))
            # MILE-003: reported_miles exceeds threshold (WARNING, per worker)
            if reported is not None and reported > MILEAGE_WARNING_THRESHOLD:
                issues.append(ValidationIssue(
                    "MILE-003", SEVERITY_WARNING,
                    f"{name} 的报告里程 {reported} 英里超过 {MILEAGE_WARNING_THRESHOLD} 英里阈值，请确认",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"reported_miles": reported, "threshold": MILEAGE_WARNING_THRESHOLD},
                ))
            # MILE-004: one_way missing but reported present
            if reported is not None and one_way is None:
                issues.append(ValidationIssue(
                    "MILE-004", SEVERITY_WARNING,
                    f"{name} 有报告里程但缺少单程里程",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"reported_miles": reported, "one_way_miles": one_way},
                ))
            # MILE-005: 行程类型与里程口径不一致（往返 → 单程×2，单程 → 单程）
            if reported is not None and one_way is not None:
                from .mileage_service import MileageService
                expected = MileageService.round_miles(one_way * trip_multiplier(trip_type))
                if abs(reported - expected) > 0.01:
                    issues.append(ValidationIssue(
                        "MILE-005", SEVERITY_WARNING,
                        f"{name} 的报告里程 {reported} 与计算值 {expected} 不一致（行程类型={trip_label(trip_type)}）",
                        subject_type=SUBJECT_WORKER, subject_id=str(uid),
                        relevant_values={
                            "reported_miles": reported,
                            "one_way_miles": one_way,
                            "trip_type": trip_type,
                            "expected": expected,
                        },
                    ))
            # MILE-006: mileage_verification_required
            if w.get("mileage_verification_required"):
                issues.append(ValidationIssue(
                    "MILE-006", SEVERITY_WARNING,
                    f"{name} 的里程需要人工确认",
                    subject_type=SUBJECT_WORKER, subject_id=str(uid),
                    relevant_values={"mileage_verification_required": True},
                ))
        return issues

    # ─── 9. Mileage Evidence (EVID) ──────────────────────────────────────

    def _check_evidence(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        """Validate evidence METADATA only. Never opens files or checks file existence."""
        issues = []
        records = d.get("evidence_records", [])
        if not isinstance(records, list):
            records = []

        # Build worker route fingerprint map for association check
        worker_routes = {}
        for w in d.get("workers", []):
            uid = w.get("user_id")
            if uid:
                worker_routes[uid] = {
                    "route_distance_meters": w.get("route_distance_meters"),
                    "one_way_miles": w.get("one_way_miles"),
                    "reported_miles": w.get("reported_miles"),
                    "trip_type": w.get("trip_type"),
                }

        for rec in records:
            if not isinstance(rec, dict):
                continue
            eid = rec.get("evidence_id", "unknown")
            status = rec.get("evidence_status", "not_generated")
            worker_uid = rec.get("worker_user_id")

            # EVID-001: failed
            if status == "failed":
                issues.append(ValidationIssue(
                    "EVID-001", SEVERITY_WARNING,
                    f"里程凭证 {eid} 生成失败: {rec.get('error', 'unknown')}",
                    subject_type=SUBJECT_EVIDENCE, subject_id=eid,
                    relevant_values={"evidence_status": status, "error": rec.get("error", "")},
                ))
            # EVID-002: stale
            elif status == "stale":
                issues.append(ValidationIssue(
                    "EVID-002", SEVERITY_WARNING,
                    f"里程凭证 {eid} 已过期，需要重新生成",
                    subject_type=SUBJECT_EVIDENCE, subject_id=eid,
                    relevant_values={"evidence_status": status},
                ))
            # EVID-003: route fingerprint mismatch (metadata comparison only)
            if status == "ready" and rec.get("route_fingerprint"):
                # We can't recompute route_fingerprint here without Google data,
                # but we can check if worker route data changed significantly
                wr = worker_routes.get(worker_uid, {})
                if wr.get("reported_miles") is not None and rec.get("reported_miles") is not None:
                    if abs(wr["reported_miles"] - rec["reported_miles"]) > 0.01:
                        issues.append(ValidationIssue(
                            "EVID-003", SEVERITY_WARNING,
                            f"里程凭证 {eid} 的里程数据与当前 worker 不一致",
                            subject_type=SUBJECT_EVIDENCE, subject_id=eid,
                            relevant_values={
                                "evidence_reported_miles": rec.get("reported_miles"),
                                "worker_reported_miles": wr.get("reported_miles"),
                            },
                        ))
            # EVID-004: worker association mismatch
            if worker_uid is not None:
                worker_exists = any(w.get("user_id") == worker_uid for w in d.get("workers", []))
                if not worker_exists:
                    issues.append(ValidationIssue(
                        "EVID-004", SEVERITY_WARNING,
                        f"里程凭证 {eid} 关联的 worker ID={worker_uid} 不在当前 Draft 中",
                        subject_type=SUBJECT_EVIDENCE, subject_id=eid,
                        relevant_values={"worker_user_id": worker_uid},
                    ))
        return issues

    # ─── 10. Arrival / Departure (TIME) ──────────────────────────────────

    def _check_time(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        arrival = d.get("arrival_time")
        departure = d.get("departure_time")
        arrival_src = d.get("arrival_time_source")
        departure_src = d.get("departure_time_source")
        valid_sources = {"photo", "manual", "user_input", "photo_timeline_confirmed", "photo_marked", None, ""}

        # TIME-001: arrival missing
        if not arrival:
            issues.append(ValidationIssue(
                "TIME-001", SEVERITY_ERROR,
                "缺少到达时间",
                subject_type=SUBJECT_DRAFT, subject_id="arrival_time",
                relevant_values={"arrival_time": arrival},
            ))
        # TIME-002: departure missing
        if not departure:
            issues.append(ValidationIssue(
                "TIME-002", SEVERITY_ERROR,
                "缺少离场时间",
                subject_type=SUBJECT_DRAFT, subject_id="departure_time",
                relevant_values={"departure_time": departure},
            ))
        # TIME-003: arrival >= departure
        if arrival and departure:
            try:
                ah, am = map(int, arrival.split(":"))
                dh, dm = map(int, departure.split(":"))
                if (ah, am) >= (dh, dm):
                    issues.append(ValidationIssue(
                        "TIME-003", SEVERITY_ERROR,
                        f"到达时间 {arrival} 不早于离场时间 {departure}",
                        subject_type=SUBJECT_DRAFT, subject_id="time_order",
                        relevant_values={"arrival_time": arrival, "departure_time": departure},
                    ))
            except (ValueError, AttributeError):
                pass
        # TIME-004/005: invalid source
        if arrival and arrival_src and arrival_src not in valid_sources:
            issues.append(ValidationIssue(
                "TIME-004", SEVERITY_WARNING,
                f"到达时间来源无效: {arrival_src!r}",
                subject_type=SUBJECT_DRAFT, subject_id="arrival_source",
                relevant_values={"arrival_time_source": arrival_src},
            ))
        if departure and departure_src and departure_src not in valid_sources:
            issues.append(ValidationIssue(
                "TIME-005", SEVERITY_WARNING,
                f"离场时间来源无效: {departure_src!r}",
                subject_type=SUBJECT_DRAFT, subject_id="departure_source",
                relevant_values={"departure_time_source": departure_src},
            ))
        # TIME-006: photo-sourced time but no photo ref
        if arrival and arrival_src in ("photo", "photo_timeline_confirmed", "photo_marked") and not d.get("arrival_photo_ref") and not d.get("arrival_photo"):
            issues.append(ValidationIssue(
                "TIME-006", SEVERITY_WARNING,
                "到达时间来自照片但缺少关联照片引用",
                subject_type=SUBJECT_DRAFT, subject_id="arrival_photo",
                relevant_values={"arrival_time_source": arrival_src},
            ))
        if departure and departure_src in ("photo", "photo_timeline_confirmed", "photo_marked") and not d.get("departure_photo_ref") and not d.get("departure_photo"):
            issues.append(ValidationIssue(
                "TIME-006", SEVERITY_WARNING,
                "离场时间来自照片但缺少关联照片引用",
                subject_type=SUBJECT_DRAFT, subject_id="departure_photo",
                relevant_values={"departure_time_source": departure_src},
            ))
        return issues

    # ─── 11. Photo Timeline (PTML) ───────────────────────────────────────

    def _check_photo_timeline(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        pts = d.get("photo_timeline_status", "not_scanned")
        # PTML-001: failed
        if pts == "failed":
            issues.append(ValidationIssue(
                "PTML-001", SEVERITY_WARNING,
                "照片时间线扫描失败",
                subject_type=SUBJECT_DRAFT, subject_id="timeline",
                relevant_values={"photo_timeline_status": pts},
            ))
        # PTML-002: suspicious
        elif pts == "suspicious":
            issues.append(ValidationIssue(
                "PTML-002", SEVERITY_WARNING,
                "照片时间线存在异常，需要确认",
                subject_type=SUBJECT_DRAFT, subject_id="timeline",
                relevant_values={"photo_timeline_status": pts},
            ))
        # PTML-003: verification_required
        elif pts == "verification_required":
            issues.append(ValidationIssue(
                "PTML-003", SEVERITY_WARNING,
                "照片时间线需要人工确认",
                subject_type=SUBJECT_DRAFT, subject_id="timeline",
                relevant_values={"photo_timeline_status": pts},
            ))
        # PTML-004: insufficient_photos / no_photos
        elif pts in ("insufficient_photos", "no_photos"):
            issues.append(ValidationIssue(
                "PTML-004", SEVERITY_WARNING,
                "照片数量不足，无法生成可靠时间线",
                subject_type=SUBJECT_DRAFT, subject_id="timeline",
                relevant_values={"photo_timeline_status": pts},
            ))
        # PTML-005: no photos but arrival/departure from photo
        candidates = d.get("photo_candidates", [])
        if not candidates:
            if d.get("arrival_time_source") in ("photo", "photo_timeline_confirmed", "photo_marked"):
                issues.append(ValidationIssue(
                    "PTML-005", SEVERITY_ERROR,
                    "到达时间来自照片但 Draft 中无照片候选",
                    subject_type=SUBJECT_DRAFT, subject_id="arrival_no_photo",
                    relevant_values={"arrival_time_source": d.get("arrival_time_source")},
                ))
            if d.get("departure_time_source") in ("photo", "photo_timeline_confirmed", "photo_marked"):
                issues.append(ValidationIssue(
                    "PTML-005", SEVERITY_ERROR,
                    "离场时间来自照片但 Draft 中无照片候选",
                    subject_type=SUBJECT_DRAFT, subject_id="departure_no_photo",
                    relevant_values={"departure_time_source": d.get("departure_time_source")},
                ))
        return issues

    # ─── 12. Safety Photo (SAFE) ─────────────────────────────────────────

    def _check_safety_photo(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        selected = collect_safety_photos(d)
        # SAFE-001: no safety photo
        if not selected:
            issues.append(ValidationIssue(
                "SAFE-001", SEVERITY_ERROR,
                "未选择安全自检照片",
                subject_type=SUBJECT_DRAFT, subject_id="safety_photo",
                relevant_values={"has_selected_safety_photo": False},
            ))
            return issues
        if d.get("safety_photo_verification_required"):
            issues.append(ValidationIssue(
                "SAFE-002", SEVERITY_WARNING,
                "安全自检照片需要人工确认",
                subject_type=SUBJECT_DRAFT, subject_id="safety_photos",
                relevant_values={"verification_required": True},
            ))
        candidate_ids = {p.get("photo_id") for p in d.get("photo_candidates", []) if isinstance(p, dict)}
        for one in selected:
            if not isinstance(one, dict):
                continue
            pid = one.get("photo_id", "unknown")
            # SAFE-002: verification required (per photo)
            if one.get("verification_required"):
                issues.append(ValidationIssue(
                    "SAFE-002", SEVERITY_WARNING,
                    "安全自检照片需要人工确认",
                    subject_type=SUBJECT_PHOTO, subject_id=pid,
                    relevant_values={"photo_id": pid, "verification_required": True},
                ))
            # SAFE-003: low confidence
            conf = one.get("confidence", 0)
            if conf and conf < 0.5:
                issues.append(ValidationIssue(
                    "SAFE-003", SEVERITY_WARNING,
                    f"安全自检照片置信度较低: {conf}",
                    subject_type=SUBJECT_PHOTO, subject_id=pid,
                    relevant_values={"photo_id": pid, "confidence": conf},
                ))
            # SAFE-004: not in candidates
            if pid and pid not in candidate_ids:
                issues.append(ValidationIssue(
                    "SAFE-004", SEVERITY_WARNING,
                    "安全自检照片不在当前照片候选列表中",
                    subject_type=SUBJECT_PHOTO, subject_id=pid,
                    relevant_values={"photo_id": pid},
                ))
        return issues

    # ─── 13. Service Photos (SVCF) ───────────────────────────────────────

    def _check_service_photos(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        selected = d.get("selected_service_photos", [])
        if not isinstance(selected, list):
            selected = []
        # SVCF-001: no service photos
        if not selected:
            issues.append(ValidationIssue(
                "SVCF-001", SEVERITY_ERROR,
                "未选择施工照片",
                subject_type=SUBJECT_DRAFT, subject_id="service_photos",
                relevant_values={"selected_count": 0},
            ))
        else:
            # SVCF-002: exceeds max
            if len(selected) > MAX_SERVICE_PHOTOS:
                issues.append(ValidationIssue(
                    "SVCF-002", SEVERITY_WARNING,
                    f"施工照片数量 {len(selected)} 超过上限 {MAX_SERVICE_PHOTOS}",
                    subject_type=SUBJECT_DRAFT, subject_id="service_photos",
                    relevant_values={"selected_count": len(selected), "max": MAX_SERVICE_PHOTOS},
                ))
            seen_pids: set = set()
            candidate_ids = {p.get("photo_id") for p in d.get("photo_candidates", []) if isinstance(p, dict)}
            for sp in selected:
                if not isinstance(sp, dict):
                    continue
                pid = sp.get("photo_id", "unknown")
                # SVCF-003: verification required (per photo)
                if sp.get("verification_required"):
                    issues.append(ValidationIssue(
                        "SVCF-003", SEVERITY_WARNING,
                        f"施工照片 {pid} 需要人工确认",
                        subject_type=SUBJECT_PHOTO, subject_id=pid,
                        relevant_values={"photo_id": pid, "verification_required": True},
                    ))
                # SVCF-004: duplicate
                if pid in seen_pids:
                    issues.append(ValidationIssue(
                        "SVCF-004", SEVERITY_WARNING,
                        f"施工照片 {pid} 重复选择",
                        subject_type=SUBJECT_PHOTO, subject_id=pid,
                        relevant_values={"photo_id": pid},
                    ))
                seen_pids.add(pid)
                # SVCF-005: not in candidates
                if pid and pid not in candidate_ids:
                    issues.append(ValidationIssue(
                        "SVCF-005", SEVERITY_WARNING,
                        f"施工照片 {pid} 不在当前照片候选列表中",
                        subject_type=SUBJECT_PHOTO, subject_id=pid,
                        relevant_values={"photo_id": pid},
                    ))
        return issues

    # ─── 14. Work Items (WORK) ───────────────────────────────────────────

    def _check_work_items(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        work_items = d.get("work_items", [])
        if not isinstance(work_items, list):
            work_items = []
        workers = d.get("workers", [])
        if not isinstance(workers, list):
            workers = []
        waiting = d.get("waiting_hours", 0) or 0
        desc = (d.get("service_description") or "").strip()

        # WORK-001: canonical empty report — no work items, no waiting, no description
        # This is the single canonical ERROR for "nothing to report".
        # SDES-001 must NOT also fire for this case.
        # Only fires when there ARE workers (no-workers case is DRFT-004/WRKR-001).
        if workers and not work_items and waiting == 0 and not desc:
            issues.append(ValidationIssue(
                "WORK-001", SEVERITY_ERROR,
                "未填写施工内容、等待时间或服务描述",
                subject_type=SUBJECT_DRAFT, subject_id="work_items",
                relevant_values={"work_item_count": 0, "waiting_hours": waiting, "has_description": False},
            ))
            return issues  # WORK-001 is canonical; skip per-item checks when empty

        # Per-item checks
        for idx, wi in enumerate(work_items):
            if not isinstance(wi, dict):
                continue
            wid = wi.get("equipment") or f"item_{idx}"
            # WORK-002: item with no equipment and no description
            if not (wi.get("equipment") or "").strip() and not (wi.get("description") or "").strip():
                issues.append(ValidationIssue(
                    "WORK-002", SEVERITY_WARNING,
                    f"施工项 {idx + 1} 缺少设备和描述",
                    subject_type=SUBJECT_WORK_ITEM, subject_id=str(wid),
                    relevant_values={"index": idx, "equipment": wi.get("equipment"), "description": wi.get("description")},
                ))
            # WORK-004: invalid action
            valid_actions = {"replace_fuse", "repair", "inspect", "install", "other", None, ""}
            action = wi.get("action")
            if action and action not in valid_actions:
                issues.append(ValidationIssue(
                    "WORK-004", SEVERITY_WARNING,
                    f"施工项 {wid} 的操作类型无效: {action!r}",
                    subject_type=SUBJECT_WORK_ITEM, subject_id=str(wid),
                    relevant_values={"action": action},
                ))

        # WORK-003: duplicate work items (same equipment + action)
        seen: set = set()
        for wi in work_items:
            if not isinstance(wi, dict):
                continue
            key = (wi.get("equipment", ""), wi.get("action", ""))
            if key in seen and key[0]:
                issues.append(ValidationIssue(
                    "WORK-003", SEVERITY_WARNING,
                    f"施工项重复: {wi.get('equipment')} ({wi.get('action')})",
                    subject_type=SUBJECT_WORK_ITEM, subject_id=str(wi.get("equipment", "dup")),
                    relevant_values={"equipment": wi.get("equipment"), "action": wi.get("action")},
                ))
            seen.add(key)
        return issues

    # ─── 15. Service Description (SDES) ──────────────────────────────────

    def _check_service_description(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        work_items = d.get("work_items", [])
        waiting = d.get("waiting_hours", 0) or 0
        desc = (d.get("service_description") or "").strip()

        # SDES-001: description empty but work_items exist (quality issue, NOT canonical empty)
        # IMPORTANT: do NOT fire when work_items empty AND waiting==0 (that's WORK-001 canonical)
        if work_items and not desc:
            issues.append(ValidationIssue(
                "SDES-001", SEVERITY_WARNING,
                "有施工内容但缺少服务描述",
                subject_type=SUBJECT_DRAFT, subject_id="service_description",
                relevant_values={"work_item_count": len(work_items), "has_description": False},
            ))
        # SDES-002: description too short (INFO)
        elif desc and len(desc) < 5:
            issues.append(ValidationIssue(
                "SDES-002", SEVERITY_INFO,
                f"服务描述较短 ({len(desc)} 字符)",
                subject_type=SUBJECT_DRAFT, subject_id="service_description",
                relevant_values={"description_length": len(desc)},
            ))
        return issues

    # ─── 16. Waiting Time (WAIT) ─────────────────────────────────────────

    def _check_waiting_time(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        waiting = d.get("waiting_hours", 0) or 0
        reason = (d.get("waiting_reason") or "").strip()

        # WAIT-002: negative
        if waiting < 0:
            issues.append(ValidationIssue(
                "WAIT-002", SEVERITY_ERROR,
                f"等待时间不能为负数: {waiting}",
                subject_type=SUBJECT_DRAFT, subject_id="waiting_hours",
                relevant_values={"waiting_hours": waiting},
            ))
        # WAIT-001: waiting > 0 but no reason
        elif waiting > 0 and not reason:
            issues.append(ValidationIssue(
                "WAIT-001", SEVERITY_ERROR,
                f"有等待时间 {waiting} 小时但缺少等待原因",
                subject_type=SUBJECT_DRAFT, subject_id="waiting_reason",
                relevant_values={"waiting_hours": waiting, "waiting_reason": reason},
            ))
        # WAIT-003: excessive
        if waiting > 12:
            issues.append(ValidationIssue(
                "WAIT-003", SEVERITY_WARNING,
                f"等待时间 {waiting} 小时较长，请确认",
                subject_type=SUBJECT_DRAFT, subject_id="waiting_hours",
                relevant_values={"waiting_hours": waiting},
            ))
        return issues

    # ─── 17. AI Verification (AIVR) ──────────────────────────────────────

    def _check_ai_verification(
        self, d: Dict[str, Any], existing_issues: List[ValidationIssue]
    ) -> List[ValidationIssue]:
        """Central verification field mapping.

        Only produces issues for verification_fields NOT already covered by
        a specific business rule. This prevents duplicate AIVR + specific rule issues.
        """
        issues = []
        if not d.get("verification_required"):
            return issues

        fields = d.get("verification_fields", [])
        if not isinstance(fields, list):
            return issues

        # Collect rule_ids already produced by specific rules
        existing_rule_ids = {i.rule_id for i in existing_issues}

        for field in fields:
            if field == "draft_data_corrupted":
                continue  # handled by DRFT-001
            # v0.1.236: 施工内容是描述文字而非表格化字段（用户 2026-09-17 决策）。
            # work_items.* 不再作为待确认字段提示（含存量草稿里已有的标记）。
            if str(field).lower().startswith("work_items."):
                continue
            mapped_rule = VERIFICATION_FIELD_RULE_MAP.get(field)
            if mapped_rule and mapped_rule in existing_rule_ids:
                continue  # already covered by specific rule — skip duplicate
            # Unknown or unmapped field → safe WARNING
            issues.append(ValidationIssue(
                "AIVR-003", SEVERITY_WARNING,
                f"AI 标记需要确认的字段: {field}",
                subject_type=SUBJECT_DRAFT, subject_id=f"verif_{field}",
                relevant_values={"field": field, "mapped_rule": mapped_rule},
            ))

        # AIVR-004: verification_override without override_fields
        if d.get("verification_override") and not d.get("override_fields"):
            issues.append(ValidationIssue(
                "AIVR-004", SEVERITY_INFO,
                "存在验证覆盖但未记录覆盖字段",
                subject_type=SUBJECT_DRAFT, subject_id="override",
                relevant_values={"verification_override": True},
            ))
        return issues

    # ─── 18. User Acknowledgements (USRO) ────────────────────────────────

    def _check_user_acknowledgements(
        self, d: Dict[str, Any], current_issues: List[ValidationIssue]
    ) -> List[ValidationIssue]:
        """Audit acknowledgement records. INFO only — never blocking."""
        issues = []
        acks = get_acknowledgements(d)
        if not acks:
            return issues

        current_keys = {i.issue_key: i for i in current_issues}
        for ack in acks:
            issue_key = ack.get("issue_key", "")
            rule_id = ack.get("rule_id", "")
            # USRO-001: stale acknowledgement (issue exists but fingerprint changed)
            if issue_key in current_keys:
                issue = current_keys[issue_key]
                if ack.get("issue_fingerprint_at_ack") != issue.issue_fingerprint:
                    issues.append(ValidationIssue(
                        "USRO-001", SEVERITY_INFO,
                        f"Warning {issue_key} 的确认已过期（相关数据已变更）",
                        subject_type=SUBJECT_DRAFT, subject_id=f"stale_{issue_key}",
                        relevant_values={"issue_key": issue_key, "rule_id": rule_id},
                    ))
            # USRO-002: acknowledgement for non-existent issue
            else:
                issues.append(ValidationIssue(
                    "USRO-002", SEVERITY_INFO,
                    f"Warning {issue_key} 的确认对应问题已不存在",
                    subject_type=SUBJECT_DRAFT, subject_id=f"orphan_{issue_key}",
                    relevant_values={"issue_key": issue_key, "rule_id": rule_id},
                ))
        return issues

    # ─── 19. Vision Verification (VISN) ──────────────────────────────────

    def _check_vision(self, d: Dict[str, Any]) -> List[ValidationIssue]:
        issues = []
        pcs = d.get("photo_classification_status", "not_classified")
        # VISN-001: classification failed
        if pcs == "failed":
            issues.append(ValidationIssue(
                "VISN-001", SEVERITY_WARNING,
                "照片分类失败",
                subject_type=SUBJECT_DRAFT, subject_id="classification",
                relevant_values={"photo_classification_status": pcs},
            ))
        # VISN-003: version mismatch (INFO)
        version = d.get("photo_classification_version", 1)
        if version != 1:
            issues.append(ValidationIssue(
                "VISN-003", SEVERITY_INFO,
                f"照片分类版本 {version} 与当前版本不同",
                subject_type=SUBJECT_DRAFT, subject_id="classification_version",
                relevant_values={"photo_classification_version": version},
            ))
        # VISN-004: photo analysis with verification_required not covered by SAFE/SVCF
        for pa in d.get("photo_analysis_results", []):
            if not isinstance(pa, dict):
                continue
            if pa.get("verification_required"):
                pid = pa.get("photo_id", "unknown")
                # Check if this photo is selected as safety or service (those have their own rules)
                safety_pids = {s.get("photo_id") for s in collect_safety_photos(d)}
                service_pids = {
                    sp.get("photo_id") for sp in d.get("selected_service_photos", [])
                    if isinstance(sp, dict)
                }
                if pid not in safety_pids and pid not in service_pids:
                    issues.append(ValidationIssue(
                        "VISN-004", SEVERITY_WARNING,
                        f"照片 {pid} 的 AI 分析结果需要确认",
                        subject_type=SUBJECT_PHOTO, subject_id=pid,
                        relevant_values={"photo_id": pid, "verification_reason": pa.get("verification_reason", "")},
                    ))
        return issues

    # ─── 20. Provenance / Integrity (PROV) ───────────────────────────────

    def _check_provenance(self, d: Dict[str, Any], draft_version: int) -> List[ValidationIssue]:
        issues = []
        # PROV-001: ai_metadata missing model (INFO)
        ai_meta = d.get("ai_metadata", {})
        if isinstance(ai_meta, dict) and not ai_meta.get("model"):
            issues.append(ValidationIssue(
                "PROV-001", SEVERITY_INFO,
                "Draft 缺少 AI 模型来源信息",
                subject_type=SUBJECT_DRAFT, subject_id="ai_metadata",
                relevant_values={"has_model": False},
            ))
        # PROV-003: photo hash mismatch in candidates (WARNING)
        for p in d.get("photo_candidates", []):
            if not isinstance(p, dict):
                continue
            pid = p.get("photo_id", "")
            phash = p.get("photo_hash", "")
            if pid and phash and pid != phash:
                # photo_id should equal full SHA256 (photo_hash) per schema convention
                # Only warn if both are non-empty and clearly different lengths
                if len(pid) == 64 and len(phash) == 64 and pid != phash:
                    issues.append(ValidationIssue(
                        "PROV-003", SEVERITY_WARNING,
                        f"照片 {pid[:12]}... 的 photo_id 与 photo_hash 不一致",
                        subject_type=SUBJECT_PHOTO, subject_id=pid,
                        relevant_values={"photo_id": pid, "photo_hash": phash},
                    ))
        return issues
