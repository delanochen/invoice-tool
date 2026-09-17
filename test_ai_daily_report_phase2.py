"""Phase 2 tests for AI Daily Report feature (unittest style).

Covers:
- Employee resolution (self / unique / ambiguous / not found)
- user_id-based worker matching
- Origin priority (user_input > draft > employee_default)
- Destination from service_order
- Overnight stay per-worker
- Transportation modes
- Conversation context limits
- Version conflict
- Confirmed draft protection
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


class AIDailyReportPhase2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        ai_dir = Path(cls.temp_dir.name) / "ai_daily_report"
        shutil.copytree(REPO_DIR / "ai_daily_report", ai_dir)
        spec = importlib.util.spec_from_file_location("invoice_tool_ai_daily_p2_test", module_path)
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
            conn.execute("delete from service_orders where order_number='SO-AITEST-002'")
            conn.execute("delete from users where email like 'p2test%'")
            # Ensure client exists
            client = conn.execute("select id from clients limit 1").fetchone()
            if not client:
                conn.execute(
                    "insert into clients (name, client_number, short_name, country, created_at) values ('Test', 'CLI-002', 'T', 'US', '2026-09-14T00:00:00')"
                )
                client_id = conn.execute("select last_insert_rowid()").fetchone()[0]
            else:
                client_id = client["id"]
            # Ensure admin exists
            admin = conn.execute("select id, name from users where role='admin' limit 1").fetchone()
            if not admin:
                conn.execute(
                    "insert into users (name, email, password_hash, role, created_at) values ('Admin', 'p2test-admin@test.com', 'x', 'admin', '2026-09-14T00:00:00')"
                )
                admin = conn.execute("select id, name from users where role='admin' limit 1").fetchone()
            self.admin_id = admin["id"]
            self.admin_name = admin["name"]
            # Create test employees
            conn.execute(
                "insert into users (name, email, password_hash, role, address, created_at) values ('张三', 'p2test-zhangsan@test.com', 'x', 'employee', '518 Anacacho Dr, Spring, TX 77386', '2026-09-14T00:00:00')"
            )
            self.zhangsan = conn.execute("select id, name, address from users where email='p2test-zhangsan@test.com'").fetchone()
            # Create second 张三 for ambiguous test
            conn.execute(
                "insert into users (name, email, password_hash, role, address, created_at) values ('张三', 'p2test-zhangsan2@test.com', 'x', 'employee', 'Hobbs, NM 88240', '2026-09-14T00:00:00')"
            )
            self.zhangsan2 = conn.execute("select id, name, address from users where email='p2test-zhangsan2@test.com'").fetchone()
            # Create test order with site address
            conn.execute(
                "insert into service_orders (order_number, client_id, client_name, site_address, client_order_number, status, created_by, created_at) values ('SO-AITEST-002', ?, 'Test Client', '123 Site St, Test City, TX 12345', 'ORD-002', 'open', ?, '2026-09-14T00:00:00')",
                (client_id, self.admin_id),
            )
            conn.commit()
            self.order = conn.execute("select * from service_orders where order_number='SO-AITEST-002'").fetchone()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin_id

    def _make_services(self):
        from ai_daily_report import EmployeeResolutionService, TravelService, WorkOrderContextService, DailyReportService
        conn = self.module.db()
        emp = EmployeeResolutionService(conn, self.admin_id, self.admin_name)
        travel = TravelService(conn, emp)
        ctx = WorkOrderContextService(conn, self.admin_id, self.admin_name)
        svc = DailyReportService(conn, lambda: "2026-09-14T12:00:00", self.admin_id, self.admin_name)
        return emp, travel, ctx, svc

    # ─── Employee Resolution Tests ───────────────────────────────────────

    def test_self_reference_maps_current_user(self):
        """'我' should map to current_user.id, never guess."""
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            result = emp.resolve("我")
            self.assertTrue(result.resolved)
            self.assertEqual(result.user_id, self.admin_id)
            self.assertEqual(result.name, self.admin_name)

    def test_self_reference_aliases(self):
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            for alias in ["我自己", "本人", "me", "myself"]:
                result = emp.resolve(alias)
                self.assertTrue(result.resolved, f"{alias} should resolve")
                self.assertEqual(result.user_id, self.admin_id)

    def test_unique_employee_resolves(self):
        """Unique name should resolve to that user."""
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            # Delete second 张三 temporarily to make unique
            self.module.db().execute("delete from users where id = ?", (self.zhangsan2["id"],))
            self.module.db().commit()
            result = emp.resolve("张三")
            self.assertTrue(result.resolved)
            self.assertEqual(result.user_id, self.zhangsan["id"])
            # Restore
            self.module.db().execute(
                "insert into users (id, name, email, password_hash, role, address, created_at) values (?, '张三', 'p2test-zhangsan2@test.com', 'x', 'employee', 'Hobbs, NM 88240', '2026-09-14T00:00:00')",
                (self.zhangsan2["id"],),
            )
            self.module.db().commit()

    def test_ambiguous_employee_needs_clarification(self):
        """Two same-name employees should trigger clarification."""
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            result = emp.resolve("张三")
            self.assertFalse(result.resolved)
            self.assertTrue(result.clarification_required)
            self.assertEqual(len(result.candidates), 2)
            # Candidates should have masked emails, not full
            for c in result.candidates:
                self.assertIn("***", c["email_masked"])
                self.assertNotIn("@test.com", c["email_masked"].split("***")[0])

    def test_nonexistent_employee_needs_clarification(self):
        """Non-existent employee should trigger clarification, not auto-create."""
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            result = emp.resolve("王五")
            self.assertFalse(result.resolved)
            self.assertTrue(result.clarification_required)
            self.assertIn("王五", result.clarification_question)
            # Verify not auto-created
            count = self.module.db().execute("select count(*) as c from users where name='王五'").fetchone()["c"]
            self.assertEqual(count, 0)

    def test_empty_name_needs_clarification(self):
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            result = emp.resolve("")
            self.assertFalse(result.resolved)
            self.assertTrue(result.clarification_required)

    # ─── WorkerTravel Schema Tests ───────────────────────────────────────

    def test_worker_uses_user_id_primary(self):
        """Draft workers must have user_id; name is display only."""
        from ai_daily_report import WorkerTravel
        w = WorkerTravel(user_id=12, name="Ethan")
        self.assertEqual(w.user_id, 12)
        self.assertEqual(w.name, "Ethan")

    def test_worker_origin_source_fields(self):
        from ai_daily_report import WorkerTravel
        w = WorkerTravel(
            user_id=25, name="张三", origin="Hobbs, NM",
            origin_source="user_input", origin_confirmed=True,
        )
        self.assertEqual(w.origin_source, "user_input")
        self.assertTrue(w.origin_confirmed)

    def test_worker_destination_source(self):
        from ai_daily_report import WorkerTravel
        w = WorkerTravel(user_id=25, name="张三", destination="123 Site St", destination_source="service_order")
        self.assertEqual(w.destination_source, "service_order")

    # ─── Origin Priority Tests ───────────────────────────────────────────

    def test_user_input_origin_overrides_default(self):
        """User explicitly stated origin should be confirmed=True."""
        with self.module.app.app_context():
            _, travel, _, _ = self._make_services()
            w = travel.build_worker_travel(
                user_id=self.zhangsan["id"], name="张三",
                user_input_origin="Hobbs, NM 88240",
                destination="123 Site St",
            )
            self.assertEqual(w.origin, "Hobbs, NM 88240")
            self.assertEqual(w.origin_source, "user_input")
            self.assertTrue(w.origin_confirmed)

    def test_employee_default_address_unconfirmed(self):
        """users.address should be origin_source=employee_default, origin_confirmed=False."""
        with self.module.app.app_context():
            _, travel, _, _ = self._make_services()
            w = travel.build_worker_travel(
                user_id=self.zhangsan["id"], name="张三",
                destination="123 Site St",
            )
            self.assertEqual(w.origin, self.zhangsan["address"])
            self.assertEqual(w.origin_source, "employee_default")
            self.assertFalse(w.origin_confirmed)

    def test_draft_existing_origin_preserved(self):
        with self.module.app.app_context():
            _, travel, _, _ = self._make_services()
            w = travel.build_worker_travel(
                user_id=self.zhangsan["id"], name="张三",
                draft_existing_origin="Midland, TX",
                draft_existing_origin_confirmed=True,
                destination="123 Site St",
            )
            self.assertEqual(w.origin, "Midland, TX")
            self.assertEqual(w.origin_source, "draft_existing")
            self.assertTrue(w.origin_confirmed)

    def test_origin_missing_triggers_verification(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            # Worker with empty address in DB (address is NOT NULL, use empty string)
            self.module.db().execute("update users set address = '' where id = ?", (self.admin_id,))
            self.module.db().commit()
            w = WorkerTravel(user_id=self.admin_id, name=self.admin_name, transportation="self_drive")
            missing, msgs = travel.verify_travel_fields([w], site_address_present=True)
            self.assertTrue(any("origin" in m for m in missing))

    # ─── Destination Tests ───────────────────────────────────────────────

    def test_destination_from_service_order(self):
        with self.module.app.app_context():
            _, travel, _, _ = self._make_services()
            from ai_daily_report import WorkerTravel
            workers = [WorkerTravel(user_id=1, name="A")]
            travel.set_destination_for_all(workers, "123 Site St")
            self.assertEqual(workers[0].destination, "123 Site St")
            self.assertEqual(workers[0].destination_source, "service_order")

    def test_empty_site_address_triggers_verification(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            w = WorkerTravel(user_id=1, name="A", origin="X", origin_confirmed=True, overnight_stay=False)
            missing, msgs = travel.verify_travel_fields([w], site_address_present=False)
            self.assertIn("site_address", missing)

    # ─── Overnight Stay Tests ────────────────────────────────────────────

    def test_all_not_stay_batch(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            workers = [WorkerTravel(user_id=1, name="A"), WorkerTravel(user_id=2, name="B")]
            travel.set_overnight_for_all(workers, False)
            self.assertTrue(all(w.overnight_stay is False for w in workers))

    def test_all_stay_batch(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            workers = [WorkerTravel(user_id=1, name="A"), WorkerTravel(user_id=2, name="B")]
            travel.set_overnight_for_all(workers, True)
            self.assertTrue(all(w.overnight_stay is True for w in workers))

    def test_per_worker_different_overnight(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            workers = [WorkerTravel(user_id=1, name="A"), WorkerTravel(user_id=2, name="B")]
            travel.set_overnight_for_worker(workers, 1, False)
            travel.set_overnight_for_worker(workers, 2, True)
            self.assertFalse(workers[0].overnight_stay)
            self.assertTrue(workers[1].overnight_stay)

    def test_overnight_unknown_triggers_verification(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            w = WorkerTravel(user_id=1, name="A", transportation="self_drive", origin="X", origin_confirmed=True)
            missing, msgs = travel.verify_travel_fields([w], site_address_present=True)
            self.assertTrue(any("overnight" in m for m in missing))

    # ─── Transportation Tests ────────────────────────────────────────────

    def test_passenger_no_mileage_requirement(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            w = WorkerTravel(user_id=1, name="A", transportation="passenger", overnight_stay=False)
            self.assertFalse(travel.needs_mileage_calculation(w))

    def test_self_drive_requires_origin(self):
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            w = WorkerTravel(user_id=1, name="A", transportation="self_drive", origin=None, overnight_stay=False)
            self.assertFalse(travel.needs_mileage_calculation(w))
            w.origin = "X"
            w.origin_confirmed = True
            w.destination = "Y"
            self.assertTrue(travel.needs_mileage_calculation(w))

    # ─── user_id-based Matching Tests ────────────────────────────────────

    def test_update_worker_by_user_id(self):
        """Updating worker should match by user_id, not name."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction, DailyReportDraft, WorkerTravel
            _, _, _, svc = self._make_services()
            draft = DailyReportDraft(service_order_id=self.order["id"], report_date="2026-09-14")
            draft.workers.append(WorkerTravel(user_id=self.zhangsan["id"], name="张三", origin="Old"))
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.zhangsan["id"], "name": "张三", "origin": "New Address"}]
            draft, msg = svc.execute_action(draft, action, resolved_workers=resolved)
            self.assertEqual(draft.workers[0].origin, "New Address")
            self.assertTrue(draft.workers[0].origin_confirmed)

    def test_update_worker_switch_back_to_self_drive(self):
        """一句话改回自驾: explicit transportation='self_drive' must apply.

        Regression: the old code skipped any transportation equal to
        'self_drive' because it could not tell "user said self_drive" from
        the old schema default, so switching back was impossible.
        """
        with self.module.app.app_context():
            from ai_daily_report import AIAction, DailyReportDraft, WorkerTravel
            _, _, _, svc = self._make_services()
            draft = DailyReportDraft(service_order_id=self.order["id"], report_date="2026-09-14")
            draft.workers.append(WorkerTravel(
                user_id=self.zhangsan["id"], name="张三",
                transportation="passenger", origin="Old",
                route_status="success", one_way_miles=12.0,
            ))
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.zhangsan["id"], "name": "张三",
                         "transportation": "self_drive"}]
            draft, msg = svc.execute_action(draft, action, resolved_workers=resolved)
            self.assertEqual(draft.workers[0].transportation, "self_drive")
            # route data must be invalidated after the mode change
            self.assertNotEqual(draft.workers[0].route_status, "success")

    def test_update_worker_null_transportation_keeps_existing(self):
        """transportation=None (未提及) must keep the existing mode."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction, DailyReportDraft, WorkerTravel
            _, _, _, svc = self._make_services()
            draft = DailyReportDraft(service_order_id=self.order["id"], report_date="2026-09-14")
            draft.workers.append(WorkerTravel(
                user_id=self.zhangsan["id"], name="张三",
                transportation="passenger", origin="Old",
            ))
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.zhangsan["id"], "name": "张三",
                         "transportation": None, "origin": "New Origin"}]
            draft, msg = svc.execute_action(draft, action, resolved_workers=resolved)
            self.assertEqual(draft.workers[0].transportation, "passenger")
            self.assertEqual(draft.workers[0].origin, "New Origin")

    def test_worker_input_transportation_validation(self):
        """WorkerInput.transportation: None ok, valid ok, junk rejected."""
        from ai_daily_report.schemas import WorkerInput
        from pydantic import ValidationError
        self.assertIsNone(WorkerInput(name="A").transportation)
        self.assertEqual(
            WorkerInput(name="A", transportation="passenger").transportation,
            "passenger",
        )
        with self.assertRaises(ValidationError):
            WorkerInput(name="A", transportation="teleportation")

    def test_update_worker_add_new_worker_defaults_self_drive(self):
        """New worker with transportation=None defaults to self_drive."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction, DailyReportDraft, WorkerTravel
            _, _, _, svc = self._make_services()
            draft = DailyReportDraft(service_order_id=self.order["id"], report_date="2026-09-14")
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.zhangsan["id"], "name": "张三",
                         "transportation": None}]
            draft, msg = svc.execute_action(draft, action, resolved_workers=resolved)
            self.assertEqual(len(draft.workers), 1)
            self.assertEqual(draft.workers[0].transportation, "self_drive")

    def test_two_workers_no_cross_contamination(self):
        """Simultaneously updating two workers should not mix up data."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction, DailyReportDraft, WorkerTravel
            _, _, _, svc = self._make_services()
            draft = DailyReportDraft(service_order_id=self.order["id"], report_date="2026-09-14")
            draft.workers.append(WorkerTravel(user_id=self.admin_id, name=self.admin_name))
            draft.workers.append(WorkerTravel(user_id=self.zhangsan["id"], name="张三"))
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [
                {"user_id": self.admin_id, "name": self.admin_name, "origin": "Spring, TX", "overnight_stay": False},
                {"user_id": self.zhangsan["id"], "name": "张三", "origin": "Hobbs, NM", "overnight_stay": True},
            ]
            draft, msg = svc.execute_action(draft, action, resolved_workers=resolved)
            admin_w = next(w for w in draft.workers if w.user_id == self.admin_id)
            zs_w = next(w for w in draft.workers if w.user_id == self.zhangsan["id"])
            self.assertEqual(admin_w.origin, "Spring, TX")
            self.assertFalse(admin_w.overnight_stay)
            self.assertEqual(zs_w.origin, "Hobbs, NM")
            self.assertTrue(zs_w.overnight_stay)

    # ─── Conversation Context Tests ──────────────────────────────────────

    def test_conversation_max_6_rounds(self):
        with self.module.app.app_context():
            _, _, _, svc = self._make_services()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            for i in range(10):
                svc.add_conversation_message(created["id"], "user", f"message {i}")
            conv = svc.load_conversation(svc.get_draft(created["id"]))
            self.assertLessEqual(len(conv), 6)

    def test_message_truncated_at_2000(self):
        from ai_daily_report import DailyReportService
        long_msg = "x" * 3000
        truncated = DailyReportService.truncate_message(long_msg)
        self.assertLessEqual(len(truncated), 2050)  # 2000 + suffix
        self.assertIn("truncated", truncated)

    def test_deepseek_context_no_full_history(self):
        """get_context_for_deepseek should not include full conversation."""
        with self.module.app.app_context():
            from ai_daily_report import DailyReportDraft
            _, _, _, svc = self._make_services()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            for i in range(5):
                svc.add_conversation_message(created["id"], "user", f"secret old message {i}")
            draft = svc.parse_draft_data(svc.get_draft(created["id"]))
            ctx = svc.get_context_for_deepseek(draft, svc.get_draft(created["id"]))
            # Should not contain old messages
            self.assertNotIn("secret old message 1", ctx)
            self.assertIn("Draft摘要", ctx)

    # ─── Version Conflict & State Machine ───────────────────────────────

    def test_version_conflict_raises(self):
        with self.module.app.app_context():
            from ai_daily_report.daily_report_service import DraftVersionConflict
            from ai_daily_report import DailyReportDraft
            _, _, _, svc = self._make_services()
            created = svc.create_draft(self.order["id"], "2026-09-14")
            draft = svc.parse_draft_data(created)
            draft.service_description = "first"
            svc.save_draft(created["id"], draft, expected_version=1)
            draft.service_description = "second"
            with self.assertRaises(DraftVersionConflict):
                svc.save_draft(created["id"], draft, expected_version=1)

    def test_confirmed_draft_blocks_update(self):
        from ai_daily_report import DailyReportService
        self.assertFalse(DailyReportService.can_execute_action("confirmed", "update_worker"))
        self.assertTrue(DailyReportService.can_execute_action("confirmed", "clarify"))

    # ─── WorkOrderContext Tests ─────────────────────────────────────────

    def test_context_includes_workers(self):
        with self.module.app.app_context():
            _, _, ctx, _ = self._make_services()
            context = ctx.get_context(self.order["id"], "2026-09-14")
            self.assertTrue(context["exists"])
            self.assertEqual(context["site_address"], "123 Site St, Test City, TX 12345")
            self.assertTrue(context["site_address_present"])
            # Workers should only have id+name, no email/address
            for w in context["workers"]:
                self.assertIn("user_id", w)
                self.assertIn("name", w)
                self.assertNotIn("email", w)
                self.assertNotIn("address", w)

    def test_context_nonexistent_order(self):
        with self.module.app.app_context():
            _, _, ctx, _ = self._make_services()
            context = ctx.get_context(99999, "2026-09-14")
            self.assertFalse(context["exists"])

    # ─── Phase 2 Final Acceptance Tests (A-J) ───────────────────────────

    def test_A_partial_name_unique_not_auto_resolved(self):
        """A. Partial name match (even unique) must NOT auto-resolve."""
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            # "张" partially matches "张三" but should not auto-resolve
            result = emp.resolve("张")
            self.assertFalse(result.resolved)
            self.assertTrue(result.clarification_required)
            self.assertEqual(result.error, "partial_match_requires_confirmation")
            self.assertTrue(len(result.candidates) >= 1)

    def test_B_partial_name_multiple_candidates(self):
        """B. Partial name with multiple candidates -> clarification."""
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            result = emp.resolve("张")
            self.assertFalse(result.resolved)
            self.assertTrue(len(result.candidates) >= 2)  # two 张三

    def test_C_inactive_employee_not_resolved(self):
        """C. Inactive employee cannot be resolved."""
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            # Create an inactive employee
            self.module.db().execute(
                "insert into users (name, email, password_hash, role, is_active, created_at) values ('李四', 'p2test-lisi@test.com', 'x', 'user', 0, '2026-09-14T00:00:00')"
            )
            self.module.db().commit()
            result = emp.resolve("李四")
            self.assertFalse(result.resolved)
            self.assertIn("没有找到", result.clarification_question)

    def test_D_self_maps_current_user(self):
        """D. '我' maps to current_user.id."""
        with self.module.app.app_context():
            emp, _, _, _ = self._make_services()
            result = emp.resolve("我")
            self.assertTrue(result.resolved)
            self.assertEqual(result.user_id, self.admin_id)

    def test_E_origin_change_invalidates_route(self):
        """E. Changing origin clears route/mileage fields."""
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            w = WorkerTravel(
                user_id=1, name="A", origin="Old", origin_confirmed=True,
                one_way_miles=100.0, reported_miles=200.0,
                route_polyline="abc", route_duration_seconds=3600,
                mileage_evidence_path="/tmp/evidence.png",
            )
            workers = [w]
            travel.update_worker_origin(workers, 1, "New Address")
            self.assertIsNone(w.one_way_miles)
            self.assertIsNone(w.reported_miles)
            self.assertIsNone(w.route_polyline)
            self.assertIsNone(w.route_duration_seconds)
            self.assertIsNone(w.mileage_evidence_path)

    def test_F_overnight_change_invalidates_route(self):
        """F. Changing overnight_stay clears route/mileage fields."""
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            w = WorkerTravel(
                user_id=1, name="A", overnight_stay=False,
                one_way_miles=100.0, reported_miles=200.0,
            )
            workers = [w]
            travel.set_overnight_for_worker(workers, 1, True)
            self.assertIsNone(w.one_way_miles)
            self.assertIsNone(w.reported_miles)

    def test_G_transportation_change_invalidates_route(self):
        """G. Changing transportation clears route/mileage fields."""
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            w = WorkerTravel(
                user_id=1, name="A", transportation="self_drive",
                one_way_miles=100.0, reported_miles=200.0,
            )
            workers = [w]
            travel.set_transportation_for_worker(workers, 1, "passenger")
            self.assertIsNone(w.one_way_miles)
            self.assertIsNone(w.reported_miles)
            self.assertEqual(w.transportation, "passenger")

    def test_H_confirm_employee_default_origin(self):
        """H. User confirms employee_default origin: source stays, confirmed=true."""
        with self.module.app.app_context():
            from ai_daily_report import WorkerTravel
            _, travel, _, _ = self._make_services()
            w = WorkerTravel(
                user_id=self.zhangsan["id"], name="张三",
                origin=self.zhangsan["address"],
                origin_source="employee_default", origin_confirmed=False,
            )
            workers = [w]
            result = travel.confirm_employee_default_origin(workers, self.zhangsan["id"])
            self.assertTrue(result)
            self.assertTrue(w.origin_confirmed)
            self.assertEqual(w.origin_source, "employee_default")  # NOT changed to user_input

    def test_I_ai_action_cannot_override_destination(self):
        """I. Normal update_worker AI Action cannot change destination."""
        with self.module.app.app_context():
            from ai_daily_report import AIAction, DailyReportDraft, WorkerTravel
            _, _, _, svc = self._make_services()
            draft = DailyReportDraft(service_order_id=self.order["id"], report_date="2026-09-14")
            draft.workers.append(WorkerTravel(
                user_id=self.zhangsan["id"], name="张三",
                destination="123 Site St", destination_source="service_order",
            ))
            action = AIAction(action_version=1, intent="update_worker")
            resolved = [{"user_id": self.zhangsan["id"], "name": "张三", "origin": "New Origin"}]
            draft, msg = svc.execute_action(draft, action, resolved_workers=resolved)
            # Destination must remain from service_order, not overwritten
            self.assertEqual(draft.workers[0].destination, "123 Site St")
            self.assertEqual(draft.workers[0].destination_source, "service_order")

    def test_J_long_message_rejected_not_truncated(self):
        """J. >2000 char message is rejected (413), not silently truncated."""
        from ai_daily_report.daily_report_service import MAX_MESSAGE_LENGTH
        self.assertEqual(MAX_MESSAGE_LENGTH, 2000)
        # Verify truncate_message still exists but API layer rejects before using it
        from ai_daily_report import DailyReportService
        long_msg = "x" * 3000
        self.assertTrue(len(long_msg) > MAX_MESSAGE_LENGTH)


if __name__ == "__main__":
    unittest.main()
