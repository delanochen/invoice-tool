"""Phase 1 tests for AI Daily Report feature (unittest style).

Covers:
- Action Schema validation (valid/invalid JSON, unknown intent, missing fields)
- Draft CRUD
- Action execution (update_worker, add_work_item, remove_work_item, etc.)
- Idempotency
- State machine (confirmed blocks updates, reopen works)
- Optimistic locking
- JSON corruption handling
- API endpoints
- No impact on existing service_reports
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


class AIDailyReportPhase1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        # Copy ai_daily_report module
        ai_dir = Path(cls.temp_dir.name) / "ai_daily_report"
        shutil.copytree(REPO_DIR / "ai_daily_report", ai_dir)
        spec = importlib.util.spec_from_file_location("invoice_tool_ai_daily_test_app", module_path)
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
            conn.execute("delete from service_orders where order_number='SO-AITEST-001'")
            # Ensure client and admin user exist
            client = conn.execute("select id from clients limit 1").fetchone()
            if not client:
                conn.execute(
                    "insert into clients (name, client_number, short_name, country, created_at) values ('Test Client', 'CLI-001', 'TC', 'US', '2026-09-14T00:00:00')"
                )
                client_id = conn.execute("select last_insert_rowid()").fetchone()[0]
            else:
                client_id = client["id"]
            admin = conn.execute("select id, name from users where role='admin' limit 1").fetchone()
            if not admin:
                conn.execute(
                    "insert into users (name, email, password_hash, role, created_at) values ('Admin', 'admin@test.com', 'x', 'admin', '2026-09-14T00:00:00')"
                )
                admin = conn.execute("select id, name from users where role='admin' limit 1").fetchone()
            self.admin_id = admin["id"]
            self.admin_name = admin["name"]
            conn.execute(
                "insert into service_orders (order_number, client_id, client_name, site_address, client_order_number, status, created_by, created_at) values ('SO-AITEST-001', ?, 'Test Client', '123 Test St', 'ORD-001', 'open', ?, '2026-09-14T00:00:00')",
                (client_id, self.admin_id),
            )
            conn.commit()
            self.order = conn.execute(
                "select * from service_orders where order_number='SO-AITEST-001'"
            ).fetchone()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin_id

    def _make_service(self):
        from ai_daily_report import DailyReportService
        return DailyReportService(
            self.module.db(),
            lambda: "2026-09-14T12:00:00",
            self.admin_id,
            self.admin_name,
        )

    # ─── Action Schema Tests ────────────────────────────────────────────

    def test_valid_create_action(self):
        from ai_daily_report import parse_and_validate
        raw = json.dumps({
            "action_version": 1, "intent": "create_daily_report",
            "workers": [{"name": "Ethan", "origin": "Spring"}],
        })
        result = parse_and_validate(raw)
        self.assertTrue(result.ok)
        self.assertEqual(result.action.intent, "create_daily_report")

    def test_invalid_json(self):
        from ai_daily_report import parse_and_validate
        result = parse_and_validate("not json")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "invalid_json")

    def test_empty_response(self):
        from ai_daily_report import parse_and_validate
        result = parse_and_validate("")
        self.assertFalse(result.ok)

    def test_unknown_intent(self):
        from ai_daily_report import parse_and_validate
        result = parse_and_validate(json.dumps({"action_version": 1, "intent": "delete_all"}))
        self.assertFalse(result.ok)

    def test_wrong_action_version(self):
        from ai_daily_report import parse_and_validate
        result = parse_and_validate(json.dumps({"action_version": 99, "intent": "create_daily_report"}))
        self.assertFalse(result.ok)

    def test_missing_action_version(self):
        from ai_daily_report import parse_and_validate
        result = parse_and_validate(json.dumps({"intent": "create_daily_report"}))
        self.assertFalse(result.ok)

    def test_invalid_date_rejected(self):
        from ai_daily_report import parse_and_validate
        result = parse_and_validate(json.dumps({
            "action_version": 1, "intent": "create_daily_report", "date": "09/14/2026"
        }))
        self.assertFalse(result.ok)

    def test_valid_date_accepted(self):
        from ai_daily_report import parse_and_validate
        result = parse_and_validate(json.dumps({
            "action_version": 1, "intent": "create_daily_report", "date": "2026-09-14"
        }))
        self.assertTrue(result.ok)
        self.assertEqual(result.action.date, "2026-09-14")

    def test_markdown_fence_stripped(self):
        from ai_daily_report import parse_and_validate
        result = parse_and_validate("```json\n{\"action_version\": 1, \"intent\": \"clarify\"}\n```")
        self.assertTrue(result.ok)

    def test_phase1_implemented_check(self):
        from ai_daily_report import parse_and_validate, is_phase1_implemented
        r1 = parse_and_validate(json.dumps({"action_version": 1, "intent": "create_daily_report"}))
        self.assertTrue(is_phase1_implemented(r1.action))
        r2 = parse_and_validate(json.dumps({"action_version": 1, "intent": "change_safety_photo"}))
        self.assertFalse(is_phase1_implemented(r2.action))

    def test_default_factory_list_isolation(self):
        from ai_daily_report import AIAction
        a1 = AIAction(action_version=1, intent="create_daily_report")
        a2 = AIAction(action_version=1, intent="create_daily_report")
        a1.missing_fields.append("test")
        self.assertNotIn("test", a2.missing_fields)

    # ─── Draft CRUD Tests ───────────────────────────────────────────────

    def test_create_draft(self):
        with self.module.app.app_context():
            svc = self._make_service()
            draft = svc.create_draft(self.order["id"], "2026-09-14", site_address="123 Test St")
            self.assertIsNotNone(draft)
            self.assertEqual(draft["status"], "draft")
            self.assertEqual(draft["draft_version"], 1)

    def test_get_draft(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            fetched = svc.get_draft(created["id"])
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["id"], created["id"])

    def test_get_active_draft(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            active = svc.get_active_draft(self.order["id"], "2026-09-14")
            self.assertIsNotNone(active)
            self.assertEqual(active["id"], created["id"])

    def test_parse_draft_data(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            self.assertEqual(draft.report_date, "2026-09-14")

    def test_corrupted_draft_data_safe(self):
        """Corrupted JSON should not raise 500; returns safe draft with verification flag."""
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            # Corrupt the JSON
            self.module.db().execute(
                "update ai_daily_report_drafts set draft_data = ? where id = ?",
                ("{corrupted json!!!", created["id"]),
            )
            self.module.db().commit()
            row = svc.get_draft(created["id"])
            draft = svc.parse_draft_data(row)
            self.assertTrue(draft.verification_required)
            self.assertIn("draft_data_corrupted", draft.verification_fields)

    def test_save_draft(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.service_description = "Updated"
            svc.save_draft(created["id"], draft)
            updated = svc.parse_draft_data(svc.get_draft(created["id"]))
            self.assertEqual(updated.service_description, "Updated")

    def test_save_draft_optimistic_lock(self):
        """Saving with wrong expected_version should raise DraftVersionConflict."""
        with self.module.app.app_context():
            from ai_daily_report.daily_report_service import DraftVersionConflict
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.service_description = "First update"
            svc.save_draft(created["id"], draft, expected_version=1)
            # Now version is 2; trying to save with version 1 should fail
            draft.service_description = "Second update"
            with self.assertRaises(DraftVersionConflict):
                svc.save_draft(created["id"], draft, expected_version=1)

    def test_draft_never_writes_to_service_reports(self):
        with self.module.app.app_context():
            conn = self.module.db()
            before = conn.execute("select count(*) as c from service_reports").fetchone()["c"]
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.service_description = "test"
            svc.save_draft(created["id"], draft)
            svc.delete_draft(created["id"])
            after = conn.execute("select count(*) as c from service_reports").fetchone()["c"]
            self.assertEqual(before, after)

    # ─── State Machine Tests ────────────────────────────────────────────

    def test_state_machine_valid_transitions(self):
        from ai_daily_report import DailyReportService
        self.assertTrue(DailyReportService.can_transition("draft", "confirmed"))
        self.assertTrue(DailyReportService.can_transition("draft", "cancelled"))
        self.assertTrue(DailyReportService.can_transition("confirmed", "draft"))
        self.assertTrue(DailyReportService.can_transition("confirmed", "saved"))
        self.assertTrue(DailyReportService.can_transition("confirmed", "cancelled"))

    def test_state_machine_invalid_transitions(self):
        from ai_daily_report import DailyReportService
        self.assertFalse(DailyReportService.can_transition("confirmed", "confirmed"))
        self.assertFalse(DailyReportService.can_transition("cancelled", "draft"))
        self.assertFalse(DailyReportService.can_transition("saved", "draft"))

    def test_confirmed_blocks_update_action(self):
        from ai_daily_report import DailyReportService
        self.assertFalse(DailyReportService.can_execute_action("confirmed", "update_worker"))
        self.assertFalse(DailyReportService.can_execute_action("confirmed", "add_work_item"))
        # clarify is allowed even when confirmed
        self.assertTrue(DailyReportService.can_execute_action("confirmed", "clarify"))

    def test_reopen_draft(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            svc.update_draft_status(created["id"], "confirmed")
            self.assertEqual(svc.get_draft(created["id"])["status"], "confirmed")
            svc.reopen_draft(created["id"])
            self.assertEqual(svc.get_draft(created["id"])["status"], "draft")

    def test_illegal_transition_raises(self):
        with self.module.app.app_context():
            from ai_daily_report.daily_report_service import DraftStateError
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            svc.update_draft_status(created["id"], "cancelled")
            with self.assertRaises(DraftStateError):
                svc.reopen_draft(created["id"])  # cancelled -> draft is illegal

    # ─── Action Execution Tests ─────────────────────────────────────────

    def test_add_worker(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            action = AIAction(action_version=1, intent="update_worker",
                              workers=[{"name": "张三", "origin": "Hobbs, NM"}])
            draft, msg = svc.execute_action(draft, action)
            self.assertEqual(len(draft.workers), 1)
            self.assertEqual(draft.workers[0].name, "张三")

    def test_update_worker_origin(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction, WorkerTravel
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.workers.append(WorkerTravel(user_id=1, name="张三", origin="Old"))
            action = AIAction(action_version=1, intent="update_worker",
                              workers=[{"name": "张三", "origin": "Midland, TX"}])
            draft, msg = svc.execute_action(draft, action)
            self.assertEqual(draft.workers[0].origin, "Midland, TX")

    def test_overnight_stay_global_shortcut(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction, WorkerTravel
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.workers.append(WorkerTravel(user_id=1, name="A"))
            draft.workers.append(WorkerTravel(user_id=2, name="B"))
            action = AIAction(action_version=1, intent="update_worker",
                              workers=[], overnight_stay=True)
            draft, msg = svc.execute_action(draft, action)
            self.assertTrue(all(w.overnight_stay is True for w in draft.workers))

    def test_per_worker_overnight_independence(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.workers.append(WorkerTravel(user_id=1, name="A", overnight_stay=True))
            draft.workers.append(WorkerTravel(user_id=2, name="B", overnight_stay=False))
            self.assertTrue(draft.workers[0].overnight_stay)
            self.assertFalse(draft.workers[1].overnight_stay)

    def test_add_work_item(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            action = AIAction(action_version=1, intent="add_work_item",
                              work_items=[{"equipment": "A313", "action": "replace_fuse", "fuse_number": 2}])
            draft, msg = svc.execute_action(draft, action)
            self.assertEqual(len(draft.work_items), 1)
            self.assertEqual(draft.work_items[0].equipment, "A313")

    def test_remove_work_item(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction, WorkItem
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.work_items.append(WorkItem(equipment="A313", action="repair"))
            draft.work_items.append(WorkItem(equipment="B200", action="inspect"))
            action = AIAction(action_version=1, intent="remove_work_item",
                              work_items=[{"equipment": "A313"}])
            draft, msg = svc.execute_action(draft, action)
            self.assertEqual(len(draft.work_items), 1)
            self.assertEqual(draft.work_items[0].equipment, "B200")

    def test_add_waiting_time(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            action = AIAction(action_version=1, intent="add_waiting_time",
                              waiting_hours=1.5, waiting_reason="客户未到")
            draft, msg = svc.execute_action(draft, action)
            self.assertEqual(draft.waiting_hours, 1.5)
            self.assertEqual(draft.waiting_reason, "客户未到")

    def test_update_arrival_time(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            action = AIAction(action_version=1, intent="update_arrival_time", arrival_time="08:15")
            draft, msg = svc.execute_action(draft, action)
            self.assertEqual(draft.arrival_time, "08:15")
            self.assertEqual(draft.arrival_time_source, "manual")

    def test_update_departure_time(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            action = AIAction(action_version=1, intent="update_departure_time", departure_time="17:30")
            draft, msg = svc.execute_action(draft, action)
            self.assertEqual(draft.departure_time, "17:30")
            self.assertEqual(draft.departure_time_source, "manual")

    # ─── Idempotency Tests ──────────────────────────────────────────────

    def test_record_action_duplicate_returns_false(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction, generate_action_id
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            action = AIAction(action_version=1, intent="clarify")
            aid = generate_action_id()
            self.assertTrue(svc.record_action(created["id"], aid, action))
            self.assertFalse(svc.record_action(created["id"], aid, action))

    def test_get_executed_action_ids(self):
        with self.module.app.app_context():
            from ai_daily_report import AIAction, generate_action_id
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            action = AIAction(action_version=1, intent="clarify")
            aid1 = generate_action_id()
            aid2 = generate_action_id()
            svc.record_action(created["id"], aid1, action)
            svc.record_action(created["id"], aid2, action)
            ids = svc.get_executed_action_ids(created["id"])
            self.assertIn(aid1, ids)
            self.assertIn(aid2, ids)

    # ─── Preview Tests ──────────────────────────────────────────────────

    def test_build_preview(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel, WorkItem
            svc = self._make_service()
            created = svc.create_draft(
                self.order["id"], "2026-09-14",
                initial_workers=[{"user_id": 1, "name": "Ethan", "origin": "Spring"}],
                initial_work_items=[{"equipment": "A313", "action": "replace_fuse"}],
            )
            draft = svc.parse_draft_data(created)
            preview = svc.build_preview(draft)
            self.assertEqual(len(preview["workers"]), 1)
            self.assertEqual(len(preview["work_items"]), 1)

    def test_build_draft_summary(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(
                self.order["id"], "2026-09-14",
                initial_workers=[{"user_id": 1, "name": "Ethan", "origin": "Spring", "overnight_stay": False}],
            )
            draft = svc.parse_draft_data(created)
            summary = svc.build_draft_summary(draft)
            self.assertIn("Ethan", summary)
            self.assertIn("Spring", summary)

    # ─── API Endpoint Tests ─────────────────────────────────────────────

    def test_get_draft_not_found(self):
        resp = self.client.get("/api/ai/daily-report/draft/99999")
        self.assertEqual(resp.status_code, 404)

    def test_chat_requires_message(self):
        resp = self.client.post("/api/ai/daily-report/chat",
                                json={"message": "", "service_order_id": self.order["id"]})
        self.assertEqual(resp.status_code, 400)

    def test_chat_requires_service_order_id(self):
        resp = self.client.post("/api/ai/daily-report/chat", json={"message": "test"})
        self.assertEqual(resp.status_code, 400)

    def test_cancel_draft(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft_id = created["id"]
            self.module.db().commit()
        url = f"/api/ai/daily-report/draft/{draft_id}/cancel"
        self.assertEqual(self.client.post(url).status_code, 403)
        token = self.client.get('/api/ai/daily-report/csrf').get_json()['csrf_token']
        resp = self.client.post(url, headers={'X-CSRF-Token': token})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "cancelled")
        with self.module.app.app_context():
            row = self._make_service().get_draft(draft_id)
            self.assertIsNotNone(row)
            self.assertEqual(row['status'], 'cancelled')

    def test_reopen_draft_api(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            svc.update_draft_status(created["id"], "confirmed")
            draft_id = created["id"]
            self.module.db().commit()
        resp = self.client.post(f"/api/ai/daily-report/draft/{draft_id}/reopen")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "draft")

    def test_confirm_with_verification_required(self):
        with self.module.app.app_context():
            svc = self._make_service()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.verification_required = True
            draft.verification_fields = ["overnight_stay"]
            svc.save_draft(created["id"], draft)
            draft_id = created["id"]
            self.module.db().commit()
        resp = self.client.post(f"/api/ai/daily-report/draft/{draft_id}/confirm")
        self.assertEqual(resp.status_code, 400)

    # ─── Migration Tests ────────────────────────────────────────────────

    def test_new_tables_exist(self):
        with self.module.app.app_context():
            conn = self.module.db()
            tables = [r["name"] for r in conn.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()]
            self.assertIn("ai_daily_report_drafts", tables)
            self.assertIn("ai_daily_report_actions", tables)
            self.assertIn("ai_photo_analysis", tables)

    def test_draft_version_column_exists(self):
        with self.module.app.app_context():
            conn = self.module.db()
            cols = [r["name"] for r in conn.execute("pragma table_info(ai_daily_report_drafts)").fetchall()]
            self.assertIn("draft_version", cols)

    def test_service_reports_audit_columns_exist(self):
        with self.module.app.app_context():
            conn = self.module.db()
            cols = [r["name"] for r in conn.execute("pragma table_info(service_reports)").fetchall()]
            for col in ["ai_generated", "arrival_time_source", "departure_time_source",
                        "arrival_photo_relative_path", "arrival_photo_hash",
                        "departure_photo_relative_path", "departure_photo_hash", "ai_draft_id"]:
                self.assertIn(col, cols)

    def test_actions_unique_constraint(self):
        with self.module.app.app_context():
            conn = self.module.db()
            conn.execute("delete from ai_daily_report_actions")
            conn.execute("delete from ai_daily_report_drafts")
            conn.execute(
                "insert into ai_daily_report_drafts (service_order_id, report_date, draft_data, created_by, created_at, updated_at) values (1, '2026-09-14', '{}', 1, '2026-09-14T00:00:00', '2026-09-14T00:00:00')"
            )
            draft_id = conn.execute("select last_insert_rowid()").fetchone()[0]
            conn.execute(
                "insert into ai_daily_report_actions (draft_id, action_id, action_version, intent, action_payload, executed_at, executed_by) values (?, 'act-1', 1, 'clarify', '{}', '2026-09-14T00:00:00', 1)",
                (draft_id,),
            )
            with self.assertRaises(Exception):
                conn.execute(
                    "insert into ai_daily_report_actions (draft_id, action_id, action_version, intent, action_payload, executed_at, executed_by) values (?, 'act-1', 1, 'clarify', '{}', '2026-09-14T00:00:00', 1)",
                    (draft_id,),
                )
            conn.rollback()


if __name__ == "__main__":
    unittest.main()
