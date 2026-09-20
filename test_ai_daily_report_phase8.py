"""AI Daily Report Phase 8 Tests - Attachment Preparation & Materialization

Covers the sealed Final Corrected Design:
- Four-layer model: Manifest / PreparedAsset / ManifestSource / ManifestRole
- Chain B state machine: Draft -> Validation -> Confirm -> Prepare -> Phase 9
- Server-side Phase 7 validation gate (frontend can_proceed is never trusted)
- Deterministic manifest fingerprint (provenance-sensitive)
- Idempotency (reuse ready manifest), crash recovery, quarantine
- Security: path confinement, IDOR, CSRF, 401/403/409/422 semantics
- Compliance granularity (only Google mileage evidence flagged)
- Zero writes to formal tables, zero external calls
- Test matrix items 1-63
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Setup test environment (same pattern as Phase 7 tests)
os.environ.setdefault("SECRET_KEY", "test-secret-phase8")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _make_valid_draft(**overrides):
    """Base draft shape passing Phase 7 validation (photo ids are real hashes
    filled in by the test base after files are created)."""
    draft = {
        "service_order_id": 300,
        "report_date": "2026-09-14",
        "workers": [
            {
                "user_id": 201, "name": "Ethan P8", "transportation": "self_drive",
                "origin": "100 Main St", "origin_source": "user_input",
                "origin_confirmed": True, "destination": "123 Site Ave, Spring, TX 77386",
                "destination_source": "service_order", "overnight_stay": False,
                "route_status": "success", "route_distance_meters": 16093.44,
                "one_way_miles": 10.0, "reported_miles": 20.0,
                "route_provider": "google_routes", "route_fingerprint": "route-fp-001",
                "mileage_evidence_id": "ev_001",
            }
        ],
        "work_items": [{"equipment": "Transformer A", "action": "repair", "description": "Replaced fuse"}],
        "arrival_time": "08:00", "departure_time": "17:00",
        "arrival_time_source": "manual", "departure_time_source": "manual",
        "selected_safety_photo": {"photo_id": "SAFETY_HASH", "confidence": 0.9, "classification": "safety_person"},
        "selected_service_photos": [{"photo_id": "SERVICE_HASH_1", "classification": "equipment"}],
        "photo_candidates": [],
        "service_description": "Replaced fuse on Transformer A",
        "waiting_hours": 0.0, "waiting_reason": "",
        "verification_required": False, "verification_fields": [],
        "ai_metadata": {"model": "deepseek-v4-flash", "created_by": "ai"},
        "photo_timeline_status": "ready",
        "photo_classification_status": "classified",
        "photo_set_fingerprint": "pset-fp-001",
        "evidence_records": [],
    }
    draft.update(overrides)
    return draft


def _make_evidence_record(evidence_id, draft_id, rel_path, sha256, **overrides):
    rec = {
        "evidence_id": evidence_id,
        "draft_id": draft_id,
        "service_order_id": 300,
        "reported_miles": 20.0,
        "overnight_stay": False,
        "route_fingerprint": "route-fp-001",
        "evidence_version": 1,
        "evidence_status": "verified",
        "generated_at": "2026-09-14T08:00:00Z",
        "generated_by": 201,
        "file_relative_path": rel_path,
        "file_sha256": sha256,
    }
    rec.update(overrides)
    return rec


class Phase8TestBase(unittest.TestCase):
    """Base class: temp DATA_DIR + SHARED_PHOTOS_DIR, users, service order,
    real photo files + evidence files, drafts in various states."""

    ORDER_NUMBER = "SO-PHASE8-001"

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="phase8_test_")
        os.environ["DATA_DIR"] = cls.temp_dir
        cls.shared_dir = os.path.join(cls.temp_dir, "shared-photos")
        os.environ["SHARED_PHOTOS_DIR"] = cls.shared_dir
        Path(cls.shared_dir).mkdir(parents=True, exist_ok=True)

        import app as app_module
        cls.app_module = app_module
        # Isolate storage to the temp dir (DATA_DIR/DB_PATH/SHARED_PHOTOS_DIR
        # are module constants; env vars alone are NOT enough after import)
        app_module.DATA_DIR = cls.temp_dir
        app_module.DB_PATH = os.path.join(cls.temp_dir, "invoices.db")
        app_module.ATTACHMENTS_DIR = os.path.join(cls.temp_dir, "attachments")
        app_module.REPORT_ATTACHMENTS_DIR = os.path.join(cls.temp_dir, "service-report-attachments")
        app_module.SHARED_PHOTOS_DIR = cls.shared_dir
        cls.app = app_module.app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            cls.app_module.init_db()
            cls._setup_test_data()

    # ── fixture helpers ──────────────────────────────────────────────────

    @classmethod
    def _setup_test_data(cls):
        db = cls.app_module.db()
        # users
        for uid, email, name, role in [
            (300, "admin-p8@test.com", "Admin P8", "admin"),
            (301, "ethan-p8@test.com", "Ethan P8", "employee"),
            (302, "finance-p8@test.com", "Finance P8", "finance"),
            (303, "outsider-p8@test.com", "Outsider P8", "employee"),
            (304, "external-p8@test.com", "External P8", "external_manager"),
            (305, "extemp-p8@test.com", "ExtEmp P8", "external_employee"),
        ]:
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (uid, email, name, "x", role, "2026-01-01T00:00:00Z"),
            )
        # service order
        db.execute(
            "insert or ignore into service_orders (id, order_number, client_name, site_address, client_order_number, status, created_by, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (300, cls.ORDER_NUMBER, "Client P8", "123 Site Ave, Spring, TX 77386", "CLIENT-P8-001", "open", 300, "2026-09-14T00:00:00Z"),
        )
        db.commit()

    @classmethod
    def _photo_files(cls):
        """(Re)create real photo files with canonical content every time.

        Always overwrites so tests start from a clean filesystem state
        (tests that tamper with photo content cannot pollute later tests).
        Returns dict {rel_path: sha256}.
        """
        photo_dir = Path(cls.shared_dir) / cls.ORDER_NUMBER / "pictures" / "2026-09-14"
        photo_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, content in [
            ("safety.jpg", b"FAKE-SAFETY-IMG-P8"),
            ("service1.jpg", b"FAKE-SERVICE-IMG-P8-1"),
            ("service2.jpg", b"FAKE-SERVICE-IMG-P8-2"),
            ("arrival.jpg", b"FAKE-ARRIVAL-IMG-P8"),
        ]:
            p = photo_dir / name
            p.write_bytes(content)
            rel = f"{cls.ORDER_NUMBER}/pictures/2026-09-14/{name}"
            files[rel] = _sha256_file(p)
        return files

    @classmethod
    def _evidence_file(cls, draft_id, evidence_id):
        ev_dir = Path(cls.temp_dir) / "ai-daily-report-drafts" / str(draft_id) / "mileage"
        ev_dir.mkdir(parents=True, exist_ok=True)
        p = ev_dir / f"{evidence_id}.png"
        if not p.exists():
            p.write_bytes(b"FAKE-EVIDENCE-PNG-P8")
        return f"ai-daily-report-drafts/{draft_id}/mileage/{evidence_id}.png", _sha256_file(p)

    @classmethod
    def _insert_draft(cls, draft_id, draft_data, status="confirmed", draft_version=1, created_by=301):
        """Upsert a draft WITHOUT deleting the existing row.

        insert-or-replace would trigger FK cascade and delete existing
        manifests (breaking history assertions); use explicit update-or-insert.
        """
        db = cls.app_module.db()
        existing = db.execute("select id from ai_daily_report_drafts where id = ?", (draft_id,)).fetchone()
        if existing:
            db.execute(
                "update ai_daily_report_drafts set service_order_id = ?, report_date = ?, status = ?, draft_version = ?, created_by = ?, draft_data = ?, updated_at = ? where id = ?",
                (300, "2026-09-14", status, draft_version, created_by, json.dumps(draft_data), "2026-09-14T00:00:00Z", draft_id),
            )
        else:
            db.execute(
                "insert into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft_id, 300, "2026-09-14", status, draft_version, created_by, json.dumps(draft_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
            )
        db.commit()

    @classmethod
    def _build_confirmed_draft(cls, draft_id=310, **draft_overrides):
        """Standard valid confirmed draft with real photos + evidence."""
        photos = cls._photo_files()
        safety_rel = f"{cls.ORDER_NUMBER}/pictures/2026-09-14/safety.jpg"
        service_rel = f"{cls.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        arrival_rel = f"{cls.ORDER_NUMBER}/pictures/2026-09-14/arrival.jpg"
        ev_rel, ev_sha = cls._evidence_file(draft_id, "ev_001")
        data = _make_valid_draft()
        data["service_order_id"] = 300
        data["workers"][0]["user_id"] = 301
        data["workers"][0]["name"] = "Ethan P8"
        data["workers"][0]["destination"] = "123 Site Ave, Spring, TX 77386"
        data["workers"][0]["route_fingerprint"] = "route-fp-001"
        data["workers"][0]["mileage_evidence_id"] = "ev_001"
        data["photo_candidates"] = [
            {"photo_id": photos[safety_rel], "photo_hash": photos[safety_rel], "relative_path": safety_rel, "classification": "safety_person"},
            {"photo_id": photos[service_rel], "photo_hash": photos[service_rel], "relative_path": service_rel, "classification": "equipment"},
            {"photo_id": photos[arrival_rel], "photo_hash": photos[arrival_rel], "relative_path": arrival_rel, "classification": "arrival"},
        ]
        data["selected_safety_photo"] = {"photo_id": photos[safety_rel], "confidence": 0.9, "classification": "safety_person"}
        data["selected_service_photos"] = [{"photo_id": photos[service_rel], "classification": "equipment"}]
        data["arrival_photo_ref"] = photos[arrival_rel]
        data["departure_photo_ref"] = photos[arrival_rel]  # same photo reused
        data["evidence_records"] = [
            _make_evidence_record("ev_001", draft_id, ev_rel, ev_sha, route_fingerprint="route-fp-001")
        ]
        data.update(draft_overrides)
        cls._insert_draft(draft_id, data, status="confirmed", draft_version=1, created_by=301)
        return draft_id, data

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def setUp(self):
        self._ctx = self.app.app_context()
        self._ctx.push()

    def tearDown(self):
        self._ctx.pop()

    def _get_csrf(self):
        resp = self.client.get("/api/ai/daily-report/csrf")
        return resp.get_json()["csrf_token"]

    def _prepare(self, draft_id, user_id=300, csrf=True, body=None):
        self._login(user_id)
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = self._get_csrf()
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/prepare-attachments",
            headers=headers,
            json=body if body is not None else {"draft_version": 1},
        )

    def _get_manifest(self, draft_id, user_id=300, include_history=False):
        self._login(user_id)
        url = f"/api/ai/daily-report/draft/{draft_id}/manifest"
        if include_history:
            url += "?include_history=1"
        return self.client.get(url)

    def _count_rows(self, table):
        db = self.app_module.db()
        row = db.execute(f"select count(*) as c from {table}").fetchone()
        return int(row["c"])

    def _manifest_service(self, user_id=300):
        from ai_daily_report.attachment_manifest import AttachmentManifestService
        db = self.app_module.db()
        return AttachmentManifestService(db, self.shared_dir, self.temp_dir, user_id)

    def _validation_result(self, draft_row):
        from ai_daily_report.validation_engine import ValidationEngine, ValidationContextBuilder
        draft_data = json.loads(draft_row["draft_data"] or "{}")
        ctx = ValidationContextBuilder(self.app_module.db()).build(draft_data)
        return ValidationEngine().validate(draft_data, ctx, draft_version=draft_row.get("draft_version", 1))

    def _get_draft_row(self, draft_id):
        db = self.app_module.db()
        row = db.execute("select * from ai_daily_report_drafts where id = ?", (draft_id,)).fetchone()
        return dict(row)

    def _prepare_service(self, draft_id):
        draft_row = self._get_draft_row(draft_id)
        validation = self._validation_result(draft_row)
        svc = self._manifest_service()
        return draft_row, validation, svc


# ─── Service Layer Tests ────────────────────────────────────────────────────

class TestManifestServiceUnit(Phase8TestBase):
    """Service-layer tests: plan/fingerprint/materialize/dedup/recovery."""

    def _prepare_service(self, draft_id):
        draft_row = self._get_draft_row(draft_id)
        validation = self._validation_result(draft_row)
        svc = self._manifest_service()
        return draft_row, validation, svc

    def test_01_valid_confirmed_draft_prepare_ready(self):
        did, _ = self._build_confirmed_draft(310)
        draft_row, validation, svc = self._prepare_service(did)
        self.assertTrue(validation.can_proceed)
        manifest = svc.prepare(draft_row, validation)
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(manifest["draft_id"], did)
        self.assertGreaterEqual(manifest["asset_count"], 1)
        self.assertGreaterEqual(manifest["source_count"], 1)
        self.assertGreaterEqual(manifest["role_count"], 1)
        # staging dir exists
        staging = Path(self.temp_dir) / "ai-daily-report-drafts" / str(did) / "prepared" / manifest["manifest_id"]
        self.assertTrue(staging.is_dir())

    def test_02_validation_blocked_can_proceed_false(self):
        did, data = self._build_confirmed_draft(311)
        # Hard Phase 7 ERROR: missing arrival_time
        data["arrival_time"] = None
        data["arrival_time_source"] = "manual"
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        draft_row, validation, svc = self._prepare_service(did)
        self.assertFalse(validation.can_proceed)
        from ai_daily_report.attachment_manifest import ManifestValidationBlockedError
        with self.assertRaises(ManifestValidationBlockedError):
            svc.prepare(draft_row, validation)

    def test_06_source_sha256_changed(self):
        did, data = self._build_confirmed_draft(312)
        # Change the actual photo file content (hash mismatch)
        photo_dir = Path(self.shared_dir) / self.ORDER_NUMBER / "pictures" / "2026-09-14"
        (photo_dir / "service1.jpg").write_bytes(b"TAMPERED-CONTENT-P8")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError) as ctx:
            svc.prepare(draft_row, validation)
        self.assertEqual(ctx.exception.code, "source_file_changed")

    def test_07_path_traversal_rejected(self):
        did, data = self._build_confirmed_draft(313)
        photos = self._photo_files()
        data["selected_service_photos"] = [{"photo_id": "TRAVERSAL_HASH", "classification": "x"}]
        data["photo_candidates"] = [{
            "photo_id": "TRAVERSAL_HASH", "photo_hash": "TRAVERSAL_HASH",
            "relative_path": f"{self.ORDER_NUMBER}/pictures/../../etc/passwd",
        }]
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError):
            svc.prepare(draft_row, validation)

    def test_08_absolute_path_injection_rejected(self):
        did, data = self._build_confirmed_draft(314)
        data["selected_service_photos"] = [{"photo_id": "ABS_HASH", "classification": "x"}]
        data["photo_candidates"] = [{
            "photo_id": "ABS_HASH", "photo_hash": "ABS_HASH",
            "relative_path": "/etc/passwd",
        }]
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError):
            svc.prepare(draft_row, validation)

    def test_09_symlink_escape_rejected(self):
        did, data = self._build_confirmed_draft(315)
        # candidate relative path points at a symlink target outside root
        outside = Path(self.temp_dir) / "outside-secret.txt"
        outside.write_text("secret")
        link_dir = Path(self.shared_dir) / self.ORDER_NUMBER / "pictures" / "2026-09-14"
        link = link_dir / "evil_link.jpg"
        if not link.exists():
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink not supported in this environment")
        data["selected_service_photos"] = [{"photo_id": "LINK_HASH", "classification": "x"}]
        data["photo_candidates"] = [{
            "photo_id": "LINK_HASH", "photo_hash": "LINK_HASH",
            "relative_path": f"{self.ORDER_NUMBER}/pictures/2026-09-14/evil_link.jpg",
        }]
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError):
            svc.prepare(draft_row, validation)

    def test_10_outside_workorder_root(self):
        did, data = self._build_confirmed_draft(316)
        data["selected_service_photos"] = [{"photo_id": "OUT_HASH", "classification": "x"}]
        data["photo_candidates"] = [{
            "photo_id": "OUT_HASH", "photo_hash": "OUT_HASH",
            "relative_path": "other-order/pictures/2026-09-14/x.jpg",
        }]
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError):
            svc.prepare(draft_row, validation)

    def test_11_cross_draft_photo(self):
        did, data = self._build_confirmed_draft(317)
        # selected photo id not in this draft's candidates
        data["selected_safety_photo"] = {"photo_id": "OTHER_DRAFT_HASH", "confidence": 0.9}
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError):
            svc.prepare(draft_row, validation)

    def test_12_cross_service_order_photo(self):
        did, data = self._build_confirmed_draft(318)
        data["selected_service_photos"] = [{"photo_id": "OTHER_ORDER_HASH", "classification": "x"}]
        data["selected_safety_photo"] = {"photo_id": "OTHER_ORDER_HASH", "confidence": 0.9}
        data["arrival_photo_ref"] = None
        data["departure_photo_ref"] = None
        data["photo_candidates"] = [{
            "photo_id": "OTHER_ORDER_HASH", "photo_hash": "OTHER_ORDER_HASH",
            "relative_path": "SO-OTHER/pictures/2026-09-14/x.jpg",
        }]
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError) as ctx:
            svc.prepare(draft_row, validation)
        self.assertEqual(ctx.exception.code, "path_unsafe")

    def test_13_wrong_report_date_photo(self):
        did, data = self._build_confirmed_draft(319)
        data["selected_service_photos"] = [{"photo_id": "WRONG_DATE_HASH", "classification": "x"}]
        data["selected_safety_photo"] = {"photo_id": "WRONG_DATE_HASH", "confidence": 0.9}
        data["arrival_photo_ref"] = None
        data["departure_photo_ref"] = None
        data["photo_candidates"] = [{
            "photo_id": "WRONG_DATE_HASH", "photo_hash": "WRONG_DATE_HASH",
            "relative_path": f"{self.ORDER_NUMBER}/pictures/2026-09-13/x.jpg",
        }]
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError) as ctx:
            svc.prepare(draft_row, validation)
        self.assertEqual(ctx.exception.code, "path_unsafe")

    def test_14_more_than_10_service_photos_blocked(self):
        did, data = self._build_confirmed_draft(320)
        photos = self._photo_files()
        service1 = f"{self.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        data["selected_service_photos"] = [
            {"photo_id": photos[service1], "classification": "x"} for _ in range(11)
        ]
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError) as ctx:
            svc.prepare(draft_row, validation)
        self.assertEqual(ctx.exception.code, "too_many_service_photos")

    def test_15_same_photo_multiple_roles_one_asset(self):
        did, data = self._build_confirmed_draft(321)
        photos = self._photo_files()
        service1 = f"{self.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        # service photo is also arrival ref -> same physical asset
        data["selected_service_photos"] = [{"photo_id": photos[service1], "classification": "x"}]
        data["arrival_photo_ref"] = photos[service1]
        data["departure_photo_ref"] = None
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        manifest = svc.prepare(draft_row, validation)
        # one physical asset for the service/arrival shared photo
        # (safety + service(shared with arrival) + evidence = 3 assets)
        self.assertEqual(manifest["asset_count"], 3)
        self.assertGreaterEqual(manifest["role_count"], 2)  # service_photo + arrival_reference
        # arrival_reference source shares the same asset as service_photo source
        db = self.app_module.db()
        svc_src = db.execute(
            "select asset_id from ai_daily_report_manifest_sources where manifest_id = ? and source_identity like ?",
            (manifest["manifest_id"], "photo:" + photos[service1] + ":%"),
        ).fetchone()
        self.assertIsNotNone(svc_src["asset_id"])

    def test_16_same_sha256_multiple_sources_one_asset(self):
        did, data = self._build_confirmed_draft(322)
        # two candidate photos with same content/hash but different paths
        photos = self._photo_files()
        safety_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/safety.jpg"
        service1_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        # overwrite service1 with safety content -> same sha256, different path
        src = Path(self.shared_dir) / safety_rel
        dst = Path(self.shared_dir) / service1_rel
        dst.write_bytes(src.read_bytes())
        new_hash = _sha256_file(dst)
        data["photo_candidates"] = [
            {"photo_id": new_hash, "photo_hash": new_hash, "relative_path": safety_rel, "classification": "safety"},
            {"photo_id": new_hash, "photo_hash": new_hash, "relative_path": service1_rel, "classification": "service"},
        ]
        data["selected_safety_photo"] = {"photo_id": new_hash, "confidence": 0.9}
        data["selected_service_photos"] = [{"photo_id": new_hash, "classification": "x"}]
        data["arrival_photo_ref"] = None
        data["departure_photo_ref"] = None
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        manifest = svc.prepare(draft_row, validation)
        # one asset for the two same-hash sources, plus evidence asset
        self.assertEqual(manifest["asset_count"], 2)
        self.assertEqual(manifest["source_count"], 3)  # 2 photo sources + 1 evidence
        db = self.app_module.db()
        assets = db.execute("select count(*) c from ai_daily_report_prepared_assets where manifest_id = ?", (manifest["manifest_id"],)).fetchone()
        sources = db.execute("select count(*) c from ai_daily_report_manifest_sources where manifest_id = ?", (manifest["manifest_id"],)).fetchone()
        self.assertEqual(assets["c"], 2)
        self.assertEqual(sources["c"], 3)

    def test_17_18_deterministic_plan_and_fingerprint(self):
        did, data = self._build_confirmed_draft(323)
        draft_row, validation, svc = self._prepare_service(did)
        plan1 = svc._build_plan(draft_row, validation)
        plan2 = svc._build_plan(draft_row, validation)
        self.assertEqual(plan1["manifest_fingerprint"], plan2["manifest_fingerprint"])
        # determinism across fresh service instances too
        svc2 = self._manifest_service()
        draft_row2 = self._get_draft_row(did)
        plan3 = svc2._build_plan(draft_row2, validation)
        self.assertEqual(plan1["manifest_fingerprint"], plan3["manifest_fingerprint"])

    def test_19_20_idempotent_reuse_same_fingerprint(self):
        did, data = self._build_confirmed_draft(324)
        draft_row, validation, svc = self._prepare_service(did)
        m1 = svc.prepare(draft_row, validation)
        m2 = svc.prepare(draft_row, validation)
        self.assertEqual(m1["manifest_id"], m2["manifest_id"])
        self.assertEqual(m1["status"], "ready")
        # no duplicate staging dirs
        staging = Path(self.temp_dir) / "ai-daily-report-drafts" / str(did) / "prepared"
        manifest_dirs = [p for p in staging.iterdir() if p.is_dir() and p.name != "_quarantine"]
        self.assertEqual(len(manifest_dirs), 1)

    def test_21_22_draft_modified_old_manifest_stale(self):
        did, data = self._build_confirmed_draft(325)
        draft_row, validation, svc = self._prepare_service(did)
        m1 = svc.prepare(draft_row, validation)
        self.assertEqual(m1["status"], "ready")
        # modify draft (bump version + change content)
        data["service_description"] = "Changed description P8"
        self._insert_draft(did, data, status="confirmed", draft_version=2)
        draft_row2 = self._get_draft_row(did)
        validation2 = self._validation_result(draft_row2)
        # old manifest no longer current
        current = svc.get_current_manifest(draft_row2, validation2)
        self.assertIsNone(current)
        # new prepare creates a fresh manifest; old one stays (audit history)
        m2 = svc.prepare(draft_row2, validation2)
        self.assertNotEqual(m1["manifest_id"], m2["manifest_id"])

    def test_23_evidence_missing_file(self):
        did, data = self._build_confirmed_draft(326)
        ev_rel, ev_sha = self._evidence_file(did, "ev_001")
        os.remove(Path(self.temp_dir) / ev_rel)
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError) as ctx:
            svc.prepare(draft_row, validation)
        self.assertEqual(ctx.exception.code, "source_not_found")

    def test_24_evidence_sha256_mismatch(self):
        did, data = self._build_confirmed_draft(327)
        ev_rel, ev_sha = self._evidence_file(did, "ev_001")
        Path(self.temp_dir, ev_rel).write_bytes(b"TAMPERED-EVIDENCE")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError) as ctx:
            svc.prepare(draft_row, validation)
        self.assertEqual(ctx.exception.code, "source_file_changed")

    def test_25_route_fingerprint_mismatch(self):
        did, data = self._build_confirmed_draft(328)
        data["evidence_records"][0]["route_fingerprint"] = "OLD-ROUTE-FP"
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError) as ctx:
            svc.prepare(draft_row, validation)
        self.assertEqual(ctx.exception.code, "evidence_stale")

    def test_26_stale_evidence_status(self):
        did, data = self._build_confirmed_draft(329)
        data["evidence_records"][0]["evidence_status"] = "stale"
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        from ai_daily_report.attachment_manifest import ManifestIntegrityError
        with self.assertRaises(ManifestIntegrityError) as ctx:
            svc.prepare(draft_row, validation)
        self.assertEqual(ctx.exception.code, "evidence_stale")

    def test_27_30_no_external_calls(self):
        did, data = self._build_confirmed_draft(330)
        draft_row, validation, svc = self._prepare_service(did)
        # the module must never reference external provider clients
        import ai_daily_report.attachment_manifest as am
        src = open(am.__file__, encoding="utf-8").read()
        for forbidden in (
            "from ai_daily_report import GoogleRoutesService",
            "import googlemaps",
            "requests.post",
            "staticmaps.googleapis",
            "routes.googleapis",
            "openai",
            "deepseek",
        ):
            self.assertNotIn(forbidden, src)
        # Actually materialize for real to confirm path works
        m = svc.prepare(draft_row, validation)
        self.assertEqual(m["status"], "ready")

    def test_31_33_zero_formal_table_writes(self):
        did, data = self._build_confirmed_draft(331)
        before = (
            self._count_rows("service_reports"),
            self._count_rows("service_report_workers"),
            self._count_rows("service_report_attachments"),
        )
        draft_row, validation, svc = self._prepare_service(did)
        svc.prepare(draft_row, validation)
        after = (
            self._count_rows("service_reports"),
            self._count_rows("service_report_workers"),
            self._count_rows("service_report_attachments"),
        )
        self.assertEqual(before, after)

    def test_41_42_43_crash_partial_copy_no_partial_ready(self):
        did, data = self._build_confirmed_draft(332)
        draft_row, validation, svc = self._prepare_service(did)
        plan = svc._build_plan(draft_row, validation)
        manifest_id = svc._insert_preparing(plan)
        # simulate crash: DB preparing row exists, but only .part files on disk
        staging = Path(self.temp_dir) / "ai-daily-report-drafts" / str(did) / "prepared"
        tmp_dir = staging / f"{manifest_id}.tmp"
        (tmp_dir / "assets").mkdir(parents=True)
        (tmp_dir / "assets" / "x.jpg.part").write_bytes(b"partial-data")
        # recovery: incomplete -> failed, never ready
        svc._recover_or_fail_previous(did)
        row = self.app_module.db().execute(
            "select status from ai_daily_report_attachment_manifests where manifest_id = ?", (manifest_id,)
        ).fetchone()
        self.assertEqual(row["status"], "failed")

    def test_54_55_recover_db_preparing_no_files(self):
        did, data = self._build_confirmed_draft(333)
        draft_row, validation, svc = self._prepare_service(did)
        plan = svc._build_plan(draft_row, validation)
        manifest_id = svc._insert_preparing(plan)
        # crash before any file written
        svc._recover_or_fail_previous(did)
        row = self.app_module.db().execute(
            "select status from ai_daily_report_attachment_manifests where manifest_id = ?", (manifest_id,)
        ).fetchone()
        self.assertEqual(row["status"], "failed")

    def test_56_recover_after_rename_before_db_ready(self):
        did, data = self._build_confirmed_draft(334)
        draft_row, validation, svc = self._prepare_service(did)
        plan = svc._build_plan(draft_row, validation)
        manifest_id = svc._insert_preparing(plan)
        # materialize fully (creates final dir) but do NOT persist/ready
        svc._materialize_all(draft_row, manifest_id, plan)
        # DB still preparing -> recovery should complete it to ready
        svc._recover_or_fail_previous(did)
        row = self.app_module.db().execute(
            "select status from ai_daily_report_attachment_manifests where manifest_id = ?", (manifest_id,)
        ).fetchone()
        self.assertEqual(row["status"], "ready")

    def test_52_arrival_photo_materialized_copy(self):
        """v0.1.257: an arrival ref photo (distinct from safety/service) is now
        materialized (its own prepared asset), so the service-report page can
        render it. Supersedes the old 'provenance-only, zero copy' contract."""
        did, data = self._build_confirmed_draft(335)
        # arrival ref is a photo NOT selected as safety/service
        photos = self._photo_files()
        arrival_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/arrival.jpg"
        data["arrival_photo_ref"] = photos[arrival_rel]
        data["departure_photo_ref"] = None
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        m = svc.prepare(draft_row, validation)
        db = self.app_module.db()
        src = db.execute(
            "select source_identity, asset_id from ai_daily_report_manifest_sources where manifest_id = ? and source_identity like ?",
            (m["manifest_id"], "photo:" + photos[arrival_rel] + ":%"),
        ).fetchone()
        self.assertIsNotNone(src)
        self.assertIsNotNone(src["asset_id"])  # materialized (own physical copy)
        # asset count: safety + service + evidence + arrival-distinct-photo
        self.assertEqual(m["asset_count"], 4)

    def test_53_arrival_already_service_reuse_asset(self):
        did, data = self._build_confirmed_draft(336)
        photos = self._photo_files()
        service1_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        data["arrival_photo_ref"] = photos[service1_rel]
        data["departure_photo_ref"] = None
        self._insert_draft(did, data, status="confirmed")
        draft_row, validation, svc = self._prepare_service(did)
        m = svc.prepare(draft_row, validation)
        db = self.app_module.db()
        src = db.execute(
            "select asset_id from ai_daily_report_manifest_sources where manifest_id = ? and source_identity like ?",
            (m["manifest_id"], "photo:" + photos[service1_rel] + ":%"),
        ).fetchone()
        self.assertIsNotNone(src["asset_id"])  # reused the service asset
        self.assertEqual(m["asset_count"], 3)  # safety + service(shared) + evidence

    def test_57_fingerprint_provenance_sensitive(self):
        did, data = self._build_confirmed_draft(337)
        photos = self._photo_files()
        safety_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/safety.jpg"
        # scenario A: arrival ref = service1
        service1_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        dataA = json.loads(json.dumps(data))
        dataA["arrival_photo_ref"] = photos[service1_rel]
        dataA["departure_photo_ref"] = None
        self._insert_draft(338, dataA, status="confirmed")
        # scenario B: arrival ref = safety (same physical set, different provenance)
        dataB = json.loads(json.dumps(data))
        dataB["arrival_photo_ref"] = photos[safety_rel]
        dataB["departure_photo_ref"] = None
        self._insert_draft(339, dataB, status="confirmed")
        rowA = self._get_draft_row(338)
        rowB = self._get_draft_row(339)
        valA = self._validation_result(rowA)
        valB = self._validation_result(rowB)
        svc = self._manifest_service()
        planA = svc._build_plan(rowA, valA)
        planB = svc._build_plan(rowB, valB)
        # same physical asset set (same source identities/sha256)
        stable = lambda p: sorted(
            (s["source_identity"], s["source_sha256"]) for s in p["sources"]
        )
        self.assertEqual(stable(planA), stable(planB))
        # different provenance (arrival ref differs) -> fingerprint MUST change
        self.assertNotEqual(planA["manifest_fingerprint"], planB["manifest_fingerprint"])

    def test_58_compliance_only_evidence(self):
        did, data = self._build_confirmed_draft(340)
        draft_row, validation, svc = self._prepare_service(did)
        m = svc.prepare(draft_row, validation)
        self.assertTrue(m["has_compliance_block"])
        db = self.app_module.db()
        rows = db.execute(
            "select source_type, compliance_review_required, compliance_status from ai_daily_report_manifest_sources where manifest_id = ?",
            (m["manifest_id"],),
        ).fetchall()
        for r in rows:
            if r["source_type"] == "mileage_evidence":
                self.assertEqual(r["compliance_review_required"], 1)
                self.assertEqual(r["compliance_status"], "review_required")
            else:
                self.assertEqual(r["compliance_review_required"], 0)
                self.assertEqual(r["compliance_status"], "na")

    def test_44_45_46_cleanup_only_staging_originals_untouched(self):
        did, data = self._build_confirmed_draft(341)
        draft_row, validation, svc = self._prepare_service(did)
        m = svc.prepare(draft_row, validation)
        # snapshot originals
        photo_dir = Path(self.shared_dir) / self.ORDER_NUMBER / "pictures" / "2026-09-14"
        originals = {p.name: p.read_bytes() for p in photo_dir.iterdir() if p.is_file()}
        ev_path = Path(self.temp_dir) / data["evidence_records"][0]["file_relative_path"]
        ev_before = ev_path.read_bytes()
        # cancel (only non-ready can be cancelled; mark failed first via service)
        db = self.app_module.db()
        db.execute("update ai_daily_report_attachment_manifests set status = 'failed' where manifest_id = ?", (m["manifest_id"],))
        db.commit()
        svc.cancel(did, m["manifest_id"])
        # staging gone
        staging = Path(self.temp_dir) / "ai-daily-report-drafts" / str(did) / "prepared"
        self.assertFalse((staging / m["manifest_id"]).exists())
        # originals untouched
        for name, content in originals.items():
            self.assertEqual((photo_dir / name).read_bytes(), content)
        self.assertEqual(ev_path.read_bytes(), ev_before)

    def test_62_orphan_staging_quarantined(self):
        did, data = self._build_confirmed_draft(342)
        draft_row, validation, svc = self._prepare_service(did)
        staging = Path(self.temp_dir) / "ai-daily-report-drafts" / str(did) / "prepared"
        staging.mkdir(parents=True, exist_ok=True)
        orphan = staging / "deadbeeforphan"
        (orphan / "assets").mkdir(parents=True)
        (orphan / "assets" / "a.jpg").write_bytes(b"orphan")
        svc._quarantine_orphans(did)
        q = staging / "_quarantine"
        self.assertTrue(q.is_dir())
        self.assertFalse(orphan.exists())
        quarantined = [p for p in q.iterdir()]
        self.assertEqual(len(quarantined), 1)
        self.assertIn("deadbeeforphan", quarantined[0].name)

    def test_60_get_current_excludes_stale(self):
        did, data = self._build_confirmed_draft(343)
        draft_row, validation, svc = self._prepare_service(did)
        m1 = svc.prepare(draft_row, validation)
        # bump draft -> old manifest not current
        data["service_description"] = "changed"
        self._insert_draft(did, data, status="confirmed", draft_version=2)
        draft_row2 = self._get_draft_row(did)
        validation2 = self._validation_result(draft_row2)
        current = svc.get_current_manifest(draft_row2, validation2)
        self.assertIsNone(current)
        m2 = svc.prepare(draft_row2, validation2)
        current = svc.get_current_manifest(draft_row2, validation2)
        self.assertEqual(current["manifest_id"], m2["manifest_id"])
        # history includes both
        history = svc.get_manifest_history(did)
        self.assertGreaterEqual(len(history), 2)

    def test_47_reopened_draft_invalidates_manifest(self):
        did, data = self._build_confirmed_draft(344)
        draft_row, validation, svc = self._prepare_service(did)
        m1 = svc.prepare(draft_row, validation)
        # reopen to draft (confirm flow bumps version when re-confirmed)
        self._insert_draft(did, data, status="draft", draft_version=2)
        draft_row2 = self._get_draft_row(did)
        current = svc.get_current_manifest(draft_row2, validation)
        self.assertIsNone(current)
        # re-confirm + modify -> prepare again works
        data["service_description"] = "after reopen"
        self._insert_draft(did, data, status="confirmed", draft_version=3)
        draft_row3 = self._get_draft_row(did)
        validation3 = self._validation_result(draft_row3)
        m2 = svc.prepare(draft_row3, validation3)
        self.assertEqual(m2["status"], "ready")


# ─── API Layer Tests ────────────────────────────────────────────────────────

class TestPrepareAPI(Phase8TestBase):
    """API tests: auth, CSRF, status codes, state machine, IDOR."""

    def test_api_01_prepare_confirmed_ok(self):
        did, _ = self._build_confirmed_draft(350)
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["manifest"]["status"], "ready")

    def test_api_04_stale_draft_version_409(self):
        did, _ = self._build_confirmed_draft(351)
        resp = self._prepare(did, user_id=300, body={"draft_version": 999})
        self.assertEqual(resp.status_code, 409)

    def test_api_05_missing_selected_photo_422(self):
        did, data = self._build_confirmed_draft(352)
        data["selected_safety_photo"] = {"photo_id": "GONE_HASH", "confidence": 0.9}
        self._insert_draft(did, data, status="confirmed")
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.get_json()["code"], "photo_not_in_discovered_set")

    def test_api_06_hash_changed_422(self):
        did, _ = self._build_confirmed_draft(353)
        photo_dir = Path(self.shared_dir) / self.ORDER_NUMBER / "pictures" / "2026-09-14"
        (photo_dir / "service1.jpg").write_bytes(b"TAMPERED-API-P8")
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.get_json()["code"], "source_file_changed")

    def test_api_03_frontend_fake_can_proceed_ignored(self):
        did, data = self._build_confirmed_draft(354)
        data["arrival_time"] = None
        data["arrival_time_source"] = "manual"
        self._insert_draft(did, data, status="confirmed")
        resp = self._prepare(did, user_id=300, body={"draft_version": 1, "can_proceed": True})
        self.assertEqual(resp.status_code, 422)  # server reruns validation

    def test_api_59_validation_blocked_422_not_403(self):
        did, data = self._build_confirmed_draft(355)
        data["arrival_time"] = None
        data["arrival_time_source"] = "manual"
        self._insert_draft(did, data, status="confirmed")
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 422)
        self.assertNotEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()["code"], "validation_cannot_proceed")

    def test_api_34_finance_prepare_denied_403(self):
        did, _ = self._build_confirmed_draft(356)
        resp = self._prepare(did, user_id=302)
        self.assertEqual(resp.status_code, 403)

    def test_api_35_unrelated_employee_denied_403(self):
        did, _ = self._build_confirmed_draft(357)
        resp = self._prepare(did, user_id=303)
        self.assertEqual(resp.status_code, 403)

    def test_api_36_participant_worker_allowed(self):
        did, _ = self._build_confirmed_draft(358)
        resp = self._prepare(did, user_id=301)  # worker in draft
        self.assertEqual(resp.status_code, 200)

    def test_api_37_admin_manager_allowed(self):
        did, _ = self._build_confirmed_draft(359)
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 200)

    def test_api_38_external_user_denied(self):
        did, _ = self._build_confirmed_draft(360)
        # external_manager / external_employee inherit Phase 6 Review Center
        # rule: is_internal_user() closes the whole AI Daily Report surface to
        # external roles (this is the existing Phase 6 gate, not a Phase 8
        # addition). So Prepare is 403 for every external role, matching
        # Phase 6 can_view (external -> False).
        self._login(304)  # external_manager
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{did}/prepare-attachments",
            json={"draft_version": 1},
        )
        self.assertEqual(resp.status_code, 403)

    def test_api_38b_external_employee_denied(self):
        did, _ = self._build_confirmed_draft(361)
        self._login(305)  # external_employee
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{did}/prepare-attachments",
            json={"draft_version": 1},
        )
        self.assertEqual(resp.status_code, 403)

    def test_api_40_csrf_required(self):
        did, _ = self._build_confirmed_draft(361)
        resp = self._prepare(did, user_id=300, csrf=False)
        self.assertEqual(resp.status_code, 403)

    def test_api_state_draft_status_not_preparable(self):
        did, data = self._build_confirmed_draft(362)
        self._insert_draft(did, data, status="draft", draft_version=1)
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 403)  # must be confirmed

    def test_api_state_cancelled_not_preparable(self):
        did, data = self._build_confirmed_draft(363)
        self._insert_draft(did, data, status="cancelled", draft_version=1)
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 403)

    def test_api_39_asset_idor_denied(self):
        did, _ = self._build_confirmed_draft(364)
        resp = self._prepare(did, user_id=300)
        asset_id = resp.get_json()["manifest"]["assets"][0]["asset_id"]
        manifest_id = resp.get_json()["manifest"]["manifest_id"]
        # unrelated employee cannot preview asset
        self._login(303)
        resp2 = self.client.get(f"/api/ai/daily-report/draft/{did}/manifest/{manifest_id}/asset/{asset_id}")
        self.assertEqual(resp2.status_code, 403)
        # owner can
        self._login(300)
        resp3 = self.client.get(f"/api/ai/daily-report/draft/{did}/manifest/{manifest_id}/asset/{asset_id}")
        self.assertEqual(resp3.status_code, 200)

    def test_api_asset_preview_no_path_param(self):
        did, _ = self._build_confirmed_draft(365)
        resp = self._prepare(did, user_id=300)
        manifest = resp.get_json()["manifest"]
        self._login(300)
        # ?path= is not accepted by route design; route only has path params
        resp2 = self.client.get(
            f"/api/ai/daily-report/draft/{did}/manifest/{manifest['manifest_id']}/asset/{manifest['assets'][0]['asset_id']}?path=/etc/passwd"
        )
        self.assertEqual(resp2.status_code, 200)  # query ignored, asset resolved via ids

    def test_api_61_prepared_path_cannot_be_supplied(self):
        did, data = self._build_confirmed_draft(366)
        resp = self._prepare(did, user_id=300, body={
            "draft_version": 1,
            "prepared_relative_path": "/tmp/evil.jpg",
            "source_relative_path": "/etc/passwd",
            "source_path": "C:\\evil",
        })
        self.assertEqual(resp.status_code, 200)
        manifest = resp.get_json()["manifest"]
        # prepared paths are server-generated, under staging root
        db = self.app_module.db()
        rows = db.execute(
            "select prepared_relative_path from ai_daily_report_prepared_assets where manifest_id = ?",
            (manifest["manifest_id"],),
        ).fetchall()
        for r in rows:
            self.assertNotIn("evil", r["prepared_relative_path"])
            self.assertNotIn("passwd", r["prepared_relative_path"])
            self.assertTrue(r["prepared_relative_path"].startswith(f"ai-daily-report-drafts/{did}/prepared/"))

    def test_api_get_manifest_current_and_history(self):
        did, _ = self._build_confirmed_draft(367)
        self._prepare(did, user_id=300)
        resp = self._get_manifest(did, user_id=300, include_history=True)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["current"]["status"], "ready")
        self.assertGreaterEqual(len(data["history"]), 1)

    def test_api_get_manifest_no_ready_returns_none_current(self):
        did, data = self._build_confirmed_draft(368)
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        resp = self._get_manifest(did, user_id=300)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()["current"])

    def test_api_delete_cancel_manifest(self):
        did, data = self._build_confirmed_draft(369)
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        # create a preparing manifest directly via service (cancel requires non-ready)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        svc = self._manifest_service()
        plan = svc._build_plan(draft_row, validation)
        manifest_id = svc._insert_preparing(plan)
        self._login(300)
        csrf = self._get_csrf()
        resp = self.client.delete(
            f"/api/ai/daily-report/draft/{did}/manifest/{manifest_id}",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["manifest"]["status"], "cancelled")
        # staging deleted
        staging = Path(self.temp_dir) / "ai-daily-report-drafts" / str(did) / "prepared"
        self.assertFalse((staging / manifest_id).exists())

    def test_api_delete_requires_csrf(self):
        did, data = self._build_confirmed_draft(370)
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        svc = self._manifest_service()
        plan = svc._build_plan(draft_row, validation)
        manifest_id = svc._insert_preparing(plan)
        self._login(300)
        resp = self.client.delete(f"/api/ai/daily-report/draft/{did}/manifest/{manifest_id}")
        self.assertEqual(resp.status_code, 403)

    def test_api_preview_zero_materialization(self):
        """GET preview must be read-only: no manifest rows, no staging dirs."""
        did, data = self._build_confirmed_draft(371)
        before_manifests = self._count_rows("ai_daily_report_attachment_manifests")
        self._login(300)
        resp = self.client.get(f"/api/ai/daily-report/draft/{did}/preview")
        self.assertEqual(resp.status_code, 200)
        preview = resp.get_json()["preview"]
        self.assertIn("attachment_preparation", preview)
        after_manifests = self._count_rows("ai_daily_report_attachment_manifests")
        self.assertEqual(before_manifests, after_manifests)
        staging = Path(self.temp_dir) / "ai-daily-report-drafts" / str(did) / "prepared"
        self.assertFalse(staging.exists())


class TestStateMachine(Phase8TestBase):
    """Confirmed/draft/reopen state machine (matrix item 63)."""

    def test_confirmed_flow_prepare_ok(self):
        did, _ = self._build_confirmed_draft(380)
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 200)

    def test_draft_cannot_prepare(self):
        did, data = self._build_confirmed_draft(381)
        self._insert_draft(did, data, status="draft", draft_version=1)
        resp = self._prepare(did, user_id=300)
        self.assertEqual(resp.status_code, 403)

    def test_reopen_then_reconfirm_new_manifest(self):
        did, data = self._build_confirmed_draft(382)
        draft_row, validation, svc = self._prepare_service(did)
        m1 = svc.prepare(draft_row, validation)
        # reopen
        self._insert_draft(did, data, status="draft", draft_version=2)
        # re-confirm with modification
        data["service_description"] = "reopen modified"
        self._insert_draft(did, data, status="confirmed", draft_version=3)
        draft_row3 = self._get_draft_row(did)
        validation3 = self._validation_result(draft_row3)
        m2 = svc.prepare(draft_row3, validation3)
        self.assertNotEqual(m1["manifest_id"], m2["manifest_id"])
        # old manifest is stale-eligible (not current)
        current = svc.get_current_manifest(draft_row3, validation3)
        self.assertEqual(current["manifest_id"], m2["manifest_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
