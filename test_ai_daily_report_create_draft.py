"""AI Daily Report "New Smart Report" entry tests.

Covers the Review Center creation entry added after Phase 9:
- GET  /api/ai/daily-report/service-orders  (order picker options)
- POST /api/ai/daily-report/draft           (create/reuse empty Draft)
- Review Center page renders the "新建智能日报" button + dialog
- authorization / CSRF / validation errors
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-create-draft")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin-ct-root@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from werkzeug.security import generate_password_hash


class NewDraftEntryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="create_draft_test_")
        os.environ["DATA_DIR"] = cls.temp_dir
        os.environ["SHARED_PHOTOS_DIR"] = os.path.join(cls.temp_dir, "shared-photos")
        Path(os.environ["SHARED_PHOTOS_DIR"]).mkdir(parents=True, exist_ok=True)

        import app as app_module
        cls.app_module = app_module
        app_module.DATA_DIR = cls.temp_dir
        app_module.DB_PATH = os.path.join(cls.temp_dir, "invoices.db")
        app_module.ATTACHMENTS_DIR = os.path.join(cls.temp_dir, "attachments")
        app_module.REPORT_ATTACHMENTS_DIR = os.path.join(cls.temp_dir, "service-report-attachments")
        app_module.SHARED_PHOTOS_DIR = os.environ["SHARED_PHOTOS_DIR"]
        cls.app = app_module.app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            cls.app_module.init_db()
            cls._setup_users_and_orders()

    @classmethod
    def _setup_users_and_orders(cls):
        db = cls.app_module.db()
        ph = generate_password_hash("pass123")
        for uid, email, name, role in [
            (500, "admin-ct@test.com", "Admin", "admin"),
            (501, "emp-ct@test.com", "Emp", "employee"),
            (502, "fin-ct@test.com", "Fin", "finance"),
            (504, "ext-ct@test.com", "ExtMgr", "external_manager"),
        ]:
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (uid, email, name, ph, role, "2026-01-01T00:00:00Z"),
            )
        # one open order + one closed order
        db.execute(
            "insert or ignore into service_orders "
            "(id, order_number, client_id, manufacturer_id, client_name, site_address, client_order_number, status, created_by, created_at) "
            "values (700, 'SO-TEST-001', NULL, NULL, 'Test Client A', '123 Test St', 'CO-001', 'open', 500, '2026-01-01T00:00:00Z')"
        )
        db.execute(
            "insert or ignore into service_orders "
            "(id, order_number, client_id, manufacturer_id, client_name, site_address, client_order_number, status, created_by, created_at) "
            "values (701, 'SO-TEST-002', NULL, NULL, 'Closed Client', '999 Gone St', 'CO-002', 'closed', 500, '2026-01-01T00:00:00Z')"
        )
        db.commit()

    # ─── helpers ─────────────────────────────────────────────────────────
    def _login(self, email="admin-ct@test.com", password="pass123"):
        resp = self.client.post("/login", data={"email": email, "password": password})
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as s:
            s["ai_daily_report_csrf"] = "test-csrf-token"
        return resp

    def _csrf_headers(self):
        return {"Content-Type": "application/json", "X-CSRF-Token": "test-csrf-token"}

    # ─── order picker API ────────────────────────────────────────────────
    def test_order_options_internal_ok(self):
        self._login()
        resp = self.client.get("/api/ai/daily-report/service-orders")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        ids = [o["id"] for o in data["orders"]]
        self.assertIn(700, ids)
        self.assertNotIn(701, ids, "closed orders must not appear in the picker")

    def test_order_options_search_filter(self):
        self._login()
        resp = self.client.get("/api/ai/daily-report/service-orders?q=SO-TEST-001")
        data = resp.get_json()
        self.assertEqual([o["id"] for o in data["orders"]], [700])
        resp = self.client.get("/api/ai/daily-report/service-orders?q=Closed%20Client")
        data = resp.get_json()
        self.assertEqual(data["orders"], [])

    def test_order_options_requires_login(self):
        fresh = self.app.test_client()
        resp = fresh.get("/api/ai/daily-report/service-orders")
        self.assertIn(resp.status_code, (302, 401), "unauthenticated must not reach picker")

    def test_order_options_external_forbidden(self):
        self._login("ext-ct@test.com")
        resp = self.client.get("/api/ai/daily-report/service-orders")
        self.assertEqual(resp.status_code, 403)

    # ─── create draft API ────────────────────────────────────────────────
    def test_create_empty_draft_ok(self):
        self._login()
        resp = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 700, "report_date": "2026-09-15"},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["reused"])
        draft_id = data["draft_id"]
        with self.app.app_context():
            row = self.app_module.db().execute(
                "select id, service_order_id, report_date, status from ai_daily_report_drafts where id = ?",
                (draft_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["service_order_id"], 700)
        self.assertEqual(row["report_date"], "2026-09-15")
        self.assertEqual(row["status"], "draft")

    def test_create_draft_reuses_existing_active(self):
        self._login()
        h = self._csrf_headers()
        first = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 700, "report_date": "2026-09-16"},
            headers=h,
        ).get_json()
        second = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 700, "report_date": "2026-09-16"},
            headers=h,
        ).get_json()
        self.assertTrue(second["reused"])
        self.assertEqual(second["draft_id"], first["draft_id"])
        with self.app.app_context():
            n = self.app_module.db().execute(
                "select count(*) as c from ai_daily_report_drafts where service_order_id = 700 and report_date = '2026-09-16'"
            ).fetchone()["c"]
        self.assertEqual(n, 1, "create must reuse, never duplicate the active draft")

    def test_create_draft_missing_order_400(self):
        self._login()
        resp = self.client.post(
            "/api/ai/daily-report/draft",
            json={"report_date": "2026-09-15"},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_draft_unknown_order_404(self):
        self._login()
        resp = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 999999, "report_date": "2026-09-15"},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_create_draft_requires_csrf(self):
        self._login()
        resp = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 700, "report_date": "2026-09-15"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_draft_external_forbidden(self):
        self._login("ext-ct@test.com")
        resp = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 700, "report_date": "2026-09-15"},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 403)

    # ─── Review Center page ──────────────────────────────────────────────
    def test_review_center_renders_new_draft_entry(self):
        self._login()
        resp = self.client.get("/ai-daily-report/drafts")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("新建智能日报", body)
        self.assertIn('id="newDraftBtn"', body)
        self.assertIn('id="newDraftDialog"', body)
        self.assertIn('id="newDraftOrderSearch"', body)

    def test_review_center_entry_hidden_for_external(self):
        self._login("ext-ct@test.com")
        resp = self.client.get("/ai-daily-report/drafts", follow_redirects=True)
        # external users have no menu permission; page should not be reachable
        self.assertNotEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
