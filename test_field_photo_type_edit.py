"""Ledger photo-type editing: new API + AI Daily Report discovery refresh (HIGH-2).

Covers:
- POST /api/field/photos/<id>/type auth / csrf / whitelist / not-found / success
- ledger page renders the editable select (or plain label without permission)
- discover_photos_for_draft idempotent branch re-applies the ledger photo_type
  (HIGH-2) and does not clear already-confirmed business roles
"""
import importlib.util
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

import test_expense_on_behalf as fixture

REPO_DIR = Path(__file__).resolve().parent


class LedgerPhotoTypeEditApiTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module = self.fixture.app
        self.http = self.fixture.http
        self.root = Path(self.fixture.temp.name) / "shared"
        self.module.SHARED_PHOTOS_DIR = str(self.root)
        self.module.app.static_folder = str(fixture.ROOT / "static")
        self.csrf = self.http.get("/api/field/session").json["csrf"]
        self.capture_time = (datetime.now(timezone.utc) - timedelta(days=2)).replace(
            hour=23, minute=30, second=0, microsecond=0
        )
        photo = BytesIO()
        Image.new("RGB", (2600, 1950), "green").save(photo, "JPEG")
        self.photo = photo.getvalue()

    def upload(self, **overrides):
        data = dict(
            client_id=uuid.uuid4().hex,
            order_id=str(self.fixture.order),
            user_id=str(self.fixture.people["Submitter"]),
            captured_at=self.capture_time.isoformat(),
            timezone_name="Pacific/Kiritimati",
            latitude="52.1",
            longitude="4.3",
            accuracy="8",
            source="camera",
            note="Equipment check",
            photo=(BytesIO(self.photo), "photo.jpg"),
        )
        data.update(
            equipment_number="BESB-2B6-1",
            position_number="B6-1",
            equipment_session=uuid.uuid4().hex,
            no_equipment_number="false",
        )
        data.update(overrides)
        return self.http.post(
            "/api/field/photos", data=data, headers={"X-Field-Token": self.csrf}
        )

    def set_type(self, photo_id, photo_type, token=None):
        return self.http.post(
            "/api/field/photos/{}/type".format(photo_id),
            json={"photo_type": photo_type},
            headers={"X-Field-Token": token if token is not None else self.csrf},
        )

    def test_requires_login(self):
        anonymous = self.module.app.test_client()
        resp = anonymous.post(
            "/api/field/photos/1/type", json={"photo_type": "arrival"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_requires_field_token(self):
        r = self.upload()
        self.assertEqual(r.status_code, 200, r.text)
        resp = self.http.post(
            "/api/field/photos/{}/type".format(r.json["id"]),
            json={"photo_type": "arrival"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_invalid_type_rejected(self):
        r = self.upload()
        pid = r.json["id"]
        for bad in ("general", "legacy", "", "equipmentx", "设备", "Arrival"):
            resp = self.set_type(pid, bad)
            self.assertEqual(resp.status_code, 422, repr(bad))

    def test_missing_photo_404(self):
        resp = self.set_type(9999999, "arrival")
        self.assertEqual(resp.status_code, 404)

    def test_equipment_to_arrival_persists(self):
        r = self.upload()
        pid = r.json["id"]
        resp = self.set_type(pid, "arrival")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json
        self.assertTrue(body["ok"])
        self.assertEqual(body["photo_type"], "arrival")
        self.assertEqual(body["photo_type_label"], "进场")
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select photo_type from field_photos where id = ?", (pid,)
            ).fetchone()
        self.assertEqual(row["photo_type"], "arrival")

    def test_all_four_types_roundtrip(self):
        r = self.upload()
        pid = r.json["id"]
        for photo_type, label in (
            ("equipment", "设备"),
            ("arrival", "进场"),
            ("departure", "离场"),
            ("safety", "自检"),
            ("equipment", "设备"),
        ):
            resp = self.set_type(pid, photo_type)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json["photo_type_label"], label)

    def test_unchanged_is_noop(self):
        r = self.upload()
        pid = r.json["id"]
        self.assertEqual(self.set_type(pid, "equipment").status_code, 200)
        resp = self.set_type(pid, "equipment")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json.get("unchanged"))

    def test_general_legacy_can_be_upgraded(self):
        for old in ("general", "legacy"):
            r = self.upload(photo_type=old)
            pid = r.json["id"]
            resp = self.set_type(pid, "safety")
            self.assertEqual(resp.status_code, 200, resp.text)
            with self.module.app.app_context():
                row = self.module.db().execute(
                    "select photo_type from field_photos where id = ?", (pid,)
                ).fetchone()
            self.assertEqual(row["photo_type"], "safety")

    def test_device_metadata_survives_type_change(self):
        r = self.upload()
        pid = r.json["id"]
        resp = self.set_type(pid, "departure")
        self.assertEqual(resp.status_code, 200)
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select equipment_number, position_number from field_photos where id = ?",
                (pid,),
            ).fetchone()
        self.assertEqual(row["equipment_number"], "BESB-2B6-1")
        self.assertEqual(row["position_number"], "B6-1")

    def test_ledger_page_renders_editable_select(self):
        self.upload()
        resp = self.http.get("/reports/field-photos")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("photo-type-select", resp.text)
        self.assertIn("data-photo-id", resp.text)
        self.assertIn("进场", resp.text)


class DiscoverPhotoTypeRefreshTest(unittest.TestCase):
    """HIGH-2: idempotent discover branch must re-apply the ledger photo_type."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        ai_dir = Path(cls.temp_dir.name) / "ai_daily_report"
        shutil.copytree(REPO_DIR / "ai_daily_report", ai_dir)
        spec = importlib.util.spec_from_file_location("invoice_ptype_test", module_path)
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
            conn.execute("delete from service_orders where order_number='SO-PTYP'")
            conn.execute("delete from users where email like 'ptyp%'")
            client = conn.execute("select id from clients limit 1").fetchone()
            if not client:
                conn.execute(
                    "insert into clients (name, client_number, short_name, country, created_at) "
                    "values ('T', 'C', 'T', 'US', '2026-09-14T00:00:00')"
                )
                client_id = conn.execute("select last_insert_rowid()").fetchone()[0]
            else:
                client_id = client["id"]
            admin = conn.execute("select id from users where role='admin' limit 1").fetchone()
            if not admin:
                conn.execute(
                    "insert into users (name, email, password_hash, role, created_at) "
                    "values ('Admin', 'ptyp@test.com', 'x', 'admin', '2026-09-14T00:00:00')"
                )
                admin = conn.execute("select id from users where role='admin' limit 1").fetchone()
            self.admin_id = admin["id"]
            conn.execute(
                "insert into service_orders (order_number, client_id, client_name, client_order_number, "
                "site_address, status, created_by, created_at) "
                "values ('SO-PTYP', ?, 'T', 'ORD-PTYP', '123 St', 'open', ?, '2026-09-14T00:00:00')",
                (client_id, self.admin_id),
            )
            conn.commit()
            self.order = conn.execute(
                "select * from service_orders where order_number='SO-PTYP'"
            ).fetchone()
        for d in self.shared_root.iterdir():
            if d.is_dir():
                shutil.rmtree(d)

    def _make_jpeg(self, rel, color=(10, 20, 30), capture_time=None):
        from test_ai_daily_report_phase4 import make_test_jpeg

        make_test_jpeg(self.shared_root / rel, color=color, capture_time=capture_time)

    def _services(self, lookup):
        from ai_daily_report import PhotoDiscoveryService, PhotoMetadataService

        def mock_order_folder(on):
            return (self.shared_root / on).resolve()

        # Normalize path separators: on Windows the scanner yields backslash
        # relative paths while the production DB stores forward slashes (Linux).
        pd = PhotoDiscoveryService(str(self.shared_root), mock_order_folder)
        pm = PhotoMetadataService(str(self.shared_root), "America/Chicago")
        return pd, pm, (lambda p: lookup.get(p.replace("\\", "/")))

    def test_idempotent_scan_refreshes_ledger_type(self):
        from ai_daily_report import DailyReportService

        with self.module.app.app_context():
            svc = DailyReportService(
                self.module.db(), lambda: "2026-09-14T12:00:00Z", self.admin_id, "Admin"
            )
            draft_row = svc.create_draft(self.order["id"], "2026-09-14", site_address="123 St")
            draft_id = draft_row["id"]
            rel = "SO-PTYP/pictures/2026-09-14/p1.jpg"
            self._make_jpeg(rel, capture_time=datetime(2026, 9, 14, 8, 0))
            lookup = {rel: "equipment"}
            pd, pm, lookup_fn = self._services(lookup)
            r1 = svc.discover_photos_for_draft(draft_id, pd, pm, photo_type_lookup=lookup_fn)
            self.assertEqual(r1["status"], "discovered")
            first = svc.parse_draft_data(svc.get_draft(draft_id))
            self.assertEqual(first.photo_candidates[0].classification, "equipment")
            # Ledger re-classification (same file set -> fingerprint unchanged)
            lookup[rel] = "arrival"
            r2 = svc.discover_photos_for_draft(draft_id, pd, pm, photo_type_lookup=lookup_fn)
            self.assertEqual(r2["status"], "discovered")
            self.assertEqual(r2["photo_set_fingerprint"], r1["photo_set_fingerprint"])
            updated = svc.parse_draft_data(svc.get_draft(draft_id))
            self.assertEqual(updated.photo_candidates[0].classification, "arrival")
            self.assertEqual(updated.photo_candidates[0].manual_classification, "arrival")

    def test_safety_marked_photo_auto_selected(self):
        """现场标记「自检照片」的照片发现后自动进入安全自检选择，且幂等不重复。"""
        from ai_daily_report import DailyReportService

        with self.module.app.app_context():
            svc = DailyReportService(
                self.module.db(), lambda: "2026-09-14T12:00:00Z", self.admin_id, "Admin"
            )
            draft_row = svc.create_draft(self.order["id"], "2026-09-14", site_address="123 St")
            draft_id = draft_row["id"]
            rel1 = "SO-PTYP/pictures/2026-09-14/p1.jpg"
            rel2 = "SO-PTYP/pictures/2026-09-14/p2.jpg"
            self._make_jpeg(rel1, color=(1, 2, 3), capture_time=datetime(2026, 9, 14, 8, 0))
            self._make_jpeg(rel2, color=(9, 9, 9), capture_time=datetime(2026, 9, 14, 12, 0))
            lookup = {rel1: "safety", rel2: "equipment"}
            pd, pm, lookup_fn = self._services(lookup)
            r = svc.discover_photos_for_draft(draft_id, pd, pm, photo_type_lookup=lookup_fn)
            self.assertEqual(r["status"], "discovered")
            draft = svc.parse_draft_data(svc.get_draft(draft_id))
            safety_ids = [a.photo_id for a in draft.selected_safety_photos]
            expected = [p.photo_id for p in draft.photo_candidates if p.classification == "safety"]
            self.assertEqual(safety_ids, expected)
            self.assertEqual(len(safety_ids), 1)
            self.assertIsNotNone(draft.selected_safety_photo)
            self.assertEqual(draft.selected_safety_photo.photo_id, safety_ids[0])
            self.assertEqual(draft.selected_safety_photo_source, "user_selected")
            self.assertIsNotNone(draft.safety_photo)
            self.assertEqual(draft.safety_photo.photo_id, safety_ids[0])
            # Idempotent: re-scan (unchanged fingerprint) must not duplicate.
            svc.discover_photos_for_draft(draft_id, pd, pm, photo_type_lookup=lookup_fn)
            again = svc.parse_draft_data(svc.get_draft(draft_id))
            self.assertEqual(len(again.selected_safety_photos), 1)
            self.assertEqual(again.selected_safety_photos[0].photo_id, safety_ids[0])

    def test_refresh_keeps_confirmed_business_roles(self):
        from ai_daily_report import DailyReportService

        with self.module.app.app_context():
            svc = DailyReportService(
                self.module.db(), lambda: "2026-09-14T12:00:00Z", self.admin_id, "Admin"
            )
            draft_row = svc.create_draft(self.order["id"], "2026-09-14", site_address="123 St")
            draft_id = draft_row["id"]
            rel1 = "SO-PTYP/pictures/2026-09-14/p1.jpg"
            rel2 = "SO-PTYP/pictures/2026-09-14/p2.jpg"
            self._make_jpeg(rel1, color=(1, 2, 3), capture_time=datetime(2026, 9, 14, 8, 0))
            self._make_jpeg(rel2, color=(4, 5, 6), capture_time=datetime(2026, 9, 14, 17, 0))
            lookup = {rel1: "equipment", rel2: "equipment"}
            pd, pm, lookup_fn = self._services(lookup)
            svc.discover_photos_for_draft(draft_id, pd, pm, photo_type_lookup=lookup_fn)
            confirmed = svc.confirm_photo_timeline(draft_id)
            self.assertTrue(confirmed["ok"])
            before = svc.parse_draft_data(svc.get_draft(draft_id))
            self.assertIsNotNone(before.arrival_photo_ref)
            self.assertIsNotNone(before.departure_photo_ref)
            # Ledger re-classification then re-scan (idempotent branch)
            lookup[rel1] = "arrival"
            lookup[rel2] = "departure"
            svc.discover_photos_for_draft(draft_id, pd, pm, photo_type_lookup=lookup_fn)
            after = svc.parse_draft_data(svc.get_draft(draft_id))
            self.assertIsNotNone(after.arrival_photo_ref)
            self.assertIsNotNone(after.departure_photo_ref)
            self.assertEqual(after.arrival_time_source, "photo_timeline_confirmed")


if __name__ == "__main__":
    unittest.main()
