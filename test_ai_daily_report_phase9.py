"""AI Daily Report Phase 9 Tests - Formal Save / Transactional Commit

Covers the sealed Phase 9 design:
- Deterministic mapping layer (travel mode, work items formatter,
  departure_address, cabinet_number, mileage_billing_method)
- Final save gate (status, Phase 7 validation, manifest freshness, integrity,
  compliance)
- Exactly-once commit (single Draft -> at most one formal service_report)
- Transaction + filesystem atomicity and crash recovery
- Attachment mapping (site / self_check / mileage_proof only;
  arrival/departure stay provenance-only)
- Authorization (finance 403, external current boundary, CSRF, IDOR)
- Post-save state machine (confirmed -> saved, formalized immutable)
- Boundary: zero DeepSeek / Vision / Routes / Static Maps; zero writes outside
  the formal commit transaction; GET preview/manifest/asset never materializes.
- Test matrix items 1-79 (Phase 9 gate)
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

os.environ.setdefault("SECRET_KEY", "test-secret-phase9")
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
    draft = {
        "service_order_id": 400,
        "report_date": "2026-09-14",
        "workers": [
            {
                "user_id": 401, "name": "Ethan P9", "transportation": "self_drive",
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
        "service_order_id": 400,
        "reported_miles": 20.0,
        "overnight_stay": False,
        "route_fingerprint": "route-fp-001",
        "evidence_version": 1,
        "evidence_status": "verified",
        "generated_at": "2026-09-14T08:00:00Z",
        "generated_by": 401,
        "file_relative_path": rel_path,
        "file_sha256": sha256,
    }
    rec.update(overrides)
    return rec


class Phase9TestBase(unittest.TestCase):
    """Base: temp DATA_DIR + SHARED_PHOTOS_DIR, users, service order, real
    photo + evidence files, drafts, manifest/prepare helpers."""

    ORDER_NUMBER = "SO-PHASE9-001"

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="phase9_test_")
        os.environ["DATA_DIR"] = cls.temp_dir
        cls.shared_dir = os.path.join(cls.temp_dir, "shared-photos")
        os.environ["SHARED_PHOTOS_DIR"] = cls.shared_dir
        Path(cls.shared_dir).mkdir(parents=True, exist_ok=True)

        import app as app_module
        cls.app_module = app_module
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

    @classmethod
    def _setup_test_data(cls):
        db = cls.app_module.db()
        for uid, email, name, role in [
            (400, "admin-p9@test.com", "Admin P9", "admin"),
            (401, "ethan-p9@test.com", "Ethan P9", "employee"),
            (402, "finance-p9@test.com", "Finance P9", "finance"),
            (403, "outsider-p9@test.com", "Outsider P9", "employee"),
            (404, "external-p9@test.com", "External P9", "external_manager"),
            (405, "extemp-p9@test.com", "ExtEmp P9", "external_employee"),
            (406, "manager-p9@test.com", "Manager P9", "manager"),
        ]:
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (uid, email, name, "x", role, "2026-01-01T00:00:00Z"),
            )
        db.execute(
            "insert or ignore into service_orders (id, order_number, client_name, site_address, client_order_number, status, created_by, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (400, cls.ORDER_NUMBER, "Client P9", "123 Site Ave, Spring, TX 77386", "CLIENT-P9-001", "open", 400, "2026-09-14T00:00:00Z"),
        )
        db.commit()

    @classmethod
    def _photo_files(cls):
        photo_dir = Path(cls.shared_dir) / cls.ORDER_NUMBER / "pictures" / "2026-09-14"
        photo_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, content in [
            ("safety.jpg", b"FAKE-SAFETY-IMG-P9"),
            ("service1.jpg", b"FAKE-SERVICE-IMG-P9-1"),
            ("service2.jpg", b"FAKE-SERVICE-IMG-P9-2"),
            ("arrival.jpg", b"FAKE-ARRIVAL-IMG-P9"),
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
            p.write_bytes(b"FAKE-EVIDENCE-PNG-P9")
        return f"ai-daily-report-drafts/{draft_id}/mileage/{evidence_id}.png", _sha256_file(p)

    @classmethod
    def _insert_draft(cls, draft_id, draft_data, status="confirmed", draft_version=1, created_by=401):
        db = cls.app_module.db()
        existing = db.execute("select id from ai_daily_report_drafts where id = ?", (draft_id,)).fetchone()
        if existing:
            db.execute(
                "update ai_daily_report_drafts set service_order_id = ?, report_date = ?, status = ?, draft_version = ?, created_by = ?, draft_data = ?, updated_at = ? where id = ?",
                (400, "2026-09-14", status, draft_version, created_by, json.dumps(draft_data), "2026-09-14T00:00:00Z", draft_id),
            )
        else:
            db.execute(
                "insert into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft_id, 400, "2026-09-14", status, draft_version, created_by, json.dumps(draft_data), "2026-09-14T00:00:00Z", "2026-09-14T00:00:00Z"),
            )
        db.commit()

    @classmethod
    def _build_confirmed_draft(cls, draft_id=410, **draft_overrides):
        photos = cls._photo_files()
        safety_rel = f"{cls.ORDER_NUMBER}/pictures/2026-09-14/safety.jpg"
        service_rel = f"{cls.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        arrival_rel = f"{cls.ORDER_NUMBER}/pictures/2026-09-14/arrival.jpg"
        ev_rel, ev_sha = cls._evidence_file(draft_id, "ev_001")
        data = _make_valid_draft()
        data["service_order_id"] = 400
        data["workers"][0]["user_id"] = 401
        data["workers"][0]["name"] = "Ethan P9"
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
        cls._insert_draft(draft_id, data, status="confirmed", draft_version=1, created_by=401)
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
        data = resp.get_json(silent=True) or {}
        return data.get("csrf_token") or None

    def _prepare(self, draft_id, user_id=400, csrf=True, body=None):
        self._login(user_id)
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = self._get_csrf()
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/prepare-attachments",
            headers=headers,
            json=body if body is not None else {"draft_version": 1},
        )

    def _form_save(self, draft_id, user_id=400, csrf=True, body=None):
        self._login(user_id)
        headers = {}
        if csrf:
            token = self._get_csrf()
            if token:
                headers["X-CSRF-Token"] = token
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/formal-save",
            headers=headers,
            json=body if body is not None else {"draft_version": 1},
        )

    def _compliance_review_api(self, draft_id, manifest_id, user_id=400, csrf=True):
        self._login(user_id)
        headers = {}
        if csrf:
            token = self._get_csrf()
            if token:
                headers["X-CSRF-Token"] = token
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/manifest/{manifest_id}/compliance-review",
            headers=headers,
        )

    def _review_compliance_direct(self, manifest_id):
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_manifest_sources set compliance_status = 'reviewed' where manifest_id = ? and compliance_review_required = 1",
            (manifest_id,),
        )
        db.commit()

    def _count_rows(self, table):
        db = self.app_module.db()
        row = db.execute(f"select count(*) as c from {table}").fetchone()
        return int(row["c"])

    def _manifest_service(self, user_id=400):
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

    def _formal_service(self, user_id=400):
        from ai_daily_report.formal_save import FormalSaveService
        return FormalSaveService(
            self.app_module.db(), self.temp_dir,
            os.path.join(self.temp_dir, "service-report-attachments"), user_id,
        )

    def _prepare_ready(self, draft_id):
        """Prepare + compliance-review -> ready manifest dict."""
        resp = self._prepare(draft_id, user_id=400)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        manifest = resp.get_json()["manifest"]
        self._review_compliance_direct(manifest["manifest_id"])
        return manifest


# ─── Deterministic mapping layer ─────────────────────────────────────────────

class TestFormalMappingDeterministic(unittest.TestCase):

    def test_01_travel_mode_enumeration(self):
        from ai_daily_report.formal_save import map_travel_mode, FormalSaveIntegrityError
        cases = {
            "self_drive": "self_drive",
            "flight": "flight",
            "carpool": "following",
            "passenger": "following",
            "rental_car": "rental_drive",
            "": "self_drive",
            None: "self_drive",
        }
        for source, expected in cases.items():
            self.assertEqual(map_travel_mode(source), expected)
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            map_travel_mode("other")
        self.assertEqual(ctx.exception.code, "travel_mode_unmappable")

    def test_02_work_items_formatter_deterministic(self):
        from ai_daily_report.formal_save import format_work_items
        items = [
            {"equipment": "A313", "action": "Replace fuse", "description": "Fuse #2"},
            {"equipment": "B9", "action": "Lubricate", "description": ""},
        ]
        text = format_work_items("Replaced fuse on Transformer A", items)
        self.assertIn("Work Performed:", text)
        self.assertIn("* A313 — Replace fuse: Fuse #2", text)
        self.assertIn("* B9 — Lubricate", text)
        # stable ordering: same input -> same output
        self.assertEqual(text, format_work_items("Replaced fuse on Transformer A", items))
        # no description -> only Work Performed block
        text2 = format_work_items("", items)
        self.assertNotIn("Replaced fuse", text2)
        self.assertIn("Work Performed:", text2)
        # empty items -> original text unchanged
        self.assertEqual(format_work_items("Plain text", []), "Plain text")

    def test_03_departure_address_rules(self):
        from ai_daily_report.formal_save import derive_departure_address
        single = [{"origin": "100 Main St", "origin_confirmed": True}]
        self.assertEqual(derive_departure_address(single), "100 Main St")
        same = [
            {"origin": "100 Main St", "origin_confirmed": True},
            {"origin": "100 Main St", "origin_normalized": "100 Main St", "origin_confirmed": True},
        ]
        self.assertEqual(derive_departure_address(same), "100 Main St")
        different = [
            {"origin": "100 Main St", "origin_confirmed": True},
            {"origin": "200 Oak Ave", "origin_confirmed": True},
        ]
        self.assertIsNone(derive_departure_address(different))
        unconfirmed = [{"origin": "100 Main St", "origin_confirmed": False}]
        self.assertIsNone(derive_departure_address(unconfirmed))
        self.assertIsNone(derive_departure_address([]))

    def test_04_cabinet_number_rules(self):
        from ai_daily_report.formal_save import derive_cabinet_number
        self.assertEqual(derive_cabinet_number([{"equipment": "Transformer A"}]), "Transformer A")
        self.assertIsNone(derive_cabinet_number([]))
        self.assertIsNone(derive_cabinet_number([{"equipment": "A"}, {"equipment": "B"}]))
        self.assertIsNone(derive_cabinet_number([{"equipment": ""}]))

    def test_05_mileage_billing_method(self):
        from ai_daily_report.formal_save import derive_mileage_billing_method
        method, source = derive_mileage_billing_method({})
        self.assertEqual((method, source), ("per_person", "system_default"))
        method, source = derive_mileage_billing_method({"mileage_billing_method": "per_vehicle"})
        self.assertEqual((method, source), ("per_vehicle", "draft_explicit"))

    def test_06_hours_and_mileage_math(self):
        from ai_daily_report.formal_save import (
            rounded_report_service_hours, compute_total_time, compute_driving_miles,
            to_formal_worker, map_travel_mode,
        )
        self.assertEqual(rounded_report_service_hours("08:00", "17:00"), 9.0)
        self.assertEqual(rounded_report_service_hours("08:00", "16:30"), 8.5)
        self.assertEqual(rounded_report_service_hours("17:00", "08:00"), 0)
        workers = [
            to_formal_worker({"user_id": 1, "transportation": "self_drive", "reported_miles": 20}),
            to_formal_worker({"user_id": 2, "transportation": "passenger", "reported_miles": 10}),
        ]
        self.assertEqual(compute_driving_miles(workers, "per_person"), 30.0)
        self.assertEqual(compute_driving_miles(workers, "per_vehicle"), 20.0)
        self.assertEqual(compute_total_time([{"travel_hours": 1.5, "public_transport_hours": 0.5}]), "2")


# ─── Service layer: gates / integrity / exactly-once ─────────────────────────

class TestFormalSaveService(Phase9TestBase):

    def test_10_happy_path_created(self):
        did, _ = self._build_confirmed_draft(410)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        result = self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(result["status"], "created")
        report_id = result["service_report_id"]
        db = self.app_module.db()
        report = db.execute("select * from service_reports where id = ?", (report_id,)).fetchone()
        self.assertEqual(report["service_order_id"], 400)
        self.assertEqual(report["report_date"], "2026-09-14")
        self.assertEqual(float(report["total_service_hours"]), 9.0)
        self.assertEqual(float(report["driving_miles"]), 20.0)
        self.assertEqual(report["mileage_billing_method"], "per_person")
        self.assertEqual(report["departure_address"], "100 Main St")
        self.assertEqual(report["cabinet_number"], "Transformer A")
        self.assertIsNone(report["report_writer_id"])
        self.assertEqual(int(report["created_by"]), 400)
        # #43: manual time source preserved through formal save
        self.assertEqual(report["arrival_time_source"], "manual")
        self.assertEqual(report["departure_time_source"], "manual")
        self.assertEqual(int(report["ai_generated"]), 1)
        self.assertEqual(int(report["ai_draft_id"]), did)
        self.assertIn("Work Performed:", report["service_description"])
        self.assertIn("* Transformer A — repair: Replaced fuse", report["service_description"])
        workers = db.execute("select * from service_report_workers where report_id = ?", (report_id,)).fetchall()
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0]["user_id"], 401)
        self.assertEqual(workers[0]["travel_mode"], "self_drive")
        attachments = db.execute("select * from service_report_attachments where report_id = ?", (report_id,)).fetchall()
        categories = {a["category"] for a in attachments}
        self.assertIn("site", categories)
        self.assertIn("self_check", categories)
        self.assertIn("mileage_proof", categories)
        self.assertNotIn("arrival", categories)
        self.assertNotIn("departure", categories)
        # files exist and hash-match prepared assets
        for att in attachments:
            stored = (Path(self.temp_dir) / "service-report-attachments" / att["stored_filename"]).resolve()
            self.assertTrue(stored.is_file(), stored)
        # formal commit row + draft saved
        commit = db.execute("select * from ai_daily_report_formal_commits where draft_id = ?", (did,)).fetchone()
        self.assertEqual(commit["status"], "committed")
        self.assertEqual(int(commit["service_report_id"]), report_id)
        draft = self._get_draft_row(did)
        self.assertEqual(draft["status"], "saved")
        self.assertEqual(int(draft["saved_report_id"]), report_id)

    def test_11_exactly_once_second_run_returns_same(self):
        did, _ = self._build_confirmed_draft(411)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        svc = self._formal_service()
        first = svc.run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        # Draft is now saved; second call (even with old row snapshot) must
        # return the existing report, never create a second one.
        draft_row2 = self._get_draft_row(did)
        validation2 = self._validation_result(draft_row2)
        second = svc.run(draft_row2, 1, manifest["manifest_id"], validation2, self._manifest_service())
        self.assertEqual(second["status"], "already_committed")
        self.assertEqual(int(second["service_report_id"]), int(first["service_report_id"]))
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 1)

    def test_12_validation_blocked(self):
        # missing safety photo -> Phase 7 ERROR -> can_proceed False
        did, _ = self._build_confirmed_draft(412)
        db = self.app_module.db()
        row = db.execute("select * from ai_daily_report_drafts where id = ?", (did,)).fetchone()
        data = json.loads(row["draft_data"])
        data["selected_safety_photo"] = None
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        self.assertFalse(validation.can_proceed)
        from ai_daily_report.formal_save import FormalSaveValidationBlockedError
        with self.assertRaises(FormalSaveValidationBlockedError) as ctx:
            self._formal_service().run(draft_row, 1, None, validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "validation_cannot_proceed")
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 0)

    def test_13_no_manifest(self):
        did, _ = self._build_confirmed_draft(413)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report.formal_save import FormalSaveIntegrityError
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            self._formal_service().run(draft_row, 1, None, validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "manifest_not_ready")

    def test_14_stale_manifest_id(self):
        did, _ = self._build_confirmed_draft(414)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report.formal_save import FormalSaveStaleError
        with self.assertRaises(FormalSaveStaleError) as ctx:
            self._formal_service().run(draft_row, 1, "wrong-manifest-id", validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "manifest_stale")

    def test_15_stale_draft_version(self):
        did, _ = self._build_confirmed_draft(415)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report.formal_save import FormalSaveStaleError
        with self.assertRaises(FormalSaveStaleError) as ctx:
            self._formal_service().run(draft_row, 999, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "version_conflict")

    def test_16_manifest_fingerprint_mismatch(self):
        did, _ = self._build_confirmed_draft(416)
        manifest = self._prepare_ready(did)
        # Change Draft data (photo_set_fingerprint) WITHOUT bumping version:
        # recomputed plan fingerprint must differ from stored manifest.
        db = self.app_module.db()
        row = db.execute("select * from ai_daily_report_drafts where id = ?", (did,)).fetchone()
        data = json.loads(row["draft_data"])
        data["photo_set_fingerprint"] = "pset-fp-CHANGED"
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report.formal_save import FormalSaveIntegrityError
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        # photo_set_fingerprint is part of the validation fingerprint, so the old
        # ready manifest is no longer current-applicable -> manifest_not_ready.
        self.assertEqual(ctx.exception.code, "manifest_not_ready")

    def test_17_prepared_asset_missing(self):
        did, _ = self._build_confirmed_draft(417)
        manifest = self._prepare_ready(did)
        # delete one prepared asset file
        db = self.app_module.db()
        assets = db.execute(
            "select prepared_relative_path from ai_daily_report_prepared_assets where manifest_id = ?",
            (manifest["manifest_id"],),
        ).fetchall()
        self.assertTrue(assets)
        target = (Path(self.temp_dir) / assets[0]["prepared_relative_path"]).resolve()
        target.unlink()
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report.formal_save import FormalSaveIntegrityError
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "source_file_changed")

    def test_18_prepared_asset_hash_changed(self):
        did, _ = self._build_confirmed_draft(418)
        manifest = self._prepare_ready(did)
        db = self.app_module.db()
        assets = db.execute(
            "select prepared_relative_path from ai_daily_report_prepared_assets where manifest_id = ?",
            (manifest["manifest_id"],),
        ).fetchall()
        target = (Path(self.temp_dir) / assets[0]["prepared_relative_path"]).resolve()
        target.write_bytes(b"TAMPERED-PREPARED-ASSET")
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report.formal_save import FormalSaveIntegrityError
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "source_file_changed")

    def test_19_compliance_unreviewed_blocks(self):
        did, _ = self._build_confirmed_draft(419)
        resp = self._prepare(did, user_id=400)
        manifest = resp.get_json()["manifest"]
        # do NOT mark compliance reviewed
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report.formal_save import FormalSaveComplianceError
        with self.assertRaises(FormalSaveComplianceError) as ctx:
            self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "compliance_blocked")
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 0)

    def test_20_travel_mode_other_blocks(self):
        did, _ = self._build_confirmed_draft(420)
        db = self.app_module.db()
        row = db.execute("select * from ai_daily_report_drafts where id = ?", (did,)).fetchone()
        data = json.loads(row["draft_data"])
        data["workers"][0]["transportation"] = "other"
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        # prepare succeeds (travel mapping is a Phase 9 mapping-layer concern)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report.formal_save import FormalSaveIntegrityError
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "travel_mode_unmappable")

    def test_21_provenance_only_arrival_no_duplicate_attachment(self):
        did, _ = self._build_confirmed_draft(421)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        result = self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        db = self.app_module.db()
        attachments = db.execute(
            "select * from service_report_attachments where report_id = ?", (result["service_report_id"],),
        ).fetchall()
        self.assertFalse([a for a in attachments if a["category"] in ("arrival", "departure")])
        # arrival photo is provenance-only; it was NOT materialized unless
        # it doubles as safety/service photo.
        src = db.execute(
            """
            select source_relative_path from ai_daily_report_manifest_sources
            where manifest_id = ? and source_type = 'photo' and source_relative_path like '%arrival.jpg'
            """,
            (manifest["manifest_id"],),
        ).fetchone()
        self.assertIsNotNone(src)

    def test_22_same_photo_service_and_arrival_single_attachment(self):
        did, _ = self._build_confirmed_draft(422)
        # make the service photo also the arrival reference
        db = self.app_module.db()
        row = db.execute("select * from ai_daily_report_drafts where id = ?", (did,)).fetchone()
        data = json.loads(row["draft_data"])
        service_photo_id = data["selected_service_photos"][0]["photo_id"]
        data["arrival_photo_ref"] = service_photo_id
        data["departure_photo_ref"] = service_photo_id
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        result = self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        db = self.app_module.db()
        site_rows = db.execute(
            "select * from service_report_attachments where report_id = ? and category = 'site'",
            (result["service_report_id"],),
        ).fetchall()
        self.assertEqual(len(site_rows), 1)  # no duplicate from arrival role

    def test_23_multi_worker_different_origins_departure_null(self):
        did, _ = self._build_confirmed_draft(423)
        db = self.app_module.db()
        row = db.execute("select * from ai_daily_report_drafts where id = ?", (did,)).fetchone()
        data = json.loads(row["draft_data"])
        data["workers"].append({
            "user_id": 403, "name": "Outsider", "transportation": "self_drive",
            "origin": "200 Oak Ave", "origin_confirmed": True,
            "destination": "123 Site Ave, Spring, TX 77386", "overnight_stay": False,
            "route_status": "success", "one_way_miles": 5.0, "reported_miles": 10.0,
        })
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        self.assertTrue(validation.can_proceed, validation.errors)
        manifest = self._prepare_ready(did)
        result = self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        db = self.app_module.db()
        report = db.execute("select * from service_reports where id = ?", (result["service_report_id"],)).fetchone()
        self.assertIsNone(report["departure_address"])
        workers = db.execute("select * from service_report_workers where report_id = ?", (result["service_report_id"],)).fetchall()
        self.assertEqual(len(workers), 2)


# ─── API layer ───────────────────────────────────────────────────────────────

class TestFormalSaveApi(Phase9TestBase):

    def test_30_happy_path_api_201(self):
        did, _ = self._build_confirmed_draft(430)
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "created")
        self.assertTrue(data["service_report_id"])
        self.assertIn("report_url", data)

    def test_31_duplicate_click_returns_200_same_report(self):
        did, _ = self._build_confirmed_draft(431)
        manifest = self._prepare_ready(did)
        body = {"draft_version": 1, "manifest_id": manifest["manifest_id"]}
        first = self._form_save(did, user_id=400, body=body)
        self.assertEqual(first.status_code, 201)
        second = self._form_save(did, user_id=400, body=body)
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertEqual(
            second.get_json()["service_report_id"], first.get_json()["service_report_id"]
        )
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 1)

    def test_32_validation_blocked_returns_422_not_403(self):
        did, _ = self._build_confirmed_draft(432)
        db = self.app_module.db()
        row = db.execute("select * from ai_daily_report_drafts where id = ?", (did,)).fetchone()
        data = json.loads(row["draft_data"])
        data["selected_safety_photo"] = None
        self._insert_draft(did, data, status="confirmed", draft_version=1)
        resp = self._form_save(did, user_id=400)
        self.assertEqual(resp.status_code, 422, resp.get_json())
        self.assertEqual(resp.get_json()["code"], "validation_cannot_proceed")

    def test_33_stale_version_returns_409(self):
        did, _ = self._build_confirmed_draft(433)
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=400, body={"draft_version": 999, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["code"], "version_conflict")

    def test_34_finance_denied_403(self):
        did, _ = self._build_confirmed_draft(434)
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=402)
        self.assertEqual(resp.status_code, 403)

    def test_35_external_roles_keep_boundary_403(self):
        did, _ = self._build_confirmed_draft(435)
        manifest = self._prepare_ready(did)
        for uid in (404, 405):
            resp = self._form_save(did, user_id=uid)
            self.assertEqual(resp.status_code, 403, uid)

    def test_36_unauthenticated_401(self):
        did, _ = self._build_confirmed_draft(436)
        with self.client.session_transaction() as sess:
            sess.clear()
        resp = self.client.post(f"/api/ai/daily-report/draft/{did}/formal-save", json={})
        self.assertEqual(resp.status_code, 401, resp.status_code)

    def test_37_csrf_required(self):
        did, _ = self._build_confirmed_draft(437)
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=400, csrf=False)
        self.assertIn(resp.status_code, (400, 403))

    def test_38_idor_unrelated_employee_403(self):
        did, _ = self._build_confirmed_draft(438)
        # outsider employee (not creator, not worker)
        resp = self._form_save(did, user_id=403)
        self.assertEqual(resp.status_code, 403)

    def test_39_participant_worker_allowed(self):
        did, _ = self._build_confirmed_draft(439)
        # creator is 401 (Ethan, also worker). Employee participant may save.
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=401, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp.status_code, 201, resp.get_json())

    def test_40_admin_and_manager_allowed(self):
        for uid in (400, 406):
            did, _ = self._build_confirmed_draft(440 + uid)
            manifest = self._prepare_ready(did)
            resp = self._form_save(did, user_id=uid, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
            self.assertEqual(resp.status_code, 201, (uid, resp.get_json()))

    def test_41_preview_get_never_materializes(self):
        did, _ = self._build_confirmed_draft(441)
        self._prepare_ready(did)
        formal_dir = Path(self.temp_dir) / "service-report-attachments"
        files_before = list(formal_dir.rglob("*")) if formal_dir.exists() else []
        self._login(400)
        resp = self.client.get(f"/api/ai/daily-report/draft/{did}/preview")
        self.assertEqual(resp.status_code, 200)
        self._login(400)
        resp2 = self.client.get(f"/api/ai/daily-report/draft/{did}/manifest")
        self.assertEqual(resp2.status_code, 200)
        files_after = list(formal_dir.rglob("*")) if formal_dir.exists() else []
        self.assertEqual([f.name for f in files_before], [f.name for f in files_after])
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 0)

    def test_42_frontend_attachment_injection_ignored(self):
        did, _ = self._build_confirmed_draft(442)
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=400, body={
            "draft_version": 1,
            "manifest_id": manifest["manifest_id"],
            "attachments": [{"category": "site", "stored_filename": "injected.jpg"}],
            "workers": [{"user_id": 999, "travel_mode": "self_drive", "driving_miles": 999}],
            "mileage": 999,
            "description": "injected description",
        })
        self.assertEqual(resp.status_code, 201, resp.get_json())
        report_id = resp.get_json()["service_report_id"]
        db = self.app_module.db()
        workers = db.execute("select * from service_report_workers where report_id = ?", (report_id,)).fetchall()
        self.assertEqual([w["user_id"] for w in workers], [401])
        self.assertEqual(float(workers[0]["driving_miles"]), 20.0)
        report = db.execute("select * from service_reports where id = ?", (report_id,)).fetchone()
        self.assertNotIn("injected description", report["service_description"])

    def test_43_compliance_review_api_admin_only(self):
        did, _ = self._build_confirmed_draft(443)
        resp = self._prepare(did, user_id=400)
        manifest = resp.get_json()["manifest"]
        # finance cannot review
        resp403 = self._compliance_review_api(did, manifest["manifest_id"], user_id=402)
        self.assertEqual(resp403.status_code, 403)
        # manager can
        resp_ok = self._compliance_review_api(did, manifest["manifest_id"], user_id=406)
        self.assertEqual(resp_ok.status_code, 200, resp_ok.get_json())
        db = self.app_module.db()
        rows = db.execute(
            "select compliance_status from ai_daily_report_manifest_sources where manifest_id = ? and compliance_review_required = 1",
            (manifest["manifest_id"],),
        ).fetchall()
        self.assertTrue(all(r["compliance_status"] == "reviewed" for r in rows))
        # formal save now proceeds
        resp_save = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp_save.status_code, 201, resp_save.get_json())

    def test_44_reopen_invalidates_manifest(self):
        did, _ = self._build_confirmed_draft(444)
        manifest = self._prepare_ready(did)
        # reopen (confirmed -> draft)
        svc = self._ai_daily_report_svc()
        svc.reopen_draft(did)
        draft_row = self._get_draft_row(did)
        self.assertEqual(draft_row["status"], "draft")
        # GATE 1 (Phase 8 semantic drift): get_current_manifest keeps Phase 8
        # sealed semantics - a ready manifest matching draft_version +
        # validation_fingerprint is still returned for audit/history/preview,
        # even after reopen. The confirmed-only requirement belongs to the
        # Phase 9 Formal Save gate, not to this Phase 8 accessor.
        manifest_svc = self._manifest_service()
        validation = self._validation_result(draft_row)
        current = manifest_svc.get_current_manifest(draft_row, validation)
        self.assertIsNotNone(current)
        self.assertEqual(current["manifest_id"], manifest["manifest_id"])
        # But formal save on draft state is blocked by the Phase 9 gate
        # (route state check + service state gate) -> 409, no report created.
        resp = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp.status_code, 409, resp.get_json())
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 0)

    def test_45_saved_draft_immutable(self):
        did, _ = self._build_confirmed_draft(445)
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp.status_code, 201)
        # saved -> no transitions out (can_transition returns False for all)
        from ai_daily_report.daily_report_service import DailyReportService
        self.assertFalse(DailyReportService.can_transition("saved", "draft"))
        self.assertFalse(DailyReportService.can_transition("saved", "confirmed"))
        # second formal save returns existing (200) - never a new report
        resp2 = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp2.status_code, 200, resp2.get_json())
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 1)

    def _ai_daily_report_svc(self):
        return self.app_module._ai_daily_report_service()


    # ── Phase 9 Final Release Gate additions ──────────────────────────────

    def test_60_wrong_validation_fingerprint_blocks(self):
        """Requirement #6 (wrong validation fingerprint) -> no current manifest,
        formal save blocked 422."""
        did, _ = self._build_confirmed_draft(460)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        # Tamper with the fingerprint the client would report (never trusted by
        # server: server re-derives it, so we instead corrupt the manifest row).
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_attachment_manifests set validation_fingerprint = 'tampered-fp' where manifest_id = ?",
            (manifest["manifest_id"],),
        )
        db.commit()
        svc = self._formal_service()
        from ai_daily_report.formal_save import FormalSaveIntegrityError
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            svc.run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "manifest_not_ready")
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 0)

    def test_61_wrong_service_order_blocks(self):
        """Requirement #8 (wrong order) -> manifest.service_order_id !=
        draft.service_order_id -> 422 integrity, no report."""
        did, _ = self._build_confirmed_draft(461)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_attachment_manifests set service_order_id = 999 where manifest_id = ?",
            (manifest["manifest_id"],),
        )
        db.commit()
        svc = self._formal_service()
        from ai_daily_report.formal_save import FormalSaveIntegrityError
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            svc.run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "integrity_failed")
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 0)

    def test_62_wrong_report_date_blocks(self):
        """Requirement #9 (wrong report date) -> 422 integrity, no report."""
        did, _ = self._build_confirmed_draft(462)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        db = self.app_module.db()
        db.execute(
            "update ai_daily_report_attachment_manifests set report_date = '2020-01-01' where manifest_id = ?",
            (manifest["manifest_id"],),
        )
        db.commit()
        svc = self._formal_service()
        from ai_daily_report.formal_save import FormalSaveIntegrityError
        with self.assertRaises(FormalSaveIntegrityError) as ctx:
            svc.run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(ctx.exception.code, "integrity_failed")
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 0)

    def test_63_safety_arrival_no_duplicate_attachment(self):
        """Requirement #32: safety + arrival shared photo -> single self_check
        formal attachment; arrival stays provenance-only."""
        did, data = self._build_confirmed_draft(463)
        photos = self._photo_files()
        safety_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/safety.jpg"
        service_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        # arrival provenance points at the SAME photo already selected as the
        # safety photo -> single self_check formal attachment; no second copy.
        data["arrival_photo_ref"] = photos[safety_rel]
        data["departure_photo_ref"] = None
        data["evidence_records"] = []
        data["selected_service_photos"] = [{"photo_id": photos[service_rel], "classification": "equipment"}]
        data["selected_safety_photo"] = {"photo_id": photos[safety_rel], "confidence": 0.9, "classification": "safety_person"}
        self._insert_draft(did, data, status="confirmed", draft_version=1, created_by=401)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        result = self._formal_service().run(
            draft_row, 1, manifest["manifest_id"], validation, self._manifest_service()
        )
        self.assertEqual(result["status"], "created")
        rid = result["service_report_id"]
        db = self.app_module.db()
        cats = sorted(r["category"] for r in db.execute(
            "select category from service_report_attachments where report_id = ?", (rid,)
        ).fetchall())
        # site (service photo) + self_check (safety photo); arrival never adds
        # a second attachment even though arrival_photo_ref == safety photo.
        self.assertEqual(cats, ["self_check", "site"])
        self.assertEqual(self._count_rows("service_report_attachments where report_id = %d" % rid), 2)

    def test_64_service_safety_shared_asset_gate4(self):
        """GATE 4: same physical prepared asset carrying service_photo +
        safety_photo -> site + self_check rows, both files exist, content SHA
        equals prepared source, provenance points to the same source."""
        did, data = self._build_confirmed_draft(464)
        photos = self._photo_files()
        service_rel = f"{self.ORDER_NUMBER}/pictures/2026-09-14/service1.jpg"
        # Same photo selected as BOTH safety and service photo.
        data["evidence_records"] = []
        data["selected_safety_photo"] = {"photo_id": photos[service_rel], "confidence": 0.9, "classification": "safety_person"}
        data["selected_service_photos"] = [{"photo_id": photos[service_rel], "classification": "equipment"}]
        self._insert_draft(did, data, status="confirmed", draft_version=1, created_by=401)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        svc = self._formal_service()
        result = svc.run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(result["status"], "created")
        rid = result["service_report_id"]
        db = self.app_module.db()
        rows = db.execute(
            "select category, stored_filename from service_report_attachments where report_id = ? order by category",
            (rid,),
        ).fetchall()
        self.assertEqual([r["category"] for r in rows], ["self_check", "site"])
        # Both formal files exist on disk and hash to the prepared source SHA.
        report_dir = Path(self.temp_dir) / "service-report-attachments"
        prepared_sha = db.execute(
            "select prepared_sha256 from ai_daily_report_prepared_assets where manifest_id = ?",
            (manifest["manifest_id"],),
        ).fetchone()["prepared_sha256"]
        for r in rows:
            path = report_dir / r["stored_filename"]
            self.assertTrue(path.is_file(), r["stored_filename"])
            import hashlib as _h
            h = _h.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(h, prepared_sha)
        # Provenance: both roles point to the same prepared asset/source.
        role_rows = db.execute(
            """
            select role_type, category from ai_daily_report_manifest_roles
            where manifest_id = ? and role_type in ('service_photo', 'safety_photo')
            order by role_type
            """,
            (manifest["manifest_id"],),
        ).fetchall()
        self.assertEqual({r["role_type"] for r in role_rows}, {"service_photo", "safety_photo"})
        self.assertEqual({r["category"] for r in role_rows}, {"site", "self_check"})

    def test_65_two_tab_concurrency_exactly_once(self):
        """GATE 7: two independent DB connections race the same formal save.
        Exactly one service_report + one committed commit row; second request
        surfaces 409 (or 200 on retry after winner commits); retry returns the
        same report id. No duplicate workers/attachments/files."""
        import threading
        did, _ = self._build_confirmed_draft(465)
        manifest = self._prepare_ready(did)
        draft_row0 = self._get_draft_row(did)
        validation = self._validation_result(draft_row0)
        results = []
        lock = threading.Lock()

        def worker():
            conn = None
            try:
                import sqlite3 as _s
                from ai_daily_report.formal_save import FormalSaveService
                from database import postgres_enabled, PostgreSQLConnection
                if postgres_enabled():
                    conn = PostgreSQLConnection()
                else:
                    conn = _s.connect(self.app_module.DB_PATH, timeout=30)
                    conn.row_factory = _s.Row
                    conn.execute("PRAGMA busy_timeout = 30000")
                    conn.execute("PRAGMA foreign_keys = ON")
                from ai_daily_report.attachment_manifest import AttachmentManifestService
                manifest_svc = AttachmentManifestService(
                    conn, self.shared_dir, self.temp_dir, 400,
                )
                svc = FormalSaveService(
                    conn, self.temp_dir,
                    os.path.join(self.temp_dir, "service-report-attachments"), 400,
                )
                r = svc.run(dict(draft_row0), 1, manifest["manifest_id"], validation, manifest_svc)
                with lock:
                    results.append(("ok", r))
            except Exception as exc:  # noqa: BLE001
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                with lock:
                    results.append(("err", type(exc).__name__, getattr(exc, "code", None)))
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        db = self.app_module.db()
        self.assertEqual(
            self._count_rows("service_reports where ai_draft_id = %d" % did), 1,
            results,
        )
        self.assertEqual(
            self._count_rows("ai_daily_report_formal_commits where draft_id = %d and status = 'committed'" % did), 1,
        )
        # workers + attachments: exactly one set
        rid = db.execute(
            "select saved_report_id from ai_daily_report_drafts where id = ?", (did,),
        ).fetchone()["saved_report_id"]
        self.assertEqual(self._count_rows("service_report_workers where report_id = %d" % rid), 1)
        # Default confirmed draft: safety (self_check) + service (site) +
        # mileage evidence (mileage_proof) = 3 formal attachment rows, no
        # duplicates from the race.
        self.assertEqual(self._count_rows("service_report_attachments where report_id = %d" % rid), 3)
        # Second request after completion -> 200 same report (via API, serial).
        resp = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(int(resp.get_json()["service_report_id"]), rid)
        # Exactly one created; the other may be already_committed (if it saw
        # the winner) or 409 (if it raced) - never a second creation.
        created = [r for r in results if r[0] == "ok" and r[1].get("status") == "created"]
        already = [r for r in results if r[0] == "ok" and r[1].get("status") == "already_committed"]
        errors = [r for r in results if r[0] == "err"]
        self.assertEqual(len(created), 1, results)
        self.assertTrue(len(already) + len(errors) >= 1, results)
        for e in errors:
            self.assertIn(e[2], ("commit_in_progress", "version_conflict", "draft_state_error"), results)

    def test_66_crash_after_replace_recovery_no_orphan(self):
        """GATE 3 fault injection: crash immediately after os.replace, before
        the follow-up journal update -> recovery deterministically removes the
        file (planned paths were durable before materialization). 0 orphan."""
        did, _ = self._build_confirmed_draft(466)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        from ai_daily_report import formal_save as fs_mod
        real_replace = os.replace
        state = {"replaced": 0}
        def crash_replace(src, dst):
            state["replaced"] += 1
            real_replace(src, dst)
            # Simulate process death right after the final rename, BEFORE the
            # journal update (which happens after os.replace returns).
            raise KeyboardInterrupt("simulated crash after os.replace")
        fs_mod.os.replace = crash_replace
        svc = self._formal_service()
        try:
            with self.assertRaises(KeyboardInterrupt):
                svc.run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        finally:
            fs_mod.os.replace = real_replace
        self.assertGreaterEqual(state["replaced"], 1)
        # No report/commit rows.
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 0)
        # The committing row exists with planned paths recorded.
        db = self.app_module.db()
        commit_row = db.execute(
            "select * from ai_daily_report_formal_commits where draft_id = ?", (did,),
        ).fetchone()
        self.assertIsNotNone(commit_row)
        self.assertEqual(commit_row["status"], "committing")
        journal = json.loads(commit_row["formal_files"] or "{}")
        self.assertIn("planned", journal)
        self.assertGreaterEqual(len(journal["planned"]), 1)
        # The os.replace()-ed file physically exists as an orphan right now.
        report_dir = Path(self.temp_dir) / "service-report-attachments"
        orphan_files = [report_dir / p for p in journal["planned"] if (report_dir / p).exists()]
        self.assertGreaterEqual(len(orphan_files), 1)
        # Restart recovery removes every planned file -> 0 orphan.
        svc.recover_pending(did)
        remaining = [p for p in journal["planned"] if (report_dir / p).exists()]
        self.assertEqual(remaining, [])
        # Retry after recovery succeeds (fresh claim).
        draft_row2 = self._get_draft_row(did)
        validation2 = self._validation_result(draft_row2)
        result = self._formal_service().run(
            draft_row2, 1, manifest["manifest_id"], validation2, self._manifest_service()
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 1)

    def test_67_compliance_review_audit_trail(self):
        """GATE 6: review records reviewed_by / reviewed_at / old state; stale
        (non-confirmed) Draft cannot be compliance-reviewed."""
        did, _ = self._build_confirmed_draft(467)
        resp = self._prepare(did, user_id=400)
        manifest = resp.get_json()["manifest"]
        # Manager reviews -> audit columns populated.
        r = self._compliance_review_api(did, manifest["manifest_id"], user_id=406)
        self.assertEqual(r.status_code, 200, r.get_json())
        db = self.app_module.db()
        rows = db.execute(
            """
            select compliance_status, compliance_reviewed_by, compliance_reviewed_at, provider
            from ai_daily_report_manifest_sources
            where manifest_id = ? and compliance_review_required = 1
            """,
            (manifest["manifest_id"],),
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["compliance_status"], "reviewed")
            self.assertEqual(row["compliance_reviewed_by"], 406)
            self.assertIsNotNone(row["compliance_reviewed_at"])
            self.assertEqual(row["provider"], "google_static_maps")
        # Audit log records who/when/old-state/fingerprint.
        audit = db.execute(
            "select summary from audit_logs where entity_type = 'ai_daily_report_manifest' order by id desc limit 1",
        ).fetchone()
        self.assertIn("-> reviewed", audit["summary"])
        self.assertIn("validation_fingerprint=", audit["summary"])
        # Reopen -> compliance review on non-confirmed Draft blocked 409.
        svc = self._ai_daily_report_svc()
        svc.reopen_draft(did)
        r2 = self._compliance_review_api(did, manifest["manifest_id"], user_id=406)
        self.assertEqual(r2.status_code, 409, r2.get_json())

    def test_68_saved_state_invariant(self):
        """GATE 8: confirmed -> ready -> formal save -> saved. Edit/reopen/
        prepare denied, no second report, audit/history/provenance visible."""
        did, _ = self._build_confirmed_draft(468)
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        rid = int(resp.get_json()["service_report_id"])
        draft_row = self._get_draft_row(did)
        self.assertEqual(draft_row["status"], "saved")
        # prepare denied (state machine + authorization)
        r_prep = self._prepare(did, user_id=400)
        self.assertIn(r_prep.status_code, (403, 409), r_prep.get_json())
        # second formal save -> 200 existing, no new report
        r2 = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(r2.status_code, 200, r2.get_json())
        self.assertEqual(int(r2.get_json()["service_report_id"]), rid)
        # reopen denied by transition rules
        from ai_daily_report.daily_report_service import DailyReportService
        self.assertFalse(DailyReportService.can_transition("saved", "draft"))
        # Draft audit / manifest history / provenance remain visible.
        db = self.app_module.db()
        manifests = db.execute(
            "select count(*) as c from ai_daily_report_attachment_manifests where draft_id = ?",
            (did,),
        ).fetchone()["c"]
        self.assertGreaterEqual(manifests, 1)
        commits = db.execute(
            "select count(*) as c from ai_daily_report_formal_commits where draft_id = ? and status = 'committed'",
            (did,),
        ).fetchone()["c"]
        self.assertEqual(commits, 1)
        # Manifest history API still returns the manifest for audit.
        resp_hist = self.client.get(f"/api/ai/daily-report/draft/{did}/manifest")
        self.assertEqual(resp_hist.status_code, 200)

    def test_69_http_retry_returns_same_report(self):
        """Requirement #15 (HTTP retry): identical retry after success returns
        200 with the same service_report_id; no duplicate rows."""
        did, _ = self._build_confirmed_draft(469)
        manifest = self._prepare_ready(did)
        r1 = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(r1.status_code, 201)
        r2 = self._form_save(did, user_id=400, body={"draft_version": 1, "manifest_id": manifest["manifest_id"]})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.get_json()["service_report_id"], r2.get_json()["service_report_id"])
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 1)

    def test_70_frontend_worker_mileage_injection_ignored(self):
        """Requirements #57/#58: frontend-submitted workers/mileage/times are
        ignored - formal values come only from the frozen Draft."""
        did, _ = self._build_confirmed_draft(470)
        manifest = self._prepare_ready(did)
        resp = self._form_save(did, user_id=400, body={
            "draft_version": 1,
            "manifest_id": manifest["manifest_id"],
            "workers": [{"user_id": 999, "driving_miles": 999, "travel_mode": "flight"}],
            "driving_miles": 9999,
            "arrival_time": "00:00",
            "departure_time": "23:59",
            "service_description": "INJECTED",
        })
        self.assertEqual(resp.status_code, 201, resp.get_json())
        rid = int(resp.get_json()["service_report_id"])
        db = self.app_module.db()
        report = db.execute("select * from service_reports where id = ?", (rid,)).fetchone()
        self.assertEqual(report["driving_miles"], 20.0)  # Draft reported 20.0
        workers = db.execute("select * from service_report_workers where report_id = ?", (rid,)).fetchall()
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0]["user_id"], 401)
        self.assertNotIn("INJECTED", report["service_description"])


# ─── Boundary: zero external calls, zero out-of-transaction writes ──────────

class TestPhase9Boundary(Phase9TestBase):

    def test_50_no_external_ai_calls_during_formal_save(self):
        did, _ = self._build_confirmed_draft(450)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        # The Phase 9 module must never reference external provider clients.
        import ai_daily_report.formal_save as fs
        src = open(fs.__file__, encoding="utf-8").read()
        for forbidden in (
            "googlemaps",
            "staticmaps.googleapis",
            "routes.googleapis",
            "deepseek",
            "openai",
            "vision",
            "requests.post",
            "urllib.request",
            "PhotoDiscoveryService",
            "discover_photos",
        ):
            self.assertNotIn(forbidden, src)
        # Real formal save runs with zero external calls by construction.
        result = self._formal_service().run(
            draft_row, 1, manifest["manifest_id"], validation, self._manifest_service()
        )
        self.assertEqual(result["status"], "created")

    def test_51_zero_out_of_transaction_writes(self):
        before = {
            "service_reports": self._count_rows("service_reports"),
            "service_report_workers": self._count_rows("service_report_workers"),
            "service_report_attachments": self._count_rows("service_report_attachments"),
        }
        # GET-only flow must not write any formal rows
        did, _ = self._build_confirmed_draft(451)
        self._prepare_ready(did)
        self._login(400)
        self.client.get(f"/api/ai/daily-report/draft/{did}/preview")
        self.client.get(f"/api/ai/daily-report/draft/{did}/manifest")
        self._login(400)
        manifest = self._manifest_service()
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        current = manifest.get_current_manifest(draft_row, validation)
        asset = self.app_module.db().execute(
            "select asset_id from ai_daily_report_prepared_assets where manifest_id = ? limit 1",
            (current["manifest_id"],),
        ).fetchone()
        self.client.get(f"/api/ai/daily-report/draft/{did}/manifest/{current['manifest_id']}/asset/{asset['asset_id']}")
        after = {
            "service_reports": self._count_rows("service_reports"),
            "service_report_workers": self._count_rows("service_report_workers"),
            "service_report_attachments": self._count_rows("service_report_attachments"),
        }
        self.assertEqual(before, after)

    def test_52_original_photos_and_evidence_untouched(self):
        did, _ = self._build_confirmed_draft(452)
        photos = self._photo_files()
        ev_rel, ev_sha = self._evidence_file(did, "ev_001")
        originals_before = {rel: _sha256_file(Path(self.shared_dir) / rel) for rel in photos}
        ev_before = _sha256_file(Path(self.temp_dir) / ev_rel)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        self._formal_service().run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        originals_after = {rel: _sha256_file(Path(self.shared_dir) / rel) for rel in photos}
        ev_after = _sha256_file(Path(self.temp_dir) / ev_rel)
        self.assertEqual(originals_before, originals_after)
        self.assertEqual(ev_before, ev_after)

    def test_53_recovery_after_crash_cleans_committing(self):
        did, _ = self._build_confirmed_draft(453)
        manifest = self._prepare_ready(did)
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        # Simulate a crash: claim a committing row with a fake formal file.
        db = self.app_module.db()
        formal_dir = Path(self.temp_dir) / "service-report-attachments" / "SO-PHASE9-001" / "20260914" / "现场服务照片"
        formal_dir.mkdir(parents=True, exist_ok=True)
        fake = formal_dir / "orphan-from-crash.tmp"
        fake.write_bytes(b"orphan")
        db.execute(
            """
            insert into ai_daily_report_formal_commits (
                commit_id, draft_id, manifest_id, draft_version, validation_fingerprint,
                manifest_fingerprint, manifest_snapshot, fields_provenance,
                service_report_id, status, failure_code, formal_files,
                created_by, started_at
            ) values (?, ?, ?, ?, ?, ?, '{}', '{}', NULL, 'committing', NULL, ?, ?, ?)
            """,
            (
                "commit-crash-test", did, manifest["manifest_id"],
                draft_row["draft_version"], validation.validation_fingerprint,
                manifest["manifest_fingerprint"],
                _canonical({"files": ["SO-PHASE9-001/20260914/现场服务照片/orphan-from-crash.tmp"], "tmp_files": []}),
                400, "2026-09-14T00:00:00Z",
            ),
        )
        db.commit()
        svc = self._formal_service()
        svc.recover_pending(did)
        self.assertFalse(fake.exists())
        row = db.execute(
            "select status, failure_code from ai_daily_report_formal_commits where commit_id = 'commit-crash-test'"
        ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["failure_code"], "recovered_after_crash")
        # retry after recovery succeeds
        manifest2 = self._prepare_ready(did) if not self._is_manifest_ready(did) else manifest
        result = svc.run(draft_row, 1, manifest["manifest_id"], validation, self._manifest_service())
        self.assertEqual(result["status"], "created")
        self.assertEqual(self._count_rows("service_reports where ai_draft_id = %d" % did), 1)

    def _is_manifest_ready(self, did):
        draft_row = self._get_draft_row(did)
        validation = self._validation_result(draft_row)
        return self._manifest_service().get_current_manifest(draft_row, validation) is not None


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    unittest.main()
