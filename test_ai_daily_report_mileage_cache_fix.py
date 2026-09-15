"""Regression tests: mileage route-cache invalidation + DeepSeek empty-response retry.

Covers two approved fixes:
1. Mileage cache invalidation (user-reported issue #1):
   - update_worker changing origin / transportation / overnight_stay must invalidate the
     cached route so MileageService re-fetches (previously stale miles were reused).
   - recalculate_mileage intent must invalidate routes (previously the hasattr guard
     always evaluated False and did nothing).
2. DeepSeek empty-content retry (user-reported "AI 返回为空"):
   - intent_service retries when DeepSeek returns HTTP 200 with empty content.
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_DIR = Path(__file__).resolve().parent


class MileageCacheFixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        ai_dir = Path(cls.temp_dir.name) / "ai_daily_report"
        shutil.copytree(REPO_DIR / "ai_daily_report", ai_dir)
        spec = importlib.util.spec_from_file_location("invoice_tool_mileage_fix_test", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")
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
            conn.execute("delete from service_report_attachments")
            conn.execute("delete from service_report_workers")
            conn.execute("delete from service_reports")
            conn.execute("delete from service_orders")
            conn.execute("delete from users")
            conn.execute("delete from clients")
            # Admin
            cur = conn.execute(
                "insert into users (name, email, password_hash, role, address, created_at) values (?, ?, ?, 'admin', 'Spring, TX', '2026-09-14T00:00:00')",
                ("Test Admin", "mileage-fix-admin@example.com", "x",),
            )
            self.admin_id = cur.lastrowid
            # Client + order
            cur = conn.execute(
                "insert into clients (name, client_number, short_name, country, created_at) values ('Test Client', 'CLI-MILEFIX', 'T', 'US', '2026-09-14T00:00:00')"
            )
            client_id = cur.lastrowid
            conn.execute(
                "insert into service_orders (order_number, client_id, client_name, site_address, client_order_number, status, created_by, created_at) values ('SO-MILEFIX-001', ?, 'Test Client', '123 Site St, Test City, TX 12345', 'ORD-MILEFIX', 'open', ?, '2026-09-14T00:00:00')",
                (client_id, self.admin_id),
            )
            conn.commit()
            self.order = conn.execute("select * from service_orders where order_number='SO-MILEFIX-001'").fetchone()

    def _make_worker(self, **kwargs):
        from ai_daily_report import WorkerTravel
        defaults = dict(
            user_id=self.admin_id, name="Test Admin",
            transportation="self_drive",
            origin="518 Anacacho Dr, Spring, TX 77386",
            origin_source="user_input", origin_confirmed=True,
            destination="123 Site St, Test City, TX 12345",
            destination_source="service_order",
            overnight_stay=False,
            route_status="success",
            route_distance_meters=160934.4,
            one_way_miles=100.0,
            reported_miles=200.0,
            route_polyline="abc",
            route_duration_seconds=7200,
        )
        defaults.update(kwargs)
        return WorkerTravel(**defaults)

    def _make_svc(self):
        from ai_daily_report import DailyReportService
        return DailyReportService(self.module.db(), lambda: "2026-09-14T12:00:00", self.admin_id, "Test Admin")

    def _make_draft(self):
        from ai_daily_report import DailyReportDraft
        draft = DailyReportDraft(service_order_id=self.order["id"], report_date="2026-09-14")
        draft.workers.append(self._make_worker())
        return draft

    # ─── Fix 1: cache invalidation ─────────────────────────────────────

    def test_origin_change_invalidates_route(self):
        """update_worker changing origin must clear cached route."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_svc()
            draft = self._make_draft()
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.admin_id, "name": "Test Admin", "origin": "New Origin, TX"}]
            draft, _ = svc.execute_action(draft, action, resolved_workers=resolved)
            w = draft.workers[0]
            self.assertEqual(w.origin, "New Origin, TX")
            self.assertNotEqual(w.route_status, "success")
            self.assertIsNone(w.route_distance_meters)
            self.assertIsNone(w.reported_miles)

    def test_transportation_change_invalidates_route(self):
        """update_worker changing transportation (self_drive -> other) must clear route."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_svc()
            draft = self._make_draft()
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.admin_id, "name": "Test Admin", "transportation": "passenger"}]
            draft, _ = svc.execute_action(draft, action, resolved_workers=resolved)
            w = draft.workers[0]
            self.assertEqual(w.transportation, "passenger")
            self.assertNotEqual(w.route_status, "success")
            self.assertIsNone(w.route_distance_meters)

    def test_worker_overnight_change_invalidates_route(self):
        """update_worker changing overnight_stay must clear route (round-trip vs one-way)."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_svc()
            draft = self._make_draft()
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.admin_id, "name": "Test Admin", "overnight_stay": True}]
            draft, _ = svc.execute_action(draft, action, resolved_workers=resolved)
            w = draft.workers[0]
            self.assertTrue(w.overnight_stay)
            self.assertNotEqual(w.route_status, "success")
            self.assertIsNone(w.reported_miles)

    def test_global_overnight_shortcut_invalidates_route(self):
        """Global overnight_stay shortcut must invalidate all workers."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_svc()
            draft = self._make_draft()
            action = AIAction(action_version=1, intent="update_worker", overnight_stay=True)
            draft, _ = svc.execute_action(draft, action, resolved_workers=[])
            for w in draft.workers:
                self.assertTrue(w.overnight_stay)
                self.assertNotEqual(w.route_status, "success")
                self.assertIsNone(w.reported_miles)

    def test_recalculate_intent_invalidates_route(self):
        """recalculate_mileage intent must invalidate all routes (hasattr guard removed)."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_svc()
            draft = self._make_draft()
            action = AIAction(action_version=1, intent="recalculate_mileage")
            draft, msg = svc.execute_action(draft, action, resolved_workers=[])
            self.assertIn("重新计算里程", msg)
            for w in draft.workers:
                self.assertNotEqual(w.route_status, "success")
                self.assertIsNone(w.route_distance_meters)
                self.assertIsNone(w.reported_miles)

    def test_recalculate_after_origin_change_recomputes(self):
        """Full loop: change origin then recalculate -> mileage is recomputed (not cached)."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction, GoogleRoutesService, MileageService
            from unittest.mock import MagicMock
            from test_ai_daily_report_phase3a import MockRouteResult
            svc = self._make_svc()
            draft = self._make_draft()

            # First calculation succeeds and caches.
            routes = GoogleRoutesService("fake-key")
            routes.get_driving_route = MagicMock(return_value=MockRouteResult(distance_meters=160934.4))
            MileageService(routes).calculate_for_worker(draft.workers[0])
            self.assertEqual(draft.workers[0].route_status, "success")
            self.assertEqual(draft.workers[0].reported_miles, 200.0)

            # Change origin -> route must be invalidated.
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.admin_id, "name": "Test Admin", "origin": "New Origin, TX"}]
            draft, _ = svc.execute_action(draft, action, resolved_workers=resolved)
            w = draft.workers[0]
            self.assertNotEqual(w.route_status, "success")

            # Recalculate with a different distance -> new value must be applied.
            routes.get_driving_route = MagicMock(return_value=MockRouteResult(distance_meters=80467.2))
            MileageService(routes).calculate_for_worker(w)
            self.assertEqual(w.route_status, "success")
            self.assertEqual(w.one_way_miles, 50.0)
            self.assertEqual(w.reported_miles, 100.0)

    # ─── Fix 2: DeepSeek empty-response retry ──────────────────────────

    def test_empty_response_retried_then_succeeds(self):
        """Empty content is retried; a later non-empty response succeeds."""
        from ai_daily_report.intent_service import AIIntentService
        svc = AIIntentService({"enabled": True, "api_key": "k", "model": "test-model"})
        calls = {"n": 0}
        valid_json = json.dumps({"action_version": 1, "intent": "create_daily_report", "date": None, "workers": [], "work_items": [], "overnight_stay": None, "arrival_time": None, "departure_time": None, "waiting_hours": None, "waiting_reason": None, "photo_hash": None, "clarification_required": False, "missing_fields": [], "clarification_question": None})
        with patch.object(svc, "_call_deepseek_json", side_effect=lambda messages: (calls.update(n=calls["n"] + 1), "" if calls["n"] < 2 else valid_json)[1]):
            result = svc.parse_intent(
                user_message="创建日报", current_business_date="2026-09-14",
                current_timezone="America/Chicago", service_order_id=1,
                service_order_number="SO-1", site_address="123 St",
                current_user_name="Test",
            )
        self.assertTrue(result.ok)
        self.assertEqual(calls["n"], 2)

    def test_persistent_empty_response_fails_with_empty_code(self):
        """All retries empty -> still fails with empty_response (not api_error)."""
        from ai_daily_report.intent_service import AIIntentService
        svc = AIIntentService({"enabled": True, "api_key": "k", "model": "test-model"})
        with patch.object(svc, "_call_deepseek_json", return_value=""):
            result = svc.parse_intent(
                user_message="创建日报", current_business_date="2026-09-14",
                current_timezone="America/Chicago", service_order_id=1,
                service_order_number="SO-1", site_address="123 St",
                current_user_name="Test",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "empty_response")


import json  # noqa: E402  (used in intent retry tests)


if __name__ == "__main__":
    unittest.main()
