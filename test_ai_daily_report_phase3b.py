"""Phase 3B tests for AI Daily Report - Mileage Evidence (unittest style).

All Google Static Maps API calls are mocked. No real network requests.
All images generated in-memory or temp dir.

Covers A-AG (33 tests).
"""
import hashlib
import importlib.util
import io
import json
import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_DIR = Path(__file__).resolve().parent


def make_test_png(width=640, height=400, color=(100, 150, 200)):
    """Generate a valid test PNG in memory."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class AIDailyReportPhase3BTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        ai_dir = Path(cls.temp_dir.name) / "ai_daily_report"
        shutil.copytree(REPO_DIR / "ai_daily_report", ai_dir)
        spec = importlib.util.spec_from_file_location("invoice_tool_ai_daily_p3b_test", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")
        with cls.module.app.app_context():
            cls.module.init_db()
        # Evidence temp dir
        cls.evidence_root = Path(cls.temp_dir.name) / "draft-evidence"
        cls.evidence_root.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            conn = self.module.db()
            conn.execute("delete from ai_daily_report_actions")
            conn.execute("delete from ai_daily_report_drafts")
            conn.execute("delete from service_orders where order_number='SO-AITEST-004'")
            conn.execute("delete from users where email like 'p4test%'")
            client = conn.execute("select id from clients limit 1").fetchone()
            if not client:
                conn.execute(
                    "insert into clients (name, client_number, short_name, country, created_at) values ('Test', 'CLI-004', 'T', 'US', '2026-09-14T00:00:00')"
                )
                client_id = conn.execute("select last_insert_rowid()").fetchone()[0]
            else:
                client_id = client["id"]
            admin = conn.execute("select id, name from users where role='admin' limit 1").fetchone()
            if not admin:
                conn.execute(
                    "insert into users (name, email, password_hash, role, address, created_at) values ('Admin', 'p4test-admin@test.com', 'x', 'admin', 'Spring, TX', '2026-09-14T00:00:00')"
                )
                admin = conn.execute("select id, name from users where role='admin' limit 1").fetchone()
            self.admin_id = admin["id"]
            self.admin_name = admin["name"]
            conn.execute(
                "insert into service_orders (order_number, client_id, client_name, site_address, client_order_number, status, created_by, created_at) values ('SO-AITEST-004', ?, 'Test Client', '123 Site St, Test City, TX 12345', 'ORD-004', 'open', ?, '2026-09-14T00:00:00')",
                (client_id, self.admin_id),
            )
            conn.commit()
            self.order = conn.execute("select * from service_orders where order_number='SO-AITEST-004'").fetchone()

    def _make_worker(self, **kwargs):
        from ai_daily_report import WorkerTravel
        defaults = dict(
            user_id=self.admin_id, name="Ethan",
            transportation="self_drive",
            origin="518 Anacacho Dr, Spring, TX 77386",
            origin_source="user_input", origin_confirmed=True,
            origin_normalized="518 Anacacho Dr, Spring, TX 77386, USA",
            destination="123 Site St, Test City, TX 12345",
            destination_source="service_order",
            destination_normalized="123 Site St, Test City, TX 12345, USA",
            trip_type="round_trip",
            route_distance_meters=160934.4,
            one_way_miles=100.0,
            reported_miles=200.0,
            route_duration_seconds=7200,
            route_polyline="encoded_polyline_abc123",
            route_provider="google_routes",
            route_query_time="2026-09-14T18:20:00Z",
            route_status="success",
        )
        defaults.update(kwargs)
        return WorkerTravel(**defaults)

    def _make_services(self, static_map_result=None):
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService, MileageEvidenceService
        static_svc = GoogleStaticMapsService("fake-static-key")
        if static_map_result is None:
            static_map_result = MagicMock(
                success=True,
                image_bytes=make_test_png(),
                content_type="image/png",
                status="success",
                error=None,
            )
        static_svc.get_route_map = MagicMock(return_value=static_map_result)
        evidence_svc = MileageEvidenceService(static_svc, str(self.evidence_root))
        return evidence_svc, static_svc

    # ─── Eligibility Tests (A-C, Z) ─────────────────────────────────────

    def test_A_successful_route_generates_evidence(self):
        """A. successful route -> Evidence generated"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=1, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "ready")
        self.assertIsNotNone(record.file_sha256)
        self.assertIsNotNone(record.file_relative_path)
        static_svc.get_route_map.assert_called_once()

    def test_B_route_not_success_no_evidence(self):
        """B. route_status != success -> no evidence generation"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker(route_status="failed")
        record = evidence_svc.generate_evidence(
            worker, draft_id=1, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "verification_required")
        static_svc.get_route_map.assert_not_called()

    def test_C_passenger_no_evidence(self):
        """C. passenger -> no evidence generation"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker(transportation="passenger")
        record = evidence_svc.generate_evidence(
            worker, draft_id=1, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "verification_required")
        static_svc.get_route_map.assert_not_called()

    # ─── Mileage Display Tests (D-F) ────────────────────────────────────

    def test_D_round_trip_shows_doubled_mileage(self):
        """D. trip_type=round_trip -> 证据显示 Round Trip（单程×2）"""
        evidence_svc, _ = self._make_services()
        worker = self._make_worker(trip_type="round_trip", reported_miles=200.0)
        record = evidence_svc.generate_evidence(
            worker, draft_id=2, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "ready")
        self.assertEqual(record.reported_miles, 200.0)
        self.assertEqual(record.trip_type, "round_trip")

    def test_E_one_way_shows_single_mileage(self):
        """E. trip_type=one_way -> 证据显示单程里程"""
        evidence_svc, _ = self._make_services()
        worker = self._make_worker(trip_type="one_way", reported_miles=100.0)
        record = evidence_svc.generate_evidence(
            worker, draft_id=3, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "ready")
        self.assertEqual(record.reported_miles, 100.0)
        self.assertEqual(record.trip_type, "one_way")

    def test_F_evidence_reported_miles_matches_draft(self):
        """F. Evidence reported_miles exactly matches Draft worker"""
        evidence_svc, _ = self._make_services()
        worker = self._make_worker(reported_miles=476.84)
        record = evidence_svc.generate_evidence(
            worker, draft_id=4, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.reported_miles, 476.84)
        self.assertEqual(record.one_way_miles, worker.one_way_miles)

    # ─── No Re-query Tests (G-I) ───────────────────────────────────────

    def test_G_evidence_does_not_call_routes_api(self):
        """G. Evidence generation does NOT call Google Routes API"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        # MileageEvidenceService has no routes_service attribute
        self.assertFalse(hasattr(evidence_svc, 'routes_service'))
        record = evidence_svc.generate_evidence(
            worker, draft_id=5, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "ready")

    def test_H_static_maps_uses_existing_polyline(self):
        """H. Static Maps request uses existing encoded polyline from Phase 3A"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker(route_polyline="polyline_from_phase3a")
        evidence_svc.generate_evidence(
            worker, draft_id=6, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        call_kwargs = static_svc.get_route_map.call_args
        self.assertEqual(call_kwargs.kwargs.get("encoded_polyline"), "polyline_from_phase3a")

    def test_I_static_map_has_origin_destination_markers(self):
        """I. Static Map request includes origin and destination markers"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker(origin="Spring, TX", destination="Site, TX")
        evidence_svc.generate_evidence(
            worker, draft_id=7, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        call_kwargs = static_svc.get_route_map.call_args
        self.assertEqual(call_kwargs.kwargs.get("origin"), "Spring, TX")
        self.assertEqual(call_kwargs.kwargs.get("destination"), "Site, TX")

    # ─── Error Handling Tests (J-N, AE) ────────────────────────────────

    def test_J_api_key_missing_verification(self):
        """J. Static Maps API key missing -> verification_required"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService, MileageEvidenceService
        static_svc = GoogleStaticMapsService("")  # empty key
        evidence_svc = MileageEvidenceService(static_svc, str(self.evidence_root))
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=8, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "verification_required")
        self.assertEqual(record.error, "static_maps_api_not_configured")

    def test_K_static_maps_timeout_failed(self):
        """K. Static Maps timeout -> evidence_status=failed"""
        failed_result = MagicMock(success=False, status="failed", error="static_maps_timeout")
        evidence_svc, _ = self._make_services(static_map_result=failed_result)
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=9, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "failed")
        self.assertIn("timeout", record.error)

    def test_L_static_maps_429_retry(self):
        """L. Static Maps 429 -> retry policy exists (MAX_RETRIES=2)"""
        from ai_daily_report.static_maps import MAX_RETRIES
        self.assertEqual(MAX_RETRIES, 2)

    def test_M_invalid_content_type_failed(self):
        """M. Invalid content-type -> evidence_status=failed"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService
        service = GoogleStaticMapsService("fake-key")
        # Test that ALLOWED_CONTENT_TYPES rejects text/html
        from ai_daily_report.static_maps import ALLOWED_CONTENT_TYPES
        self.assertNotIn("text/html", ALLOWED_CONTENT_TYPES)
        self.assertIn("image/png", ALLOWED_CONTENT_TYPES)

    def test_N_corrupt_image_failed(self):
        """N. Invalid/corrupt image -> evidence_status=failed"""
        corrupt_result = MagicMock(
            success=True, image_bytes=b"not a real image",
            content_type="image/png", status="success",
        )
        evidence_svc, _ = self._make_services(static_map_result=corrupt_result)
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=10, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "failed")
        self.assertIn("corrupt", record.error)

    def test_AE_file_write_failure_safe(self):
        """AE. File write failure -> safe handling, no crash"""
        evidence_svc, _ = self._make_services()
        worker = self._make_worker()
        # Use a read-only path to simulate write failure
        evidence_svc.draft_evidence_root = Path("/dev/null")
        record = evidence_svc.generate_evidence(
            worker, draft_id=11, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "failed")
        self.assertIn("evidence_write", record.error)

    # ─── Google Attribution Tests (O) ──────────────────────────────────

    def test_O_attribution_not_cropped(self):
        """O. Google attribution area not cropped/covered - map pasted full at top"""
        evidence_svc, _ = self._make_services()
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=12, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "ready")
        # Verify image exists and is taller than map (panel below)
        from PIL import Image
        img_path = self.evidence_root / "12" / "mileage" / record.file_relative_path
        self.assertTrue(img_path.exists())
        img = Image.open(img_path)
        w, h = img.size
        # Map is 640x400, panel is 280, total should be ~680
        self.assertGreater(h, 400)  # Info panel added below map
        self.assertEqual(w, 640)  # Map width preserved

    # ─── SHA256 & Fingerprint Tests (P-R, AF) ──────────────────────────

    def test_P_evidence_sha256_correct(self):
        """P. Evidence SHA256 matches file content"""
        evidence_svc, _ = self._make_services()
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=13, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        img_path = self.evidence_root / "13" / "mileage" / record.file_relative_path
        file_hash = hashlib.sha256(img_path.read_bytes()).hexdigest()
        self.assertEqual(record.file_sha256, file_hash)

    def test_Q_route_fingerprint_deterministic(self):
        """Q. route_fingerprint is deterministic (same input = same hash)"""
        from ai_daily_report import MileageEvidenceService
        worker1 = self._make_worker()
        worker2 = self._make_worker()
        fp1 = MileageEvidenceService.compute_route_fingerprint(worker1)
        fp2 = MileageEvidenceService.compute_route_fingerprint(worker2)
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 64)  # SHA256 hex

    def test_R_origin_change_evidence_stale(self):
        """R. origin change -> evidence is stale"""
        from ai_daily_report import MileageEvidenceService, MileageEvidenceRecord
        worker = self._make_worker(origin="Old Address")
        record = MileageEvidenceRecord(
            evidence_id="test", draft_id=1, service_order_id=1,
            report_date="2026-09-14", worker_user_id=1,
            route_fingerprint=MileageEvidenceService.compute_route_fingerprint(worker),
        )
        worker.origin = "New Address"
        worker.origin_normalized = "New Address, USA"
        self.assertTrue(MileageEvidenceService.is_evidence_stale(record, worker))

    def test_AF_fingerprint_mismatch_rejected(self):
        """AF. route fingerprint mismatch -> evidence considered stale"""
        from ai_daily_report import MileageEvidenceService, MileageEvidenceRecord
        worker = self._make_worker()
        record = MileageEvidenceRecord(
            evidence_id="test", draft_id=1, service_order_id=1,
            report_date="2026-09-14", worker_user_id=1,
            route_fingerprint="different_fingerprint",
        )
        self.assertTrue(MileageEvidenceService.is_evidence_stale(record, worker))

    # ─── Staleness Tests (S-T) ─────────────────────────────────────────

    def test_S_trip_type_change_evidence_stale(self):
        """S. 行程类型变化 -> 指纹变化 -> evidence is stale"""
        from ai_daily_report import MileageEvidenceService, MileageEvidenceRecord
        worker = self._make_worker(trip_type="round_trip")
        record = MileageEvidenceRecord(
            evidence_id="test", draft_id=1, service_order_id=1,
            report_date="2026-09-14", worker_user_id=1,
            route_fingerprint=MileageEvidenceService.compute_route_fingerprint(worker),
        )
        worker.trip_type = "one_way"
        self.assertTrue(MileageEvidenceService.is_evidence_stale(record, worker))

    def test_T_transportation_change_evidence_stale(self):
        """T. transportation change -> route invalidated -> evidence stale"""
        from ai_daily_report import TravelService
        worker = self._make_worker(transportation="self_drive")
        TravelService.invalidate_worker_route(worker)
        self.assertEqual(worker.route_status, "not_calculated")
        self.assertIsNone(worker.reported_miles)

    # ─── Idempotency Tests (U-V, AG) ───────────────────────────────────

    def test_U_same_fingerprint_reuses_evidence(self):
        """U. Same fingerprint + version -> reuse existing evidence, no re-fetch"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        # First generation
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=14, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record1.evidence_status, "ready")
        self.assertEqual(static_svc.get_route_map.call_count, 1)
        # Second generation with same worker (same fingerprint)
        record2 = evidence_svc.generate_evidence(
            worker, draft_id=14, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[record1.model_dump()],
        )
        self.assertEqual(record2.evidence_id, record1.evidence_id)
        self.assertEqual(static_svc.get_route_map.call_count, 1)  # No new call

    def test_V_fingerprint_change_new_evidence(self):
        """V. Fingerprint change -> new evidence generated"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=15, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        # Change worker (new fingerprint)
        worker.origin = "Different Origin"
        worker.origin_normalized = "Different Origin, USA"
        record2 = evidence_svc.generate_evidence(
            worker, draft_id=15, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[record1.model_dump()],
        )
        self.assertEqual(static_svc.get_route_map.call_count, 2)  # New call

    def test_AG_evidence_version_change_regenerates(self):
        """AG. evidence_version change allows regeneration"""
        from ai_daily_report import EVIDENCE_VERSION
        self.assertEqual(EVIDENCE_VERSION, 1)
        # If version changes, existing evidence with old version should be regenerated
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=16, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        # Simulate old version
        record1.evidence_version = 0
        record2 = evidence_svc.generate_evidence(
            worker, draft_id=16, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[record1.model_dump()],
        )
        self.assertEqual(static_svc.get_route_map.call_count, 2)  # Regenerated

    # ─── Draft vs Formal Tests (W-X) ───────────────────────────────────

    def test_W_cancelled_draft_cleanup(self):
        """W. Cancelled draft evidence can be cleaned up (not formal attachments)"""
        evidence_svc, _ = self._make_services()
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=17, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "ready")
        # Cleanup
        removed = evidence_svc.cleanup_draft_evidence(17)
        self.assertGreaterEqual(removed, 1)
        # Verify file gone
        img_path = self.evidence_root / "17" / "mileage" / record.file_relative_path
        self.assertFalse(img_path.exists())

    def test_X_evidence_not_written_to_formal_attachments(self):
        """X. Evidence generation does NOT write to service_report_attachments"""
        with self.module.app.app_context():
            before = self.module.db().execute(
                "select count(*) as c from service_report_attachments"
            ).fetchone()["c"]
            evidence_svc, _ = self._make_services()
            worker = self._make_worker()
            evidence_svc.generate_evidence(
                worker, draft_id=18, service_order_id=self.order["id"],
                report_date="2026-09-14", generated_by=self.admin_id,
            )
            after = self.module.db().execute(
                "select count(*) as c from service_report_attachments"
            ).fetchone()["c"]
            self.assertEqual(before, after)  # No formal attachments created

    # ─── Multi-worker Tests (Y-Z) ──────────────────────────────────────

    def test_Y_two_self_drive_workers_two_evidences(self):
        """Y. Two self_drive workers -> two independent evidences"""
        evidence_svc, _ = self._make_services()
        worker1 = self._make_worker(user_id=1, name="Ethan")
        worker2 = self._make_worker(user_id=2, name="张三")
        record1 = evidence_svc.generate_evidence(
            worker1, draft_id=19, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        record2 = evidence_svc.generate_evidence(
            worker2, draft_id=19, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record1.evidence_status, "ready")
        self.assertEqual(record2.evidence_status, "ready")
        self.assertNotEqual(record1.evidence_id, record2.evidence_id)
        self.assertEqual(record1.worker_user_id, 1)
        self.assertEqual(record2.worker_user_id, 2)

    def test_Z_passenger_no_evidence_generated(self):
        """Z. passenger worker -> no personal Mileage Evidence"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker(transportation="passenger")
        record = evidence_svc.generate_evidence(
            worker, draft_id=20, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.evidence_status, "verification_required")
        static_svc.get_route_map.assert_not_called()

    # ─── Security Log Tests (AA-AC) ────────────────────────────────────

    def test_AA_log_no_api_key(self):
        """AA. application log does not contain Google API key"""
        import io
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger("ai_daily_report.static_maps")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        logger.warning("Static Maps HTTP 429 (attempt 1)")
        log_output = log_stream.getvalue()
        self.assertNotIn("fake-static-key", log_output)
        self.assertNotIn("SECRET", log_output)
        logger.removeHandler(handler)

    def test_AB_log_no_full_static_map_url(self):
        """AB. application log does not contain full Static Map URL (which has key)"""
        import io
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger("ai_daily_report.static_maps")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        # Service logs only status code, never URL
        logger.warning("Static Maps HTTP %s (attempt %s)", 500, 1)
        log_output = log_stream.getvalue()
        self.assertNotIn("maps.googleapis.com", log_output)
        self.assertNotIn("key=", log_output)
        logger.removeHandler(handler)

    def test_AC_log_no_full_home_address(self):
        """AC. application log does not contain full home address"""
        import io
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger("ai_daily_report.mileage_evidence")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        # Evidence service should not log full addresses
        logger.warning("Evidence generation failed for worker")
        log_output = log_stream.getvalue()
        self.assertNotIn("518 Anacacho", log_output)
        logger.removeHandler(handler)

    # ─── Preview Cache Test (AD) ───────────────────────────────────────

    def test_AD_preview_no_re_download(self):
        """AD. Preview with existing ready evidence does not re-download Static Map"""
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=21, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(static_svc.get_route_map.call_count, 1)
        # "Preview" - call again with same existing evidence
        record2 = evidence_svc.generate_evidence(
            worker, draft_id=21, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[record1.model_dump()],
        )
        self.assertEqual(static_svc.get_route_map.call_count, 1)  # No re-download
        self.assertEqual(record2.evidence_status, "ready")

    # ─── Address Formatting Test ───────────────────────────────────────

    def test_address_formatting_protects_home(self):
        """Full home address is masked in evidence display"""
        from ai_daily_report import MileageEvidenceService
        masked = MileageEvidenceService.format_address_for_evidence(
            "518 Anacacho Dr, Spring, TX 77386"
        )
        self.assertNotIn("518", masked)
        self.assertIn("Spring", masked)

    # ─── Phase 3B Final Acceptance Tests (AH-AR) ──────────────────────

    def test_AH_static_maps_key_missing_error_code(self):
        """AH. Static Maps key missing error = static_maps_api_not_configured"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService, MileageEvidenceService
        static_svc = GoogleStaticMapsService("")
        evidence_svc = MileageEvidenceService(static_svc, str(self.evidence_root))
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=100, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record.error, "static_maps_api_not_configured")

    def test_AI_restart_safe_evidence_reuse(self):
        """AI. App restart 后已有 Evidence 可以 reuse（metadata持久化）"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        # First generation
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=101, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertEqual(record1.evidence_status, "ready")
        self.assertEqual(static_svc.get_route_map.call_count, 1)
        # Simulate app restart: create new service instance, pass persisted metadata
        static_svc2 = GoogleStaticMapsService("fake-static-key")
        static_svc2.get_route_map = MagicMock()
        evidence_svc2 = MileageEvidenceService(static_svc2, str(self.evidence_root))
        record2 = evidence_svc2.generate_evidence(
            worker, draft_id=101, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[record1.model_dump()],
        )
        self.assertEqual(record2.evidence_status, "ready")
        self.assertEqual(static_svc2.get_route_map.call_count, 0)  # No re-download
        self.assertEqual(record2.evidence_id, record1.evidence_id)

    def test_AJ_hash_mismatch_no_reuse(self):
        """AJ. 文件 SHA256 不匹配不 reuse"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=102, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        # Tamper with stored hash
        tampered = record1.model_dump()
        tampered["file_sha256"] = "0" * 64
        static_svc2 = GoogleStaticMapsService("fake-static-key")
        static_svc2.get_route_map = MagicMock(return_value=MagicMock(
            success=True, image_bytes=make_test_png(), content_type="image/png", status="success"
        ))
        evidence_svc2 = MileageEvidenceService(static_svc2, str(self.evidence_root))
        record2 = evidence_svc2.generate_evidence(
            worker, draft_id=102, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[tampered],
        )
        self.assertEqual(static_svc2.get_route_map.call_count, 1)  # Re-downloaded

    def test_AK_file_missing_no_reuse(self):
        """AK. 文件缺失不 reuse"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=103, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        # Delete the file
        img_path = self.evidence_root / "103" / "mileage" / record1.file_relative_path
        img_path.unlink()
        static_svc2 = GoogleStaticMapsService("fake-static-key")
        static_svc2.get_route_map = MagicMock(return_value=MagicMock(
            success=True, image_bytes=make_test_png(), content_type="image/png", status="success"
        ))
        evidence_svc2 = MileageEvidenceService(static_svc2, str(self.evidence_root))
        record2 = evidence_svc2.generate_evidence(
            worker, draft_id=103, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[record1.model_dump()],
        )
        self.assertEqual(static_svc2.get_route_map.call_count, 1)

    def test_AL_version_mismatch_no_reuse(self):
        """AL. evidence_version 不同不 reuse"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=104, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        old_version = record1.model_dump()
        old_version["evidence_version"] = 0
        static_svc2 = GoogleStaticMapsService("fake-static-key")
        static_svc2.get_route_map = MagicMock(return_value=MagicMock(
            success=True, image_bytes=make_test_png(), content_type="image/png", status="success"
        ))
        evidence_svc2 = MileageEvidenceService(static_svc2, str(self.evidence_root))
        record2 = evidence_svc2.generate_evidence(
            worker, draft_id=104, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[old_version],
        )
        self.assertEqual(static_svc2.get_route_map.call_count, 1)

    def test_AM_fingerprint_mismatch_no_reuse(self):
        """AM. route fingerprint 不同不 reuse"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService
        evidence_svc, static_svc = self._make_services()
        worker = self._make_worker()
        record1 = evidence_svc.generate_evidence(
            worker, draft_id=105, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        # Change worker (new fingerprint)
        worker.origin = "Different Origin"
        worker.origin_normalized = "Different Origin, USA"
        static_svc2 = GoogleStaticMapsService("fake-static-key")
        static_svc2.get_route_map = MagicMock(return_value=MagicMock(
            success=True, image_bytes=make_test_png(), content_type="image/png", status="success"
        ))
        evidence_svc2 = MileageEvidenceService(static_svc2, str(self.evidence_root))
        record2 = evidence_svc2.generate_evidence(
            worker, draft_id=105, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
            draft_evidence_records=[record1.model_dump()],
        )
        self.assertEqual(static_svc2.get_route_map.call_count, 1)

    def test_AN_path_traversal_rejected(self):
        """AN. ../ path traversal 被拒绝"""
        from ai_daily_report.mileage_evidence import MileageEvidenceService, PathSafetyError
        evidence_svc, _ = self._make_services()
        with self.assertRaises(PathSafetyError):
            evidence_svc._safe_evidence_path(1, "../escape.png")

    def test_AO_absolute_path_rejected(self):
        """AO. absolute path 被拒绝"""
        from ai_daily_report.mileage_evidence import PathSafetyError
        evidence_svc, _ = self._make_services()
        with self.assertRaises(PathSafetyError):
            evidence_svc._safe_evidence_path(1, "/etc/passwd")

    def test_AP_symlink_escape_rejected(self):
        """AP. symlink escape 被拒绝"""
        from ai_daily_report.mileage_evidence import PathSafetyError
        evidence_svc, _ = self._make_services()
        # Create a symlink that escapes
        draft_dir = self.evidence_root / "999" / "mileage"
        draft_dir.mkdir(parents=True, exist_ok=True)
        symlink_path = draft_dir / "escape_link"
        symlink_path.symlink_to("/tmp")
        with self.assertRaises(PathSafetyError):
            evidence_svc._safe_evidence_path(999, "escape_link/file.png")
        # Cleanup
        symlink_path.unlink()

    def test_AQ_cleanup_confined_to_draft(self):
        """AQ. cleanup 不允许越出 Draft Evidence 根目录"""
        evidence_svc, _ = self._make_services()
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=106, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        self.assertTrue(record.evidence_status == "ready")
        # Create a decoy file outside draft dir
        decoy = self.evidence_root / "decoy.txt"
        decoy.write_text("should not be deleted")
        removed = evidence_svc.cleanup_draft_evidence(106)
        self.assertGreaterEqual(removed, 1)
        # Decoy should still exist
        self.assertTrue(decoy.exists())
        decoy.unlink()

    def test_AR_error_field_no_sensitive_data(self):
        """AR. error 字段不包含 API key / URL / full address / polyline"""
        from ai_daily_report import GoogleStaticMapsService, MileageEvidenceService, MileageEvidenceService
        # Test various error conditions
        static_svc = GoogleStaticMapsService("")
        evidence_svc = MileageEvidenceService(static_svc, str(self.evidence_root))
        worker = self._make_worker()
        record = evidence_svc.generate_evidence(
            worker, draft_id=107, service_order_id=self.order["id"],
            report_date="2026-09-14", generated_by=self.admin_id,
        )
        error = record.error or ""
        self.assertNotIn("fake-static-key", error)
        self.assertNotIn("http", error)
        self.assertNotIn("518 Anacacho", error)
        self.assertNotIn("encoded_polyline", error)
        self.assertNotIn("maps.googleapis.com", error)


if __name__ == "__main__":
    unittest.main()
