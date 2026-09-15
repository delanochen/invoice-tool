"""AI Daily Report - AttachmentManifestService (Phase 8)

Frozen Attachment Preparation / Manifest layer between Phase 7 Validation
and Phase 9 Formal Save.

BOUNDARY LOCK (Phase 8):
- Does NOT INSERT/UPDATE service_reports / service_report_workers /
  service_report_attachments.
- Does NOT call DeepSeek / Vision / Google Routes / Google Static Maps.
- Does NOT regenerate mileage evidence, re-scan photos, re-classify photos,
  or re-infer arrival/departure.
- Only consumes stable references already stored in the Draft.

DESIGN (sealed):
- PreparedAsset          = physical file (dedup by prepared_sha256)
- ManifestSource         = source/provenance relation (asset_id NULL = provenance-only)
- ManifestRole           = business role (references source_id)
- Manifest               = frozen frozen header (idempotent by
                           draft_id + draft_version + validation_fingerprint
                           + manifest_fingerprint)

STATE MACHINE:
  Draft -> Phase 7 Validation -> Confirm -> Prepare Attachments -> Phase 9 Save
  Prepare is only allowed when draft status == 'confirmed'.

CRASH SAFETY:
  DB preparing row first (with expected_plan) -> filesystem .tmp/.part
  -> verify all SHA256 -> atomic rename -> freshness recheck
  -> persist assets/sources/roles -> status 'ready'.
  Any crash point is recoverable; never expose partial-ready.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1

# Internal error codes (safe, no paths/secrets in user-facing messages)
ERROR_SOURCE_NOT_FOUND = "source_not_found"
ERROR_SOURCE_CHANGED = "source_file_changed"
ERROR_PATH_UNSAFE = "path_unsafe"
ERROR_PHOTO_NOT_IN_DISCOVERED_SET = "photo_not_in_discovered_set"
ERROR_TOO_MANY_SERVICE_PHOTOS = "too_many_service_photos"
ERROR_EVIDENCE_STALE = "evidence_stale"
ERROR_EVIDENCE_MISMATCH = "evidence_mismatch"
ERROR_VALIDATION_BLOCKED = "validation_cannot_proceed"
ERROR_STALE_MANIFEST = "manifest_stale"
ERROR_NOT_FOUND = "manifest_not_found"
ERROR_STATE = "manifest_state_error"

# Stage names under DATA_DIR/ai-daily-report-drafts/<draft_id>/
PREPARED_DIR_NAME = "prepared"
QUARANTINE_DIR_NAME = "_quarantine"
EVIDENCE_SUBDIR = "mileage"  # matches Phase 3B <draft_id>/mileage/ layout

# compliance constants
COMPLIANCE_PROVIDER_GOOGLE_STATIC_MAPS = "google_static_maps"
COMPLIANCE_CONTENT_MAP = "map_image+route_overlay"
COMPLIANCE_STATUS_NA = "na"
COMPLIANCE_STATUS_REVIEW_REQUIRED = "review_required"


def _canonical_json(obj: Any) -> str:
    """Stable JSON serialization: sort_keys, no whitespace, ensure_ascii=False."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """Compute SHA256 of a file, chunked (1 MiB)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Exceptions ─────────────────────────────────────────────────────────────

class ManifestError(Exception):
    """Base manifest error. code is a safe internal error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ManifestValidationBlockedError(ManifestError):
    """Phase 7 validation cannot proceed (ERROR unresolved). -> HTTP 422."""


class ManifestIntegrityError(ManifestError):
    """Source integrity / path / count failure. -> HTTP 422."""


class ManifestStaleError(ManifestError):
    """Draft changed during prepare (optimistic lock). -> HTTP 409."""


class ManifestNotFoundError(ManifestError):
    """Manifest or asset not found. -> HTTP 404."""


# ─── Plan helpers ───────────────────────────────────────────────────────────

def _photo_source_identity(photo_id: str, relative_path: str) -> str:
    """Stable provenance identity for a photo source.

    Includes relative_path so that two distinct source paths sharing the same
    SHA256 (photo_id == hash) remain distinct provenance rows.
    """
    return f"photo:{photo_id}:{relative_path}"


def _evidence_source_identity(evidence_id: str) -> str:
    return f"evidence:{evidence_id}"


# ─── Service ────────────────────────────────────────────────────────────────

class AttachmentManifestService:
    """Build, persist, materialize and query Attachment Manifests (Phase 8).

    Args:
        db_connection: Flask/SQLite connection with execute() (dict rows).
        shared_photos_root: absolute path to shared-photos root.
        data_dir: absolute DATA_DIR (staging lives under
                  <data_dir>/ai-daily-report-drafts/<draft_id>/prepared/).
        user_id: current user id (for created_by audit).
    """

    def __init__(
        self,
        db_connection,
        shared_photos_root: str,
        data_dir: str,
        user_id: int,
    ):
        self.db = db_connection
        self.shared_photos_root = Path(shared_photos_root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.user_id = user_id

    # ─── Public API ───────────────────────────────────────────────────────

    def prepare(self, draft_row: Dict[str, Any], validation_result) -> Dict[str, Any]:
        """Prepare (or reuse) a ready manifest for a confirmed draft.

        Args:
            draft_row: dict row from ai_daily_report_drafts (must be confirmed).
            validation_result: server-side Phase 7 ValidationResult (fresh).

        Returns:
            manifest dict (status ready, or reused ready manifest).

        Raises:
            ManifestValidationBlockedError: can_proceed == False (-> 422).
            ManifestIntegrityError: source/path/count integrity failure (-> 422).
            ManifestStaleError: draft changed concurrently (-> 409).
        """
        if not validation_result.can_proceed:
            raise ManifestValidationBlockedError(
                ERROR_VALIDATION_BLOCKED,
                "Phase 7 校验未通过（存在未解决的 ERROR），无法准备附件清单。",
            )

        plan = self._build_plan(draft_row, validation_result)

        # Idempotency: reuse existing ready manifest with identical fingerprint.
        existing = self._find_ready_manifest(plan)
        if existing is not None:
            # Reuse only when every materialized SOURCE is still byte-identical:
            # a tampered original must not be silently served by an old manifest.
            self._verify_ready_manifest_sources(draft_row, plan, existing)
            logger.info("Manifest reuse: draft=%s fp=%s", draft_row["id"], existing["manifest_id"])
            return self._manifest_row_to_dict(existing)

        # Recover or fail any stale 'preparing' manifest from a previous crash.
        self._recover_or_fail_previous(draft_row["id"])

        manifest_id = self._insert_preparing(plan)
        logger.info("Manifest preparing: draft=%s manifest=%s", draft_row["id"], manifest_id)
        try:
            materialized = self._materialize_all(draft_row, manifest_id, plan)
            self._check_freshness(draft_row["id"], plan)
            self._persist_assets_sources_roles(manifest_id, plan, materialized)
            self._mark_ready(manifest_id)
        except (ManifestIntegrityError, ManifestStaleError, OSError) as exc:
            self._mark_failed(manifest_id)
            logger.warning("Manifest failed: draft=%s manifest=%s reason=%s", draft_row["id"], manifest_id, exc)
            if isinstance(exc, (ManifestIntegrityError, ManifestStaleError)):
                raise
            raise ManifestIntegrityError(ERROR_SOURCE_NOT_FOUND, f"附件准备失败：{exc}") from exc

        row = self._get_manifest_row(manifest_id)
        logger.info("Manifest ready: draft=%s manifest=%s", draft_row["id"], manifest_id)
        return self._manifest_row_to_dict(row)

    def get_current_manifest(self, draft_row: Dict[str, Any], validation_result) -> Optional[Dict[str, Any]]:
        """Return current_applicable_manifest matching draft_version +
        validation_fingerprint. Stale/cancelled/failed never returned here.

        Phase 8 sealed semantics (unchanged): a ready manifest matching the
        current draft_version + validation_fingerprint is returned regardless
        of Draft status, so saved/reopened Drafts can still audit / preview /
        history the manifest. The confirmed-only requirement is enforced by the
        Phase 9 Formal Save gate itself (FormalSaveService + formal-save route),
        never by this Phase 8 accessor.
        """
        row = self.db.execute(
            """
            select * from ai_daily_report_attachment_manifests
            where draft_id = ? and draft_version = ?
              and validation_fingerprint = ? and status = 'ready'
            order by created_at desc, id desc
            limit 1
            """,
            (draft_row["id"], draft_row.get("draft_version", 1), validation_result.validation_fingerprint),
        ).fetchone()
        return self._manifest_row_to_dict(dict(row)) if row else None

    def get_manifest_history(self, draft_id: int) -> List[Dict[str, Any]]:
        """Return all manifests for a draft (newest first), for audit/history UI."""
        rows = self.db.execute(
            """
            select * from ai_daily_report_attachment_manifests
            where draft_id = ? order by created_at desc, id desc
            """,
            (draft_id,),
        ).fetchall()
        return [self._manifest_row_to_dict(dict(r)) for r in rows]

    def get_asset(self, draft_id: int, manifest_id: str, asset_id: str) -> Tuple[Path, str]:
        """Resolve a prepared asset file with full confinement.

        Returns (absolute path, content_type). Raises ManifestNotFoundError.
        """
        manifest = self._get_manifest_row(manifest_id)
        if not manifest or manifest["draft_id"] != draft_id:
            raise ManifestNotFoundError(ERROR_NOT_FOUND, "Manifest 不存在或不属于该 Draft。")
        asset = self.db.execute(
            "select * from ai_daily_report_prepared_assets where asset_id = ? and manifest_id = ?",
            (asset_id, manifest_id),
        ).fetchone()
        if not asset:
            raise ManifestNotFoundError(ERROR_NOT_FOUND, "Asset 不存在。")
        rel = str(asset["prepared_relative_path"] or "")
        if not rel:
            raise ManifestNotFoundError(ERROR_NOT_FOUND, "Asset 路径缺失。")
        try:
            full = (self.data_dir / rel).resolve()
            root = self.data_dir.resolve()
            full.relative_to(root)
        except (ValueError, OSError) as exc:
            raise ManifestNotFoundError(ERROR_PATH_UNSAFE, "Asset 路径不安全。") from exc
        if not full.is_file():
            raise ManifestNotFoundError(ERROR_SOURCE_NOT_FOUND, "Asset 文件不存在。")
        content_type = asset["content_type"] or "application/octet-stream"
        return full, content_type

    def cancel(self, draft_id: int, manifest_id: str) -> Dict[str, Any]:
        """Cancel a manifest: mark cancelled and delete ONLY its staging files.

        Original photos / Phase 3B evidence / formal attachments are never touched.
        """
        manifest = self._get_manifest_row(manifest_id)
        if not manifest or manifest["draft_id"] != draft_id:
            raise ManifestNotFoundError(ERROR_NOT_FOUND, "Manifest 不存在或不属于该 Draft。")
        if manifest["status"] == "ready":
            raise ManifestError(ERROR_STATE, "已 ready 的 Manifest 不能取消，请通过重新准备使其过期。")
        self.db.execute(
            "update ai_daily_report_attachment_manifests set status = 'cancelled', updated_at = ? where manifest_id = ?",
            (_now_iso(), manifest_id),
        )
        self.db.commit()
        self._delete_staging_dir(draft_id, manifest_id)
        self._quarantine_orphans(draft_id)
        return self._manifest_row_to_dict(self._get_manifest_row(manifest_id))

    # ─── Plan building (source resolution + fingerprint) ──────────────────

    def _build_plan(self, draft_row: Dict[str, Any], validation_result) -> Dict[str, Any]:
        """Resolve all sources/roles from stable Draft references (deterministic)."""
        draft_id = draft_row["id"]
        draft_data = self._parse_draft_data(draft_row)

        candidates = {}
        for p in draft_data.get("photo_candidates", []):
            if not isinstance(p, dict) or not p.get("photo_id"):
                continue
            candidates.setdefault(p["photo_id"], []).append(p)
        for pid in candidates:
            candidates[pid].sort(key=lambda p: str(p.get("relative_path") or ""))

        sources: Dict[str, Dict[str, Any]] = {}

        def _ensure_photo_source(photo_id: str, materialize: bool) -> Dict[str, Any]:
            refs = candidates.get(photo_id)
            if not refs:
                raise ManifestIntegrityError(
                    ERROR_PHOTO_NOT_IN_DISCOVERED_SET,
                    f"所选照片 {photo_id[:12]}... 不在 Phase 4 发现集合中。",
                )
            primary = None
            for ref in refs:
                relative_path = str(ref.get("relative_path") or "")
                if not relative_path:
                    raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "照片缺少 relative_path。")
                source_hash = str(ref.get("photo_hash") or photo_id)
                identity = _photo_source_identity(photo_id, relative_path)
                if identity not in sources:
                    sources[identity] = {
                        "source_id": uuid.uuid4().hex,
                        "source_type": "photo",
                        "source_identity": identity,
                        "source_photo_id": photo_id,
                        "source_evidence_id": None,
                        "source_relative_path": relative_path,
                        "source_sha256": source_hash,
                        "materialize": materialize,
                        "compliance_provider": None,
                        "compliance_provider_content": None,
                        "compliance_review_required": 0,
                        "compliance_status": COMPLIANCE_STATUS_NA,
                        "roles": [],
                    }
                elif materialize:
                    sources[identity]["materialize"] = True
                if primary is None:
                    primary = sources[identity]
            return primary

        # Safety photo (role: safety_photo, category self_check)
        safety = draft_data.get("selected_safety_photo")
        if isinstance(safety, dict) and safety.get("photo_id"):
            src = _ensure_photo_source(safety["photo_id"], materialize=True)
            src["roles"].append({
                "source_identity": src["source_identity"],
                "role_type": "safety_photo",
                "category": "self_check",
                "visibility": "client",
                "purpose": "client_report",
                "materialization_required": 1,
                "sort_order": 0,
            })

        # Service photos (role: service_photo, category site, max 10)
        service_photos = [s for s in draft_data.get("selected_service_photos", [])
                          if isinstance(s, dict) and s.get("photo_id")]
        if len(service_photos) > 10:
            raise ManifestIntegrityError(
                ERROR_TOO_MANY_SERVICE_PHOTOS,
                f"施工照片数量 {len(service_photos)} 超过上限 10。",
            )
        for idx, sp in enumerate(service_photos):
            src = _ensure_photo_source(sp["photo_id"], materialize=True)
            src["roles"].append({
                "source_identity": src["source_identity"],
                "role_type": "service_photo",
                "category": "site",
                "visibility": "client",
                "purpose": "client_report",
                "materialization_required": 1,
                "sort_order": 1 + idx,
            })

        # Arrival / Departure references (provenance-only roles; never
        # materialize independently, never auto-create formal attachments).
        for key, role_type, category, sort_order in (
            ("arrival_photo_ref", "arrival_reference", "arrival", 100),
            ("departure_photo_ref", "departure_reference", "departure", 101),
        ):
            pid = draft_data.get(key)
            if not pid:
                continue
            src = _ensure_photo_source(pid, materialize=False)
            src["roles"].append({
                "source_identity": src["source_identity"],
                "role_type": role_type,
                "category": category,
                "visibility": "client",
                "purpose": "timeline_evidence",
                "materialization_required": 0,
                "sort_order": sort_order,
            })

        # Mileage evidence (Phase 3B persisted records only)
        workers_by_evidence = {}
        for w in draft_data.get("workers", []):
            if isinstance(w, dict) and w.get("mileage_evidence_id"):
                workers_by_evidence[str(w["mileage_evidence_id"])] = w
        for ev in draft_data.get("evidence_records", []):
            if not isinstance(ev, dict) or not ev.get("evidence_id"):
                continue
            eid = str(ev["evidence_id"])
            status = str(ev.get("evidence_status") or "")
            if status in ("stale", "failed", "verification_required"):
                raise ManifestIntegrityError(
                    ERROR_EVIDENCE_STALE,
                    f"里程佐证 {eid[:12]}... 状态为 {status}，请回到 Phase 3B 重新生成。",
                )
            # route_fingerprint consistency: evidence must match current worker route
            worker = workers_by_evidence.get(eid)
            if worker is not None:
                worker_fp = str(worker.get("route_fingerprint") or "")
                ev_fp = str(ev.get("route_fingerprint") or "")
                if worker_fp and ev_fp and worker_fp != ev_fp:
                    raise ManifestIntegrityError(
                        ERROR_EVIDENCE_STALE,
                        f"里程佐证 {eid[:12]}... 的路线指纹已变化，请重新生成。",
                    )
            rel = str(ev.get("file_relative_path") or "")
            source_hash = str(ev.get("file_sha256") or "")
            if not rel or not source_hash:
                raise ManifestIntegrityError(ERROR_EVIDENCE_MISMATCH, f"里程佐证 {eid[:12]}... 元数据不完整。")
            identity = _evidence_source_identity(eid)
            if identity not in sources:
                sources[identity] = {
                    "source_id": uuid.uuid4().hex,
                    "source_type": "mileage_evidence",
                    "source_identity": identity,
                    "source_photo_id": None,
                    "source_evidence_id": eid,
                    "source_relative_path": rel,
                    "source_sha256": source_hash,
                    "materialize": True,
                    "compliance_provider": COMPLIANCE_PROVIDER_GOOGLE_STATIC_MAPS,
                    "compliance_provider_content": COMPLIANCE_CONTENT_MAP,
                    "compliance_review_required": 1,
                    "compliance_status": COMPLIANCE_STATUS_REVIEW_REQUIRED,
                    "roles": [],
                }
            src = sources[identity]
            src["roles"].append({
                "source_identity": src["source_identity"],
                "role_type": "mileage_evidence",
                "category": "mileage_proof",
                "visibility": "internal",
                "purpose": "billing_evidence",
                "materialization_required": 1,
                "sort_order": 200 + len(src["roles"]),
            })

        if not sources:
            raise ManifestIntegrityError(ERROR_SOURCE_NOT_FOUND, "Draft 中没有可准备的附件来源。")

        plan = {
            "draft_id": draft_id,
            "draft_version": int(draft_row.get("draft_version", 1)),
            "validation_fingerprint": validation_result.validation_fingerprint,
            "draft_data_hash": validation_result.draft_data_hash,
            "service_order_id": int(draft_row.get("service_order_id", 0)),
            "report_date": str(draft_row.get("report_date") or draft_data.get("report_date") or ""),
            "photo_set_fingerprint": draft_data.get("photo_set_fingerprint"),
            "order_number": self._order_number(int(draft_row.get("service_order_id", 0))),
            "sources": sorted(sources.values(), key=lambda s: s["source_identity"]),
        }
        plan["manifest_fingerprint"] = self._compute_manifest_fingerprint(plan)
        return plan

    def _compute_manifest_fingerprint(self, plan: Dict[str, Any]) -> str:
        """Deterministic fingerprint.

        Includes: manifest_version, draft_id/version, validation_fingerprint,
        service_order_id, report_date, photo_set_fingerprint, full source
        provenance (source_identity + relative_path + sha256), roles
        (source_identity + role_type + category + visibility + purpose +
        materialization_required).

        Excludes: UUIDs (source_id/asset_id), prepared paths, timestamps,
        temp paths.
        """
        sources_payload = sorted(
            [
                {
                    "source_type": s["source_type"],
                    "source_identity": s["source_identity"],
                    "source_relative_path": s["source_relative_path"],
                    "source_sha256": s["source_sha256"],
                }
                for s in plan["sources"]
            ],
            key=lambda x: _canonical_json(x),
        )
        roles_payload = sorted(
            [
                {
                    "source_identity": r["source_identity"],
                    "role_type": r["role_type"],
                    "category": r["category"],
                    "visibility": r["visibility"],
                    "purpose": r["purpose"],
                    "materialization_required": r["materialization_required"],
                }
                for s in plan["sources"]
                for r in s["roles"]
            ],
            key=lambda x: _canonical_json(x),
        )
        payload = {
            "manifest_version": MANIFEST_VERSION,
            "draft_id": plan["draft_id"],
            "draft_version": plan["draft_version"],
            "validation_fingerprint": plan["validation_fingerprint"],
            "service_order_id": plan["service_order_id"],
            "report_date": plan["report_date"],
            "photo_set_fingerprint": plan["photo_set_fingerprint"],
            "sources": sources_payload,
            "roles": roles_payload,
        }
        return _sha256_hex(_canonical_json(payload))

    # ─── Persistence ──────────────────────────────────────────────────────

    def _insert_preparing(self, plan: Dict[str, Any]) -> str:
        manifest_id = uuid.uuid4().hex
        now = _now_iso()
        self.db.execute(
            """
            insert into ai_daily_report_attachment_manifests (
                manifest_id, draft_id, draft_version, service_order_id, report_date,
                manifest_version, validation_fingerprint, draft_data_hash,
                photo_set_fingerprint, status, manifest_fingerprint, expected_plan,
                created_by, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'preparing', ?, ?, ?, ?, ?)
            """,
            (
                manifest_id,
                plan["draft_id"],
                plan["draft_version"],
                plan["service_order_id"],
                plan["report_date"],
                MANIFEST_VERSION,
                plan["validation_fingerprint"],
                plan["draft_data_hash"],
                plan["photo_set_fingerprint"],
                plan["manifest_fingerprint"],
                _canonical_json(plan),
                self.user_id,
                now,
                now,
            ),
        )
        self.db.commit()
        return manifest_id

    def _mark_failed(self, manifest_id: str) -> None:
        self.db.execute(
            "update ai_daily_report_attachment_manifests set status = 'failed', updated_at = ? where manifest_id = ? and status = 'preparing'",
            (_now_iso(), manifest_id),
        )
        self.db.commit()

    def _mark_ready(self, manifest_id: str) -> None:
        self.db.execute(
            "update ai_daily_report_attachment_manifests set status = 'ready', updated_at = ? where manifest_id = ? and status = 'preparing'",
            (_now_iso(), manifest_id),
        )
        self.db.commit()

    def _mark_stale(self, manifest_id: str) -> None:
        self.db.execute(
            "update ai_daily_report_attachment_manifests set status = 'stale', updated_at = ? where manifest_id = ?",
            (_now_iso(), manifest_id),
        )
        self.db.commit()

    def _persist_assets_sources_roles(
        self,
        manifest_id: str,
        plan: Dict[str, Any],
        materialized: Dict[str, Dict[str, Any]],
    ) -> None:
        """Write assets (dedup by prepared_sha256), sources (asset_id nullable)
        and roles. Called once all files are verified."""
        now = _now_iso()
        asset_by_sha: Dict[str, str] = {}
        for sha, info in materialized.items():
            asset_by_sha[sha] = info["asset_id"]
            self.db.execute(
                """
                insert into ai_daily_report_prepared_assets (
                    asset_id, manifest_id, prepared_relative_path, prepared_sha256,
                    content_type, file_size, integrity_status, prepared_at
                ) values (?, ?, ?, ?, ?, ?, 'verified', ?)
                """,
                (
                    info["asset_id"],
                    manifest_id,
                    info["prepared_relative_path"],
                    info["prepared_sha256"],
                    info["content_type"],
                    info["file_size"],
                    now,
                ),
            )
        for src in plan["sources"]:
            asset_id = None
            if src["materialize"]:
                asset_id = asset_by_sha.get(src["source_sha256"])
                if not asset_id:
                    raise ManifestIntegrityError(
                        ERROR_SOURCE_NOT_FOUND,
                        f"来源 {src['source_identity'][:24]}... 缺少已准备好的物理文件。",
                    )
            self.db.execute(
                """
                insert into ai_daily_report_manifest_sources (
                    source_id, manifest_id, source_type, source_identity,
                    source_photo_id, source_evidence_id, source_relative_path,
                    source_sha256, asset_id, provider, provider_content,
                    compliance_review_required, compliance_status
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    src["source_id"],
                    manifest_id,
                    src["source_type"],
                    src["source_identity"],
                    src["source_photo_id"],
                    src["source_evidence_id"],
                    src["source_relative_path"],
                    src["source_sha256"],
                    asset_id,
                    src["compliance_provider"],
                    src["compliance_provider_content"],
                    src["compliance_review_required"],
                    src["compliance_status"],
                ),
            )
            for role in src["roles"]:
                self.db.execute(
                    """
                    insert into ai_daily_report_manifest_roles (
                        manifest_id, source_id, role_type, category,
                        visibility, purpose, materialization_required, sort_order
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest_id,
                        src["source_id"],
                        role["role_type"],
                        role["category"],
                        role["visibility"],
                        role["purpose"],
                        role["materialization_required"],
                        role["sort_order"],
                    ),
                )
        self.db.commit()

    # ─── Materialization ──────────────────────────────────────────────────

    def _materialize_all(
        self,
        draft_row: Dict[str, Any],
        manifest_id: str,
        plan: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        """Copy every materialize-required source into staging.

        Flow: <manifest_id>.tmp/assets/<name>.part -> verify SHA256 ->
        rename -> after ALL verified, atomic rename .tmp -> <manifest_id>.

        Returns {source_sha256: {asset_id, prepared_relative_path,
        prepared_sha256, content_type, file_size}} (dedup by sha256).
        """
        draft_id = draft_row["id"]
        staging = self._staging_root(draft_id)
        staging.mkdir(parents=True, exist_ok=True)
        tmp_dir = staging / f"{manifest_id}.tmp"
        final_dir = staging / manifest_id
        if final_dir.exists():
            # previous crash left a final dir but DB not ready: validate and reuse
            logger.info("Manifest final dir exists (crash recovery): %s", final_dir)
        assets_dir = tmp_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        materialized: Dict[str, Dict[str, Any]] = {}
        for src in plan["sources"]:
            if not src["materialize"]:
                continue
            if src["source_sha256"] in materialized:
                # physical dedup: same content already copied once
                continue
            full = self._resolve_source_path(draft_id, src, plan.get("order_number", ""), plan.get("report_date", ""))
            actual_sha = file_sha256(full)
            if actual_sha != src["source_sha256"]:
                raise ManifestIntegrityError(
                    ERROR_SOURCE_CHANGED,
                    f"来源文件已变化（{src['source_identity'][:24]}...），请重新确认照片/佐证。",
                )
            ext = full.suffix.lower().lstrip(".")
            if not ext:
                ext = "bin"
            asset_name = f"{uuid.uuid4().hex}.{ext}"
            part_path = assets_dir / f"{asset_name}.part"
            shutil.copyfile(full, part_path)
            copy_sha = file_sha256(part_path)
            if copy_sha != actual_sha:
                part_path.unlink(missing_ok=True)
                raise ManifestIntegrityError(ERROR_SOURCE_CHANGED, "复制后的文件哈希校验失败。")
            final_path = assets_dir / asset_name
            os.replace(part_path, final_path)
            materialized[src["source_sha256"]] = {
                "asset_id": uuid.uuid4().hex,
                "prepared_relative_path": f"ai-daily-report-drafts/{draft_id}/prepared/{manifest_id}/assets/{asset_name}",
                "prepared_sha256": copy_sha,
                "content_type": mimetypes.guess_type(full.name)[0] or "application/octet-stream",
                "file_size": final_path.stat().st_size,
            }

        # All materialize-required assets done and verified -> atomic rename.
        if final_dir.exists():
            shutil.rmtree(final_dir)
        os.replace(tmp_dir, final_dir)
        return materialized

    def _resolve_source_path(self, draft_id: int, src: Dict[str, Any], order_number: str = "", report_date: str = "") -> Path:
        """Resolve a source to an absolute path with full confinement."""
        if src["source_type"] == "photo":
            return self._resolve_photo_path(draft_id, src, order_number, report_date)
        return self._resolve_evidence_path(draft_id, src)

    def _order_number(self, service_order_id: int) -> str:
        if not service_order_id:
            return ""
        row = self.db.execute(
            "select order_number from service_orders where id = ?",
            (service_order_id,),
        ).fetchone()
        return str(row["order_number"]) if row and row["order_number"] else ""

    def _resolve_photo_path(self, draft_id: int, src: Dict[str, Any], order_number: str = "", report_date: str = "") -> Path:
        rel = src["source_relative_path"]
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "照片路径不安全。")
        # Semantic confinement: <order_number>/pictures/<report_date>/<file>
        parts = rel.split("/")
        if len(parts) < 3 or parts[1] != "pictures":
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "照片路径不属于 Work Order 照片目录。")
        if order_number and parts[0] != order_number:
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "照片不属于当前服务工单。")
        if report_date and parts[2] != report_date:
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "照片不属于当前报告日期。")
        try:
            root = self.shared_photos_root
            full = (root / rel).resolve()
            full.relative_to(root)
        except (ValueError, OSError) as exc:
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "照片路径超出共享目录。") from exc
        if not full.is_file():
            raise ManifestIntegrityError(ERROR_SOURCE_NOT_FOUND, "照片文件不存在。")
        # symlink escape protection: resolved path must stay under root (done above)
        return full

    def _resolve_evidence_path(self, draft_id: int, src: Dict[str, Any]) -> Path:
        rel = src["source_relative_path"]
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "佐证路径不安全。")
        expected_prefix = f"ai-daily-report-drafts/{draft_id}/mileage/"
        if not rel.startswith(expected_prefix):
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "佐证路径不在 Draft 佐证目录中。")
        try:
            full = (self.data_dir / rel).resolve()
            full.relative_to(self.data_dir)
        except (ValueError, OSError) as exc:
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "佐证路径超出数据目录。") from exc
        if not full.is_file():
            raise ManifestIntegrityError(ERROR_SOURCE_NOT_FOUND, "佐证文件不存在。")
        return full

    # ─── Freshness / optimistic locking ───────────────────────────────────

    def _check_freshness(self, draft_id: int, plan: Dict[str, Any]) -> None:
        """Re-check current draft_version + validation_fingerprint before ready.

        If the draft changed (reopened/modified/confirmed again), the old
        manifest must NOT become ready.
        """
        row = self.db.execute(
            "select draft_version, status, draft_data from ai_daily_report_drafts where id = ?",
            (draft_id,),
        ).fetchone()
        if not row:
            raise ManifestStaleError(ERROR_STALE_MANIFEST, "Draft 已不存在。")
        if row["status"] != "confirmed" or int(row["draft_version"]) != plan["draft_version"]:
            raise ManifestStaleError(ERROR_STALE_MANIFEST, "Draft 在准备期间被修改，清单已过期。")
        draft_data = json.loads(row["draft_data"] or "{}")
        from .validation_engine import ValidationEngine, ValidationContextBuilder
        ctx = ValidationContextBuilder(self.db).build(draft_data)
        result = ValidationEngine().validate(
            draft_data=draft_data,
            context=ctx,
            draft_version=int(row["draft_version"]),
        )
        if result.validation_fingerprint != plan["validation_fingerprint"]:
            raise ManifestStaleError(ERROR_STALE_MANIFEST, "校验指纹已变化，清单已过期。")

    # ─── Idempotency / recovery / quarantine ──────────────────────────────

    def _find_ready_manifest(self, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        row = self.db.execute(
            """
            select * from ai_daily_report_attachment_manifests
            where draft_id = ? and draft_version = ? and validation_fingerprint = ?
              and manifest_fingerprint = ? and status = 'ready'
            order by created_at desc, id desc
            limit 1
            """,
            (plan["draft_id"], plan["draft_version"], plan["validation_fingerprint"], plan["manifest_fingerprint"]),
        ).fetchone()
        return dict(row) if row else None

    def _verify_ready_manifest_sources(
        self,
        draft_row: Dict[str, Any],
        plan: Dict[str, Any],
        manifest_row: Dict[str, Any],
    ) -> None:
        """Re-check byte-integrity of every materialized SOURCE before reuse.

        The fingerprint is derived from Draft data (which may be unchanged
        while the underlying file was tampered with), so a ready manifest can
        only be reused after confirming each materialize source still hashes
        to its recorded source_sha256. Raises ManifestIntegrityError
        (source_file_changed) otherwise; never silently serves stale copies.
        """
        rows = self.db.execute(
            """
            select * from ai_daily_report_manifest_sources
            where manifest_id = ? and asset_id is not null
            """,
            (manifest_row["manifest_id"],),
        ).fetchall()
        for row in rows:
            src = {
                "source_type": row["source_type"],
                "source_identity": row["source_identity"],
                "source_photo_id": row["source_photo_id"],
                "source_evidence_id": row["source_evidence_id"],
                "source_relative_path": row["source_relative_path"],
                "source_sha256": row["source_sha256"],
            }
            full = self._resolve_source_path(
                draft_row["id"],
                src,
                plan.get("order_number", ""),
                plan.get("report_date", ""),
            )
            actual_sha = file_sha256(full)
            if actual_sha != row["source_sha256"]:
                raise ManifestIntegrityError(
                    ERROR_SOURCE_CHANGED,
                    f"来源文件已变化（{src['source_identity'][:24]}...），请重新确认照片/佐证。",
                )

    def _recover_or_fail_previous(self, draft_id: int) -> None:
        """Recover a previous crash: any 'preparing' manifest for this draft.

        - Filesystem complete (all materialize sources verifiable):
          finalize to ready after freshness check.
        - Otherwise: mark failed (files retained for cleanup/quarantine).
        """
        rows = self.db.execute(
            "select * from ai_daily_report_attachment_manifests where draft_id = ? and status = 'preparing'",
            (draft_id,),
        ).fetchall()
        for row in rows:
            manifest_id = row["manifest_id"]
            try:
                plan = json.loads(row["expected_plan"] or "{}")
            except (json.JSONDecodeError, TypeError):
                self._mark_failed(manifest_id)
                continue
            try:
                materialized = self._collect_existing_materialized(draft_id, manifest_id, plan)
                if materialized is None:
                    self._mark_failed(manifest_id)
                    continue
                # freshness recheck before completing recovery
                self._check_freshness(draft_id, plan)
                self._persist_assets_sources_roles(manifest_id, plan, materialized)
                self._mark_ready(manifest_id)
                logger.info("Manifest recovered to ready: %s", manifest_id)
            except (ManifestIntegrityError, ManifestStaleError) as exc:
                self._mark_failed(manifest_id)
                logger.warning("Manifest recovery failed: %s (%s)", manifest_id, exc.code)
        self._quarantine_orphans(draft_id)

    def _collect_existing_materialized(
        self,
        draft_id: int,
        manifest_id: str,
        plan: Dict[str, Any],
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Check existing staging for completed materialization.

        Returns {source_sha256: asset_info} if every materialize source is
        present and hash-verified; None if incomplete.
        """
        staging = self._staging_root(draft_id)
        final_dir = staging / manifest_id
        tmp_dir = staging / f"{manifest_id}.tmp"
        assets_dir = None
        if final_dir.is_dir():
            assets_dir = final_dir / "assets"
        elif tmp_dir.is_dir():
            # files done but dir rename never happened -> complete here
            assets_dir = tmp_dir / "assets"
        if assets_dir is None or not assets_dir.is_dir():
            return None
        result: Dict[str, Dict[str, Any]] = {}
        for src in plan.get("sources", []):
            if not src.get("materialize"):
                continue
            sha = src.get("source_sha256")
            if sha in result:
                continue
            matched = None
            for f in assets_dir.iterdir():
                if not f.is_file() or f.name.endswith(".part"):
                    continue
                if file_sha256(f) == sha:
                    matched = f
                    break
            if matched is None:
                return None
            result[sha] = {
                "asset_id": uuid.uuid4().hex,
                "prepared_relative_path": f"ai-daily-report-drafts/{draft_id}/prepared/{manifest_id}/assets/{matched.name}",
                "prepared_sha256": sha,
                "content_type": mimetypes.guess_type(matched.name)[0] or "application/octet-stream",
                "file_size": matched.stat().st_size,
            }
        # all materialize sources matched -> complete. Ensure final dir layout.
        if final_dir.is_dir():
            if tmp_dir.is_dir():
                shutil.rmtree(tmp_dir)
        else:
            os.replace(tmp_dir, final_dir)
        return result

    def _quarantine_orphans(self, draft_id: int) -> None:
        """Move Phase 8 staging dirs that have no DB manifest row into
        prepared/_quarantine/. Never delete; never touch other directories."""
        staging = self._staging_root(draft_id)
        if not staging.is_dir():
            return
        known = set()
        rows = self.db.execute(
            "select manifest_id from ai_daily_report_attachment_manifests where draft_id = ?",
            (draft_id,),
        ).fetchall()
        for r in rows:
            known.add(r["manifest_id"])
        q_root = staging / QUARANTINE_DIR_NAME
        for entry in staging.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name == QUARANTINE_DIR_NAME:
                continue
            base = name[:-4] if name.endswith(".tmp") else name
            if base in known:
                continue
            q_root.mkdir(parents=True, exist_ok=True)
            target = q_root / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{name}"
            try:
                os.replace(entry, target)
                logger.info("Quarantined orphan staging dir: %s", target)
            except OSError as exc:
                logger.warning("Quarantine move failed for %s: %s", entry, exc)

    def _delete_staging_dir(self, draft_id: int, manifest_id: str) -> None:
        """Delete ONLY this manifest's own staging directory."""
        staging = self._staging_root(draft_id)
        target = staging / manifest_id
        tmp = staging / f"{manifest_id}.tmp"
        for d in (tmp, target):
            if d.is_dir():
                try:
                    resolved = d.resolve()
                    resolved.relative_to(staging.resolve())
                    shutil.rmtree(resolved)
                except (ValueError, OSError) as exc:
                    logger.warning("Staging delete refused for %s: %s", d, exc)

    # ─── Path helpers ─────────────────────────────────────────────────────

    def _staging_root(self, draft_id: int) -> Path:
        if not draft_id or draft_id <= 0:
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "无效的 draft_id。")
        p = (self.data_dir / "ai-daily-report-drafts" / str(draft_id) / PREPARED_DIR_NAME).resolve()
        try:
            p.relative_to(self.data_dir.resolve())
        except ValueError as exc:
            raise ManifestIntegrityError(ERROR_PATH_UNSAFE, "staging 路径不安全。") from exc
        return p

    def _parse_draft_data(self, draft_row: Dict[str, Any]) -> Dict[str, Any]:
        try:
            data = json.loads(draft_row.get("draft_data") or "{}")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            raise ManifestIntegrityError("draft_data_corrupted", "Draft 数据损坏，无法准备附件。")

    def _get_manifest_row(self, manifest_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.execute(
            "select * from ai_daily_report_attachment_manifests where manifest_id = ?",
            (manifest_id,),
        ).fetchone()
        return dict(row) if row else None

    def _manifest_row_to_dict(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize a manifest row for API responses (with counts)."""
        manifest_id = row["manifest_id"]
        asset_count = self._count("ai_daily_report_prepared_assets", "manifest_id", manifest_id)
        source_count = self._count("ai_daily_report_manifest_sources", "manifest_id", manifest_id)
        role_count = self._count("ai_daily_report_manifest_roles", "manifest_id", manifest_id)
        compliance_items = []
        for src in self.db.execute(
            "select source_id, provider, provider_content, compliance_review_required, compliance_status from ai_daily_report_manifest_sources where manifest_id = ?",
            (manifest_id,),
        ).fetchall():
            if src["compliance_review_required"]:
                compliance_items.append({
                    "source_id": src["source_id"],
                    "provider": src["provider"],
                    "provider_content": src["provider_content"],
                    "compliance_review_required": src["compliance_review_required"],
                    "compliance_status": src["compliance_status"],
                })
        return {
            "manifest_id": manifest_id,
            "draft_id": row["draft_id"],
            "draft_version": row["draft_version"],
            "service_order_id": row["service_order_id"],
            "report_date": row["report_date"],
            "manifest_version": row["manifest_version"],
            "status": row["status"],
            "validation_fingerprint": row["validation_fingerprint"],
            "draft_data_hash": row["draft_data_hash"],
            "photo_set_fingerprint": row["photo_set_fingerprint"],
            "manifest_fingerprint": row["manifest_fingerprint"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "asset_count": asset_count,
            "source_count": source_count,
            "role_count": role_count,
            "has_compliance_block": len(compliance_items) > 0,
            "compliance_summary": compliance_items,
            "assets": self._asset_list(manifest_id),
        }

    def _count(self, table: str, column: str, value: str) -> int:
        row = self.db.execute(f"select count(*) as c from {table} where {column} = ?", (value,)).fetchone()
        return int(row["c"]) if row else 0

    def _asset_list(self, manifest_id: str) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            "select asset_id, prepared_sha256, content_type, file_size, prepared_at from ai_daily_report_prepared_assets where manifest_id = ? order by prepared_at, id",
            (manifest_id,),
        ).fetchall()
        return [
            {
                "asset_id": r["asset_id"],
                "prepared_sha256": r["prepared_sha256"],
                "content_type": r["content_type"],
                "file_size": r["file_size"],
                "prepared_at": r["prepared_at"],
            }
            for r in rows
        ]

    def summary_for_preview(self, draft_row: Dict[str, Any], validation_result) -> Dict[str, Any]:
        """Read-only summary for the Review Center (never materializes)."""
        current = self.get_current_manifest(draft_row, validation_result)
        history = self.get_manifest_history(draft_row["id"])
        return {
            "current": current,
            "history": history,
            "draft_version": draft_row.get("draft_version", 1),
            "status": draft_row.get("status"),
        }
