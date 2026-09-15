"""AI Daily Report - Photo Classification Service (Phase 5)

Core business logic for Vision photo classification:
- Safety Photo auto-selection (threshold, ranking, candidates)
- Equipment Photo classification
- Service Photo Selection (stage diversity, max 10, near-duplicate suppression)
- ai_photo_analysis cache (key = photo_hash + model + version, cross-restart)
- User modification support (change_safety, add_service, remove_service)
- User override persistence (user_selected_service_photo_ids, user_removed_service_photo_ids)

CRITICAL PRINCIPLES:
- Only uses DailyReportDraft.photo_candidates (from Phase 4)
- Does NOT re-scan directories, re-infer EXIF, recalculate Arrival/Departure
- Does NOT modify Mileage or save formal Daily Report
- User selections take priority over AI (user_selected not overwritten)
- User-removed photos are NOT re-added by AI re-analysis
- Equipment ID from Vision does NOT modify Draft.work_items
- Near duplicate: <=10 equipment photos all kept (marked); >10 used for suppression
- Deterministic selection: same input -> same output regardless of order
- Cache key: photo_hash + analysis_model + analysis_version (path is provenance only)
- Cache validation: Pydantic strict validation, bad cache = cache miss
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .schemas import PhotoAnalysis, PhotoRef
from .perceptual_hash import PerceptualHashService, is_near_duplicate
from .vision_provider import PHOTO_ANALYSIS_VERSION

logger = logging.getLogger(__name__)

# Stage priority order for Service Photo Selection (max diversity)
STAGE_PRIORITY = [
    "equipment_overview",
    "nameplate",
    "fault_location",
    "before_repair",
    "during_disassembly",
    "replacement",
    "fuse_wiring",
    "after_repair",
    "startup",
    "final_state",
]

# Stage priority index for deterministic sorting
STAGE_PRIORITY_INDEX = {stage: i for i, stage in enumerate(STAGE_PRIORITY)}


class PhotoClassificationService:
    """Service for classifying photos and selecting safety/service photos."""

    def __init__(
        self,
        vision_service,
        shared_photos_root: str,
        db_connection=None,
        safety_auto_select_confidence: float = 0.80,
        safety_verify_confidence: float = 0.60,
        max_service_photos: int = 10,
    ):
        self.vision = vision_service
        self.root = Path(shared_photos_root).resolve()
        self.db = db_connection
        self.safety_auto_select_confidence = safety_auto_select_confidence
        self.safety_verify_confidence = safety_verify_confidence
        self.max_service_photos = max_service_photos
        self.perceptual = PerceptualHashService()

    # ─── Main Classification Entry Point ─────────────────────────────────

    def classify_draft_photos(
        self,
        photo_candidates: List[PhotoRef],
        analysis_model: str = "",
        user_selected_service_photo_ids: Optional[Set[str]] = None,
        user_removed_service_photo_ids: Optional[Set[str]] = None,
    ) -> Tuple[List[PhotoAnalysis], str, Dict]:
        """Classify all photos in a draft. Returns (analysis_results, status, metadata).

        Uses ai_photo_analysis cache when available (cross-restart).
        Does NOT modify draft - caller saves results.

        Args:
            photo_candidates: List of PhotoRef from Phase 4.
            analysis_model: Vision model name (from config).
            user_selected_service_photo_ids: Set of photo_ids user manually added.
            user_removed_service_photo_ids: Set of photo_ids user manually removed.

        Returns:
            (analysis_results, status, metadata_dict)
        """
        if not photo_candidates:
            return [], "no_photos", {}

        user_selected = user_selected_service_photo_ids or set()
        user_removed = user_removed_service_photo_ids or set()

        results = []
        disabled_count = 0
        failed_count = 0

        for photo in photo_candidates:
            # Only analyze original field photos
            if not self._is_original_field_photo(photo):
                analysis = PhotoAnalysis(
                    photo_id=photo.photo_id,
                    photo_hash=photo.photo_hash,
                    photo_path=photo.relative_path,
                    analysis_model=analysis_model,
                    analysis_version=PHOTO_ANALYSIS_VERSION,
                    classification="other",
                    confidence=0.0,
                    analysis_status="skipped",
                    verification_required=True,
                    verification_reason="not_original_field_photo",
                )
                results.append(analysis)
                continue

            # Check cache first (key: photo_hash + model + version)
            cached = self._get_cached_analysis(photo, analysis_model)
            if cached:
                results.append(cached)
                continue

            # Resolve file path safely
            full_path = self._resolve_photo_path(photo.relative_path)
            if not full_path or not full_path.exists():
                analysis = PhotoAnalysis(
                    photo_id=photo.photo_id,
                    photo_hash=photo.photo_hash,
                    photo_path=photo.relative_path,
                    analysis_model=analysis_model,
                    analysis_version=PHOTO_ANALYSIS_VERSION,
                    classification="unknown",
                    analysis_status="failed",
                    verification_required=True,
                    verification_reason="file_not_found",
                )
                results.append(analysis)
                failed_count += 1
                continue

            # Compute perceptual hash
            dhash = self.perceptual.get_hash(full_path, photo.photo_hash)

            # Call Vision (single photo failure does not stop batch)
            analysis = self.vision.analyze_photo(
                photo_path=full_path,
                photo_hash=photo.photo_hash,
                photo_id=photo.photo_id,
                analysis_model=analysis_model,
            )
            analysis.photo_path = photo.relative_path
            analysis.perceptual_hash = dhash
            analysis.capture_time = photo.capture_time
            analysis.capture_time_source = photo.capture_time_source

            if analysis.analysis_status == "disabled":
                disabled_count += 1
            elif analysis.analysis_status == "failed":
                failed_count += 1

            # Save to cache (only successful results)
            self._save_analysis(analysis)

            results.append(analysis)

        # Determine overall status
        status = self._determine_overall_status(results, disabled_count, failed_count)

        metadata = {
            "total": len(results),
            "success": sum(1 for r in results if r.analysis_status == "success"),
            "disabled": disabled_count,
            "failed": failed_count,
            "user_selected_count": len(user_selected),
            "user_removed_count": len(user_removed),
        }

        return results, status, metadata

    # ─── Safety Photo Selection ───────────────────────────────────────────

    def select_safety_photo(
        self,
        analysis_results: List[PhotoAnalysis],
        existing_selected: Optional[PhotoAnalysis] = None,
    ) -> Tuple[Optional[PhotoAnalysis], List[PhotoAnalysis]]:
        """Select best safety photo from analysis results.

        Ranking factors (deterministic):
        1. sub_category == front_standing_worker
        2. confidence (higher better)
        3. photo_id lexical (tie-break)

        Returns (selected_safety, all_safety_candidates).
        Does NOT overwrite user-selected photos.
        """
        # If user already selected, keep it
        if existing_selected and existing_selected.selected_source == "user_selected":
            safety_candidates = [a for a in analysis_results if a.classification == "safety_person"]
            return existing_selected, safety_candidates

        # Get all safety_person candidates (success only)
        safety_candidates = [
            a for a in analysis_results
            if a.classification == "safety_person" and a.analysis_status == "success"
        ]

        if not safety_candidates:
            return None, []

        # Deterministic ranking
        def safety_rank(a: PhotoAnalysis):
            is_front = 1 if a.sub_category == "front_standing_worker" else 0
            return (is_front, a.confidence, a.photo_id)

        safety_candidates.sort(key=safety_rank, reverse=True)
        best = safety_candidates[0]

        # Threshold check
        if best.confidence >= self.safety_auto_select_confidence:
            best.selected_source = "ai_selected"
            return best, safety_candidates
        elif best.confidence >= self.safety_verify_confidence:
            # Verification required - return candidate but mark
            best.verification_required = True
            best.verification_reason = "safety_confidence_below_auto_threshold"
            return None, safety_candidates
        else:
            # Too low confidence - don't auto select
            return None, safety_candidates

    # ─── Service Photo Selection ──────────────────────────────────────────

    def select_service_photos(
        self,
        analysis_results: List[PhotoAnalysis],
        existing_selected: List[PhotoAnalysis] = None,
        user_selected_ids: Optional[Set[str]] = None,
        user_removed_ids: Optional[Set[str]] = None,
    ) -> Tuple[List[PhotoAnalysis], Optional[str]]:
        """Select best service (equipment) photos, max N, with stage diversity.

        Algorithm (deterministic):
        1. Filter equipment photos with success status
        2. Exclude user_removed_ids
        3. Mark near-duplicates (but keep all if <=10)
        4. If <= max, select all (user-added first, then AI)
        5. If > max: stage diversity selection + near-duplicate suppression
        6. User-selected photos are preserved and not overwritten

        Returns (selected_list, error_code). error_code is None on success.
        If user selection causes > max, returns error "max_service_photos_exceeded".
        """
        existing_selected = existing_selected or []
        user_selected = user_selected_ids or set()
        user_removed = user_removed_ids or set()

        # Get all equipment photos (success only), exclude user-removed
        equipment_photos = [
            a for a in analysis_results
            if a.classification == "equipment"
            and a.analysis_status == "success"
            and a.photo_id not in user_removed
        ]

        if not equipment_photos:
            return [], None

        # If user has manually selected more than max, return error (don't silently delete)
        if len(user_selected) > self.max_service_photos:
            return [], "max_service_photos_exceeded"

        # Near-duplicate marking (for all, but only used for suppression if > max)
        unique_photos = self._mark_near_duplicates(equipment_photos)

        # If <= max, select all (deterministic order)
        if len(unique_photos) <= self.max_service_photos:
            selected = []
            selected_ids = set()
            # User-selected first
            for a in unique_photos:
                if a.photo_id in user_selected:
                    a.selected_source = "user_selected"
                    selected.append(a)
                    selected_ids.add(a.photo_id)
            # Then AI-selected
            for a in unique_photos:
                if a.photo_id not in selected_ids:
                    a.selected_source = "ai_selected"
                    selected.append(a)
                    selected_ids.add(a.photo_id)
            return selected, None

        # Stage diversity selection (deterministic)
        selected = []
        selected_ids = set()

        # First pass: one photo per stage (in priority order)
        for stage in STAGE_PRIORITY:
            if len(selected) >= self.max_service_photos:
                break
            stage_photos = [
                a for a in unique_photos
                if a.sub_category == stage and a.photo_id not in selected_ids
                and not getattr(a, "near_duplicate_suppressed", False)
            ]
            if stage_photos:
                # Deterministic: highest confidence, then photo_id
                best = max(stage_photos, key=lambda a: (a.confidence, a.photo_id))
                selected.append(best)
                selected_ids.add(best.photo_id)

        # Second pass: fill remaining with highest confidence (excluding near-duplicates)
        remaining = [
            a for a in unique_photos
            if a.photo_id not in selected_ids
            and not getattr(a, "near_duplicate_suppressed", False)
        ]
        # Deterministic sort: confidence desc, then photo_id
        remaining.sort(key=lambda a: (-a.confidence, a.photo_id))
        for a in remaining:
            if len(selected) >= self.max_service_photos:
                break
            selected.append(a)
            selected_ids.add(a.photo_id)

        # If still not enough (all were near-duplicates), include suppressed ones
        if len(selected) < self.max_service_photos:
            suppressed = [
                a for a in unique_photos
                if a.photo_id not in selected_ids
            ]
            suppressed.sort(key=lambda a: (-a.confidence, a.photo_id))
            for a in suppressed:
                if len(selected) >= self.max_service_photos:
                    break
                selected.append(a)
                selected_ids.add(a.photo_id)

        # Mark selection source (user-selected preserved)
        for a in selected:
            if a.photo_id in user_selected:
                a.selected_source = "user_selected"
            else:
                a.selected_source = "ai_selected"

        return selected, None

    # ─── User Modification Support ────────────────────────────────────────

    def change_safety_photo(
        self,
        photo_id: str,
        analysis_results: List[PhotoAnalysis],
        valid_photo_ids: Set[str],
    ) -> Tuple[Optional[PhotoAnalysis], Optional[str]]:
        """User manually selects a safety photo by photo_id.

        Validates photo_id exists in current draft's photo_candidates.
        Returns (selected_analysis, error_code).
        """
        if photo_id not in valid_photo_ids:
            return None, "invalid_photo_id"
        for a in analysis_results:
            if a.photo_id == photo_id:
                a.selected_source = "user_selected"
                a.verification_required = False
                a.verification_reason = None
                return a, None
        return None, "photo_not_analyzed"

    def add_service_photo(
        self,
        photo_id: str,
        analysis_results: List[PhotoAnalysis],
        current_selected: List[PhotoAnalysis],
        valid_photo_ids: Set[str],
    ) -> Tuple[List[PhotoAnalysis], Optional[str]]:
        """User manually adds a service photo.

        Validates photo_id exists in current draft's photo_candidates.
        Returns (updated_list, error_code).
        """
        if photo_id not in valid_photo_ids:
            return current_selected, "invalid_photo_id"
        current_ids = {x.photo_id for x in current_selected}
        if photo_id in current_ids:
            return current_selected, None  # already selected
        for a in analysis_results:
            if a.photo_id == photo_id:
                a.selected_source = "user_selected"
                current_selected.append(a)
                return current_selected, None
        return current_selected, "photo_not_analyzed"

    def remove_service_photo(
        self,
        photo_id: str,
        current_selected: List[PhotoAnalysis],
    ) -> List[PhotoAnalysis]:
        """User manually removes a service photo."""
        return [a for a in current_selected if a.photo_id != photo_id]

    # ─── Internal Helpers ─────────────────────────────────────────────────

    def _is_original_field_photo(self, photo: PhotoRef) -> bool:
        """Check if photo is an original field photo (not generated/evidence/thumbnail)."""
        if photo.source != "server_original":
            return False
        # Check filename patterns
        name_lower = photo.relative_path.lower()
        excluded_patterns = ["mileage_evidence", "thumbnail", "thumb_", "screenshot", ".tmp"]
        if any(p in name_lower for p in excluded_patterns):
            return False
        # Check path - should be in pictures/ directory
        if "/pictures/" not in photo.relative_path and "pictures/" not in photo.relative_path:
            return False
        return True

    def _resolve_photo_path(self, relative_path: str) -> Optional[Path]:
        """Resolve relative path safely, rejecting traversal/absolute/symlink escape."""
        if not relative_path:
            return None
        if Path(relative_path).is_absolute():
            return None
        if ".." in Path(relative_path).parts:
            return None
        try:
            full = (self.root / relative_path).resolve()
            if not str(full).startswith(str(self.root)):
                return None
            return full
        except OSError:
            return None

    def _mark_near_duplicates(self, photos: List[PhotoAnalysis]) -> List[PhotoAnalysis]:
        """Mark near-duplicate photos using perceptual hash.

        Sets near_duplicate_suppressed=True on lower-confidence duplicates.
        Does NOT delete or remove any photos.
        If <= max photos, all are kept (just marked).
        """
        if len(photos) <= 1:
            return photos

        # Sort by confidence first (higher first) for deterministic marking
        sorted_photos = sorted(photos, key=lambda a: (-a.confidence, a.photo_id))

        for i, photo in enumerate(sorted_photos):
            if not photo.perceptual_hash:
                continue
            # Check against all previously kept (higher confidence) photos
            for j in range(i):
                other = sorted_photos[j]
                if other.perceptual_hash and is_near_duplicate(photo.perceptual_hash, other.perceptual_hash):
                    photo.near_duplicate_suppressed = True
                    break

        return sorted_photos

    def _determine_overall_status(self, results: List[PhotoAnalysis], disabled_count: int, failed_count: int) -> str:
        """Determine overall classification status from results."""
        if not results:
            return "no_photos"
        success_count = sum(1 for r in results if r.analysis_status == "success")
        if disabled_count > 0 and success_count == 0:
            return "disabled"
        if failed_count > 0 and success_count == 0:
            return "failed"
        if success_count > 0:
            return "classified"
        return "failed"

    # ─── Cache (ai_photo_analysis table) ─────────────────────────────────

    def _get_cached_analysis(self, photo: PhotoRef, analysis_model: str) -> Optional[PhotoAnalysis]:
        """Get cached analysis from ai_photo_analysis table.

        Cache key priority: photo_hash + analysis_model + analysis_version.
        relative_path is provenance only - same image at different paths shares cache.
        Validates with Pydantic - bad cache = cache miss.
        """
        if not self.db:
            return None
        try:
            # Lookup by photo_hash + model + version (path-independent)
            row = self.db.execute(
                """select * from ai_photo_analysis
                   where photo_hash = ? and analysis_model = ? and analysis_version = ?
                   order by id desc limit 1""",
                (photo.photo_hash, analysis_model, PHOTO_ANALYSIS_VERSION),
            ).fetchone()
            if not row:
                return None

            # Pydantic strict validation
            try:
                analysis = PhotoAnalysis(
                    photo_id=photo.photo_id,
                    photo_path=row["photo_path"],
                    photo_hash=row["photo_hash"],
                    analysis_model=row["analysis_model"],
                    analysis_version=row["analysis_version"],
                    classification=row["classification"],
                    sub_category=row.get("sub_category"),
                    confidence=row["confidence"],
                    description=row.get("description"),
                    equipment_id=row.get("equipment_id"),
                    capture_time=photo.capture_time,
                    capture_time_source=photo.capture_time_source,
                    analyzed_at=row["analyzed_at"],
                    analysis_status="success",
                )
                # Validate classification enum
                valid = {"safety_person", "equipment", "other", "unknown"}
                if analysis.classification not in valid:
                    return None
                return analysis
            except Exception:
                # Bad cache record - treat as miss
                logger.debug("Cache validation failed for hash %s", photo.photo_hash[:8])
                return None
        except Exception as e:
            logger.debug("Cache read failed: %s", type(e).__name__)
            return None

    def _save_analysis(self, analysis: PhotoAnalysis) -> None:
        """Save analysis to ai_photo_analysis table (upsert).

        Only saves successful results.
        UNIQUE constraint on (photo_path, photo_hash, analysis_model, analysis_version).
        """
        if not self.db or analysis.analysis_status != "success":
            return
        try:
            self.db.execute(
                """insert or replace into ai_photo_analysis
                   (photo_path, photo_hash, analysis_model, analysis_version,
                    classification, sub_category, confidence, description,
                    equipment_id, capture_time, analyzed_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    analysis.photo_path,
                    analysis.photo_hash,
                    analysis.analysis_model,
                    analysis.analysis_version,
                    analysis.classification,
                    analysis.sub_category,
                    analysis.confidence,
                    analysis.description,
                    analysis.equipment_id,
                    analysis.capture_time,
                    analysis.analyzed_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )
        except Exception as e:
            logger.debug("Cache save failed: %s", type(e).__name__)
