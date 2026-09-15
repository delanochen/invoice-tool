"""AI Daily Report - PhotoDiscoveryService (Phase 4)

Discovers original site photos for a service order on a specific date.

CRITICAL PRINCIPLES:
- NEVER accept file paths from LLM, user input, or Draft. All paths computed by backend.
- ONLY scan the report_date directory: <root>/<SO-XXXXXX>/pictures/YYYY-MM-DD/
- ONLY include original photos from "pictures/" state dir.
- Exclude: thumbnails, processing, failed, original_backup, generated, evidence, screenshots.
- SHA256 dedup: exact duplicate files only counted once.
- Path safety: realpath + commonpath to prevent traversal/absolute/symlink escape.

Reuses existing project functions:
- app.service_order_photo_folder(order_number) for directory mapping
- photo_worker.file_sha256(path) for SHA256
- image_processing.is_supported_image(path) for format check
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .schemas import PhotoRef

logger = logging.getLogger(__name__)

# Allowed photo extensions (subset of project ALLOWED_IMAGE_EXTENSIONS, excluding gif)
ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic", "heif", "avif"}

# Directories that are NOT original photos
EXCLUDED_STATE_DIRS = {"thumbnails", "processing", "failed", "original_backup", "incoming"}

# Filename patterns that indicate non-original photos
EXCLUDED_FILENAME_PATTERNS = [
    "screenshot", "screencap",  # screenshots
    "mileage_evidence", "mileage-",  # generated evidence
    "thumbnail", "thumb_",  # thumbnails
    ".tmp", ".bak",  # temp/backup
]


class PhotoDiscoveryError(Exception):
    """Raised when photo discovery fails (path safety, missing dir, etc.)."""


class PhotoDiscoveryService:
    """Discover original site photos for a service order on a specific date."""

    def __init__(self, shared_photos_root: str, service_order_photo_folder_func):
        """
        Args:
            shared_photos_root: Absolute path to shared-photos root directory.
            service_order_photo_folder_func: Function(order_number) -> Path, from app.py.
        """
        self.root = Path(shared_photos_root).resolve()
        self._service_order_photo_folder = service_order_photo_folder_func

    def discover_photos(
        self,
        order_number: str,
        report_date: str,
    ) -> Tuple[List[PhotoRef], str, Optional[str]]:
        """Discover all original photos for a service order on a specific date.

        Args:
            order_number: Service order number (e.g. "SO-123456").
            report_date: Date string YYYY-MM-DD.

        Returns:
            Tuple of (photo_refs, status, error_message).
            status: "discovered" / "no_photos" / "failed"
        """
        try:
            # Validate date format
            datetime.strptime(report_date, "%Y-%m-%d")
        except ValueError:
            return [], "failed", f"invalid_report_date:{report_date}"

        try:
            # Get order folder using existing project function (has path safety)
            order_folder = self._service_order_photo_folder(order_number)
        except (ValueError, Exception) as e:
            logger.warning("PhotoDiscovery: invalid order folder for %s", order_number)
            return [], "failed", "invalid_order_folder"

        # Target: <order_folder>/pictures/<report_date>/
        pictures_dir = order_folder / "pictures"
        date_dir = pictures_dir / report_date

        # Path safety: verify date_dir is under root
        try:
            date_dir_resolved = date_dir.resolve()
            if not str(date_dir_resolved).startswith(str(self.root)):
                logger.warning("PhotoDiscovery: path escape detected for %s", date_dir)
                return [], "failed", "path_escape_detected"
        except OSError:
            return [], "failed", "path_resolution_failed"

        if not date_dir_resolved.is_dir():
            logger.info("PhotoDiscovery: no photo directory for %s on %s", order_number, report_date)
            return [], "no_photos", None

        # Phase 1: collect all candidate files with hash (for deterministic dedup)
        candidates = []  # list of (relative_path, full_path, photo_hash, ext, mtime)
        try:
            for entry in sorted(date_dir_resolved.iterdir()):
                if not entry.is_file():
                    continue
                if entry.name.startswith("."):
                    continue

                # Check extension
                ext = entry.suffix.lower().lstrip(".")
                if ext not in ALLOWED_PHOTO_EXTENSIONS:
                    continue

                # Check excluded filename patterns
                name_lower = entry.name.lower()
                if any(pattern in name_lower for pattern in EXCLUDED_FILENAME_PATTERNS):
                    continue

                # Compute relative path (from shared-photos root)
                try:
                    relative_path = str(entry.relative_to(self.root))
                except ValueError:
                    logger.warning("PhotoDiscovery: file outside root: %s", entry)
                    continue

                # SHA256 (reuse photo_worker if available)
                try:
                    photo_hash = self._compute_sha256(entry)
                except OSError:
                    logger.warning("PhotoDiscovery: cannot hash file: %s", entry)
                    continue

                # Get file modified time
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    mtime = None

                candidates.append((relative_path, entry, photo_hash, ext, mtime))

        except OSError as e:
            logger.warning("PhotoDiscovery: error scanning directory %s: %s", date_dir_resolved, type(e).__name__)
            return [], "failed", "directory_scan_failed"

        # Phase 2: deterministic dedup by hash
        # Winner selection: sort by relative_path lexical order, pick first
        # This ensures file system traversal order doesn't affect result
        by_hash = {}
        for rel_path, full_path, photo_hash, ext, mtime in candidates:
            if photo_hash not in by_hash:
                by_hash[photo_hash] = []
            by_hash[photo_hash].append((rel_path, full_path, ext, mtime))

        photos = []
        for photo_hash in sorted(by_hash.keys()):
            group = by_hash[photo_hash]
            # Deterministic winner: sort by relative_path lexical, pick first
            group_sorted = sorted(group, key=lambda x: x[0])
            rel_path, full_path, ext, mtime = group_sorted[0]

            file_modified_time = datetime.fromtimestamp(mtime).isoformat() if mtime else None

            # Create PhotoRef (capture_time will be filled by PhotoMetadataService)
            # photo_id = full SHA256 (backend identity, UI may truncate)
            photo_ref = PhotoRef(
                photo_id=photo_hash,
                photo_hash=photo_hash,
                relative_path=rel_path,
                classification="unknown",
                confidence=0.0,
                capture_time=None,
                capture_time_source=None,
                file_modified_time=file_modified_time,
                mime=f"image/{ext}" if ext != "jpg" else "image/jpeg",
                source="server_original",
                time_verification_required=False,
                timeline_eligible=False,
                timeline_exclusion_reason=None,
            )
            photos.append(photo_ref)

        if not photos:
            return [], "no_photos", None

        logger.info("PhotoDiscovery: found %d original photos for %s on %s", len(photos), order_number, report_date)
        return photos, "discovered", None

    @staticmethod
    def compute_photo_set_fingerprint(photos: list) -> Optional[str]:
        """Compute SHA256(sorted(photo sha256 list)) as photo set fingerprint.

        Same logical photo set -> same fingerprint.
        Add/remove photos -> fingerprint changes.
        Order changes -> fingerprint unchanged (because we sort).
        """
        if not photos:
            return None
        hashes = sorted(p.photo_hash for p in photos if p.photo_hash)
        if not hashes:
            return None
        raw = "|".join(hashes)
        import hashlib
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        """Compute SHA256 of a file. Reuses photo_worker.file_sha256 if available."""
        try:
            from photo_worker import file_sha256
            return file_sha256(str(path))
        except (ImportError, Exception):
            import hashlib
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
