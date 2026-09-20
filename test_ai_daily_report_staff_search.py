"""AI Daily Report - Staff Search & Add-Worker Role Alignment Tests (v0.1.255)

User report (2026-09-19): searching "Antonio" in the draft detail page's
add-worker dialog returned "未找到匹配的员工". The staff search endpoint and
the add-worker endpoint both hard-coded role IN ('admin','manager','employee'),
while the AI parsing path (EmployeeResolutionService.WORKER_ELIGIBLE_ROLES)
already includes finance / external_manager / external_employee since the
2026-09-16 product decision ("a daily report often records work performed by
external staff"). External staff like Antonio were therefore invisible to the
manual add-worker flow.

Fix (v0.1.255):
- Both endpoints now align with WORKER_ELIGIBLE_ROLES.
- Staff matching folds case + diacritics (Antonio matches António).
- Empty results carry a hint distinguishing "no account" vs "inactive/ineligible".

Covers search matching, hint logic, add-worker acceptance/rejection, limit.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-staff-search")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


class StaffSearchTests(unittest.TestCase):
    """Temp DATA_DIR + seeded users/orders/draft; endpoint-level tests."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="staff_search_test_")
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
            app_module.init_db()
            db = app_module.db()
            users = [
                (500, "admin-search@test.com", "Admin Search", "admin", 1),
                # The user-reported scenario: external staff named Antonio.
                (501, "antonio.ext@test.com", "Antonio", "external_manager", 1),
                (502, "antonio2.ext@test.com", "Antonio Mendez", "external_employee", 1),
                (503, "bea.fin@test.com", "Bea", "finance", 1),
                # Diacritics variant: "Antonio" must still find "António".
                (504, "antonio.acc@test.com", "António Silva", "employee", 1),
                # Inactive Antonio: excluded from results, hinted instead.
                (505, "antonio.old@test.com", "Antonio Retired", "employee", 0),
            ]
            for uid, email, name, role, active in users:
                db.execute(
                    "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, 'x', ?, ?, ?)",
                    (uid, email, name, role, active, "2026-01-01T00:00:00Z"),
                )
            db.execute(
                "insert or ignore into service_orders (id, order_number, client_name, site_address, client_order_number, status, created_by, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (500, "SO-STAFF-001", "Client Search", "1 Site Ave", "CLIENT-STAFF-001", "open", 500, "2026-09-19T00:00:00Z"),
            )
            db.commit()

    @classmethod
    def tearDownClass(cls):
        # The shared database factory must remain callable for subsequent tests.
        pass

    @classmethod
    def _insert_draft(cls, draft_id, status="draft", draft_version=1):
        db = cls.app_module.db()
        draft_data = {
            "service_order_id": 500,
            "report_date": "2026-09-19",
            "workers": [],
            "work_items": [],
            "verification_required": False,
            "verification_fields": [],
        }
        db.execute(
            "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (draft_id, 500, "2026-09-19", status, draft_version, 500, json.dumps(draft_data), "2026-09-19T00:00:00Z", "2026-09-19T00:00:00Z"),
        )
        db.commit()

    def setUp(self):
        self._ctx = self.app.app_context()
        self._ctx.push()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 500  # admin

    def tearDown(self):
        self._ctx.pop()

    def _search(self, q):
        resp = self.client.get(
            "/api/ai/daily-report/staff", query_string={"q": q}
        )
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()

    def _get_csrf(self):
        resp = self.client.get("/api/ai/daily-report/csrf")
        return (resp.get_json(silent=True) or {}).get("csrf_token")

    def _add_worker(self, draft_id, user_id):
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": user_id, "draft_version": 1},
            headers={"X-CSRF-Token": self._get_csrf()},
        )

    def test_01_external_manager_searchable(self):
        """THE reported bug: external staff named Antonio must be found."""
        data = self._search("Antonio")
        ids = {s["id"] for s in data["staff"]}
        self.assertIn(501, ids, "external_manager Antonio not found")
        self.assertIn(502, ids, "external_employee Antonio Mendez not found")
        roles = {s["id"]: s["role"] for s in data["staff"]}
        self.assertEqual(roles[501], "external_manager")

    def test_02_diacritics_and_case_folded(self):
        """'Antonio' finds stored 'António'; case-insensitive both ways."""
        data = self._search("Antonio")
        ids = {s["id"] for s in data["staff"]}
        self.assertIn(504, ids, "stored 'António Silva' must match 'Antonio'")
        upper = self._search("ANTONIO")
        self.assertEqual({s["id"] for s in upper["staff"]}, ids)

    def test_03_finance_searchable(self):
        data = self._search("Bea")
        self.assertEqual([s["id"] for s in data["staff"]], [503])
        self.assertEqual(data["staff"][0]["role"], "finance")

    def test_04_email_match(self):
        data = self._search("antonio.acc@")
        self.assertEqual([s["id"] for s in data["staff"]], [504])

    def test_05_inactive_excluded_with_hint(self):
        data = self._search("Retired")
        self.assertEqual(data["staff"], [])
        self.assertIn("停用", data.get("hint") or "")

    def test_06_no_account_hint(self):
        data = self._search("不存在的名字xyz")
        self.assertEqual(data["staff"], [])
        self.assertIn("账号", data.get("hint") or "")

    def test_07_empty_query_returns_empty(self):
        data = self._search("")
        self.assertEqual(data["staff"], [])
        self.assertIsNone(data.get("hint"))

    def test_08_add_worker_accepts_external(self):
        """v0.1.255: add-worker aligns with WORKER_ELIGIBLE_ROLES."""
        self._insert_draft(700)
        resp = self._add_worker(700, 501)  # external_manager Antonio
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertTrue(resp.get_json()["ok"])
        db = self.app_module.db()
        stored = json.loads(
            db.execute(
                "select draft_data from ai_daily_report_drafts where id = 700"
            ).fetchone()["draft_data"]
        )
        self.assertEqual(len(stored["workers"]), 1)
        self.assertEqual(stored["workers"][0]["user_id"], 501)

    def test_09_add_worker_rejects_inactive(self):
        self._insert_draft(701)
        resp = self._add_worker(701, 505)  # inactive Antonio Retired
        self.assertEqual(resp.status_code, 404)

    def test_10_search_limit_20(self):
        db = self.app_module.db()
        for i in range(25):
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, 'x', 'employee', 1, ?)",
                (600 + i, f"bulk{i}@test.com", f"Bulk Person {i}", "2026-01-01T00:00:00Z"),
            )
        db.commit()
        data = self._search("Bulk Person")
        self.assertEqual(len(data["staff"]), 20)

    def test_11_external_caller_forbidden(self):
        """Only internal users may search staff (unchanged)."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = 501  # external_manager
        resp = self.client.get("/api/ai/daily-report/staff", query_string={"q": "a"})
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
