"""HTTP-level regression: AI daily-report mutation routes must COMMIT.

v0.1.222 found 8 routes whose db().commit() was dead code (indented inside an
already-returned branch). With SQLite in the default deferred-transaction mode
and teardown only closing the connection, every write silently rolled back:
discover-photos returned 200 but the draft stayed not_discovered, timeline
confirmation returned 200 but arrival/departure stayed empty, and safety /
service-photo changes were lost.

These tests drive the real HTTP endpoints and then read the database from a
fresh connection, so they fail if the route forgets to commit.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-commit")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin-commit-root@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from werkzeug.security import generate_password_hash
from PIL import Image


def _make_jpeg(path, capture_dt):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (640, 480), (90, 120, 200))
    exif = img.getexif()
    exif[36867] = capture_dt.strftime("%Y:%m:%d %H:%M:%S")
    img.save(path, format="JPEG", exif=exif)


class DailyReportCommitRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="commit_test_")
        os.environ["DATA_DIR"] = cls.temp_dir
        cls.shared = os.path.join(cls.temp_dir, "shared-photos")
        Path(cls.shared).mkdir(parents=True, exist_ok=True)

        import app as app_module
        cls.app_module = app_module
        app_module.DATA_DIR = cls.temp_dir
        app_module.DB_PATH = os.path.join(cls.temp_dir, "invoices.db")
        app_module.ATTACHMENTS_DIR = os.path.join(cls.temp_dir, "attachments")
        app_module.REPORT_ATTACHMENTS_DIR = os.path.join(cls.temp_dir, "service-report-attachments")
        app_module.SHARED_PHOTOS_DIR = cls.shared
        cls.app = app_module.app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            cls.app_module.init_db()
            db = cls.app_module.db()
            ph = generate_password_hash("pass123")
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) "
                "values (500, ?, 'Admin', ?, 'admin', 1, ?)",
                ("admin-commit@test.com", ph, "2026-01-01T00:00:00Z"),
            )
            db.execute(
                "insert or ignore into service_orders "
                "(id, order_number, client_id, manufacturer_id, client_name, site_address, client_order_number, status, created_by, created_at) "
                "values (700, 'SO-COMMIT', NULL, NULL, 'Commit Client', '123 Test St', 'CO-001', 'open', 500, '2026-01-01T00:00:00Z')"
            )
            db.commit()

    def setUp(self):
        self._login()
        # clean up any draft + photos from a previous test in this class
        with self.app.app_context():
            db = self.app_module.db()
            db.execute("delete from ai_daily_report_drafts")
            db.commit()
        day = Path(self.shared) / "SO-COMMIT" / "pictures" / "2026-09-15"
        if day.exists():
            import shutil
            shutil.rmtree(day.parent)

    def _login(self):
        resp = self.client.post("/login", data={"email": "admin-commit@test.com", "password": "pass123"})
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as s:
            s["ai_daily_report_csrf"] = "test-csrf-token"

    def _csrf(self):
        return {"Content-Type": "application/json", "X-CSRF-Token": "test-csrf-token"}

    def _make_draft(self):
        resp = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 700, "report_date": "2026-09-15"},
            headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["draft_id"]

    def _draft_data(self, draft_id):
        with self.app.app_context():
            row = self.app_module.db().execute(
                "select draft_data from ai_daily_report_drafts where id = ?", (draft_id,)
            ).fetchone()
            return json.loads(row["draft_data"])

    def test_discover_photos_persists_after_http(self):
        """discover-photos must commit: the DB row changes, not just the response."""
        draft_id = self._make_draft()
        _make_jpeg(Path(self.shared) / "SO-COMMIT" / "pictures" / "2026-09-15" / "a.jpg",
                   datetime(2026, 9, 15, 8, 5, 0))
        _make_jpeg(Path(self.shared) / "SO-COMMIT" / "pictures" / "2026-09-15" / "b.jpg",
                   datetime(2026, 9, 15, 11, 30, 0))

        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/discover-photos",
            json={},
            headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body["status"], "discovered")
        self.assertEqual(body["photo_count"], 2)

        data = self._draft_data(draft_id)
        self.assertEqual(data["photo_discovery_status"], "discovered")
        self.assertEqual(len(data.get("photo_candidates") or []), 2)
        self.assertEqual(data["photo_timeline_status"], "ready")

    def test_confirm_photo_timeline_persists_after_http(self):
        """confirm-photo-timeline must commit arrival/departure times."""
        draft_id = self._make_draft()
        _make_jpeg(Path(self.shared) / "SO-COMMIT" / "pictures" / "2026-09-15" / "a.jpg",
                   datetime(2026, 9, 15, 8, 5, 0))
        _make_jpeg(Path(self.shared) / "SO-COMMIT" / "pictures" / "2026-09-15" / "b.jpg",
                   datetime(2026, 9, 15, 11, 30, 0))

        discover = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/discover-photos",
            json={}, headers=self._csrf(),
        )
        self.assertEqual(discover.status_code, 200, discover.get_data(as_text=True))

        confirm = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/confirm-photo-timeline",
            json={}, headers=self._csrf(),
        )
        self.assertEqual(confirm.status_code, 200, confirm.get_data(as_text=True))
        body = confirm.get_json()
        self.assertTrue(body.get("ok"))

        data = self._draft_data(draft_id)
        self.assertIsNotNone(data.get("arrival_time"))
        self.assertIsNotNone(data.get("departure_time"))
        self.assertEqual(data.get("arrival_time_source"), "photo_timeline_confirmed")
        self.assertEqual(data.get("departure_time_source"), "photo_timeline_confirmed")


if __name__ == "__main__":
    unittest.main()
