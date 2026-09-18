"""AI Daily Report - MileageEvidenceService (Phase 3B Final)

Generates auditable Mileage Evidence PNG from Phase 3A route data.

CRITICAL PRINCIPLES:
- NEVER re-calls Google Routes API. Only consumes saved route data.
- NEVER writes to formal service_report_attachments (Draft stage only).
- NEVER crops/covers/obscures Google attribution on the map image.
- NEVER logs full addresses, API keys, or full Static Map URLs.
- Error fields contain ONLY internal safe error codes, never Google response body/URL/key.

GOOGLE MAPS COMPLIANCE (compliance_review_required):
- Phase 3B only allows Draft / temporary Evidence.
- Before Phase 9 Confirm & Save, do NOT permanently store Google Static Map PNG
  as formal mileage_proof. Formal storage compliance plan must be confirmed before Phase 9.
- Business audit data (distance, miles, worker, date, fingerprint) is our own data.
- Google Maps Platform Content (map image, route display) has storage policy limits.

Evidence layout:
  [Google Static Map with route polyline + A/B markers]  (top, full width, attribution intact)
  [Mileage Information Panel]                            (bottom, our own text)

Persistence (restart-safe):
  Evidence metadata is stored in DailyReportDraft.evidence_records (Draft JSON).
  After app restart, existing evidence can be reused by matching:
  worker_user_id + route_fingerprint + evidence_version + file_relative_path + file_sha256.

Path safety:
  file_relative_path is ALWAYS resolved and confined to:
  <draft_evidence_root>/<draft_id>/mileage/
  Path traversal (../), absolute paths, and symlink escapes are rejected.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schemas import (
    EVIDENCE_VERSION,
    MileageEvidenceRecord,
    WorkerTravel,
)
from .static_maps import GoogleStaticMapsService, StaticMapResult

logger = logging.getLogger(__name__)

# Info panel dimensions
PANEL_HEIGHT = 244
PANEL_BG_COLOR = (255, 255, 255)
PANEL_TEXT_COLOR = (30, 30, 30)
PANEL_TITLE_COLOR = (0, 80, 160)
PANEL_PADDING = 20
PANEL_LINE_HEIGHT = 22

# Internal safe error codes (never contain Google response body/URL/key/address/polyline)
ERROR_STATIC_MAPS_NOT_CONFIGURED = "static_maps_api_not_configured"
ERROR_MISSING_POLYLINE = "missing_route_polyline"
ERROR_STATIC_MAP_TIMEOUT = "static_map_timeout"
ERROR_STATIC_MAP_RATE_LIMIT = "static_map_rate_limit"
ERROR_STATIC_MAP_SERVER_ERROR = "static_map_server_error"
ERROR_STATIC_MAP_AUTH_FAILED = "static_map_auth_failed"
ERROR_STATIC_MAP_BAD_REQUEST = "static_map_bad_request"
ERROR_STATIC_MAP_HTTP_ERROR = "static_map_http_error"
ERROR_STATIC_MAP_INVALID_CONTENT_TYPE = "static_map_invalid_content_type"
ERROR_STATIC_MAP_IMAGE_TOO_LARGE = "static_map_image_too_large"
ERROR_STATIC_MAP_EMPTY_RESPONSE = "static_map_empty_response"
ERROR_STATIC_MAP_UNEXPECTED = "static_map_unexpected_error"
ERROR_CORRUPT_MAP_IMAGE = "corrupt_map_image"
ERROR_IMAGE_COMPOSITION_FAILED = "image_composition_failed"
ERROR_DISK_WRITE_FAILED = "evidence_write_failed"
ERROR_PATH_UNSAFE = "evidence_path_unsafe"
ERROR_FILE_NOT_FOUND = "evidence_file_not_found"
ERROR_HASH_MISMATCH = "evidence_hash_mismatch"


class PathSafetyError(Exception):
    """Raised when a file path escapes the allowed draft evidence directory."""


class MileageEvidenceService:
    """Generate and manage Mileage Evidence for Draft workers."""

    def __init__(
        self,
        static_maps_service: GoogleStaticMapsService,
        draft_evidence_root: str,
    ):
        self.static_maps = static_maps_service
        self.draft_evidence_root = Path(draft_evidence_root).resolve()

    # ─── Path Safety ────────────────────────────────────────────────────

    def _safe_evidence_dir(self, draft_id: int) -> Path:
        """Get/create draft evidence directory, confined to root."""
        if draft_id is None or draft_id <= 0:
            raise PathSafetyError(f"Invalid draft_id: {draft_id}")
        d = (self.draft_evidence_root / str(draft_id) / "mileage").resolve()
        # Verify resolved path is under root
        if not str(d).startswith(str(self.draft_evidence_root)):
            raise PathSafetyError(f"Path escape detected: {d}")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _safe_evidence_path(self, draft_id: int, relative_path: str) -> Path:
        """Resolve a relative path safely, rejecting traversal/absolute/symlink escapes."""
        if not relative_path:
            raise PathSafetyError("Empty relative path")
        # Reject absolute paths
        if os.path.isabs(relative_path):
            raise PathSafetyError(f"Absolute path not allowed: {relative_path}")
        # Reject path traversal
        if ".." in Path(relative_path).parts:
            raise PathSafetyError(f"Path traversal not allowed: {relative_path}")
        # Resolve and verify confinement
        full = (self.draft_evidence_root / str(draft_id) / "mileage" / relative_path).resolve()
        expected_root = (self.draft_evidence_root / str(draft_id) / "mileage").resolve()
        if not str(full).startswith(str(expected_root)):
            raise PathSafetyError(f"Path escape detected: {full}")
        # Check for symlink escape
        try:
            if full.is_symlink() or expected_root.is_symlink():
                raise PathSafetyError("Symlink escape detected")
        except OSError:
            pass
        return full

    # ─── Eligibility ────────────────────────────────────────────────────

    @staticmethod
    def is_eligible(worker: WorkerTravel) -> Tuple[bool, Optional[str]]:
        """Check if worker is eligible for evidence generation.

        ALL conditions must be met. Returns (eligible, reason_if_not).
        """
        if worker.transportation != "self_drive":
            return False, "transportation_not_self_drive"
        if worker.route_status != "success":
            return False, f"route_status_{worker.route_status}"
        if worker.route_distance_meters is None:
            return False, "route_distance_meters_null"
        if worker.one_way_miles is None:
            return False, "one_way_miles_null"
        if worker.reported_miles is None:
            return False, "reported_miles_null"
        if worker.route_provider != "google_routes":
            return False, "route_provider_not_google"
        if not worker.origin:
            return False, "origin_null"
        if not worker.destination:
            return False, "destination_null"
        if not worker.origin_confirmed:
            return False, "origin_not_confirmed"
        if worker.overnight_stay is None:
            return False, "overnight_stay_null"
        if not worker.route_polyline:
            return False, "route_polyline_null"
        return True, None

    # ─── Route Fingerprint ──────────────────────────────────────────────

    @staticmethod
    def compute_route_fingerprint(worker: WorkerTravel) -> str:
        """Compute SHA256 fingerprint of route identity fields."""
        components = [
            str(worker.origin_normalized or worker.origin or ""),
            str(worker.destination_normalized or worker.destination or ""),
            str(worker.route_distance_meters or ""),
            str(worker.route_polyline or ""),
            str(worker.overnight_stay),
            str(worker.route_query_time or ""),
        ]
        raw = "|".join(components)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ─── Address Formatting for Evidence ────────────────────────────────

    @staticmethod
    def format_address_for_evidence(address: str) -> str:
        """Format address for customer-visible evidence.

        If address looks like a full US address with street number,
        show only city/state/zip to protect employee home address.
        """
        if not address:
            return ""
        addr = address.strip()
        parts = [p.strip() for p in addr.split(",")]
        if len(parts) >= 2 and parts[0] and parts[0][0].isdigit():
            return ", ".join(parts[1:])
        return addr

    # ─── Evidence Metadata Persistence ──────────────────────────────────

    @staticmethod
    def find_existing_evidence(
        draft_evidence_records: List[Dict[str, Any]],
        worker_user_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Find existing evidence metadata for a worker in persisted draft records."""
        for rec in draft_evidence_records:
            if rec.get("worker_user_id") == worker_user_id:
                return rec
        return None

    @staticmethod
    def upsert_evidence_record(
        draft_evidence_records: List[Dict[str, Any]],
        record: MileageEvidenceRecord,
    ) -> List[Dict[str, Any]]:
        """Insert or update evidence record in draft metadata list."""
        record_dict = record.model_dump() if hasattr(record, 'model_dump') else dict(record)
        for i, existing in enumerate(draft_evidence_records):
            if existing.get("worker_user_id") == record.worker_user_id:
                draft_evidence_records[i] = record_dict
                return draft_evidence_records
        draft_evidence_records.append(record_dict)
        return draft_evidence_records

    def verify_evidence_integrity(
        self,
        evidence_meta: Dict[str, Any],
        worker: WorkerTravel,
        draft_id: int,
    ) -> Tuple[bool, Optional[str]]:
        """Verify existing evidence is still valid for reuse.

        Checks ALL of:
        - evidence metadata exists
        - file exists on disk
        - current SHA256 == stored file_sha256
        - route_fingerprint matches current worker
        - evidence_version matches
        """
        if not evidence_meta:
            return False, "no_metadata"

        # Version check
        if evidence_meta.get("evidence_version") != EVIDENCE_VERSION:
            return False, "version_mismatch"

        # Fingerprint check
        current_fp = self.compute_route_fingerprint(worker)
        if evidence_meta.get("route_fingerprint") != current_fp:
            return False, "fingerprint_mismatch"

        # Status check
        if evidence_meta.get("evidence_status") != "ready":
            return False, f"status_{evidence_meta.get('evidence_status')}"

        # File existence and hash check
        rel_path = evidence_meta.get("file_relative_path")
        stored_hash = evidence_meta.get("file_sha256")
        if not rel_path or not stored_hash:
            return False, "missing_file_ref"

        try:
            full_path = self._safe_evidence_path(draft_id, rel_path)
        except PathSafetyError:
            return False, ERROR_PATH_UNSAFE

        if not full_path.exists():
            return False, ERROR_FILE_NOT_FOUND

        try:
            current_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
        except OSError:
            return False, "hash_read_failed"

        if current_hash != stored_hash:
            return False, ERROR_HASH_MISMATCH

        return True, None

    # ─── Evidence Generation ────────────────────────────────────────────

    def generate_evidence(
        self,
        worker: WorkerTravel,
        draft_id: int,
        service_order_id: int,
        report_date: str,
        generated_by: int,
        draft_evidence_records: Optional[List[Dict[str, Any]]] = None,
    ) -> MileageEvidenceRecord:
        """Generate Mileage Evidence for a worker.

        Restart-safe idempotency: if draft_evidence_records contains a matching
        record with valid integrity, reuse it without re-fetching Static Map.

        Returns MileageEvidenceRecord (may have status=failed/verification_required).
        """
        draft_evidence_records = draft_evidence_records or []

        # Check eligibility
        eligible, reason = self.is_eligible(worker)
        if not eligible:
            return MileageEvidenceRecord(
                evidence_id=uuid.uuid4().hex,
                draft_id=draft_id,
                service_order_id=service_order_id,
                report_date=report_date,
                worker_user_id=worker.user_id,
                worker_name=worker.name,
                evidence_status="verification_required",
                error=reason,
            )

        # Compute fingerprint
        fingerprint = self.compute_route_fingerprint(worker)

        # Check for existing valid evidence (restart-safe)
        existing_meta = self.find_existing_evidence(draft_evidence_records, worker.user_id)
        if existing_meta:
            valid, _ = self.verify_evidence_integrity(existing_meta, worker, draft_id)
            if valid:
                # Reuse existing - convert dict back to record
                return MileageEvidenceRecord(**existing_meta)

        evidence_id = existing_meta.get("evidence_id") if existing_meta else uuid.uuid4().hex

        # Check Static Maps availability
        if not self.static_maps.is_available():
            return MileageEvidenceRecord(
                evidence_id=evidence_id,
                draft_id=draft_id,
                service_order_id=service_order_id,
                report_date=report_date,
                worker_user_id=worker.user_id,
                worker_name=worker.name,
                route_fingerprint=fingerprint,
                evidence_status="verification_required",
                error=ERROR_STATIC_MAPS_NOT_CONFIGURED,
            )

        # Fetch Static Map (uses Phase 3A polyline, does NOT re-query Routes)
        map_result = self.static_maps.get_route_map(
            origin=worker.origin,
            destination=worker.destination,
            encoded_polyline=worker.route_polyline or "",
        )

        if not map_result.success:
            return MileageEvidenceRecord(
                evidence_id=evidence_id,
                draft_id=draft_id,
                service_order_id=service_order_id,
                report_date=report_date,
                worker_user_id=worker.user_id,
                worker_name=worker.name,
                route_provider=worker.route_provider or "google_routes",
                route_query_time=worker.route_query_time,
                route_distance_meters=worker.route_distance_meters,
                one_way_miles=worker.one_way_miles,
                reported_miles=worker.reported_miles,
                overnight_stay=worker.overnight_stay,
                route_fingerprint=fingerprint,
                evidence_status="failed",
                error=map_result.error or ERROR_STATIC_MAP_UNEXPECTED,
            )

        # Validate image
        try:
            from PIL import Image
            map_img = Image.open(io.BytesIO(map_result.image_bytes))
            map_img.load()
        except Exception:
            return MileageEvidenceRecord(
                evidence_id=evidence_id,
                draft_id=draft_id,
                service_order_id=service_order_id,
                report_date=report_date,
                worker_user_id=worker.user_id,
                worker_name=worker.name,
                route_fingerprint=fingerprint,
                evidence_status="failed",
                error=ERROR_CORRUPT_MAP_IMAGE,
            )

        # Compose final image
        try:
            final_img = self._compose_evidence_image(map_img, worker, report_date)
        except Exception:
            return MileageEvidenceRecord(
                evidence_id=evidence_id,
                draft_id=draft_id,
                service_order_id=service_order_id,
                report_date=report_date,
                worker_user_id=worker.user_id,
                worker_name=worker.name,
                route_fingerprint=fingerprint,
                evidence_status="failed",
                error=ERROR_IMAGE_COMPOSITION_FAILED,
            )

        # Save to draft evidence directory
        try:
            file_rel_path, file_sha256 = self._save_evidence_image(
                final_img, draft_id, evidence_id
            )
        except PathSafetyError:
            return MileageEvidenceRecord(
                evidence_id=evidence_id,
                draft_id=draft_id,
                service_order_id=service_order_id,
                report_date=report_date,
                worker_user_id=worker.user_id,
                worker_name=worker.name,
                route_fingerprint=fingerprint,
                evidence_status="failed",
                error=ERROR_PATH_UNSAFE,
            )
        except Exception:
            return MileageEvidenceRecord(
                evidence_id=evidence_id,
                draft_id=draft_id,
                service_order_id=service_order_id,
                report_date=report_date,
                worker_user_id=worker.user_id,
                worker_name=worker.name,
                route_fingerprint=fingerprint,
                evidence_status="failed",
                error=ERROR_DISK_WRITE_FAILED,
            )

        return MileageEvidenceRecord(
            evidence_id=evidence_id,
            draft_id=draft_id,
            service_order_id=service_order_id,
            report_date=report_date,
            worker_user_id=worker.user_id,
            worker_name=worker.name,
            route_provider=worker.route_provider or "google_routes",
            route_query_time=worker.route_query_time,
            route_distance_meters=worker.route_distance_meters,
            one_way_miles=worker.one_way_miles,
            reported_miles=worker.reported_miles,
            overnight_stay=worker.overnight_stay,
            route_fingerprint=fingerprint,
            evidence_version=EVIDENCE_VERSION,
            evidence_status="ready",
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            generated_by=generated_by,
            file_relative_path=file_rel_path,
            file_sha256=file_sha256,
        )

    # ─── Image Composition ──────────────────────────────────────────────

    def _compose_evidence_image(self, map_img, worker: WorkerTravel, report_date: str):
        """Compose map + info panel. Map is preserved full (attribution intact)."""
        from PIL import Image, ImageDraw, ImageFont

        map_w, map_h = map_img.size
        total_h = map_h + PANEL_HEIGHT

        final = Image.new("RGB", (map_w, total_h), PANEL_BG_COLOR)

        if map_img.mode != "RGB":
            map_img = map_img.convert("RGB")
        final.paste(map_img, (0, 0))

        draw = ImageDraw.Draw(final)

        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        except (OSError, IOError):
            font_title = ImageFont.load_default()
            font_body = ImageFont.load_default()

        y = map_h + PANEL_PADDING
        x = PANEL_PADDING

        draw.text((x, y), "Mileage Evidence", fill=PANEL_TITLE_COLOR, font=font_title)
        y += 28

        origin_display = self.format_address_for_evidence(worker.origin or "")
        dest_display = self.format_address_for_evidence(worker.destination or "")
        travel_rule = "Round Trip (one-way x 2)" if worker.overnight_stay is False else "One-way only (overnight stay)"

        lines = [
            f"Employee: {worker.name}",
            f"Origin: {origin_display}",
            f"Destination: {dest_display}",
            f"One-way Distance: {worker.one_way_miles:.2f} mi",
            f"Mileage Rule: {travel_rule}",
            f"Reported Mileage: {worker.reported_miles:.2f} mi",
            f"Overnight Stay: {'Yes' if worker.overnight_stay else 'No'}",
            f"Route Provider: {worker.route_provider or 'Google Maps'}",
        ]
        # v0.1.235: Report Date and Route Calculated lines were removed from
        # the evidence panel per product decision (2026-09-17).
        # report_date and worker.route_query_time stay in the evidence record
        # (fingerprint & audit) — only the on-image display changed.

        for line in lines:
            draw.text((x, y), line, fill=PANEL_TEXT_COLOR, font=font_body)
            y += PANEL_LINE_HEIGHT

        return final

    # ─── File Storage ───────────────────────────────────────────────────

    def _save_evidence_image(self, img, draft_id: int, evidence_id: str) -> Tuple[str, str]:
        """Save evidence image and return (relative_path, sha256). Path-safe."""
        evidence_dir = self._safe_evidence_dir(draft_id)
        filename = f"{evidence_id}.png"
        # Verify filename is safe
        if "/" in filename or ".." in filename:
            raise PathSafetyError(f"Unsafe filename: {filename}")
        full_path = evidence_dir / filename

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        with open(full_path, "wb") as f:
            f.write(img_bytes)

        file_sha256 = hashlib.sha256(img_bytes).hexdigest()
        return filename, file_sha256

    # ─── Staleness Detection ────────────────────────────────────────────

    @staticmethod
    def is_evidence_stale(
        evidence: MileageEvidenceRecord, worker: WorkerTravel
    ) -> bool:
        """Check if existing evidence is stale relative to current worker route."""
        if not evidence.route_fingerprint:
            return True
        current_fingerprint = MileageEvidenceService.compute_route_fingerprint(worker)
        return evidence.route_fingerprint != current_fingerprint

    # ─── Cleanup (Safe) ─────────────────────────────────────────────────

    def cleanup_draft_evidence(self, draft_id: int) -> int:
        """Remove all evidence files for a cancelled/expired draft.

        SAFE: only deletes files under <root>/<draft_id>/mileage/.
        NEVER touches service_report_attachments, shared photos, or other DATA_DIR content.
        Returns number of files removed.
        """
        try:
            evidence_dir = self._safe_evidence_dir(draft_id)
        except PathSafetyError:
            return 0

        # Double-check resolved path is exactly the expected draft dir
        expected = (self.draft_evidence_root / str(draft_id) / "mileage").resolve()
        if evidence_dir.resolve() != expected:
            logger.warning("Cleanup path mismatch, aborting")
            return 0

        if not evidence_dir.exists():
            return 0

        count = 0
        for f in evidence_dir.glob("*.png"):
            try:
                # Verify file is under expected dir (no symlink escape)
                if str(f.resolve()).startswith(str(expected)):
                    f.unlink()
                    count += 1
            except OSError:
                pass

        # Remove empty dirs (only our own)
        try:
            evidence_dir.rmdir()
            parent = evidence_dir.parent
            if parent.exists() and parent.resolve() == (self.draft_evidence_root / str(draft_id)).resolve():
                if not any(parent.iterdir()):
                    parent.rmdir()
        except OSError:
            pass
        return count
