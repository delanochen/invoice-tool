"""AI Daily Report worker management tests (add-worker / remove-worker / staff search).

Covers the new "添加工作人员" feature added for the mobile flow:
- staff search API (internal only, admin/manager/employee candidates)
- add-worker mutation (CSRF, optimistic lock, duplicate guard, role guard)
- remove-worker mutation
- integration: added workers show up in preview, then editable via update-worker
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-workers")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin-wk-root@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from werkzeug.security import generate_password_hash


class WorkerManagementTest(unittest.TestCase):
    _date_counter = 16

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="workers_test_")
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
            (500, "admin-wk@test.com", "Admin", "admin"),
            (501, "gaoyang@test.com", "高阳", "employee"),
            (502, "antonio@test.com", "Antonio", "employee"),
            (503, "fin-wk@test.com", "Fin", "finance"),
            (504, "ext-wk@test.com", "ExtMgr", "external_manager"),
            (505, "chenyicheng@test.com", "陈亦珹", "employee"),
        ]:
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (uid, email, name, ph, role, "2026-01-01T00:00:00Z"),
            )
        db.execute(
            "insert or ignore into service_orders "
            "(id, order_number, client_id, manufacturer_id, client_name, site_address, client_order_number, status, created_by, created_at) "
            "values (700, 'SO-WK-001', NULL, NULL, 'WK Client', '456 Test St', 'CO-001', 'open', 500, '2026-01-01T00:00:00Z')"
        )
        db.commit()

    # ─── helpers ─────────────────────────────────────────────────────────
    def _login(self, email="admin-wk@test.com", password="pass123"):
        resp = self.client.post("/login", data={"email": email, "password": password})
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as s:
            s["ai_daily_report_csrf"] = "test-csrf-token"
        return resp

    def _csrf_headers(self):
        return {"Content-Type": "application/json", "X-CSRF-Token": "test-csrf-token"}

    def _make_draft(self):
        WorkerManagementTest._date_counter += 1
        resp = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 700, "report_date": f"2026-09-{WorkerManagementTest._date_counter:02d}"},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        draft_id = resp.get_json()["draft_id"]
        with self.app.app_context():
            row = self.app_module.db().execute(
                "select draft_version from ai_daily_report_drafts where id = ?", (draft_id,)
            ).fetchone()
        return draft_id, row["draft_version"]

    # ─── staff search ────────────────────────────────────────────────────
    def test_staff_search_unauthenticated_redirects(self):
        fresh = self.app.test_client()
        resp = fresh.get("/api/ai/daily-report/staff?q=gao")
        self.assertIn(resp.status_code, (302, 401))

    def test_staff_search_external_forbidden(self):
        self._login("ext-wk@test.com")
        resp = self.client.get("/api/ai/daily-report/staff?q=gao")
        self.assertEqual(resp.status_code, 403)

    def test_staff_search_by_name_and_email(self):
        self._login()
        resp = self.client.get("/api/ai/daily-report/staff?q=高阳")
        self.assertEqual(resp.status_code, 200)
        staff = resp.get_json()["staff"]
        self.assertTrue(any(p["id"] == 501 for p in staff))
        resp = self.client.get("/api/ai/daily-report/staff?q=antonio@test.com")
        self.assertEqual(resp.status_code, 200)
        staff = resp.get_json()["staff"]
        self.assertTrue(any(p["id"] == 502 for p in staff))

    def test_staff_search_excludes_finance_and_external(self):
        self._login()
        resp = self.client.get("/api/ai/daily-report/staff?q=fin-wk")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["staff"])
        resp = self.client.get("/api/ai/daily-report/staff?q=ext-wk")
        self.assertFalse(resp.get_json()["staff"])

    def test_staff_search_empty_q_returns_list(self):
        self._login()
        resp = self.client.get("/api/ai/daily-report/staff?q=")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json()["staff"], list)

    # ─── add-worker ──────────────────────────────────────────────────────
    def test_add_worker_happy_path(self):
        self._login()
        draft_id, v = self._make_draft()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 501, "draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertTrue(resp.get_json()["ok"])
        # preview shows worker
        preview = self.client.get(f"/api/ai/daily-report/draft/{draft_id}/preview").get_json()["preview"]
        self.assertEqual(len(preview["workers"]), 1)
        self.assertEqual(preview["workers"][0]["user_id"], 501)
        self.assertEqual(preview["workers"][0]["name"], "高阳")
        self.assertEqual(preview["workers"][0]["transportation"], "self_drive")

    def test_add_worker_then_update_worker_editable(self):
        self._login()
        draft_id, v = self._make_draft()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 501, "draft_version": v},
            headers=self._csrf_headers(),
        )
        v = resp.get_json()["draft_version"]
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/update-worker",
            json={"user_id": 501, "origin": "123 Origin Ave", "draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        preview = self.client.get(f"/api/ai/daily-report/draft/{draft_id}/preview").get_json()["preview"]
        self.assertEqual(preview["workers"][0]["origin"], "123 Origin Ave")
        self.assertEqual(preview["workers"][0]["origin_confirmed"], True)

    def test_add_worker_duplicate_400(self):
        self._login()
        draft_id, v = self._make_draft()
        self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 501, "draft_version": v},
            headers=self._csrf_headers(),
        )
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 501, "draft_version": 999},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("已在 Draft 中", resp.get_json()["error"])

    def test_add_worker_finance_role_rejected(self):
        self._login()
        draft_id, v = self._make_draft()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 503, "draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_add_worker_unknown_user_404(self):
        self._login()
        draft_id, v = self._make_draft()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 99999, "draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 404)

    def test_add_worker_missing_csrf_403(self):
        self._login()
        draft_id, v = self._make_draft()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 501, "draft_version": v},
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_add_worker_external_forbidden(self):
        self._login("admin-wk@test.com")
        draft_id, v = self._make_draft()
        self._login("ext-wk@test.com")
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 501, "draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 403)

    def test_add_worker_stale_409(self):
        self._login()
        draft_id, v = self._make_draft()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 501, "draft_version": v + 100},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 409)

    # ─── remove-worker ───────────────────────────────────────────────────
    def test_remove_worker_happy_path(self):
        self._login()
        draft_id, v = self._make_draft()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/add-worker",
            json={"user_id": 501, "draft_version": v},
            headers=self._csrf_headers(),
        )
        v = resp.get_json()["draft_version"]
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/remove-worker",
            json={"user_id": 501, "draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        preview = self.client.get(f"/api/ai/daily-report/draft/{draft_id}/preview").get_json()["preview"]
        self.assertEqual(preview["workers"], [])

    def test_remove_worker_not_in_draft_404(self):
        self._login()
        draft_id, v = self._make_draft()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/remove-worker",
            json={"user_id": 501, "draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 404)

    # ─── frontend wiring ─────────────────────────────────────────────────
    def test_detail_js_has_add_worker_wiring(self):
        js = open(os.path.join(PROJECT_ROOT, "static", "ai-daily-report-review.js"), encoding="utf-8", errors="replace").read()
        for needle in ["addWorkerBtn", "staffSearch", "add-worker", "remove-worker", "data-remove-worker"]:
            self.assertIn(needle, js, f"missing {needle} in review.js")

    def test_template_has_add_worker_dialog(self):
        html = open(os.path.join(PROJECT_ROOT, "templates", "ai_daily_report_draft_detail.html"), encoding="utf-8", errors="replace").read()
        for needle in ["addWorkerBtn", "addWorkerDialog", "staffSearchInput", "staffSearchBtn", "staffSearchResults"]:
            self.assertIn(needle, html, f"missing {needle} in template")


if __name__ == "__main__":
    unittest.main()
