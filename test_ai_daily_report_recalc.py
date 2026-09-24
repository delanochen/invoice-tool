"""AI Daily Report "recalculate-mileage" API tests (v0.1.197).

Covers the new server-side recalculate-mileage endpoint that reuses the
Phase 2 / 3A / 3B logic (destination + travel field verification + Google
Routes mileage + Static Maps evidence). Also verifies the new detail-page
action buttons exist in the JS bundle.

Scope:
- authorization / CSRF / optimistic locking
- missing travel fields -> 422 (not 403)
- Google Routes key missing -> 422
- happy path: mileage calculated, evidence generated, persisted, preview shows it
- evidence idempotency (repeat call reuses existing evidence)
- no self-drive workers -> ok, no-op
"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-recalc")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin-rc-root@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from werkzeug.security import generate_password_hash

_1PX_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class RecalculateMileageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="recalc_test_")
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
            (500, "admin-rc@test.com", "Admin", "admin"),
            (501, "emp-rc@test.com", "Emp", "employee"),
            (502, "fin-rc@test.com", "Fin", "finance"),
            (504, "ext-rc@test.com", "ExtMgr", "external_manager"),
        ]:
            db.execute(
                "insert or ignore into users (id, email, name, password_hash, role, is_active, created_at) values (?, ?, ?, ?, ?, 1, ?)",
                (uid, email, name, ph, role, "2026-01-01T00:00:00Z"),
            )
        db.execute(
            "insert or ignore into service_orders "
            "(id, order_number, client_id, manufacturer_id, client_name, site_address, client_order_number, status, created_by, created_at) "
            "values (700, 'SO-RC-001', NULL, NULL, 'RC Client', '123 Test St', 'CO-001', 'open', 500, '2026-01-01T00:00:00Z')"
        )
        db.commit()

    # ─── helpers ─────────────────────────────────────────────────────────
    def _login(self, email="admin-rc@test.com", password="pass123"):
        resp = self.client.post("/login", data={"email": email, "password": password})
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as s:
            s["ai_daily_report_csrf"] = "test-csrf-token"
        return resp

    def _csrf_headers(self):
        return {"Content-Type": "application/json", "X-CSRF-Token": "test-csrf-token"}

    def _make_draft(self, workers=None):
        resp = self.client.post(
            "/api/ai/daily-report/draft",
            json={"service_order_id": 700, "report_date": "2026-09-15"},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        draft_id = resp.get_json()["draft_id"]
        if workers is not None:
            with self.app.app_context():
                row = self.app_module.db().execute(
                    "select draft_data from ai_daily_report_drafts where id = ?", (draft_id,)
                ).fetchone()
                d = json.loads(row["draft_data"])
                d["workers"] = workers
                self.app_module.db().execute(
                    "update ai_daily_report_drafts set draft_data = ?, draft_version = draft_version + 1 where id = ?",
                    (json.dumps(d, ensure_ascii=False), draft_id),
                )
                self.app_module.db().commit()
        with self.app.app_context():
            row = self.app_module.db().execute(
                "select draft_version from ai_daily_report_drafts where id = ?", (draft_id,)
            ).fetchone()
        return draft_id, row["draft_version"]

    @staticmethod
    def _self_drive_worker():
        return {
            "user_id": 501,
            "name": "Emp",
            "transportation": "self_drive",
            "origin": "123 Origin Ave",
            "origin_source": "user_input",
            "origin_confirmed": True,
            "trip_type": "round_trip",
        }

    def _patch_google(self):
        route_result = MagicMock(
            success=True,
            distance_meters=80467.2,  # ~50 miles
            duration_seconds=3600,
            encoded_polyline="abcd",
            origin_normalized="Origin A",
            destination_normalized="123 Test St",
            provider="google_routes",
            query_time="2026-09-15T00:00:00Z",
            status="success",
            error=None,
        )
        static_result = MagicMock(
            success=True,
            image_bytes=_1PX_PNG,
            content_type="image/png",
            status="success",
            error=None,
        )
        p1 = patch("app.get_google_routes_api_key", return_value="fake-routes-key")
        p2 = patch("app.get_google_static_maps_api_key", return_value="fake-static-key")
        p3 = patch("app.GoogleRoutesService")
        p4 = patch("ai_daily_report.GoogleStaticMapsService")
        p1.start(); p2.start()
        mock_routes_cls = p3.start()
        mock_static_cls = p4.start()
        mock_routes_cls.return_value.get_driving_route.return_value = route_result
        mock_static_cls.return_value.get_route_map.return_value = static_result
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.addCleanup(p3.stop)
        self.addCleanup(p4.stop)
        return mock_routes_cls, mock_static_cls

    # ─── auth / CSRF ─────────────────────────────────────────────────────
    def test_unauthenticated_redirects(self):
        fresh = self.app.test_client()
        resp = fresh.post("/api/ai/daily-report/draft/1/recalculate-mileage", json={})
        self.assertIn(resp.status_code, (302, 401))

    def test_external_manager_forbidden(self):
        self._login("admin-rc@test.com")
        draft_id, _ = self._make_draft([self._self_drive_worker()])
        self._login("ext-rc@test.com")
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
            json={},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 403)

    def test_missing_csrf_403(self):
        self._login()
        draft_id, _ = self._make_draft([self._self_drive_worker()])
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
            json={},
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_draft_not_found_404(self):
        self._login()
        resp = self.client.post(
            "/api/ai/daily-report/draft/99999/recalculate-mileage",
            json={},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 404)

    # ─── travel field gate ───────────────────────────────────────────────
    def test_missing_origin_422_not_403(self):
        self._login()
        worker = self._self_drive_worker()
        worker["origin"] = None
        worker["origin_confirmed"] = False
        draft_id, v = self._make_draft([worker])
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
            json={"draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 422)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertTrue(any("origin" in m for m in data.get("missing", [])))

    def test_no_routes_key_422(self):
        self._login()
        draft_id, v = self._make_draft([self._self_drive_worker()])
        with patch("app.get_google_routes_api_key", return_value=""):
            resp = self.client.post(
                f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
                json={"draft_version": v},
                headers=self._csrf_headers(),
            )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("API key", resp.get_json()["error"])

    # ─── happy path ──────────────────────────────────────────────────────
    def test_happy_path_calculates_mileage_and_evidence(self):
        self._login()
        draft_id, v = self._make_draft([self._self_drive_worker()])
        self._patch_google()
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
            json={"draft_version": v},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["workers"][0]["route_status"], "success")
        self.assertGreater(data["workers"][0]["one_way_miles"], 0)
        self.assertGreater(data["workers"][0]["reported_miles"], 0)
        self.assertTrue(data["workers"][0]["mileage_evidence_id"])

        # evidence persisted in draft + preview shows it
        resp = self.client.get(f"/api/ai/daily-report/draft/{draft_id}/preview")
        preview = resp.get_json()["preview"]
        worker = preview["workers"][0]
        self.assertEqual(worker["mileage_evidence_id"], data["workers"][0]["mileage_evidence_id"])
        self.assertEqual(worker["route_status"], "success")
        with self.app.app_context():
            row = self.app_module.db().execute(
                "select draft_data from ai_daily_report_drafts where id = ?", (draft_id,)
            ).fetchone()
            d = json.loads(row["draft_data"])
            self.assertEqual(len(d.get("evidence_records", [])), 1)
            self.assertEqual(d["evidence_records"][0]["evidence_status"], "ready")
            self.assertEqual(d["workers"][0]["mileage_evidence_id"], d["evidence_records"][0]["evidence_id"])

        # evidence file actually written + preview route serves it
        with self.app.app_context():
            row = self.app_module.db().execute(
                "select draft_data from ai_daily_report_drafts where id = ?", (draft_id,)
            ).fetchone()
            ev = json.loads(row["draft_data"])["evidence_records"][0]
        ev_path = Path(self.temp_dir) / "ai-daily-report-drafts" / str(draft_id) / "mileage" / ev["file_relative_path"]
        self.assertTrue(ev_path.is_file(), f"evidence file missing: {ev_path}")
        preview_resp = self.client.get(
            f"/api/ai/daily-report/draft/{draft_id}/evidence/{ev['evidence_id']}"
        )
        self.assertEqual(preview_resp.status_code, 200)

    def test_evidence_idempotent_repeat_reuses(self):
        self._login()
        draft_id, v = self._make_draft([self._self_drive_worker()])
        self._patch_google()
        resp1 = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
            json={"draft_version": v},
            headers=self._csrf_headers(),
        )
        v1 = resp1.get_json()["draft_version"]
        ev1 = resp1.get_json()["workers"][0]["mileage_evidence_id"]
        resp2 = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
            json={"draft_version": v1},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp2.status_code, 200)
        ev2 = resp2.get_json()["workers"][0]["mileage_evidence_id"]
        self.assertEqual(ev1, ev2, "repeat recalc must reuse the same evidence")
        with self.app.app_context():
            row = self.app_module.db().execute(
                "select draft_data from ai_daily_report_drafts where id = ?", (draft_id,)
            ).fetchone()
            d = json.loads(row["draft_data"])
            self.assertEqual(len(d.get("evidence_records", [])), 1, "no duplicate evidence records")

    def test_no_self_drive_workers_noop_ok(self):
        self._login()
        worker = {"user_id": 501, "name": "Emp", "transportation": "carpool",
                  "origin": "123 Origin Ave", "origin_source": "user_input",
                  "origin_confirmed": True, "trip_type": "round_trip"}
        draft_id, v = self._make_draft([worker])
        with patch("app.get_google_routes_api_key", return_value="fake"):
            resp = self.client.post(
                f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
                json={"draft_version": v},
                headers=self._csrf_headers(),
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["workers"], [], "carpool is not self_drive -> no mileage")

    def test_stale_draft_version_409(self):
        self._login()
        draft_id, v = self._make_draft([self._self_drive_worker()])
        resp = self.client.post(
            f"/api/ai/daily-report/draft/{draft_id}/recalculate-mileage",
            json={"draft_version": v + 100},
            headers=self._csrf_headers(),
        )
        self.assertEqual(resp.status_code, 409)

    # ─── frontend buttons presence ───────────────────────────────────────
    def test_detail_js_has_action_buttons(self):
        js = io.open(
            os.path.join(PROJECT_ROOT, "static", "ai-daily-report-review.js"),
            encoding="utf-8",
            errors="replace",
        ).read()
        for needle in ["discover-photos", "confirm-photo-timeline", "classify-photos", "recalculate-mileage"]:
            self.assertIn(needle, js, f"missing {needle} wiring in review.js")


if __name__ == "__main__":
    unittest.main()
