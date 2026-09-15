"""AI Daily Report - PhotoMetadataService (Phase 4 Final)

Reads photo capture time with priority and anomaly detection.

CRITICAL PRINCIPLES:
- Time priority: EXIF DateTimeOriginal > EXIF DateTimeDigitized/CreateDate > EXIF DateTime > verified filename > filesystem mtime
- NO folder date + mtime combination (that can forge fake capture times)
- filesystem mtime is LOW-CONFIDENCE fallback: time_verification_required=True
- Strict date match: local capture date MUST equal report_date (no ±1 day tolerance)
- timeline_eligible photos ONLY can be auto arrival/departure candidates
- GPS data is NEVER read, saved, or returned (privacy)
- timezone-aware: capture_time is local, with timezone_name and timezone_source

Timezone handling:
- EXIF DateTimeOriginal is mostly naive (no timezone)
- Default: app_timezone() (e.g. America/Chicago)
- Future: site timezone can override
- If EXIF has OffsetTimeOriginal: use that (timezone_source=exif_offset)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .schemas import PhotoRef

logger = logging.getLogger(__name__)

# Timeline suspicious thresholds (configurable, no magic numbers)
MIN_AUTO_TIMELINE_MINUTES = 10  # < 10 minutes = suspicious
MAX_AUTO_TIMELINE_HOURS = 16  # > 16 hours = suspicious

# Anomaly year thresholds
MIN_VALID_YEAR = 2010
DEFAULT_YEARS = {1970, 1980, 2000, 2001, 2002}

# EXIF GPS tags (we NEVER read these)
EXIF_GPS_TAGS = {34853, 0x8825}  # GPSInfo


class PhotoMetadataService:
    """Read and validate photo capture metadata."""

    def __init__(self, shared_photos_root: str, default_timezone: str = "America/Chicago"):
        self.root = Path(shared_photos_root).resolve()
        self.default_timezone = default_timezone

    # ─── Public API ──────────────────────────────────────────────────────

    def enrich_photo_with_time(
        self,
        photo: PhotoRef,
        report_date: str,
    ) -> PhotoRef:
        """Read capture time for a photo and mark anomalies.

        Args:
            photo: PhotoRef with relative_path set.
            report_date: Expected date YYYY-MM-DD.

        Returns:
            Updated PhotoRef with capture_time, timezone, timeline_eligible, etc.
        """
        try:
            full_path = self._resolve_photo_path(photo.relative_path)
        except (ValueError, OSError):
            photo.time_verification_required = True
            photo.capture_time_source = "unknown"
            photo.timeline_eligible = False
            photo.timeline_exclusion_reason = "missing_timestamp"
            return photo

        if not full_path.exists():
            photo.time_verification_required = True
            photo.capture_time_source = "unknown"
            photo.timeline_eligible = False
            photo.timeline_exclusion_reason = "missing_timestamp"
            return photo

        # Try to read metadata (may fail for unsupported formats like HEIC without plugin)
        try:
            capture_dt_naive, source = self._read_capture_time(full_path)
        except Exception as e:
            logger.debug("PhotoMetadata: metadata read failed for %s: %s", photo.relative_path, type(e).__name__)
            photo.metadata_unsupported = True
            photo.time_verification_required = True
            photo.capture_time_source = "unknown"
            photo.timeline_eligible = False
            photo.timeline_exclusion_reason = "metadata_unsupported"
            return photo

        # Determine timezone
        tz_name, tz_source = self._determine_timezone(full_path)

        if capture_dt_naive is None:
            photo.time_verification_required = True
            photo.capture_time_source = "unknown"
            photo.timeline_eligible = False
            photo.timeline_exclusion_reason = "missing_timestamp"
            return photo

        # mtime fallback is low-confidence
        if source == "file_mtime":
            photo.time_verification_required = True

        # Validate time (anomaly detection)
        is_anomaly, reason = self._is_anomalous_time(capture_dt_naive, report_date)
        if is_anomaly:
            photo.time_verification_required = True

        # Strict date match for timeline eligibility
        date_match = self._check_date_match(capture_dt_naive, report_date, tz_name)

        # Set timeline eligibility
        if not date_match:
            photo.timeline_eligible = False
            photo.timeline_exclusion_reason = "date_mismatch"
        elif is_anomaly:
            photo.timeline_eligible = False
            photo.timeline_exclusion_reason = reason
        elif source == "file_mtime":
            photo.timeline_eligible = False
            photo.timeline_exclusion_reason = "mtime_only"
        else:
            photo.timeline_eligible = True
            photo.timeline_exclusion_reason = None

        # Store capture time as local ISO8601 (no timezone offset in string, but timezone_name separate)
        photo.capture_time = capture_dt_naive.isoformat()
        photo.capture_time_source = source
        photo.capture_timezone = tz_name
        photo.capture_timezone_source = tz_source

        return photo

    def enrich_all_photos(
        self,
        photos: List[PhotoRef],
        report_date: str,
    ) -> List[PhotoRef]:
        """Enrich all photos with capture time. Returns updated list."""
        return [self.enrich_photo_with_time(p, report_date) for p in photos]

    def compute_arrival_departure_candidates(
        self,
        photos: List[PhotoRef],
    ) -> Tuple[Optional[PhotoRef], Optional[PhotoRef], str, List[str]]:
        """Compute arrival and departure candidates from timeline-eligible photos.

        ONLY timeline_eligible photos are used for auto candidates.
        Photos with time_verification_required are NOT auto candidates.

        Returns:
            Tuple of (arrival_candidate, departure_candidate, timeline_status, verification_fields)
            timeline_status: ready / no_photos / insufficient_photos / suspicious / verification_required
        """
        verification_fields = []

        if not photos:
            return None, None, "no_photos", ["no_photos"]

        # Filter to timeline-eligible photos only
        eligible = [p for p in photos if p.timeline_eligible and not p.time_verification_required]

        if not eligible:
            # No eligible photos - check if we have any photos at all
            any_with_time = [p for p in photos if p.capture_time]
            if not any_with_time:
                return None, None, "no_photos", ["no_eligible_photos"]
            return None, None, "verification_required", ["no_eligible_photos"]

        # Sort by capture_time
        def sort_key(p: PhotoRef):
            try:
                return datetime.fromisoformat(p.capture_time)
            except (ValueError, TypeError):
                return datetime.max

        eligible_sorted = sorted(eligible, key=sort_key)

        arrival = eligible_sorted[0]
        departure = eligible_sorted[-1] if len(eligible_sorted) >= 2 else None

        # Single photo rule: departure = null, verification required
        if len(eligible_sorted) == 1:
            verification_fields.append("insufficient_photo_timeline")
            return arrival, None, "insufficient_photos", verification_fields

        # Check timeline span for suspicious
        try:
            arrival_dt = datetime.fromisoformat(arrival.capture_time)
            departure_dt = datetime.fromisoformat(departure.capture_time)
            span_minutes = (departure_dt - arrival_dt).total_seconds() / 60

            if span_minutes < MIN_AUTO_TIMELINE_MINUTES:
                verification_fields.append("timeline_too_short")
                return arrival, departure, "suspicious", verification_fields
            if span_minutes > MAX_AUTO_TIMELINE_HOURS * 60:
                verification_fields.append("timeline_too_long")
                return arrival, departure, "suspicious", verification_fields
        except (ValueError, TypeError):
            pass

        return arrival, departure, "ready", verification_fields

    @staticmethod
    def is_timeline_eligible(photo: PhotoRef, report_date: str) -> bool:
        """Clear function: check if a photo is eligible for auto arrival/departure timeline.

        A photo is eligible ONLY if:
        - Has valid capture time
        - Local capture date == report_date (strict, no tolerance)
        - Not anomalous (default year, future, etc.)
        - Not mtime-only fallback
        - Not metadata_unsupported
        - time_verification_required == False
        """
        if not photo.capture_time:
            return False
        if photo.time_verification_required:
            return False
        if photo.metadata_unsupported:
            return False
        if photo.timeline_exclusion_reason:
            return False
        return photo.timeline_eligible

    # ─── Internal: Time Reading ──────────────────────────────────────────

    def _read_capture_time(self, path: Path) -> Tuple[Optional[datetime], Optional[str]]:
        """Read capture time with priority. NEVER reads GPS.

        Priority:
        1. EXIF DateTimeOriginal (tag 36867)
        2. EXIF DateTimeDigitized (tag 36868) / CreateDate
        3. EXIF DateTime (tag 306)
        4. verified filename timestamp
        5. filesystem mtime (LOW CONFIDENCE)
        """
        # Try EXIF via PIL (never reads GPS tags)
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS

            with Image.open(path) as img:
                exif = img.getexif()

                # Tag 36867 = DateTimeOriginal
                if 36867 in exif:
                    val = str(exif[36867]).strip()
                    dt = self._parse_exif_datetime(val)
                    if dt:
                        return dt, "exif_original"

                # Tag 36868 = DateTimeDigitized
                if 36868 in exif:
                    val = str(exif[36868]).strip()
                    dt = self._parse_exif_datetime(val)
                    if dt:
                        return dt, "exif_digitized"

                # Tag 306 = DateTime
                if 306 in exif:
                    val = str(exif[306]).strip()
                    dt = self._parse_exif_datetime(val)
                    if dt:
                        return dt, "exif_datetime"
        except Exception:
            pass

        # Try photo_worker.image_taken_datetime (reuse existing)
        try:
            from photo_worker import image_taken_datetime
            dt = image_taken_datetime(str(path))
            if dt:
                return dt, "exif"
        except (ImportError, Exception):
            pass

        # Try filename timestamp
        dt = self._read_filename_time(path)
        if dt:
            return dt, "filename"

        # Fallback: filesystem mtime (LOW CONFIDENCE)
        try:
            mtime = path.stat().st_mtime
            return datetime.fromtimestamp(mtime), "file_mtime"
        except OSError:
            pass

        return None, None

    @staticmethod
    def _parse_exif_datetime(value: str) -> Optional[datetime]:
        """Parse EXIF datetime string."""
        if not value:
            return None
        value = value.strip()[:19]
        for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, pattern)
            except ValueError:
                continue
        return None

    @staticmethod
    def _read_filename_time(path: Path) -> Optional[datetime]:
        """Read timestamp from filename (YYYYMMDD_HHMMSS format)."""
        try:
            from photo_worker import filename_taken_datetime
            dt = filename_taken_datetime(path)
            if dt:
                return dt
        except (ImportError, Exception):
            pass
        return None

    # ─── Internal: Timezone ──────────────────────────────────────────────

    def _determine_timezone(self, path: Path) -> Tuple[str, str]:
        """Determine timezone for a photo.

        Priority:
        1. EXIF OffsetTimeOriginal (if available) -> timezone_source=exif_offset
        2. Default business timezone -> timezone_source=business_timezone

        Note: site_timezone can be added in future.
        """
        # Try EXIF offset (tag 36880 = OffsetTimeOriginal)
        try:
            from PIL import Image
            with Image.open(path) as img:
                exif = img.getexif()
                if 36880 in exif:  # OffsetTimeOriginal
                    offset_str = str(exif[36880]).strip()
                    # Parse offset like "+05:00" or "-08:00"
                    try:
                        hours = int(offset_str[:3])
                        minutes = int(offset_str[4:6]) if len(offset_str) >= 6 else 0
                        offset_minutes = hours * 60 + (1 if hours > 0 else -1) * minutes
                        tz_name = f"UTC{offset_str}"
                        return tz_name, "exif_offset"
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass

        return self.default_timezone, "business_timezone"

    # ─── Internal: Validation ────────────────────────────────────────────

    @staticmethod
    def _is_anomalous_time(capture_dt: datetime, report_date: str) -> Tuple[bool, str]:
        """Check if capture time is anomalous. Returns (is_anomaly, reason)."""
        # Check default years
        if capture_dt.year in DEFAULT_YEARS:
            return True, f"invalid_timestamp_default_year_{capture_dt.year}"

        # Check minimum valid year
        if capture_dt.year < MIN_VALID_YEAR:
            return True, f"invalid_timestamp_year_too_old_{capture_dt.year}"

        # Check future time (beyond report_date + 1 day is definitely future)
        try:
            expected_date = datetime.strptime(report_date, "%Y-%m-%d").date()
            tomorrow = expected_date + timedelta(days=1)
            if capture_dt.date() > tomorrow:
                return True, f"future_timestamp_{capture_dt.date()}"
        except ValueError:
            pass

        return False, ""

    @staticmethod
    def _check_date_match(capture_dt: datetime, report_date: str, tz_name: str) -> bool:
        """Strict date match: local capture date MUST equal report_date.

        NO ±1 day tolerance. A photo from 2026-09-13 23:58 does NOT belong to 9/14 timeline.
        """
        try:
            expected_date = datetime.strptime(report_date, "%Y-%m-%d").date()
            capture_date = capture_dt.date()
            return capture_date == expected_date
        except ValueError:
            return False

    # ─── Internal: Path Safety ───────────────────────────────────────────

    def _resolve_photo_path(self, relative_path: str) -> Path:
        """Resolve relative path safely, rejecting traversal/absolute/symlink escape."""
        if not relative_path:
            raise ValueError("empty relative path")
        if Path(relative_path).is_absolute():
            raise ValueError("absolute path not allowed")
        if ".." in Path(relative_path).parts:
            raise ValueError("path traversal not allowed")
        full = (self.root / relative_path).resolve()
        if not str(full).startswith(str(self.root)):
            raise ValueError("path escape detected")
        return full
