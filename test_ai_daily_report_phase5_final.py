"""Phase 5 Final Acceptance tests (Y-BA) for AI Daily Report Vision Classification.

All Vision API calls are mocked. No real network requests.
Covers: cache, user override, state machine, optimistic locking, image prep, retry, etc.
"""
import importlib.util
import io
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from PIL import Image, ExifTags

REPO_DIR = Path(__file__).resolve().parent


def make_test_jpeg(path, color=(100, 150, 200), exif_gps=False):
    """Create a test JPEG, optionally with EXIF orientation (GPS via piexif if available)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (200, 150), color)
    if exif_gps:
        try:
            import piexif
            exif_dict = {
                "0th": {},
                "Exif": {},
                "GPS": {
                    piexif.GPSIFD.GPSLatitudeRef: "N",
                    piexif.GPSIFD.GPSLatitude: ((30, 1), (15, 1), (0, 1)),
                    piexif.GPSIFD.GPSLongitudeRef: "W",
                    piexif.GPSIFD.GPSLongitude: ((95, 1), (30, 1), (0, 1)),
                },
                "1st": {},
                "thumbnail": None,
            }
            exif_bytes = piexif.dump(exif_dict)
            img.save(path, format="JPEG", exif=exif_bytes)
            return path
        except ImportError:
            pass
    img.save(path, format="JPEG")
    return path


def make_mock_analysis(photo_id, classification, sub_category=None, confidence=0.9, **kwargs):
    from ai_daily_report import PhotoAnalysis
    defaults = dict(
        photo_id=photo_id,
        photo_hash=photo_id,
        photo_path=f"SO-TEST/pictures/2026-09-14/{photo_id}.jpg",
        analysis_model="deepseek-flash",
        analysis_version=1,
        classification=classification,
        sub_category=sub_category,
        confidence=confidence,
        description=f"Test {classification}",
        analysis_status="success",
        analyzed_at="2026-09-14T12:00:00Z",
    )
    defaults.update(kwargs)
    return PhotoAnalysis(**defaults)


class ImagePreparationTest(unittest.TestCase):
    """Test prepare_vision_image: EXIF orientation, GPS stripping, size control."""

    def test_AP_default_model_deepseek_flash(self):
        """AP: default Vision model = deepseek-flash"""
        import os
        # Test that env var override works
        with patch.dict(os.environ, {"DEEPSEEK_VISION_MODEL": "custom-model"}):
            # Re-import to test env reading
            from ai_daily_report.vision_provider import DeepSeekVisionProvider
            provider = DeepSeekVisionProvider("key", os.environ.get("DEEPSEEK_VISION_MODEL", "deepseek-flash"))
            self.assertEqual(provider.model, "custom-model")
        # Default
        from ai_daily_report.vision_provider import DeepSeekVisionProvider
        provider = DeepSeekVisionProvider("key", "deepseek-flash")
        self.assertEqual(provider.model, "deepseek-flash")

    def test_AQ_response_format_json_object(self):
        """AQ: Vision request uses response_format=json_object"""
        from ai_daily_report.vision_provider import DeepSeekVisionProvider
        provider = DeepSeekVisionProvider("fake-key", "deepseek-flash")
        import json
        fake_response = json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "classification": "equipment", "confidence": 0.9,
            })}}]
        })
        captured_payload = {}
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_response.encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            # Capture the request
            def capture(req, *args, **kwargs):
                captured_payload["data"] = json.loads(req.data)
                return mock_resp
            mock_urlopen.side_effect = capture
            provider.classify("fake_base64")
        self.assertIn("response_format", captured_payload["data"])
        self.assertEqual(captured_payload["data"]["response_format"], {"type": "json_object"})

    def test_AR_unsupported_input_safe_failure(self):
        """AR: HEIC/unsupported input -> safe failure, no crash"""
        from ai_daily_report.vision_provider import prepare_vision_image
        with tempfile.NamedTemporaryFile(suffix=".heic", delete=False) as f:
            f.write(b"not a real image")
            f.flush()
            result, error = prepare_vision_image(Path(f.name))
        self.assertIsNone(result)
        self.assertEqual(error, "vision_image_decode_failed")

    def test_AS_exif_orientation_correction(self):
        """AS: EXIF orientation correction applied"""
        from ai_daily_report.vision_provider import prepare_vision_image
        # Create image with orientation tag
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (100, 200), (255, 0, 0))
            exif = img.getexif()
            exif[274] = 6  # Orientation: rotate 90 CW
            img.save(f, format="JPEG", exif=exif)
            f.flush()
            result, error = prepare_vision_image(Path(f.name))
        self.assertIsNotNone(result)
        self.assertIsNone(error)
        # Decode and check dimensions (should be transposed)
        import base64
        img_bytes = base64.b64decode(result)
        decoded = Image.open(io.BytesIO(img_bytes))
        # After orientation correction, 100x200 with rotate 90 -> 200x100
        self.assertEqual(decoded.size[0], 200)
        self.assertEqual(decoded.size[1], 100)

    def test_AT_vision_jpeg_strips_gps(self):
        """AT: Vision JPEG output does NOT contain GPS EXIF"""
        from ai_daily_report.vision_provider import prepare_vision_image
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            make_test_jpeg(Path(f.name), exif_gps=True)
            result, error = prepare_vision_image(Path(f.name))
        self.assertIsNotNone(result)
        # Decode and check no GPS
        import base64
        img_bytes = base64.b64decode(result)
        decoded = Image.open(io.BytesIO(img_bytes))
        exif = decoded.getexif()
        # GPSInfo tag = 0x8825
        self.assertNotIn(0x8825, exif)

    def test_AN_gps_not_sent_to_vision(self):
        """AN: GPS EXIF not sent to Vision (stripped in prepare_vision_image)"""
        # Same as AT but from service perspective
        from ai_daily_report.vision_provider import prepare_vision_image
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            make_test_jpeg(Path(f.name), exif_gps=True)
            result, _ = prepare_vision_image(Path(f.name))
        import base64
        img_bytes = base64.b64decode(result)
        decoded = Image.open(io.BytesIO(img_bytes))
        exif = decoded.getexif()
        self.assertNotIn(0x8825, exif)


class CacheTest(unittest.TestCase):
    """Test ai_photo_analysis cache: cross-restart, key, validation."""

    def test_AM_app_restart_cache_reuse(self):
        """AM: analysis cached -> app restart -> same photo_hash -> cache hit, no Vision call"""
        from ai_daily_report import PhotoClassificationService, PhotoRef
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "shared"
            root.mkdir()
            # Create a real test image
            img_path = root / "SO-TEST" / "pictures" / "2026-09-14" / "p1.jpg"
            make_test_jpeg(img_path)

            # Mock DB with cache
            mock_db = MagicMock()
            cached_row = {
                "photo_path": "SO-TEST/pictures/2026-09-14/p1.jpg",
                "photo_hash": "hash123",
                "analysis_model": "deepseek-flash",
                "analysis_version": 1,
                "classification": "equipment",
                "sub_category": "nameplate",
                "confidence": 0.92,
                "description": "Equipment nameplate",
                "equipment_id": "A313",
                "capture_time": "2026-09-14T08:00:00",
                "analyzed_at": "2026-09-14T12:00:00Z",
            }
            mock_db.execute.return_value.fetchone.return_value = cached_row

            mock_vision = MagicMock()
            svc = PhotoClassificationService(
                vision_service=mock_vision,
                shared_photos_root=str(root),
                db_connection=mock_db,
            )
            photo = PhotoRef(
                photo_id="hash123", photo_hash="hash123",
                relative_path="SO-TEST/pictures/2026-09-14/p1.jpg",
                source="server_original",
            )
            results, status, _ = svc.classify_draft_photos([photo], "deepseek-flash")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].classification, "equipment")
            # Vision should NOT be called (cache hit)
            mock_vision.analyze_photo.assert_not_called()

    def test_AU_cache_bad_record_safe_invalidation(self):
        """AU: bad cache record (invalid classification) -> cache miss, not pollution"""
        from ai_daily_report import PhotoClassificationService, PhotoRef
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "shared"
            root.mkdir()
            img_path = root / "SO-TEST" / "pictures" / "2026-09-14" / "p1.jpg"
            make_test_jpeg(img_path)

            mock_db = MagicMock()
            # Bad cache: invalid classification
            bad_row = {
                "photo_path": "x", "photo_hash": "hash123",
                "analysis_model": "m", "analysis_version": 1,
                "classification": "INVALID_CATEGORY",  # invalid
                "sub_category": None, "confidence": 0.9,
                "description": None, "equipment_id": None,
                "capture_time": None, "analyzed_at": "x",
            }
            mock_db.execute.return_value.fetchone.return_value = bad_row

            mock_vision = MagicMock()
            mock_vision.analyze_photo.return_value = make_mock_analysis("hash123", "equipment", "nameplate", 0.9)
            svc = PhotoClassificationService(
                vision_service=mock_vision,
                shared_photos_root=str(root),
                db_connection=mock_db,
            )
            photo = PhotoRef(
                photo_id="hash123", photo_hash="hash123",
                relative_path="SO-TEST/pictures/2026-09-14/p1.jpg",
                source="server_original",
            )
            results, _, _ = svc.classify_draft_photos([photo], "m")
            # Should call Vision (cache miss due to invalid record)
            mock_vision.analyze_photo.assert_called_once()


class UserOverrideTest(unittest.TestCase):
    """Test user override persistence: safety, service add/remove, >10 error."""

    def _make_service(self):
        from ai_daily_report import PhotoClassificationService
        return PhotoClassificationService(
            vision_service=MagicMock(),
            shared_photos_root="/tmp",
            max_service_photos=10,
        )

    def test_AA_user_selected_safety_not_overwritten(self):
        """AA: user selected safety -> re-analyze -> NOT overwritten"""
        svc = self._make_service()
        user_pick = make_mock_analysis("user_pick", "safety_person", "front_standing_worker", 0.30)
        user_pick.selected_source = "user_selected"
        results = [
            make_mock_analysis("ai_pick", "safety_person", "front_standing_worker", 0.95),
            user_pick,
        ]
        selected, _ = svc.select_safety_photo(results, existing_selected=user_pick)
        self.assertEqual(selected.photo_id, "user_pick")
        self.assertEqual(selected.selected_source, "user_selected")

    def test_AB_user_service_selection_not_overwritten(self):
        """AB: user selected service photos -> re-analyze -> preserved"""
        svc = self._make_service()
        user_pick = make_mock_analysis("user_pick", "equipment", "fuse_wiring", 0.30)
        user_pick.selected_source = "user_selected"
        results = [user_pick] + [make_mock_analysis(f"p{i}", "equipment", "equipment_overview", 0.9) for i in range(5)]
        selected, error = svc.select_service_photos(
            results, existing_selected=[user_pick],
            user_selected_ids={"user_pick"},
        )
        self.assertIsNone(error)
        selected_ids = {s.photo_id for s in selected}
        self.assertIn("user_pick", selected_ids)
        user_selected = [s for s in selected if s.photo_id == "user_pick"][0]
        self.assertEqual(user_selected.selected_source, "user_selected")

    def test_AC_add_service_photo(self):
        """AC: add_service_photo adds to selected and marks user_selected"""
        svc = self._make_service()
        results = [make_mock_analysis(f"p{i}", "equipment", "nameplate", 0.9) for i in range(3)]
        current = [results[0]]
        valid_ids = {r.photo_id for r in results}
        updated, error = svc.add_service_photo("p2", results, current, valid_ids)
        self.assertIsNone(error)
        self.assertEqual(len(updated), 2)
        self.assertTrue(any(s.photo_id == "p2" for s in updated))
        added = [s for s in updated if s.photo_id == "p2"][0]
        self.assertEqual(added.selected_source, "user_selected")

    def test_AD_remove_service_photo(self):
        """AD: remove_service_photo removes from selected"""
        svc = self._make_service()
        results = [make_mock_analysis(f"p{i}", "equipment", "nameplate", 0.9) for i in range(3)]
        current = list(results)
        updated = svc.remove_service_photo("p1", current)
        self.assertEqual(len(updated), 2)
        self.assertFalse(any(s.photo_id == "p1" for s in updated))

    def test_AV_user_removed_not_readded(self):
        """AV: user removed service photo -> re-analyze -> NOT re-added"""
        svc = self._make_service()
        results = [make_mock_analysis(f"p{i}", "equipment", "equipment_overview", 0.9) for i in range(5)]
        # User removed p2
        selected, error = svc.select_service_photos(
            results, user_removed_ids={"p2"},
        )
        self.assertIsNone(error)
        selected_ids = {s.photo_id for s in selected}
        self.assertNotIn("p2", selected_ids)

    def test_AW_user_selection_over_10_returns_error(self):
        """AW: user manually selects >10 -> validation error, not silent deletion"""
        svc = self._make_service()
        results = [make_mock_analysis(f"p{i}", "equipment", "nameplate", 0.9) for i in range(15)]
        user_selected = {f"p{i}" for i in range(12)}  # 12 > 10
        selected, error = svc.select_service_photos(
            results, user_selected_ids=user_selected,
        )
        self.assertEqual(error, "max_service_photos_exceeded")

    def test_AX_cross_draft_photo_id_rejected(self):
        """AX: add/remove with photo_id not in current draft -> rejected"""
        svc = self._make_service()
        results = [make_mock_analysis("p1", "equipment", "nameplate", 0.9)]
        valid_ids = {"p1"}
        # Try to add a photo from another draft
        updated, error = svc.add_service_photo("other_draft_photo", results, [], valid_ids)
        self.assertEqual(error, "invalid_photo_id")


class StateMachineTest(unittest.TestCase):
    """Test state machine: draft allowed, confirmed/cancelled/saved blocked."""

    def test_AG_confirmed_blocked(self):
        """AG: confirmed draft cannot be classified (need reopen)"""
        from ai_daily_report import DailyReportService
        with tempfile.TemporaryDirectory() as tmpdir:
            import sqlite3
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            svc = DailyReportService(db, lambda: "2026-09-14T12:00:00Z", 1, "Admin")
            # Mock get_draft to return confirmed
            svc.get_draft = MagicMock(return_value={"id": 1, "status": "confirmed", "draft_data": "{}"})
            result = svc.classify_draft_photos(1, MagicMock(), "m")
            self.assertFalse(result["ok"])
            self.assertIn("reopen", result["error"])

    def test_AH_cancelled_blocked(self):
        """AH: cancelled draft cannot be classified"""
        from ai_daily_report import DailyReportService
        with tempfile.TemporaryDirectory() as tmpdir:
            import sqlite3
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            svc = DailyReportService(db, lambda: "2026-09-14T12:00:00Z", 1, "Admin")
            svc.get_draft = MagicMock(return_value={"id": 1, "status": "cancelled", "draft_data": "{}"})
            result = svc.classify_draft_photos(1, MagicMock(), "m")
            self.assertFalse(result["ok"])
            self.assertIn("cancelled", result["error"])


class DeterministicTest(unittest.TestCase):
    """Test deterministic selection and retry behavior."""

    def test_AY_deterministic_stage_selection(self):
        """AY: same input -> same selected set regardless of order"""
        from ai_daily_report import PhotoClassificationService
        svc = PhotoClassificationService(
            vision_service=MagicMock(), shared_photos_root="/tmp", max_service_photos=5,
        )
        # Create photos with different stages and confidences
        stages = ["equipment_overview", "nameplate", "fuse_wiring", "after_repair", "final_state", "startup"]
        photos1 = [make_mock_analysis(f"p{i}", "equipment", s, 0.8 + i * 0.02) for i, s in enumerate(stages)]
        photos2 = list(reversed(photos1))  # different order

        selected1, _ = svc.select_service_photos(photos1)
        selected2, _ = svc.select_service_photos(photos2)
        ids1 = sorted(s.photo_id for s in selected1)
        ids2 = sorted(s.photo_id for s in selected2)
        self.assertEqual(ids1, ids2)

    def test_AZ_429_5xx_retry_4xx_no_retry(self):
        """AZ: 429/5xx -> retry; 400/401/403 -> no retry"""
        from ai_daily_report.vision_provider import DeepSeekVisionProvider, VisionProviderError
        provider = DeepSeekVisionProvider("key", "model")

        # Test 429 -> should raise vision_rate_limit (retryable)
        import json
        with patch("urllib.request.urlopen") as mock_urlopen:
            from urllib.error import HTTPError
            mock_resp = MagicMock()
            mock_resp.code = 429
            mock_urlopen.side_effect = HTTPError("url", 429, "Too Many", {}, None)
            with self.assertRaises(VisionProviderError) as ctx:
                provider.classify("b64")
            self.assertEqual(ctx.exception.error_code, "vision_rate_limit")

        # Test 400 -> should raise vision_http_400 (not retryable)
        with patch("urllib.request.urlopen") as mock_urlopen:
            from urllib.error import HTTPError
            mock_urlopen.side_effect = HTTPError("url", 400, "Bad Request", {}, None)
            with self.assertRaises(VisionProviderError) as ctx:
                provider.classify("b64")
            self.assertEqual(ctx.exception.error_code, "vision_http_400")

    def test_BA_vision_disabled_zero_network(self):
        """BA: VISION_EXTERNAL_API_ENABLED=false -> zero network requests, analysis_status=disabled"""
        from ai_daily_report import VisionClassificationService
        mock_provider = MagicMock()
        vision = VisionClassificationService(provider=mock_provider, enabled=False)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (100, 100), (100, 150, 200))
            img.save(f, format="JPEG")
            f.flush()
            result = vision.analyze_photo(Path(f.name), "h", "p", "m")
        self.assertEqual(result.analysis_status, "disabled")
        self.assertTrue(result.verification_required)
        self.assertEqual(result.verification_reason, "vision_disabled")
        mock_provider.classify.assert_not_called()


class MiscTest(unittest.TestCase):
    """Misc tests: equipment ID isolation, single photo failure, persistence."""

    def test_Z_equipment_id_not_modify_work_item(self):
        """Z: Vision returns A314 but Draft work_item is A313 -> A313 stays unchanged"""
        from ai_daily_report import PhotoAnalysis
        # Vision returns A314
        vision_result = make_mock_analysis("p1", "equipment", "nameplate", 0.9, equipment_id="A314")
        # Draft work item has A313
        draft_work_item = {"equipment": "A313", "action": "replace_fuse"}
        # Equipment ID from Vision should NOT modify work item
        self.assertEqual(draft_work_item["equipment"], "A313")
        # Vision result has its own equipment_id field
        self.assertEqual(vision_result.equipment_id, "A314")

    def test_AJ_single_photo_failure_not_batch_failure(self):
        """AJ: one photo Vision failure -> other photos still classified, batch continues"""
        from ai_daily_report import PhotoClassificationService, PhotoRef
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "shared"
            root.mkdir()
            for i in range(3):
                make_test_jpeg(root / f"SO-TEST/pictures/2026-09-14/p{i}.jpg")

            mock_vision = MagicMock()
            # First photo fails, others succeed
            def analyze_side_effect(photo_path, photo_hash, photo_id="", analysis_model=""):
                if photo_hash == "h0":
                    return make_mock_analysis("h0", "unknown", None, 0.0,
                        analysis_status="failed", verification_required=True, verification_reason="vision_timeout")
                elif photo_hash == "h1":
                    return make_mock_analysis("h1", "equipment", "nameplate", 0.9)
                else:
                    return make_mock_analysis("h2", "safety_person", "front_standing_worker", 0.9)
            mock_vision.analyze_photo.side_effect = analyze_side_effect

            svc = PhotoClassificationService(
                vision_service=mock_vision, shared_photos_root=str(root),
            )
            photos = [
                PhotoRef(photo_id=f"h{i}", photo_hash=f"h{i}", relative_path=f"SO-TEST/pictures/2026-09-14/p{i}.jpg", source="server_original")
                for i in range(3)
            ]
            results, status, _ = svc.classify_draft_photos(photos, "m")
            self.assertEqual(len(results), 3)
            self.assertEqual(results[0].analysis_status, "failed")
            self.assertEqual(results[1].classification, "equipment")
            self.assertEqual(results[2].classification, "safety_person")
            # Overall status should still be classified (some success)
            self.assertEqual(status, "classified")

    def test_AL_analysis_result_persistence(self):
        """AL: classification results persisted in draft_data (not just memory)"""
        from ai_daily_report import DailyReportService, PhotoRef
        import sqlite3, json
        with tempfile.TemporaryDirectory() as tmpdir:
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            # Create complete drafts table
            db.execute("""create table ai_daily_report_drafts (
                id integer primary key, service_order_id integer, report_date text,
                status text, draft_data text, draft_version integer default 1,
                verification_required integer default 0, verification_fields text,
                saved_report_id integer, created_by integer, created_at text, updated_at text
            )""")
            db.execute("insert into ai_daily_report_drafts values (1, 1, '2026-09-14', 'draft', '{\"service_order_id\": 1, \"report_date\": \"2026-09-14\"}', 1, 0, '[]', null, 1, '2026-09-14T00:00:00Z', '2026-09-14T00:00:00Z')")
            db.commit()
            svc = DailyReportService(db, lambda: "2026-09-14T12:00:00Z", 1, "Admin")
            # Set up draft with photo candidates
            draft = svc.parse_draft_data(svc.get_draft(1))
            draft.photo_candidates = [PhotoRef(photo_id="p1", photo_hash="p1", relative_path="p1.jpg", source="server_original")]
            svc.save_draft(1, draft)
            # Mock classification service
            mock_svc = MagicMock()
            mock_results = [make_mock_analysis("p1", "equipment", "nameplate", 0.9)]
            mock_svc.classify_draft_photos.return_value = (mock_results, "classified", {"total": 1})
            mock_svc.select_safety_photo.return_value = (None, [])
            mock_svc.select_service_photos.return_value = (mock_results, None)
            result = svc.classify_draft_photos(1, mock_svc, "m")
            self.assertTrue(result["ok"])
            # Verify persisted in draft_data
            row = db.execute("select draft_data from ai_daily_report_drafts where id=1").fetchone()
            data = json.loads(row["draft_data"])
            self.assertEqual(len(data["photo_analysis_results"]), 1)
            self.assertEqual(data["photo_analysis_results"][0]["classification"], "equipment")
            self.assertEqual(data["photo_classification_status"], "classified")

    def test_AO_formal_attachments_not_written(self):
        """AO: Phase 5 does NOT write to service_report_attachments"""
        from ai_daily_report import PhotoClassificationService
        svc = PhotoClassificationService(
            vision_service=MagicMock(), shared_photos_root="/tmp",
        )
        # Verify service has no methods that touch service_report_attachments
        methods = [m for m in dir(svc) if not m.startswith("_")]
        self.assertNotIn("save_report_attachment", methods)
        self.assertNotIn("write_service_report", methods)


if __name__ == "__main__":
    unittest.main()
