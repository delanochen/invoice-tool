"""AI Daily Report - FormalSaveService (Phase 9)

Deterministic, exactly-once, transactional commit of a confirmed Draft +
frozen ready Attachment Manifest into the existing formal tables:

    service_reports / service_report_workers / service_report_attachments

BOUNDARY LOCK (Phase 9):
- Only consumes frozen validated data (confirmed Draft + current ready Manifest).
- NEVER calls DeepSeek / Vision / Google Routes / Google Static Maps.
- NEVER rescans photos, regenerates mileage evidence, re-infers times,
  re-calculates mileage, or re-selects photos.
- NEVER writes arrival/departure formal attachment categories
  (arrival/departure stay provenance-only).
- Formal attachment categories are limited to: site / self_check / mileage_proof.

EXACTLY-ONCE:
- ai_daily_report_drafts.saved_report_id (pre-existing, Phase 1 reserved) is the
  authoritative pointer: a Draft maps to at most one formal service_report.
- ai_daily_report_formal_commits.draft_id UNIQUE provides the claim/锁 for
  concurrent requests; a committed row returns the existing report (200).

CRASH SAFETY:
- DB preparing commit row first (with manifest fingerprints + snapshot)
  -> materialize formal files (.tmp -> sha256 verify -> os.replace, recording
  every copied path in formal_files) -> single DB transaction (report + workers
  + attachment rows + commit row + draft saved) -> commit.
- Any crash point is recoverable; never exposes partial-ready formal state.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FORMAL_SAVE_VERSION = 1

# ─── Error codes (safe, no paths/secrets in user-facing messages) ──────────

CODE_VALIDATION_BLOCKED = "validation_cannot_proceed"
CODE_MANIFEST_NOT_READY = "manifest_not_ready"
CODE_MANIFEST_STALE = "manifest_stale"
CODE_MANIFEST_FINGERPRINT = "manifest_fingerprint_mismatch"
CODE_DRAFT_STATE = "draft_state_error"
CODE_VERSION_CONFLICT = "version_conflict"
CODE_COMPLIANCE_BLOCKED = "compliance_blocked"
CODE_INTEGRITY = "integrity_failed"
CODE_SOURCE_CHANGED = "source_file_changed"
CODE_PATH_UNSAFE = "path_unsafe"
CODE_TRAVEL_MODE = "travel_mode_unmappable"
CODE_COMMIT_CONFLICT = "commit_in_progress"
CODE_NOT_FOUND = "not_found"

# Formal attachment categories allowed by Phase 9 (sealed Q4 ruling).
FORMAL_CATEGORIES = ("self_check", "site", "mileage_proof")

# Role type -> formal attachment category (only materialization_required=1 roles).
ROLE_TO_CATEGORY = {
    "safety_photo": "self_check",
    "service_photo": "site",
    "mileage_evidence": "mileage_proof",
}

# Category -> subfolder name (must match REPORT_PHOTO_FOLDERS in app.py).
CATEGORY_FOLDERS = {
    "arrival": "现场到达时间照片",
    "departure": "离开现场时间照片",
    "self_check": "自检照片",
    "site": "现场服务照片",
    "mileage_proof": "里程佐证",
}

# Draft transportation (WorkerTravel.transportation) -> formal travel_mode.
# Deterministic mapping (sealed Q6). 'other' is unmappable -> 422.
TRAVEL_MODE_MAP = {
    "self_drive": "self_drive",
    "flight": "flight",
    "carpool": "following",
    "passenger": "following",
    "rental_car": "rental_drive",
}

REPORT_TRAVEL_MODES = {"self_drive", "flight", "following", "rental_drive"}


def _canonical_json(obj: Any) -> str:
    """Stable JSON serialization: sort_keys, no whitespace, ensure_ascii=False."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ─── Exceptions ─────────────────────────────────────────────────────────────

class FormalSaveError(Exception):
    """Base formal-save error. code is a safe internal error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class FormalSaveValidationBlockedError(FormalSaveError):
    """Phase 7 validation cannot proceed. -> HTTP 422."""


class FormalSaveIntegrityError(FormalSaveError):
    """Manifest / source / asset integrity failure. -> HTTP 422."""


class FormalSaveComplianceError(FormalSaveError):
    """Compliance review required but not reviewed. -> HTTP 422."""


class FormalSaveStaleError(FormalSaveError):
    """Draft version / manifest stale (optimistic lock). -> HTTP 409."""


class FormalSaveStateError(FormalSaveError):
    """Draft state machine violation. -> HTTP 409."""


class FormalSaveCommitConflictError(FormalSaveError):
    """Concurrent formal commit in progress. -> HTTP 409."""


# ─── Deterministic mapping layer (pure functions) ───────────────────────────

def map_travel_mode(transportation: Any) -> str:
    """Draft transportation -> formal travel_mode (deterministic, sealed Q6).

    Raises FormalSaveIntegrityError(CODE_TRAVEL_MODE) for unmappable values.
    """
    value = str(transportation or "self_drive").strip()
    mapped = TRAVEL_MODE_MAP.get(value)
    if mapped is None:
        raise FormalSaveIntegrityError(
            CODE_TRAVEL_MODE,
            f"出行方式 {value!r} 无法确定映射到正式日报，请回到 Draft 修正后重新确认。",
        )
    return mapped


def format_work_items(description: str, work_items: Any) -> str:
    """Deterministic, fixed-template append of structured work items.

    Sealed Q2 ruling: never drop work_items; never use LLM; stable ordering
    (original list order); idempotent (appended exactly once per formal save
    because formal creation is exactly-once).
    """
    text = str(description or "").strip()
    items = []
    for item in work_items or []:
        if not isinstance(item, dict):
            continue
        equipment = str(item.get("equipment") or "").strip()
        action = str(item.get("action") or "").strip()
        detail = str(item.get("description") or "").strip()
        if not (equipment or action or detail):
            continue
        items.append((equipment, action, detail))
    if not items:
        return text
    lines = [text] if text else []
    lines.append("Work Performed:")
    for equipment, action, detail in items:
        label = equipment or "-"
        if action:
            label = f"{label} — {action}"
        if detail:
            label = f"{label}: {detail}"
        lines.append(f"* {label}")
    return "\n\n".join(lines)


def derive_departure_address(workers: Any) -> Optional[str]:
    """Deterministic departure_address derivation (sealed Q1 ruling).

    - single worker with confirmed origin -> that origin
    - multiple workers with identical confirmed origins -> the common origin
    - differing / unconfirmed origins -> None (never guess, never pick first)
    """
    confirmed = []
    for worker in workers or []:
        if not isinstance(worker, dict):
            continue
        if not worker.get("origin_confirmed"):
            continue
        origin = str(worker.get("origin_normalized") or worker.get("origin") or "").strip()
        if origin:
            confirmed.append(origin)
    if not confirmed:
        return None
    if len(set(confirmed)) == 1:
        return confirmed[0]
    return None


def derive_cabinet_number(work_items: Any) -> Optional[str]:
    """Deterministic cabinet_number derivation (sealed Q1 ruling).

    Only when work_items contain exactly one unique non-empty equipment
    identifier -> that value; otherwise None (never guess / never pick first).
    """
    values = []
    for item in work_items or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("cabinet") or item.get("equipment") or "").strip()
        if value:
            values.append(value)
    unique = set(values)
    if len(unique) == 1:
        return values[0]
    return None


def derive_mileage_billing_method(draft_data: Dict[str, Any]) -> Tuple[str, str]:
    """Draft explicit legal value wins; otherwise system default 'per_person'."""
    explicit = str(draft_data.get("mileage_billing_method") or "").strip()
    if explicit in {"per_person", "per_vehicle"}:
        return explicit, "draft_explicit"
    return "per_person", "system_default"


def parse_report_minutes(value: Any) -> Optional[int]:
    value = normalize_report_time(value)
    if not value or ":" not in str(value):
        return None
    try:
        hour_text, minute_text = str(value).split(":", 1)
        return int(hour_text) * 60 + int(minute_text)
    except ValueError:
        return None


def normalize_report_time(value: Any) -> Optional[str]:
    """Normalize a draft time into the manual flow's 'HH:MM' format (v0.1.243).

    Photo timeline candidates are ISO local timestamps
    ('2026-09-17T08:50:00'); the manual report flow stores 'HH:MM' and the
    service report form splits on ':' expecting hours at parts[0]. Storing
    the raw ISO string broke the hour dropdown (parts[0] was the date+hour).
    Accepts 'HH:MM', 'HH:MM:SS' and ISO datetimes; returns None otherwise.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[1]
    elif " " in text:
        text = text.split(" ", 1)[1]
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def rounded_report_service_hours(arrival_time: Any, departure_time: Any) -> float:
    """Same rounding semantics as the existing manual report flow."""
    arrival = parse_report_minutes(arrival_time)
    departure = parse_report_minutes(departure_time)
    if arrival is None or departure is None or departure <= arrival:
        return 0
    duration_minutes = departure - arrival
    rounded_minutes = (duration_minutes // 15) * 15
    if duration_minutes % 15 > 7:
        rounded_minutes += 15
    return round(rounded_minutes / 60, 2)


def format_report_hours(value: Any) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def to_formal_worker(draft_worker: Dict[str, Any]) -> Dict[str, Any]:
    """Map one Draft worker to formal service_report_workers values.

    Deterministic only; never infers missing travel/work data. Missing
    travel_hours / public_transport_hours / work_description are recorded as
    default zero / empty with provenance (see build_fields_provenance).
    """
    mode = map_travel_mode(draft_worker.get("transportation") or "self_drive")
    driving = _to_float(draft_worker.get("reported_miles"))
    if mode == "flight":
        driving = 0.0
    travel_hours = _to_float(draft_worker.get("travel_hours"))
    public_transport_hours = _to_float(draft_worker.get("public_transport_hours"))
    if mode == "flight":
        travel_hours = 0.0
    return {
        "user_id": int(draft_worker.get("user_id") or 0),
        "driving_miles": round(driving, 2),
        "travel_mode": mode,
        "travel_hours": travel_hours,
        "public_transport_hours": public_transport_hours,
        "work_description": str(draft_worker.get("work_description") or ""),
    }


def compute_total_time(workers: List[Dict[str, Any]]) -> str:
    total = sum(
        (w.get("travel_hours") or 0) + (w.get("public_transport_hours") or 0)
        for w in workers
    )
    return format_report_hours(total)


def compute_driving_miles(workers: List[Dict[str, Any]], method: str) -> float:
    """Same billing semantics as the existing manual report flow."""
    if method == "per_vehicle":
        total = sum(w["driving_miles"] for w in workers if w["travel_mode"] == "self_drive")
    else:
        total = sum(w["driving_miles"] for w in workers if w["travel_mode"] in {"self_drive", "following"})
    return round(total, 2)


def build_fields_provenance(
    draft_data: Dict[str, Any],
    formal_workers: List[Dict[str, Any]],
    user_id: Any,
    departure_address: Optional[str],
    cabinet_number: Optional[str],
    mileage_method: str,
    mileage_method_source: str,
) -> Dict[str, Any]:
    """Deterministic field-level provenance record (canonical JSON).

    Answers "where did each formal field value come from?" without leaking
    paths or secrets. Keys are sorted by _canonical_json at persist time.
    """
    missing_worker_fields = bool(
        not draft_data.get("workers")
        or any(
            (w.get("travel_hours") is None) or (w.get("public_transport_hours") is None)
            for w in (draft_data.get("workers") or [])
        )
    )
    departure_source = "worker_origin"
    if departure_address is None:
        departure_source = "no_single_confirmed_origin"
    cabinet_source = "unique_work_item" if cabinet_number is not None else "zero_or_multiple_identifiers"
    return {
        "mileage_billing_method": {"value": mileage_method, "source": mileage_method_source},
        "departure_address": {"value": departure_address, "source": departure_source},
        "cabinet_number": {"value": cabinet_number, "source": cabinet_source},
        "report_writer_id": {"value": None, "source": "not_in_draft"},
        "created_by": {"value": int(user_id), "source": "formal_save_actor"},
        "actual_work_date": {"value": draft_data.get("report_date"), "source": "report_date_fallback"},
        "travel_hours": {"value": 0, "source": "not_in_draft_default_zero" if missing_worker_fields else "draft_explicit"},
        "public_transport_hours": {"value": 0, "source": "not_in_draft_default_zero" if missing_worker_fields else "draft_explicit"},
        "work_description": {"value": "", "source": "not_in_draft_empty"},
        "arrival_departure": {"value": "provenance_only", "source": "phase8_sealed_boundary"},
    }


# ─── Formal Save Service ────────────────────────────────────────────────────

class FormalSaveService:
    """Phase 9 transactional commit engine.

    Args:
        db: active sqlite3 connection (same per-request connection as app.db()).
        data_dir: DATA_DIR (prepared assets + evidence live under it).
        report_attachments_dir: REPORT_ATTACHMENTS_DIR (formal attachments).
        user_id: authenticated Formal Save actor.
    """

    def __init__(
        self,
        db,
        data_dir: str,
        report_attachments_dir: str,
        user_id: int,
    ):
        self.db = db
        self.data_dir = Path(data_dir).resolve()
        self.report_attachments_dir = Path(report_attachments_dir).resolve()
        self.user_id = int(user_id)

    # ─── Public entry ──────────────────────────────────────────────────────

    def run(
        self,
        draft_row: Dict[str, Any],
        expected_draft_version: Optional[int],
        client_manifest_id: Optional[str],
        validation_result,
        manifest_svc,
    ) -> Dict[str, Any]:
        """Execute the formal save for a confirmed Draft + ready Manifest.

        Returns:
            {"status": "created"|"already_committed", "service_report_id": int}
        """
        draft_id = int(draft_row["id"])

        # 0. Exactly-once pre-check: an existing formal report wins immediately,
        #    even if the Draft moved to 'saved' after a previous success.
        existing = self._existing_report(draft_id)
        if existing is not None:
            return {"status": "already_committed", "service_report_id": int(existing)}

        # 1. State machine gate
        if draft_row.get("status") != "confirmed":
            raise FormalSaveStateError(
                CODE_DRAFT_STATE,
                f"Draft 状态为 {draft_row.get('status')}，仅 confirmed 状态可正式保存。",
            )
        # 2. Optimistic locking on draft_version (frontend-supplied, optional)
        current_version = int(draft_row.get("draft_version", 1))
        if expected_draft_version is not None and int(expected_draft_version) != current_version:
            raise FormalSaveStaleError(CODE_VERSION_CONFLICT, "Draft 版本冲突，请刷新后重试。")

        # 3. Phase 7 validation gate (server-side, never trusts frontend)
        if not validation_result.can_proceed:
            raise FormalSaveValidationBlockedError(
                CODE_VALIDATION_BLOCKED,
                "Phase 7 校验未通过（存在未解决的 ERROR），无法正式保存。",
            )

        # 4. Resolve current applicable manifest + recompute fingerprint.
        current = manifest_svc.get_current_manifest(draft_row, validation_result)
        if not current:
            raise FormalSaveIntegrityError(
                CODE_MANIFEST_NOT_READY,
                "当前没有与 Draft 版本匹配的 ready 附件清单，请先准备附件。",
            )
        if client_manifest_id is not None and str(client_manifest_id) != str(current["manifest_id"]):
            raise FormalSaveStaleError(
                CODE_MANIFEST_STALE,
                "附件清单已过期，请刷新后重试。",
            )
        plan = manifest_svc._build_plan(draft_row, validation_result)
        if str(plan["manifest_fingerprint"]) != str(current["manifest_fingerprint"]):
            raise FormalSaveIntegrityError(
                CODE_MANIFEST_FINGERPRINT,
                "附件清单指纹校验失败，请重新准备附件。",
            )
        # Manifest must belong to the SAME service order + report date as the
        # current Draft (compare the stored manifest row, not the rebuilt plan
        # which trivially matches the Draft).
        if int(current["service_order_id"]) != int(draft_row.get("service_order_id", 0)):
            raise FormalSaveIntegrityError(CODE_INTEGRITY, "附件清单服务工单不匹配。")
        if str(current["report_date"]) != str(draft_row.get("report_date") or ""):
            raise FormalSaveIntegrityError(CODE_INTEGRITY, "附件清单报告日期不匹配。")

        # 5. Source integrity: original source files must still hash-match.
        manifest_svc._verify_ready_manifest_sources(draft_row, plan, current)

        # 6. Prepared asset integrity: files exist + prepared_sha256 match.
        assets = self._verify_prepared_assets(draft_row, current["manifest_id"])

        # 7. Compliance final gate (per-source granularity).
        self._verify_compliance(current["manifest_id"])

        # 8. Deterministic field mapping.
        mapping = self._build_formal_mapping(draft_row)

        # 9. Claim the exactly-once commit row (or reuse a recoverable one).
        commit_id = self._claim_commit(draft_row, current, plan)

        # 10. Materialize formal attachments (recorded for recovery).
        formal_files, attachment_rows = self._materialize_formal_files(
            draft_row, current["manifest_id"], assets, plan,
        )
        mapping["attachment_rows"] = attachment_rows

        # 11. Single DB transaction.
        try:
            report_id = self._insert_formal_report(draft_row, mapping, plan)
            self._insert_formal_workers(draft_id, report_id, mapping["workers"])
            for row in mapping["attachment_rows"]:
                self.db.execute(
                    """
                    insert into service_report_attachments (
                        report_id, category, original_filename, stored_filename,
                        content_type, uploaded_by, uploaded_at
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id, row["category"], row["original_filename"],
                        row["stored_filename"], row["content_type"], self.user_id, _now_iso(),
                    ),
                )
            self.db.execute(
                """
                update ai_daily_report_formal_commits
                set service_report_id = ?, status = 'committed', committed_at = ?, failure_code = NULL
                where id = ?
                """,
                (report_id, _now_iso(), commit_id),
            )
            cursor = self.db.execute(
                """
                update ai_daily_report_drafts
                set status = 'saved', saved_report_id = ?, updated_at = ?
                where id = ? and status = 'confirmed'
                """,
                (report_id, _now_iso(), draft_id),
            )
            if cursor.rowcount != 1:
                raise FormalSaveStaleError(
                    CODE_DRAFT_STATE,
                    "Draft 状态在保存期间发生变化，未写入正式日报。",
                )
            self._log_action(
                draft_id, report_id,
                mapping["order_number"], mapping["report_date"],
                "创建 AI 工作日报（Formal Save）",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            # Remove copied formal files recorded for this commit (best effort).
            self._cleanup_formal_files(formal_files)
            raise

        return {"status": "created", "service_report_id": int(report_id)}

    # ─── Recovery ──────────────────────────────────────────────────────────

    def recover_pending(self, draft_id: int) -> None:
        """Recover any 'committing'/'failed' commit row for this draft.

        - A committing row whose files were recorded but no report exists is
          cleaned up (files removed) so a retry can claim a fresh commit.
        - committed rows are left untouched (exactly-once result wins).
        """
        rows = self.db.execute(
            "select * from ai_daily_report_formal_commits where draft_id = ? and status != 'committed'",
            (draft_id,),
        ).fetchall()
        for row in rows:
            if row["service_report_id"]:
                continue
            self._cleanup_formal_files(json.loads(row["formal_files"] or "{}"))
            self.db.execute(
                "update ai_daily_report_formal_commits set status = 'failed', failure_code = 'recovered_after_crash', updated_at = ? where id = ?",
                (_now_iso(), row["id"]),
            )
        if rows:
            self.db.commit()

    # ─── Exactly-once claim ────────────────────────────────────────────────

    COMMIT_STALE_SECONDS = 300

    def _is_stale_commit(self, started_at: Any) -> bool:
        """A committing row older than COMMIT_STALE_SECONDS is treated as a
        crashed process (safe to recover); fresher rows belong to an in-flight
        writer and must not be touched."""
        try:
            started = datetime.fromisoformat(str(started_at))
        except (TypeError, ValueError):
            return True
        age = (datetime.now(timezone.utc) - started.replace(tzinfo=timezone.utc)).total_seconds()
        return age > self.COMMIT_STALE_SECONDS

    def _existing_report(self, draft_id: int) -> Optional[int]:
        row = self.db.execute(
            "select saved_report_id from ai_daily_report_drafts where id = ?",
            (draft_id,),
        ).fetchone()
        if row and row["saved_report_id"]:
            return int(row["saved_report_id"])
        commit = self.db.execute(
            "select service_report_id from ai_daily_report_formal_commits where draft_id = ? and status = 'committed'",
            (draft_id,),
        ).fetchone()
        if commit and commit["service_report_id"]:
            return int(commit["service_report_id"])
        return None

    def _claim_commit(self, draft_row: Dict[str, Any], current: Dict[str, Any], plan: Dict[str, Any]) -> int:
        draft_id = int(draft_row["id"])
        now = _now_iso()
        existing = self.db.execute(
            "select id, status, service_report_id from ai_daily_report_formal_commits where draft_id = ?",
            (draft_id,),
        ).fetchone()
        if existing:
            if existing["status"] == "committed" and existing["service_report_id"]:
                # Another request already finished; exactly-once result wins.
                return int(existing["id"])
            if existing["status"] == "committing":
                if self._is_stale_commit(existing["started_at"]):
                    # A previous process crashed mid-flight; recover its files,
                    # mark failed, then fall through to reuse the row.
                    self.recover_pending(draft_id)
                else:
                    # Genuine concurrent request: do NOT recover (that would
                    # delete the in-flight writer's files). Surface 409 and let
                    # the client retry after the winner commits.
                    raise FormalSaveCommitConflictError(
                        CODE_COMMIT_CONFLICT,
                        "正式保存正在进行中，请稍候重试。",
                    )
            # failed row: reuse it (keeps id/created_at for audit).
            self.db.execute(
                """
                update ai_daily_report_formal_commits
                set manifest_id = ?, draft_version = ?, validation_fingerprint = ?,
                    manifest_fingerprint = ?, manifest_snapshot = ?, fields_provenance = ?,
                    status = 'committing', failure_code = NULL, formal_files = '{}',
                    started_at = ?
                where id = ?
                """,
                (
                    str(current["manifest_id"]),
                    int(draft_row.get("draft_version", 1)),
                    str(current["validation_fingerprint"]),
                    str(current["manifest_fingerprint"]),
                    _canonical_json(self._manifest_snapshot(current, plan)),
                    _canonical_json(self._fields_provenance_cache),
                    now,
                    int(existing["id"]),
                ),
            )
            self.db.commit()
            return int(existing["id"])
        cursor = self.db.execute(
            """
            insert or ignore into ai_daily_report_formal_commits (
                commit_id, draft_id, manifest_id, draft_version, validation_fingerprint,
                manifest_fingerprint, manifest_snapshot, fields_provenance,
                service_report_id, status, failure_code, formal_files,
                created_by, started_at, committed_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'committing', NULL, '{}', ?, ?, NULL)
            """,
            (
                "fc_" + uuid.uuid4().hex,
                draft_id,
                str(current["manifest_id"]),
                int(draft_row.get("draft_version", 1)),
                str(current["validation_fingerprint"]),
                str(current["manifest_fingerprint"]),
                _canonical_json(self._manifest_snapshot(current, plan)),
                _canonical_json(self._fields_provenance_cache),
                self.user_id,
                now,
            ),
        )
        if cursor.rowcount != 1:
            # Concurrent request claimed it first. Never recover here: the
            # winner is mid-flight and its files must not be deleted.
            raise FormalSaveCommitConflictError(
                CODE_COMMIT_CONFLICT,
                "正式保存正在进行中，请稍候重试。",
            )
        self.db.commit()
        row = self.db.execute(
            "select id from ai_daily_report_formal_commits where draft_id = ?",
            (draft_id,),
        ).fetchone()
        return int(row["id"])

    # ─── Verification helpers ──────────────────────────────────────────────

    def _verify_prepared_assets(self, draft_row: Dict[str, Any], manifest_id: str) -> Dict[str, Dict[str, Any]]:
        """Verify every prepared asset exists and still hashes to prepared_sha256."""
        rows = self.db.execute(
            "select * from ai_daily_report_prepared_assets where manifest_id = ?",
            (manifest_id,),
        ).fetchall()
        assets: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            rel = str(row["prepared_relative_path"] or "")
            if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise FormalSaveIntegrityError(CODE_PATH_UNSAFE, "附件文件路径不安全。")
            try:
                full = (self.data_dir / rel).resolve()
                full.relative_to(self.data_dir)
            except (ValueError, OSError) as exc:
                raise FormalSaveIntegrityError(CODE_PATH_UNSAFE, "附件文件路径超出数据目录。") from exc
            if not full.is_file():
                raise FormalSaveIntegrityError(
                    CODE_SOURCE_CHANGED,
                    "附件物理文件缺失，请重新准备附件。",
                )
            actual = file_sha256(full)
            if actual != str(row["prepared_sha256"]):
                raise FormalSaveIntegrityError(
                    CODE_SOURCE_CHANGED,
                    "附件文件哈希已变化，请重新准备附件。",
                )
            assets[str(row["asset_id"])] = {
                "prepared_relative_path": rel,
                "prepared_sha256": str(row["prepared_sha256"]),
                "content_type": str(row["content_type"] or "application/octet-stream"),
                "file_size": int(row["file_size"] or 0),
                "absolute_path": full,
            }
        return assets

    def _verify_compliance(self, manifest_id: str) -> None:
        rows = self.db.execute(
            """
            select source_id, provider, compliance_review_required, compliance_status
            from ai_daily_report_manifest_sources
            where manifest_id = ? and compliance_review_required = 1
            """,
            (manifest_id,),
        ).fetchall()
        for row in rows:
            if str(row["compliance_status"]) != "reviewed":
                raise FormalSaveComplianceError(
                    CODE_COMPLIANCE_BLOCKED,
                    f"里程佐证合规审查未完成（{str(row['provider'] or 'unknown')}），无法正式保存。",
                )

    # ─── Mapping ───────────────────────────────────────────────────────────

    def _build_formal_mapping(self, draft_row: Dict[str, Any]) -> Dict[str, Any]:
        draft_data = json.loads(draft_row["draft_data"] or "{}")
        workers_raw = draft_data.get("workers", [])
        formal_workers = [to_formal_worker(w) for w in workers_raw if isinstance(w, dict)]
        if not formal_workers:
            raise FormalSaveIntegrityError(CODE_INTEGRITY, "Draft 中没有可保存的服务人员。")

        mileage_method, mileage_source = derive_mileage_billing_method(draft_data)
        departure_address = derive_departure_address(workers_raw)
        cabinet_number = derive_cabinet_number(draft_data.get("work_items"))
        # v0.1.243: normalize ISO photo-timeline timestamps to 'HH:MM' so the
        # service report form's hour/minute dropdowns bind correctly.
        arrival_time = normalize_report_time(draft_data.get("arrival_time"))
        departure_time = normalize_report_time(draft_data.get("departure_time"))

        total_service_hours = round(
            rounded_report_service_hours(arrival_time, departure_time) * len(formal_workers), 2
        )
        total_time = compute_total_time(formal_workers)
        driving_miles = compute_driving_miles(formal_workers, mileage_method)
        service_description = format_work_items(
            draft_data.get("service_description"), draft_data.get("work_items")
        )

        self._fields_provenance_cache = build_fields_provenance(
            draft_data, formal_workers, self.user_id,
            departure_address, cabinet_number, mileage_method, mileage_source,
        )

        order_row = self.db.execute(
            "select order_number from service_orders where id = ?",
            (draft_row["service_order_id"],),
        ).fetchone()
        order_number = str(order_row["order_number"]) if order_row else ""

        return {
            "draft_data": draft_data,
            "workers": formal_workers,
            "report_date": str(draft_row.get("report_date") or ""),
            "order_number": order_number,
            "mileage_method": mileage_method,
            "departure_address": departure_address,
            "cabinet_number": cabinet_number,
            "arrival_time": arrival_time,
            "departure_time": departure_time,
            "total_service_hours": total_service_hours,
            "total_time": total_time,
            "driving_miles": driving_miles,
            "service_description": service_description,
            "attachment_rows": [],
        }

    def _manifest_snapshot(self, current: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
        """Compact, deterministic manifest snapshot for provenance persistence."""
        return {
            "manifest_version": 1,
            "manifest_id": current["manifest_id"],
            "draft_id": current["draft_id"],
            "draft_version": current["draft_version"],
            "validation_fingerprint": current["validation_fingerprint"],
            "manifest_fingerprint": current["manifest_fingerprint"],
            "service_order_id": current["service_order_id"],
            "report_date": current["report_date"],
            "photo_set_fingerprint": current.get("photo_set_fingerprint"),
            "sources": [
                {
                    "source_type": s["source_type"],
                    "source_identity": s["source_identity"],
                    "source_relative_path": s["source_relative_path"],
                    "source_sha256": s["source_sha256"],
                    "materialize": s["materialize"],
                    "compliance_review_required": s["compliance_review_required"],
                    "roles": [
                        {
                            "role_type": r["role_type"],
                            "category": r["category"],
                            "materialization_required": r["materialization_required"],
                            "purpose": r["purpose"],
                            "visibility": r["visibility"],
                        }
                        for r in s["roles"]
                    ],
                }
                for s in plan["sources"]
            ],
        }

    # ─── Formal files materialization ──────────────────────────────────────

    def _materialize_formal_files(
        self,
        draft_row: Dict[str, Any],
        manifest_id: str,
        assets: Dict[str, Dict[str, Any]],
        plan: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Copy each materialize-required asset into formal attachment storage.

        One physical copy per asset (dedup by prepared_sha256); one DB row per
        materialize role category (sealed Phase 9 design). arrival/departure
        roles never materialize here.

        Returns (recorded_files, attachment_rows).
        """
        draft_id = int(draft_row["id"])
        report_date = str(draft_row.get("report_date") or "")
        order_number = str(plan.get("order_number") or "")

        # asset (by source_sha256) -> sorted materialize categories.
        categories_by_sha: Dict[str, List[str]] = {}
        for src in plan["sources"]:
            if not src["materialize"]:
                continue
            for role in src["roles"]:
                if not role.get("materialization_required"):
                    continue
                category = ROLE_TO_CATEGORY.get(role["role_type"])
                if category is None:
                    continue
                key = str(src["source_sha256"])
                categories_by_sha.setdefault(key, [])
                if category not in categories_by_sha[key]:
                    categories_by_sha[key].append(category)

        sha_to_source: Dict[str, Dict[str, Any]] = {
            str(s["source_sha256"]): s for s in plan["sources"] if s["materialize"]
        }

        # Plan every (category, token, final, tmp) path BEFORE touching the
        # filesystem and persist the full planned set durably in the commit
        # journal. Crash recovery can then deterministically find and remove
        # any file that may have been os.replace()-ed even if the follow-up
        # journal update never happened (GATE 3, option A: no orphan window).
        entries: List[Dict[str, Any]] = []
        for sha in sorted(categories_by_sha.keys()):
            categories = sorted(categories_by_sha[sha])
            asset_info = self._asset_by_sha(assets, manifest_id, sha)
            asset_path = asset_info["absolute_path"]
            prepared_sha = asset_info["prepared_sha256"]
            ext = asset_path.suffix.lower().lstrip(".") or "bin"
            src = sha_to_source[sha]
            original_filename = Path(str(src["source_relative_path"])).name or "attachment"
            for category in categories:
                stored_dir_rel = self._formal_attachment_dir_relative(
                    report_date, order_number, category
                )
                target_dir = (self.report_attachments_dir / stored_dir_rel).resolve()
                if not target_dir.is_relative_to(self.report_attachments_dir):
                    raise FormalSaveIntegrityError(CODE_PATH_UNSAFE, "正式附件目录超出根目录。")
                token = secrets.token_hex(12)
                basename = f"{token}.{ext}"
                tmp_name = f".{token}.{ext}.tmp"
                stored_final = str(Path(stored_dir_rel) / basename).replace("\\", "/")
                stored_tmp = str(Path(stored_dir_rel) / tmp_name).replace("\\", "/")
                entries.append({
                    "sha": sha,
                    "category": category,
                    "target_dir": target_dir,
                    "asset_path": asset_path,
                    "prepared_sha": prepared_sha,
                    "original_filename": original_filename,
                    "content_type": asset_info["content_type"],
                    "tmp_path": target_dir / tmp_name,
                    "final_path": target_dir / basename,
                    "stored_final": stored_final,
                    "stored_tmp": stored_tmp,
                })

        recorded: Dict[str, Any] = {
            "planned": sorted({e["stored_final"] for e in entries} | {e["stored_tmp"] for e in entries}),
            "tmp": [],
            "files": [],
        }
        self._commit_formal_files_record(manifest_id, recorded)

        attachment_rows: List[Dict[str, Any]] = []
        for entry in entries:
            entry["target_dir"].mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry["asset_path"], entry["tmp_path"])
            recorded["tmp"].append(entry["stored_tmp"])
            if file_sha256(entry["tmp_path"]) != entry["prepared_sha"]:
                entry["tmp_path"].unlink(missing_ok=True)
                recorded["tmp"] = [p for p in recorded["tmp"] if p != entry["stored_tmp"]]
                raise FormalSaveIntegrityError(
                    CODE_SOURCE_CHANGED,
                    "正式附件复制后哈希校验失败。",
                )
            os.replace(entry["tmp_path"], entry["final_path"])
            recorded["tmp"] = [p for p in recorded["tmp"] if p != entry["stored_tmp"]]
            recorded["files"].append(entry["stored_final"])
            attachment_rows.append({
                "category": entry["category"],
                "original_filename": entry["original_filename"],
                "stored_filename": entry["stored_final"],
                "content_type": entry["content_type"],
            })
            self._commit_formal_files_record(manifest_id, recorded)
        return recorded, attachment_rows

    # ─── DB transaction pieces ─────────────────────────────────────────────

    def _insert_formal_report(
        self, draft_row: Dict[str, Any], mapping: Dict[str, Any], plan: Dict[str, Any]
    ) -> int:
        workers = mapping["workers"]
        draft_data = mapping["draft_data"]
        arrival_ref = self._photo_ref_from_plan(plan, "arrival_reference")
        departure_ref = self._photo_ref_from_plan(plan, "departure_reference")
        cursor = self.db.execute(
            """
            insert into service_reports (
                service_order_id, report_date, actual_work_date, total_service_hours,
                travel_hours, public_transport_hours, driving_miles, mileage_billing_method,
                departure_address, site_address, total_time, cabinet_number,
                arrival_time, departure_time, service_description, report_writer_id,
                created_by, created_at, updated_at,
                ai_generated, ai_draft_id,
                arrival_time_source, departure_time_source,
                arrival_photo_relative_path, arrival_photo_hash,
                departure_photo_relative_path, departure_photo_hash
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(draft_row["service_order_id"]),
                mapping["report_date"],
                mapping["report_date"],
                mapping["total_service_hours"],
                round(sum(w["travel_hours"] for w in workers), 2),
                round(sum(w["public_transport_hours"] for w in workers), 2),
                mapping["driving_miles"],
                mapping["mileage_method"],
                mapping["departure_address"],
                str(draft_data.get("site_address") or "").strip(),
                mapping["total_time"],
                mapping["cabinet_number"],
                mapping["arrival_time"],
                mapping["departure_time"],
                mapping["service_description"],
                None,  # report_writer_id (sealed Q1: not in Draft -> NULL)
                self.user_id,
                _now_iso(),
                _now_iso(),
                1,  # ai_generated
                int(draft_row["id"]),
                str(draft_data.get("arrival_time_source") or "") or None,
                str(draft_data.get("departure_time_source") or "") or None,
                arrival_ref["relative_path"] if arrival_ref else None,
                arrival_ref["sha256"] if arrival_ref else None,
                departure_ref["relative_path"] if departure_ref else None,
                departure_ref["sha256"] if departure_ref else None,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _photo_ref_from_plan(plan: Dict[str, Any], role_type: str) -> Optional[Dict[str, Any]]:
        """Return photo provenance (relative_path + sha256) for a provenance role."""
        for src in plan["sources"]:
            for role in src["roles"]:
                if role.get("role_type") == role_type:
                    return {
                        "photo_id": src.get("source_photo_id"),
                        "relative_path": src.get("source_relative_path"),
                        "sha256": src.get("source_sha256"),
                    }
        return None

    def _insert_formal_workers(self, draft_id: int, report_id: int, workers: List[Dict[str, Any]]) -> None:
        for worker in workers:
            self.db.execute(
                """
                insert into service_report_workers (
                    report_id, user_id, driving_miles, travel_mode, travel_hours,
                    public_transport_hours, work_description
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    int(worker["user_id"]),
                    worker["driving_miles"],
                    worker["travel_mode"],
                    worker["travel_hours"],
                    worker["public_transport_hours"],
                    worker["work_description"],
                ),
            )

    def _log_action(
        self, draft_id: int, report_id: int,
        order_number: str, report_date: str, summary: str,
    ) -> None:
        user_name = ""
        row = self.db.execute("select name from users where id = ?", (self.user_id,)).fetchone()
        if row:
            user_name = str(row["name"] or "")
        self.db.execute(
            """
            insert into audit_logs (
                user_id, user_name, action, entity_type, entity_id, entity_label, summary, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.user_id,
                user_name,
                "create",
                "service_report",
                report_id,
                f"{order_number} / {report_date}",
                summary,
                _now_iso(),
            ),
        )

    # ─── Small helpers ─────────────────────────────────────────────────────

    def _asset_by_sha(self, assets: Dict[str, Dict[str, Any]], manifest_id: str, sha: str) -> Dict[str, Any]:
        for info in assets.values():
            if info["prepared_sha256"] == sha:
                return info
        # Fallback: direct lookup by prepared_sha256.
        row = self.db.execute(
            "select * from ai_daily_report_prepared_assets where manifest_id = ? and prepared_sha256 = ?",
            (manifest_id, sha),
        ).fetchone()
        if row:
            rel = str(row["prepared_relative_path"])
            full = (self.data_dir / rel).resolve()
            return {
                "prepared_relative_path": rel,
                "prepared_sha256": str(row["prepared_sha256"]),
                "content_type": str(row["content_type"] or "application/octet-stream"),
                "file_size": int(row["file_size"] or 0),
                "absolute_path": full,
            }
        raise FormalSaveIntegrityError(CODE_SOURCE_CHANGED, "附件物理文件缺失，请重新准备附件。")

    def _formal_attachment_dir_relative(
        self, report_date: str, order_number: str, category: str
    ) -> str:
        if category not in CATEGORY_FOLDERS:
            raise FormalSaveIntegrityError(CODE_INTEGRITY, f"未知的正式附件类别 {category!r}。")
        if not order_number:
            raise FormalSaveIntegrityError(CODE_INTEGRITY, "缺少工单号，无法生成正式附件路径。")
        date_part = (report_date or "").replace("-", "") or "unknown-date"
        return Path(order_number, date_part, CATEGORY_FOLDERS[category]).as_posix()

    def _commit_formal_files_record(self, manifest_id: str, recorded: Dict[str, Any]) -> None:
        self.db.execute(
            "update ai_daily_report_formal_commits set formal_files = ? where manifest_id = ? and status = 'committing'",
            (_canonical_json(recorded), manifest_id),
        )
        self.db.commit()

    def _cleanup_formal_files(self, recorded: Dict[str, Any]) -> None:
        if not isinstance(recorded, dict):
            return
        paths = []
        for key in ("planned", "files", "tmp", "tmp_files"):
            value = recorded.get(key)
            if isinstance(value, list):
                paths.extend(value)
        for rel in paths:
            if not isinstance(rel, str) or not rel:
                continue
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                continue
            try:
                target = (self.report_attachments_dir / rel).resolve()
                if target.is_relative_to(self.report_attachments_dir) and target.is_file():
                    target.unlink(missing_ok=True)
            except (ValueError, OSError):
                continue
