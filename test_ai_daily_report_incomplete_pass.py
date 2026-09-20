"""AI Daily Report - Incomplete Draft Forced Pass-to-Order Tests (v0.1.252)

Product decision 2026-09-18: an AI daily report that cannot pass Phase 7
validation (e.g. a freshly created draft with no parsed content / no workers)
must still be passable to the order, and the user keeps editing on the order
report page.

Covers:
- Empty draft triggers blocking validation (documents the user-reported
  dead-end scenario: confirm blocked + no manifest -> no formal-save button).
- FormalSaveService.run_incomplete(): creates the service report with zero
  workers/attachments, draft -> saved, commit row with sentinel
  manifest_id='force_incomplete'.
- Exactly-once: repeated run_incomplete returns already_committed.
- State gate: non-confirmed drafts are rejected.
- Version lock: stale expected_draft_version raises FormalSaveStaleError.
- _build_formal_mapping(allow_empty_workers=True) bypass; default still raises.
- Endpoint formal-save with force_incomplete=true -> 201 + incomplete flag;
  without the flag the normal path still 422s on blocking validation.
- Endpoint confirm auto-chain with force_incomplete=true -> one-shot
  confirm + forced pass (auto_formal_save.incomplete = true).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-incomplete")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def _make_empty_draft_data(**overrides):
    """A freshly created '智能日报': order + date selected, nothing parsed."""
    data = {
        "service_order_id": 400,
        "report_date": "2026-08-27",
        "workers": [],
        "work_items": [],
        "service_description": "",
        "photo_candidates": [],
        "verification_required": False,
        "verification_fields": [],
        "ai_metadata": {"model": "deepseek-v4-flash", "created_by": "ai"},
    }
    data.update(overrides)
    return data


class IncompletePassTestBase(unittest.TestCase):
    """Temp DATA_DIR + minimal users/orders/drafts; direct service + endpoint."""

    ORDER_NUMBER = "SO-INCOMPLETE-001"

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="incomplete_pass_test_")
        os.environ["DATA_DIR"] = cls.temp_dir
        cls.shared_dir = os.path.join(cls.temp_dir, "shared-photos")
        os.environ["SHARED_PHOTOS_DIR"] = cls.shared_dir
        Path(cls.shared_dir).mkdir(parents=True, exist_ok=True)

        import app as app_module
        cls.app_module = app_module
        app_module.DATA_DIR = cls.temp_dir
        app_module.DB_PATH = os.path.join(cls.temp_dir, "invoices.db")
        app_module.ATTACHMENTS_DIR = os.path.join(cls.temp_dir, "attachments")
        app_module.REPORT_ATTACHMENTS_DIR = os.path.join(
            cls.temp_dir, "service-report-attachments"
        )
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
            (400, "admin-inc@test.com", "Admin Inc", "admin"),
            (401, "ethan-inc@test.com", "Ethan Inc", "employee"),
        ]:
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (uid, email, name, "x", role, "2026-01-01T00:00:00Z"),
            )
        db.execute(
            "insert or ignore into service_orders (id, order_number, client_name, site_address, client_order_number, status, created_by, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (400, cls.ORDER_NUMBER, "Client Inc", "123 Site Ave", "CLIENT-INC-001", "open", 400, "2026-08-27T00:00:00Z"),
        )
        db.commit()

    @classmethod
    def _insert_draft(cls, draft_id, draft_data, status="confirmed", draft_version=1, created_by=401, report_date="2026-08-27"):
        db = cls.app_module.db()
        existing = db.execute(
            "select id from ai_daily_report_drafts where id = ?", (draft_id,)
        ).fetchone()
        if existing:
            db.execute(
                "update ai_daily_report_drafts set service_order_id = ?, report_date = ?, status = ?, draft_version = ?, created_by = ?, draft_data = ?, saved_report_id = NULL, updated_at = ? where id = ?",
                (400, report_date, status, draft_version, created_by, json.dumps(draft_data), "2026-08-27T00:00:00Z", draft_id),
            )
        else:
            db.execute(
                "insert into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft_id, 400, report_date, status, draft_version, created_by, json.dumps(draft_data), "2026-08-27T00:00:00Z", "2026-08-27T00:00:00Z"),
            )
        db.commit()

    @classmethod
    def _get_draft_row(cls, draft_id):
        row = cls.app_module.db().execute(
            "select * from ai_daily_report_drafts where id = ?", (draft_id,)
        ).fetchone()
        return dict(row) if row else None

    def _make_service(self):
        m = self.app_module
        return m.FormalSaveService(
            m.db(), m.DATA_DIR, m.REPORT_ATTACHMENTS_DIR, 400
        )

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def _get_csrf(self):
        resp = self.client.get("/api/ai/daily-report/csrf")
        data = resp.get_json(silent=True) or {}
        return data.get("csrf_token") or None

    def setUp(self):
        self._ctx = self.app.app_context()
        self._ctx.push()

    def tearDown(self):
        self._ctx.pop()


class RunIncompleteServiceTests(IncompletePassTestBase):
    """Direct FormalSaveService.run_incomplete tests."""

    def test_01_empty_draft_validation_blocks(self):
        """Documents the reported dead-end: empty draft -> blocking ERRORs."""
        self._insert_draft(510, _make_empty_draft_data(), status="confirmed")
        draft_row = self._get_draft_row(510)
        validation = self.app_module._run_phase7_validation(draft_row)
        self.assertFalse(validation.can_proceed)
        rule_ids = {i.rule_id for i in validation.issues}
        self.assertTrue(
            {"DRFT-004", "WRKR-001"} & rule_ids,
            f"expected empty-draft blocking rules, got {rule_ids}",
        )

    def test_02_run_incomplete_creates_report(self):
        self._insert_draft(511, _make_empty_draft_data(), status="confirmed")
        draft_row = self._get_draft_row(511)
        result = self._make_service().run_incomplete(draft_row, None)
        self.assertEqual(result["status"], "created")
        report_id = result["service_report_id"]

        db = self.app_module.db()
        report = db.execute(
            "select * from service_reports where id = ?", (report_id,)
        ).fetchone()
        self.assertIsNotNone(report)
        self.assertEqual(int(report["service_order_id"]), 400)
        self.assertEqual(str(report["report_date"]), "2026-08-27")
        self.assertEqual(int(report["ai_generated"]), 1)
        self.assertEqual(int(report["ai_draft_id"]), 511)
        self.assertEqual(float(report["total_service_hours"]), 0.0)
        self.assertEqual(float(report["driving_miles"]), 0.0)

        workers = db.execute(
            "select count(*) as c from service_report_workers where report_id = ?",
            (report_id,),
        ).fetchone()
        self.assertEqual(int(workers["c"]), 0)
        attachments = db.execute(
            "select count(*) as c from service_report_attachments where report_id = ?",
            (report_id,),
        ).fetchone()
        self.assertEqual(int(attachments["c"]), 0)

        draft_after = self._get_draft_row(511)
        self.assertEqual(draft_after["status"], "saved")
        self.assertEqual(int(draft_after["saved_report_id"]), report_id)

        commit = db.execute(
            "select * from ai_daily_report_formal_commits where draft_id = 511"
        ).fetchone()
        self.assertIsNotNone(commit)
        self.assertEqual(commit["status"], "committed")
        self.assertEqual(str(commit["manifest_id"]), "force_incomplete")
        self.assertEqual(int(commit["service_report_id"]), report_id)
        snapshot = json.loads(commit["manifest_snapshot"] or "{}")
        self.assertTrue(snapshot.get("incomplete"))

        audit = db.execute(
            "select summary from audit_logs where entity_type = 'service_report' and entity_id = ?",
            (report_id,),
        ).fetchone()
        self.assertIsNotNone(audit)
        self.assertIn("不完整", audit["summary"])

    def test_03_run_incomplete_exactly_once(self):
        self._insert_draft(512, _make_empty_draft_data(), status="confirmed")
        draft_row = self._get_draft_row(512)
        svc = self._make_service()
        first = svc.run_incomplete(draft_row, None)
        self.assertEqual(first["status"], "created")

        # Fresh row simulates a retry after the first response was lost.
        # After the first pass the draft is 'saved' with saved_report_id, so
        # the exactly-once pre-check must win over the state gate.
        second = svc.run_incomplete(self._get_draft_row(512), None)
        self.assertEqual(second["status"], "already_committed")
        self.assertEqual(
            int(second["service_report_id"]), int(first["service_report_id"])
        )
        count = self.app_module.db().execute(
            "select count(*) as c from service_reports where ai_draft_id = 512"
        ).fetchone()
        self.assertEqual(int(count["c"]), 1)

    def test_04_run_incomplete_rejects_non_confirmed(self):
        self._insert_draft(513, _make_empty_draft_data(), status="draft")
        draft_row = self._get_draft_row(513)
        with self.assertRaises(self.app_module.FormalSaveStateError):
            self._make_service().run_incomplete(draft_row, None)

    def test_05_run_incomplete_version_conflict(self):
        self._insert_draft(514, _make_empty_draft_data(), status="confirmed", draft_version=3)
        draft_row = self._get_draft_row(514)
        with self.assertRaises(self.app_module.FormalSaveStaleError):
            self._make_service().run_incomplete(draft_row, 99)

    def test_06_build_formal_mapping_empty_workers(self):
        self._insert_draft(515, _make_empty_draft_data(), status="confirmed")
        draft_row = self._get_draft_row(515)
        svc = self._make_service()
        # Default: still raises (normal formal-save keeps its guarantee).
        with self.assertRaises(self.app_module.FormalSaveIntegrityError):
            svc._build_formal_mapping(draft_row)
        # Incomplete path: empty mapping is allowed.
        mapping = svc._build_formal_mapping(draft_row, allow_empty_workers=True)
        self.assertEqual(mapping["workers"], [])
        self.assertEqual(mapping["report_date"], "2026-08-27")
        self.assertEqual(mapping["order_number"], self.ORDER_NUMBER)


class IncompletePassEndpointTests(IncompletePassTestBase):
    """Endpoint-level force_incomplete tests."""

    def test_10_formal_save_without_force_still_422(self):
        """Normal path unchanged: empty draft formal-save -> 422."""
        self._insert_draft(520, _make_empty_draft_data(), status="confirmed")
        self._login(400)
        resp = self.client.post(
            "/api/ai/daily-report/draft/520/formal-save",
            headers={"X-CSRF-Token": self._get_csrf()},
            json={"draft_version": 1},
        )
        self.assertEqual(resp.status_code, 422)
        data = resp.get_json(silent=True) or {}
        self.assertEqual(data.get("code"), "validation_cannot_proceed")

    def test_11_formal_save_force_incomplete_201(self):
        self._insert_draft(521, _make_empty_draft_data(), status="confirmed")
        self._login(400)
        resp = self.client.post(
            "/api/ai/daily-report/draft/521/formal-save",
            headers={"X-CSRF-Token": self._get_csrf()},
            json={"draft_version": 1, "force_incomplete": True},
        )
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        data = resp.get_json(silent=True) or {}
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("incomplete"))
        self.assertTrue(data.get("report_url"))
        report_id = int(data["service_report_id"])
        report = self.app_module.db().execute(
            "select * from service_reports where id = ?", (report_id,)
        ).fetchone()
        self.assertIsNotNone(report)
        self.assertEqual(int(report["ai_draft_id"]), 521)

    def test_12_confirm_force_incomplete_one_shot(self):
        """confirm with force_incomplete=true: draft -> confirmed -> formal."""
        self._insert_draft(522, _make_empty_draft_data(), status="draft")
        self._login(401)
        resp = self.client.post(
            "/api/ai/daily-report/draft/522/confirm",
            json={"force_incomplete": True},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json(silent=True) or {}
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("status"), "saved")
        auto = data.get("auto_formal_save") or {}
        self.assertTrue(auto.get("formal_saved"))
        self.assertTrue(auto.get("incomplete"))
        self.assertIsNone(auto.get("blocked_code"))
        self.assertTrue(auto.get("report_url"))
        draft_after = self._get_draft_row(522)
        self.assertEqual(draft_after["status"], "saved")
        self.assertEqual(
            int(draft_after["saved_report_id"]), int(auto["service_report_id"])
        )

    def test_13_confirm_without_force_stays_confirmed(self):
        """confirm without the flag: blocked auto-chain keeps 'confirmed'."""
        self._insert_draft(523, _make_empty_draft_data(), status="draft")
        self._login(401)
        resp = self.client.post(
            "/api/ai/daily-report/draft/523/confirm",
            json={},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json(silent=True) or {}
        self.assertEqual(data.get("status"), "confirmed")
        auto = data.get("auto_formal_save") or {}
        self.assertFalse(auto.get("formal_saved"))
        self.assertEqual(auto.get("blocked_code"), "validation_cannot_proceed")
        draft_after = self._get_draft_row(523)
        self.assertEqual(draft_after["status"], "confirmed")

    def test_14_confirm_force_on_confirmed_retry(self):
        """Frontend retry path: blocked confirm first, then force pass."""
        self._insert_draft(524, _make_empty_draft_data(), status="draft")
        self._login(401)
        first = self.client.post(
            "/api/ai/daily-report/draft/524/confirm", json={}
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual((first.get_json() or {}).get("status"), "confirmed")

        # Retry via formal-save force (the UI's "不完整也传到工单" button).
        self._login(400)
        retry = self.client.post(
            "/api/ai/daily-report/draft/524/formal-save",
            headers={"X-CSRF-Token": self._get_csrf()},
            json={"force_incomplete": True},
        )
        self.assertEqual(retry.status_code, 201, retry.get_data(as_text=True))
        data = retry.get_json(silent=True) or {}
        self.assertTrue(data.get("incomplete"))
        draft_after = self._get_draft_row(524)
        self.assertEqual(draft_after["status"], "saved")
        self.assertEqual(
            int(draft_after["saved_report_id"]), int(data["service_report_id"])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
