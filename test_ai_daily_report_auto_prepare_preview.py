"""AI Daily Report - Auto-Prepare Preview Shape Regression Tests (v0.1.254)

User-reported bug (2026-09-19): on the draft detail page the three action
buttons (生成日报 / 取消 / 删除) "flashed and disappeared".

Root cause: POST /draft/<id>/auto-prepare returned the flat
DailyReportService.build_preview() payload which carries NO `status` and NO
`draft_version` (and a completely different field shape). The review page JS
assigns that response wholesale to `currentPreview` and re-renders, so
renderActions() saw status === undefined and cleared the action buttons.
It also silently broke currentDraftVersion for subsequent mutations.

Fix: auto-prepare now returns the same aggregated Review-Center preview as
GET /draft/<id>/preview (shared helper _build_review_center_preview).

Covers:
- auto-prepare preview carries status / draft_version / aggregated sections.
- top-level draft_version matches preview.draft_version.
- shape parity with the GET preview endpoint.
- status is preserved per draft state (draft / confirmed / saved) so the
  action buttons re-render correctly (saved -> empty is intended).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-autoprepare")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

AGGREGATED_KEYS = {
    "draft_id", "status", "draft_version", "basic_info", "workers",
    "timeline", "safety_photo", "service_photos", "work_items",
    "verification_checklist", "validation_result", "ai_metadata", "audit",
    "attachment_preparation", "formal_save",
}


def _make_draft_data(**overrides):
    """Mirror the user-reported card: SO2609007-like, 0 photos, no_photos待确认."""
    data = {
        "service_order_id": 420,
        "report_date": "2026-09-17",
        "workers": [],
        "work_items": [],
        "service_description": "",
        "photo_candidates": [],
        "verification_required": True,
        "verification_fields": ["no_photos"],
        "ai_metadata": {"model": "deepseek-v4-flash", "created_by": "ai"},
    }
    data.update(overrides)
    return data


class AutoPreparePreviewTests(unittest.TestCase):
    """Temp DATA_DIR + minimal users/orders/drafts; endpoint-level tests."""

    ORDER_NUMBER = "SO-FLICKER-001"

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="auto_prepare_preview_test_")
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
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (420, "admin-flicker@test.com", "Admin Flicker", "x", "admin", "2026-01-01T00:00:00Z"),
            )
            db.execute(
                "insert or ignore into service_orders (id, order_number, client_name, site_address, client_order_number, status, created_by, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (420, cls.ORDER_NUMBER, "Client Flicker", "400 Agave Dr, Hobbs, NM 88240", "DEP_Agave_US", "open", 420, "2026-09-17T00:00:00Z"),
            )
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.app_module.db = None

    @classmethod
    def _insert_draft(cls, draft_id, status="draft", draft_version=1):
        db = cls.app_module.db()
        db.execute(
            "insert or replace into ai_daily_report_drafts (id, service_order_id, report_date, status, draft_version, created_by, draft_data, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (draft_id, 420, "2026-09-17", status, draft_version, 420, json.dumps(_make_draft_data()), "2026-09-17T00:00:00Z", "2026-09-19T00:00:00Z"),
        )
        db.commit()

    def setUp(self):
        self._ctx = self.app.app_context()
        self._ctx.push()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 420

    def tearDown(self):
        self._ctx.pop()

    def _get_csrf(self):
        resp = self.client.get("/api/ai/daily-report/csrf")
        return (resp.get_json(silent=True) or {}).get("csrf_token")

    def _auto_prepare(self, draft_id):
        return self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/auto-prepare",
            json={},
            headers={"X-CSRF-Token": self._get_csrf()},
        )

    def test_01_auto_prepare_returns_aggregated_preview(self):
        """The core regression: preview must carry status + draft_version."""
        self._insert_draft(620, status="draft")
        resp = self._auto_prepare(620)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data["ok"])

        preview = data["preview"]
        self.assertEqual(preview["status"], "draft")
        self.assertIsInstance(preview["draft_version"], int)
        self.assertEqual(data["draft_version"], preview["draft_version"])
        missing = AGGREGATED_KEYS - set(preview.keys())
        self.assertFalse(missing, f"aggregated preview missing keys: {missing}")

    def test_02_shape_matches_get_preview_endpoint(self):
        """auto-prepare preview and GET /preview must be the same shape."""
        self._insert_draft(621, status="draft")
        get_resp = self.client.get("/api/ai/daily-report/draft/621/preview")
        self.assertEqual(get_resp.status_code, 200)
        get_preview = get_resp.get_json()["preview"]

        post_resp = self._auto_prepare(621)
        self.assertEqual(post_resp.status_code, 200)
        post_preview = post_resp.get_json()["preview"]

        self.assertEqual(
            set(get_preview.keys()), set(post_preview.keys()),
            "auto-prepare preview shape diverges from GET /preview",
        )
        self.assertEqual(post_preview["status"], get_preview["status"])
        # NOTE: draft_version intentionally NOT compared across the two calls:
        # auto-prepare legitimately bumps it (confirm-timeline saves the draft).
        # Internal consistency (top-level == preview) is asserted in test_01.

    def test_03_confirmed_draft_keeps_status_for_force_button(self):
        """confirmed + blocking validation drives the 强制传递 button render."""
        self._insert_draft(622, status="confirmed")
        resp = self._auto_prepare(622)
        self.assertEqual(resp.status_code, 200)
        preview = resp.get_json()["preview"]
        self.assertEqual(preview["status"], "confirmed")
        self.assertFalse(preview["validation_result"]["can_proceed"])

    def test_04_saved_draft_reports_saved(self):
        """saved -> renderActions renders no buttons; that is intended."""
        self._insert_draft(623, status="saved")
        resp = self._auto_prepare(623)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["preview"]["status"], "saved")


if __name__ == "__main__":
    unittest.main()
