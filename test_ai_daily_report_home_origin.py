# -*- coding: utf-8 -*-
"""Tests for "从家出发" (depart from home) auto-confirmation of employee default origin.

When the natural-language draft says the worker departs from home, the
employee default address (users.address) is used as origin and marked
origin_confirmed=True:
  - ORIG-002 must NOT fire (origin is confirmed)
  - Mileage eligibility passes (origin_confirmed=True)
  - origin_source stays "employee_default" for audit trail
Real street addresses are never treated as home.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).resolve().parent


class HomeOriginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        ai_dir = Path(cls.temp_dir.name) / "ai_daily_report"
        shutil.copytree(REPO_DIR / "ai_daily_report", ai_dir)
        spec = importlib.util.spec_from_file_location("invoice_tool_ai_home_test", module_path)
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
            conn.execute("delete from service_orders where order_number='SO-HOME'")
            conn.execute("delete from users where email like 'home%'")
            client = conn.execute("select id from clients limit 1").fetchone()
            if not client:
                conn.execute(
                    "insert into clients (name, client_number, short_name, country, created_at) values ('Test', 'CLI-H', 'T', 'US', '2026-09-14T00:00:00')"
                )
                client_id = conn.execute("select last_insert_rowid()").fetchone()[0]
            else:
                client_id = client["id"]
            admin = conn.execute("select id, name from users where role='admin' limit 1").fetchone()
            if not admin:
                conn.execute(
                    "insert into users (name, email, password_hash, role, created_at) values ('Admin', 'home-admin@test.com', 'x', 'admin', '2026-09-14T00:00:00')"
                )
                admin = conn.execute("select id, name from users where role='admin' limit 1").fetchone()
            self.admin_id = admin["id"]
            self.admin_name = admin["name"]
            # Admin's default address (home)
            conn.execute(
                "update users set address = '518 Anacacho Dr, Spring, TX 77386' where id = ?",
                (self.admin_id,),
            )
            conn.execute(
                "insert into service_orders (order_number, client_id, client_name, site_address, client_order_number, status, created_by, created_at) values ('SO-HOME', ?, 'Test Client', '123 Site St, Test City, TX 12345', 'ORD-H', 'open', ?, '2026-09-14T00:00:00')",
                (client_id, self.admin_id),
            )
            conn.commit()
            self.order = conn.execute("select * from service_orders where order_number='SO-HOME'").fetchone()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin_id

    def _fake_parse_intent(self, origin_value):
        """Return a parse_intent stub that yields an update_worker action."""

        def fake(self_, **kwargs):
            from ai_daily_report.schemas import AIAction
            from ai_daily_report.action_validator import ValidationResult
            action = AIAction(
                action_version=1,
                intent="update_worker",
                workers=[{"name": self.admin_name, "transportation": "self_drive", "origin": origin_value}],
            )
            return ValidationResult(True, action=action)

        return fake

    def _chat(self, origin_value):
        with mock.patch.object(
            self.module.AIIntentService, "is_available", return_value=True
        ), mock.patch.object(
            self.module.AIIntentService, "parse_intent", self._fake_parse_intent(origin_value)
        ):
            return self.client.post(
                "/api/ai/daily-report/chat",
                json={
                    "message": "我从家出发去现场",
                    "service_order_id": self.order["id"],
                    "report_date": "2026-09-14",
                },
            )

    def _draft_workers(self, draft_id):
        from ai_daily_report.schemas import DailyReportDraft
        with self.module.app.app_context():
            conn = self.module.db()
            row = conn.execute(
                "select draft_data from ai_daily_report_drafts where id = ?", (draft_id,)
            ).fetchone()
            draft = DailyReportDraft.model_validate(json.loads(row["draft_data"]))
            return draft.workers

    # ─── is_home_origin unit tests ────────────────────────────────────────

    def test_is_home_origin_matches_home_wording(self):
        from ai_daily_report.travel_service import TravelService
        for v in ["家", "家里", "从家出发", "从家里出发", "从家", "home", "from home", "从家里出发去现场", "家走"]:
            self.assertTrue(TravelService.is_home_origin(v), f"should be home: {v!r}")

    def test_is_home_origin_rejects_real_addresses(self):
        from ai_daily_report.travel_service import TravelService
        for v in ["518 Anacacho Dr, Spring, TX 77386", "8022 Pine Wood Ct, Baytown, TX 77523", None, "", "Houston"]:
            self.assertFalse(TravelService.is_home_origin(v), f"should NOT be home: {v!r}")

    # ─── chat integration: "从家出发" ─────────────────────────────────────

    def test_from_home_uses_default_address_and_confirms(self):
        resp = self._chat("从家出发")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        self.assertTrue(body["ok"], body)
        draft_id = body["draft_id"]
        workers = self._draft_workers(draft_id)
        self.assertEqual(len(workers), 1)
        w = workers[0]
        self.assertEqual(w.origin, "518 Anacacho Dr, Spring, TX 77386")
        self.assertEqual(w.origin_source, "employee_default")
        self.assertTrue(w.origin_confirmed)

    def test_from_home_variant_zhome(self):
        resp = self._chat("家")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        draft_id = body["draft_id"]
        w = self._draft_workers(draft_id)[0]
        self.assertEqual(w.origin, "518 Anacacho Dr, Spring, TX 77386")
        self.assertTrue(w.origin_confirmed)

    def test_no_orig002_when_from_home(self):
        resp = self._chat("从家出发")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        draft_id = resp.get_json()["draft_id"]
        from ai_daily_report.validation_engine import ValidationEngine
        from ai_daily_report.schemas import DailyReportDraft
        with self.module.app.app_context():
            conn = self.module.db()
            row = conn.execute(
                "select draft_data from ai_daily_report_drafts where id = ?", (draft_id,)
            ).fetchone()
            draft = DailyReportDraft.model_validate(json.loads(row["draft_data"]))
            ctx = mock.Mock()
            ctx.service_order_exists = True
            ctx.site_address = "123 Site St, Test City, TX 12345"
            result = ValidationEngine().validate(draft, ctx)
            issues = result.issues if hasattr(result, "issues") else result
            orig2 = [i for i in issues if i.rule_id == "ORIG-002"]
            self.assertEqual(len(orig2), 0, f"ORIG-002 should not fire: {[i.message for i in orig2]}")

    def test_real_address_keeps_user_input_confirmed(self):
        resp = self._chat("8022 Pine Wood Ct, Baytown, TX 77523")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        draft_id = resp.get_json()["draft_id"]
        w = self._draft_workers(draft_id)[0]
        self.assertEqual(w.origin, "8022 Pine Wood Ct, Baytown, TX 77523")
        self.assertEqual(w.origin_source, "user_input")
        self.assertTrue(w.origin_confirmed)

    def test_no_origin_still_requires_confirmation(self):
        """No home wording and no origin -> employee_default + origin_confirmed=False (ORIG-002 still needed)."""
        resp = self._chat(None)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        draft_id = resp.get_json()["draft_id"]
        w = self._draft_workers(draft_id)[0]
        self.assertEqual(w.origin, "518 Anacacho Dr, Spring, TX 77386")
        self.assertEqual(w.origin_source, "employee_default")
        self.assertFalse(w.origin_confirmed)


if __name__ == "__main__":
    unittest.main()
