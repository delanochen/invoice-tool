"""AI Daily Report menu visibility + persistent login session (hotfix) tests.

Covers the two production issues reported after Phase 9 deploy:
- ai_daily_report menu was never registered in MENU_PERMISSION_GROUPS, so
  has_menu_permission("ai_daily_report") was always False and the nav link
  never rendered.
- login never set session.permanent, so the session cookie was a browser
  session cookie; iOS Safari PWA discards it on close -> re-login every open.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-menu-session")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "true")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from werkzeug.security import generate_password_hash


class MenuSessionHotfixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="menu_session_test_")
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
            cls._setup_users()

    @classmethod
    def _setup_users(cls):
        db = cls.app_module.db()
        ph = generate_password_hash("pass123")
        for uid, email, name, role in [
            (400, "admin-hf@test.com", "Admin", "admin"),
            (401, "emp@test.com", "Emp", "employee"),
            (402, "fin@test.com", "Fin", "finance"),
            (406, "mgr@test.com", "Mgr", "manager"),
            (404, "extmgr@test.com", "ExtMgr", "external_manager"),
            (405, "extemp@test.com", "ExtEmp", "external_employee"),
        ]:
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (uid, email, name, ph, role, "2026-01-01T00:00:00Z"),
            )
        db.commit()

    # ─── Menu permission ────────────────────────────────────────────────
    def test_ai_daily_report_menu_registered_for_internal_roles(self):
        with self.app.app_context():
            for uid in (400, 401, 402, 406):  # admin / employee / finance / manager
                from types import SimpleNamespace
                with self.app.test_request_context("/"):
                    import flask
                    flask.g.user = {"id": uid, "role": self._role_of(uid)}
                    self.assertTrue(
                        self.app_module.has_menu_permission("ai_daily_report"),
                        "role %s should see AI daily report menu" % self._role_of(uid),
                    )

    def test_ai_daily_report_menu_hidden_for_external_roles(self):
        with self.app.app_context():
            for uid in (404, 405):  # external_manager / external_employee
                from types import SimpleNamespace
                with self.app.test_request_context("/"):
                    import flask
                    flask.g.user = {"id": uid, "role": self._role_of(uid)}
                    self.assertFalse(
                        self.app_module.has_menu_permission("ai_daily_report"),
                        "external role %s must NOT see AI daily report menu" % self._role_of(uid),
                    )

    def test_nav_link_renders_for_internal_user(self):
        # Login as admin then GET dashboard; nav must contain the review link.
        resp = self.client.post("/login", data={"email": "admin-hf@test.com", "password": "pass123"},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("AI 日报审查", body)
        self.assertIn("/ai-daily-report/drafts", body)

    def test_nav_link_hidden_for_external_user(self):
        resp = self.client.post("/login", data={"email": "extmgr@test.com", "password": "pass123"},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn("AI 日报审查", body)

    # ─── Persistent session ─────────────────────────────────────────────
    def test_login_sets_permanent_session_cookie(self):
        resp = self.client.post("/login", data={"email": "admin-hf@test.com", "password": "pass123"})
        self.assertEqual(resp.status_code, 302)
        set_cookie = resp.headers.get("Set-Cookie", "")
        self.assertIn("session=", set_cookie)
        self.assertIn("Expires=", set_cookie, "session cookie must be persistent (Expires present)")
        self.assertIn("Secure", set_cookie, "session cookie must be Secure on production-like config")
        self.assertIn("SameSite=Lax", set_cookie)

    def test_session_survives_client_restart(self):
        # Login, then simulate a fresh client (new cookie jar) that re-sends
        # the same persisted cookie value - session must still be valid.
        login = self.client.post("/login", data={"email": "admin-hf@test.com", "password": "pass123"})
        set_cookie = login.headers.get("Set-Cookie", "")
        cookie_part = set_cookie.split(";", 1)[0]  # session=<value>
        fresh = self.app.test_client()
        fresh.set_cookie("session", cookie_part.split("=", 1)[1])
        resp = fresh.get("/")
        self.assertNotIn(resp.status_code, (401, 403, 302), "persisted session must still authenticate")

    @classmethod
    def _role_of(cls, uid):
        return {400: "admin", 401: "employee", 402: "finance", 406: "manager",
                404: "external_manager", 405: "external_employee"}[uid]


if __name__ == "__main__":
    unittest.main()
