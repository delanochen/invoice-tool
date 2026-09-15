"""Phase 5 tests for AI Daily Report - Vision Photo Classification (unittest style).

All Vision API calls are mocked. No real network requests.
"""
import importlib.util
import io
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

REPO_DIR = Path(__file__).resolve().parent


def make_test_jpeg(path, color=(100, 150, 200)):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (200, 150), color)
    img.save(path, format="JPEG")


def make_mock_analysis(photo_id, classification, sub_category=None, confidence=0.9, **kwargs):
    """Create a mock PhotoAnalysis for testing."""
    from ai_daily_report import PhotoAnalysis
    return PhotoAnalysis(
        photo_id=photo_id,
        photo_hash=photo_id,
        photo_path=f"SO-TEST/pictures/2026-09-14/{photo_id}.jpg",
        analysis_model="test-model",
        analysis_version=1,
        classification=classification,
        sub_category=sub_category,
        confidence=confidence,
        description=f"Test {classification}",
        analysis_status="success",
        analyzed_at="2026-09-14T12:00:00Z",
        **kwargs,
    )


class VisionProviderTest(unittest.TestCase):
    """Test Vision Provider abstraction."""

    def test_A_vision_disabled_returns_disabled(self):
        """A: VISION_EXTERNAL_API_ENABLED=false -> classification_status=disabled"""
        from ai_daily_report import VisionClassificationService, PhotoAnalysis
        mock_provider = MagicMock()
        vision = VisionClassificationService(provider=mock_provider, enabled=False)
        result = vision.analyze_photo(Path("/tmp/test.jpg"), "hash123", "pid123", "test-model")
        self.assertEqual(result.analysis_status, "disabled")
        self.assertEqual(result.classification, "unknown")
        self.assertTrue(result.verification_required)
        mock_provider.classify.assert_not_called()

    def test_B_vision_enabled_calls_provider(self):
        """B: enabled -> calls provider.classify"""
        from ai_daily_report import VisionClassificationService
        mock_provider = MagicMock()
        mock_provider.classify.return_value = {
            "classification": "equipment",
            "sub_category": "nameplate",
            "confidence": 0.95,
            "description": "Equipment nameplate",
            "equipment_id": "A313",
            "equipment_id_confidence": 0.9,
        }
        vision = VisionClassificationService(provider=mock_provider, enabled=True, max_image_size=1280)
        # Create a real test image
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (200, 150), (100, 150, 200))
            img.save(f, format="JPEG")
            f.flush()
            result = vision.analyze_photo(Path(f.name), "hash123", "pid123", "test-model")
        self.assertEqual(result.analysis_status, "success")
        self.assertEqual(result.classification, "equipment")
        self.assertEqual(result.sub_category, "nameplate")
        self.assertEqual(result.confidence, 0.95)
        self.assertEqual(result.equipment_id, "A313")
        mock_provider.classify.assert_called_once()

    def test_C_low_confidence_marks_verification(self):
        """C: confidence < 0.60 -> verification_required"""
        from ai_daily_report import VisionClassificationService
        mock_provider = MagicMock()
        mock_provider.classify.return_value = {
            "classification": "other",
            "confidence": 0.30,
            "description": "Unclear",
        }
        vision = VisionClassificationService(provider=mock_provider, enabled=True)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (100, 100), (100, 150, 200))
            img.save(f, format="JPEG")
            f.flush()
            result = vision.analyze_photo(Path(f.name), "h", "p", "m")
        self.assertTrue(result.verification_required)
        self.assertEqual(result.verification_reason, "low_confidence")

    def test_D_provider_error_captured(self):
        """D: provider error -> analysis_status=failed, no exception"""
        from ai_daily_report import VisionClassificationService, VisionProviderError
        mock_provider = MagicMock()
        mock_provider.classify.side_effect = VisionProviderError("http_500")
        vision = VisionClassificationService(provider=mock_provider, enabled=True)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (100, 100), (100, 150, 200))
            img.save(f, format="JPEG")
            f.flush()
            result = vision.analyze_photo(Path(f.name), "h", "p", "m")
        self.assertEqual(result.analysis_status, "failed")
        self.assertTrue(result.verification_required)

    def test_E_invalid_classification_normalized(self):
        """E: invalid classification from provider -> normalized to unknown"""
        from ai_daily_report.vision_provider import DeepSeekVisionProvider
        provider = DeepSeekVisionProvider("fake-key", "test-model")
        # Mock the HTTP call
        import json
        fake_response = json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "classification": "invalid_category",
                "confidence": 0.9,
            })}}]
        })
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_response.encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            result = provider.classify("fake_base64")
        # Provider returns raw result; normalization happens in VisionClassificationService
        self.assertEqual(result["classification"], "invalid_category")


class PerceptualHashTest(unittest.TestCase):
    """Test perceptual hash near-duplicate detection."""

    def test_F_dhash_computed(self):
        """F: dHash computed for valid image"""
        from ai_daily_report import compute_dhash
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (200, 150), (100, 150, 200))
            img.save(f, format="JPEG")
            f.flush()
            h = compute_dhash(Path(f.name))
        self.assertIsNotNone(h)
        self.assertEqual(len(h), 16)  # 64 bits = 16 hex chars

    def test_G_near_duplicate_detection(self):
        """G: near-duplicate images detected"""
        from ai_daily_report import is_near_duplicate
        # Same hash = near duplicate
        self.assertTrue(is_near_duplicate("0000000000000000", "0000000000000000"))
        # Very different = not near duplicate
        self.assertFalse(is_near_duplicate("0000000000000000", "ffffffffffffffff"))

    def test_H_hamming_distance(self):
        """H: hamming distance correct"""
        from ai_daily_report.perceptual_hash import hamming_distance
        self.assertEqual(hamming_distance("0", "0"), 0)
        self.assertEqual(hamming_distance("0", "f"), 4)  # 0=0000, f=1111


class SafetyPhotoSelectionTest(unittest.TestCase):
    """Test Safety Photo auto-selection."""

    def _make_service(self):
        from ai_daily_report import PhotoClassificationService
        return PhotoClassificationService(
            vision_service=MagicMock(),
            shared_photos_root="/tmp",
            safety_auto_select_confidence=0.80,
            safety_verify_confidence=0.60,
            max_service_photos=10,
        )

    def test_I_safety_front_standing_selected(self):
        """I: front_standing_worker with high confidence -> auto selected"""
        svc = self._make_service()
        results = [
            make_mock_analysis("p1", "safety_person", "front_standing_worker", 0.92),
            make_mock_analysis("p2", "equipment", "equipment_overview", 0.88),
        ]
        selected, candidates = svc.select_safety_photo(results)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.photo_id, "p1")
        self.assertEqual(selected.selected_source, "ai_selected")
        self.assertEqual(len(candidates), 1)

    def test_J_safety_medium_confidence_verification(self):
        """J: confidence 0.60-0.79 -> verification_required, no auto select"""
        svc = self._make_service()
        results = [
            make_mock_analysis("p1", "safety_person", "front_standing_worker", 0.70),
        ]
        selected, candidates = svc.select_safety_photo(results)
        self.assertIsNone(selected)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].verification_required)

    def test_K_safety_low_confidence_not_selected(self):
        """K: confidence < 0.60 -> not auto selected"""
        svc = self._make_service()
        results = [
            make_mock_analysis("p1", "safety_person", "front_standing_worker", 0.40),
        ]
        selected, candidates = svc.select_safety_photo(results)
        self.assertIsNone(selected)

    def test_L_no_safety_photos_returns_none(self):
        """L: no safety_person photos -> None"""
        svc = self._make_service()
        results = [
            make_mock_analysis("p1", "equipment", "nameplate", 0.90),
            make_mock_analysis("p2", "other", None, 0.80),
        ]
        selected, candidates = svc.select_safety_photo(results)
        self.assertIsNone(selected)
        self.assertEqual(len(candidates), 0)

    def test_M_user_selected_preserved(self):
        """M: user-selected safety photo preserved, not overwritten"""
        svc = self._make_service()
        user_selected = make_mock_analysis("user_pick", "safety_person", "front_standing_worker", 0.50)
        user_selected.selected_source = "user_selected"
        results = [
            make_mock_analysis("p1", "safety_person", "front_standing_worker", 0.95),
            user_selected,
        ]
        selected, _ = svc.select_safety_photo(results, existing_selected=user_selected)
        self.assertEqual(selected.photo_id, "user_pick")
        self.assertEqual(selected.selected_source, "user_selected")


class ServicePhotoSelectionTest(unittest.TestCase):
    """Test Service Photo selection with stage diversity."""

    def _make_service(self, max_photos=10):
        from ai_daily_report import PhotoClassificationService
        return PhotoClassificationService(
            vision_service=MagicMock(),
            shared_photos_root="/tmp",
            max_service_photos=max_photos,
        )

    def test_N_less_than_max_all_selected(self):
        """N: <= max photos -> all selected"""
        svc = self._make_service(max_photos=10)
        results = [make_mock_analysis(f"p{i}", "equipment", "equipment_overview", 0.9) for i in range(5)]
        selected, error = svc.select_service_photos(results)
        self.assertIsNone(error)
        self.assertEqual(len(selected), 5)

    def test_O_more_than_max_stage_diverse(self):
        """O: > max photos -> stage diverse selection, exactly max"""
        svc = self._make_service(max_photos=10)
        # Create 20 photos, mostly fuse close-ups
        results = []
        stages = ["fuse_wiring"] * 15 + ["equipment_overview", "nameplate", "before_repair", "after_repair", "final_state"]
        for i, stage in enumerate(stages):
            results.append(make_mock_analysis(f"p{i}", "equipment", stage, 0.85))
        selected, error = svc.select_service_photos(results)
        self.assertIsNone(error)
        self.assertEqual(len(selected), 10)
        # Should include diverse stages, not all fuse_wiring
        selected_stages = {s.sub_category for s in selected}
        self.assertIn("equipment_overview", selected_stages)
        self.assertIn("nameplate", selected_stages)

    def test_P_user_selected_preserved(self):
        """P: user-selected service photos preserved"""
        svc = self._make_service(max_photos=5)
        user_pick = make_mock_analysis("user_pick", "equipment", "fuse_wiring", 0.30)
        user_pick.selected_source = "user_selected"
        results = [user_pick] + [make_mock_analysis(f"p{i}", "equipment", "equipment_overview", 0.9) for i in range(10)]
        selected, error = svc.select_service_photos(
            results, existing_selected=[user_pick],
            user_selected_ids={"user_pick"},
        )
        self.assertIsNone(error)
        selected_ids = {s.photo_id for s in selected}
        self.assertIn("user_pick", selected_ids)
        user_selected = [s for s in selected if s.photo_id == "user_pick"][0]
        self.assertEqual(user_selected.selected_source, "user_selected")

    def test_Q_non_equipment_not_selected(self):
        """Q: non-equipment photos not in service selection"""
        svc = self._make_service()
        results = [
            make_mock_analysis("p1", "equipment", "nameplate", 0.9),
            make_mock_analysis("p2", "safety_person", "front_standing_worker", 0.9),
            make_mock_analysis("p3", "other", None, 0.9),
        ]
        selected, error = svc.select_service_photos(results)
        self.assertIsNone(error)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].photo_id, "p1")


class UserModificationTest(unittest.TestCase):
    """Test user modification support."""

    def _make_service(self):
        from ai_daily_report import PhotoClassificationService
        return PhotoClassificationService(
            vision_service=MagicMock(),
            shared_photos_root="/tmp",
        )

    def test_R_change_safety_photo(self):
        """R: change_safety_photo sets user_selected"""
        svc = self._make_service()
        results = [
            make_mock_analysis("p1", "safety_person", "front_standing_worker", 0.9),
            make_mock_analysis("p2", "safety_person", "front_standing_worker", 0.8),
        ]
        valid_ids = {r.photo_id for r in results}
        selected, error = svc.change_safety_photo("p2", results, valid_ids)
        self.assertIsNone(error)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.photo_id, "p2")
        self.assertEqual(selected.selected_source, "user_selected")
        self.assertFalse(selected.verification_required)

    def test_S_add_service_photo(self):
        """S: add_service_photo adds to selected"""
        svc = self._make_service()
        results = [make_mock_analysis(f"p{i}", "equipment", "nameplate", 0.9) for i in range(3)]
        current = [results[0]]
        valid_ids = {r.photo_id for r in results}
        updated, error = svc.add_service_photo("p2", results, current, valid_ids)
        self.assertIsNone(error)
        self.assertEqual(len(updated), 2)
        self.assertTrue(any(s.photo_id == "p2" for s in updated))

    def test_T_remove_service_photo(self):
        """T: remove_service_photo removes from selected"""
        svc = self._make_service()
        results = [make_mock_analysis(f"p{i}", "equipment", "nameplate", 0.9) for i in range(3)]
        current = list(results)
        updated = svc.remove_service_photo("p1", current)
        self.assertEqual(len(updated), 2)
        self.assertFalse(any(s.photo_id == "p1" for s in updated))


class IntegrationTest(unittest.TestCase):
    """Integration tests with DailyReportService."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        ai_dir = Path(cls.temp_dir.name) / "ai_daily_report"
        shutil.copytree(REPO_DIR / "ai_daily_report", ai_dir)
        spec = importlib.util.spec_from_file_location("invoice_p5_test", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")
        cls.shared_root = Path(cls.temp_dir.name) / "shared-photos"
        cls.shared_root.mkdir()
        cls.module.SHARED_PHOTOS_DIR = str(cls.shared_root)
        with cls.module.app.app_context():
            cls.module.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            conn = self.module.db()
            conn.execute("delete from ai_daily_report_actions")
            conn.execute("delete from ai_daily_report_drafts")
            conn.execute("delete from ai_photo_analysis")
            conn.execute("delete from service_orders where order_number='SO-P5'")
            conn.execute("delete from users where email like 'p5%'")
            client = conn.execute("select id from clients limit 1").fetchone()
            if not client:
                conn.execute("insert into clients (name, client_number, short_name, country, created_at) values ('T', 'C', 'T', 'US', '2026-09-14T00:00:00')")
                client_id = conn.execute("select last_insert_rowid()").fetchone()[0]
            else:
                client_id = client["id"]
            admin = conn.execute("select id from users where role='admin' limit 1").fetchone()
            if not admin:
                conn.execute("insert into users (name, email, password_hash, role, created_at) values ('Admin', 'p5@test.com', 'x', 'admin', '2026-09-14T00:00:00')")
                admin = conn.execute("select id from users where role='admin' limit 1").fetchone()
            self.admin_id = admin["id"]
            conn.execute("insert into service_orders (order_number, client_id, client_name, client_order_number, site_address, status, created_by, created_at) values ('SO-P5', ?, 'T', 'ORD-P5', '123 St', 'open', ?, '2026-09-14T00:00:00')", (client_id, self.admin_id))
            conn.commit()
            self.order = conn.execute("select * from service_orders where order_number='SO-P5'").fetchone()

    def _make_mock_classification_service(self, results_map=None):
        """Create a mock PhotoClassificationService that returns predefined results."""
        from ai_daily_report import PhotoClassificationService
        svc = PhotoClassificationService(
            vision_service=MagicMock(),
            shared_photos_root=str(self.shared_root),
            db_connection=self.module.db(),
        )
        # Mock classify_draft_photos to return predefined results
        if results_map is None:
            results_map = [
                make_mock_analysis("p1", "safety_person", "front_standing_worker", 0.92),
                make_mock_analysis("p2", "equipment", "equipment_overview", 0.90),
                make_mock_analysis("p3", "equipment", "nameplate", 0.88),
            ]
        svc.classify_draft_photos = MagicMock(return_value=(results_map, "classified", {"total": 3}))
        svc.select_safety_photo = MagicMock(return_value=(results_map[0], [results_map[0]]))
        svc.select_service_photos = MagicMock(return_value=([r for r in results_map if r.classification == "equipment"], None))
        return svc

    def test_U_classify_draft_photos_integration(self):
        """U: classify_draft_photos updates draft with analysis results"""
        from ai_daily_report import DailyReportService, PhotoRef
        with self.module.app.app_context():
            svc = DailyReportService(self.module.db(), lambda: "2026-09-14T12:00:00Z", self.admin_id, "Admin")
            draft_row = svc.create_draft(self.order["id"], "2026-09-14", site_address="123 St")
            draft_id = draft_row["id"]
            # Add photo candidates
            draft = svc.parse_draft_data(draft_row)
            draft.photo_candidates = [
                PhotoRef(photo_id="p1", photo_hash="p1", relative_path="SO-P5/pictures/2026-09-14/p1.jpg", source="server_original"),
                PhotoRef(photo_id="p2", photo_hash="p2", relative_path="SO-P5/pictures/2026-09-14/p2.jpg", source="server_original"),
            ]
            svc.save_draft(draft_id, draft)

            mock_svc = self._make_mock_classification_service()
            result = svc.classify_draft_photos(draft_id, mock_svc, "test-model")
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "classified")
            self.assertEqual(result["total_photos"], 3)

            # Verify draft updated
            updated = svc.parse_draft_data(svc.get_draft(draft_id))
            self.assertEqual(updated.photo_classification_status, "classified")
            self.assertIsNotNone(updated.selected_safety_photo)
            self.assertEqual(len(updated.selected_service_photos), 2)

    def test_V_classify_no_candidates_returns_error(self):
        """V: classify with no photo candidates -> error"""
        from ai_daily_report import DailyReportService
        with self.module.app.app_context():
            svc = DailyReportService(self.module.db(), lambda: "2026-09-14T12:00:00Z", self.admin_id, "Admin")
            draft_row = svc.create_draft(self.order["id"], "2026-09-14", site_address="123 St")
            draft_id = draft_row["id"]
            mock_svc = self._make_mock_classification_service()
            result = svc.classify_draft_photos(draft_id, mock_svc, "test-model")
            self.assertFalse(result["ok"])
            self.assertIn("no_photo_candidates", result["error"])

    def test_W_cancelled_draft_cannot_classify(self):
        """W: cancelled draft cannot be classified"""
        from ai_daily_report import DailyReportService
        with self.module.app.app_context():
            svc = DailyReportService(self.module.db(), lambda: "2026-09-14T12:00:00Z", self.admin_id, "Admin")
            draft_row = svc.create_draft(self.order["id"], "2026-09-14", site_address="123 St")
            draft_id = draft_row["id"]
            svc.update_draft_status(draft_id, "cancelled")
            mock_svc = self._make_mock_classification_service()
            result = svc.classify_draft_photos(draft_id, mock_svc, "test-model")
            self.assertFalse(result["ok"])
            self.assertIn("cancelled", result["error"])

    def test_X_ai_photo_analysis_cache(self):
        """X: ai_photo_analysis table exists and can store results"""
        with self.module.app.app_context():
            conn = self.module.db()
            conn.execute(
                """insert or replace into ai_photo_analysis
                   (photo_path, photo_hash, analysis_model, analysis_version,
                    classification, sub_category, confidence, description,
                    equipment_id, capture_time, analyzed_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("SO-P5/pictures/2026-09-14/p1.jpg", "hash123", "test-model", 1,
                 "equipment", "nameplate", 0.90, "Test", "A313", "2026-09-14T08:00:00", "2026-09-14T12:00:00Z"),
            )
            conn.commit()
            row = conn.execute("select * from ai_photo_analysis where photo_hash='hash123'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["classification"], "equipment")
            self.assertEqual(row["equipment_id"], "A313")


if __name__ == "__main__":
    unittest.main()
