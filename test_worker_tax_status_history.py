"""Phase 2B：W-2 / 1099 税务身份历史（worker_tax_status_history）回归测试。"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PERIOD_START = "2026-08-03"


class WorkerTaxStatusHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("worker_tax_status_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="tax-status-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "employee_payment_components", "payment_order_events", "payment_order_sources",
                "employee_payment_orders", "worker_tax_status_history",
                "service_report_workers", "service_reports", "service_orders",
                "audit_logs", "users", "employee_grades",
            ):
                db.execute(f"delete from {table}")
            self.admin_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Admin','ts-admin@test.invalid','unused','admin',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.other_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Other','other@test.invalid','unused','employee',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.worker_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Worker','ts-worker@test.invalid','unused','employee',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.grade_id = db.execute(
                "insert into employee_grades (grade_name,base_salary,standard_hourly_rate,"
                "meal_daily_amount,is_active,created_at) values ('TSGrade',500,20,0,1,?)",
                (self.module.now(),),
            ).lastrowid
            db.execute("update users set employee_grade_id=? where id=?", (self.grade_id, self.worker_id))
            self.order1 = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,"
                "start_date,created_by,created_at) values ('SO-TSH','C','S','C1','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            db.commit()
        self.http = self.module.app.test_client()
        self._login(self.admin_id)

    def _login(self, user_id):
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    def _add(self, user_id=None, **overrides):
        data = {"tax_status": "W2", "effective_from": "2026-07-01",
                "effective_to": "", "notes": "test"}
        data.update(overrides)
        response = self.http.post(f"/users/{user_id or self.worker_id}/tax-status", data=data)
        self.assertEqual(response.status_code, 302)
        return self.http.get(response.headers["Location"])

    def _count(self, user_id=None):
        with self.module.app.app_context():
            return self.module.db().execute(
                "select count(*) from worker_tax_status_history where employee_id=?",
                (user_id or self.worker_id,)).fetchone()[0]

    def test_add_entry_and_view_history_with_current_status(self):
        page = self._add()
        self.assertIn("W-2", page.get_data(as_text=True))
        self.assertEqual(self._count(), 1)
        detail = self.http.get(f"/users/{self.worker_id}/tax-status")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("当前税务身份", detail.get_data(as_text=True))
        self.assertIn("2026-07-01", detail.get_data(as_text=True))

    def test_open_ended_entry_sets_current_status_indefinitely(self):
        self._add()
        with self.module.app.app_context():
            self.assertEqual(
                self.module.worker_tax_status_current(self.worker_id, "2026-07-01"), "W2")
            self.assertEqual(
                self.module.worker_tax_status_current(self.worker_id, "2027-01-01"), "W2")
            self.assertIsNone(self.module.worker_tax_status_current(self.worker_id, "2026-06-30"))

    def test_overlapping_segment_is_rejected(self):
        self._add(tax_status="1099", effective_from="2026-01-01", effective_to="2026-06-30")
        self._add()  # W2 2026-07-01~ 至今
        self.assertEqual(self._count(), 2)
        # 与 1099 段重叠
        self._add(tax_status="1099", effective_from="2026-06-01", effective_to="2026-06-15")
        # 与 W2 开放段重叠
        self._add(tax_status="1099", effective_from="2026-08-01", effective_to="")
        self.assertEqual(self._count(), 2, "重叠记录必须被拒绝")

    def test_invalid_status_and_reversed_dates_are_rejected(self):
        self._add(tax_status="W-2")            # 非法枚举
        self._add(tax_status="employee")       # 非法枚举
        self._add(effective_from="2026-07-01", effective_to="2026-06-30")  # 结束早于开始
        self._add(effective_from="")           # 缺生效日期
        self.assertEqual(self._count(), 0)

    def test_view_permission_is_self_or_manager(self):
        self._login(self.other_id)
        response = self.http.get(f"/users/{self.worker_id}/tax-status")
        self.assertEqual(response.status_code, 403)
        response = self.http.get(f"/users/{self.other_id}/tax-status")
        self.assertEqual(response.status_code, 200)
        # 普通员工不能写入
        response = self.http.post(f"/users/{self.other_id}/tax-status",
                                  data={"tax_status": "W2", "effective_from": "2026-07-01"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._count(self.other_id), 0)

    def test_existing_component_snapshots_are_not_rewritten_by_later_history(self):
        """先无身份生成 SL（快照 NULL），后补登记身份 → 旧快照保持 NULL 不改写。"""
        with self.module.app.app_context():
            db = self.module.db()
            report_id = db.execute(
                "insert into service_reports (service_order_id,report_date,actual_work_date,"
                "total_service_hours,created_by,created_at,updated_at)"
                " values (?,'2026-08-03','2026-08-03',8,?,'t','t')",
                (self.order1, self.admin_id)).lastrowid
            db.execute(
                "insert into service_report_workers (report_id,user_id,travel_mode,"
                "public_transport_hours,work_description) values (?,?,'legacy',0,'t')",
                (report_id, self.worker_id))
            db.commit()
        response = self.http.post("/employee-payments/generate-payroll",
                                  data={"period_start": PERIOD_START})
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            payment = dict(self.module.db().execute(
                "select * from employee_payment_orders order by id desc limit 1").fetchone())
            before = [dict(row) for row in self.module.db().execute(
                "select * from employee_payment_components where payment_order_id=?",
                (payment["id"],)).fetchall()]
        self.assertTrue(before)
        self.assertTrue(all(row["tax_status_snapshot"] is None for row in before))
        # 现在补登记身份
        self._add()
        with self.module.app.app_context():
            after = [dict(row) for row in self.module.db().execute(
                "select * from employee_payment_components where payment_order_id=?",
                (payment["id"],)).fetchall()]
        self.assertEqual(after, before, "补登记身份绝不能改写已有组件快照")

    def test_employee_form_links_to_tax_status_page(self):
        page = self.http.get(f"/users/{self.worker_id}/edit")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f"/users/{self.worker_id}/tax-status", page.get_data(as_text=True))

    def test_legacy_role_value_user_still_shows_tax_status_entry(self):
        """旧角色值 'user'（normalized_role 后为 employee）也必须显示税务身份入口。"""
        with self.module.app.app_context():
            self.module.db().execute(
                "update users set role='user' where id=?", (self.worker_id,))
            self.module.db().commit()
        page = self.http.get(f"/users/{self.worker_id}/edit")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f"/users/{self.worker_id}/tax-status", page.get_data(as_text=True))

    def test_users_list_dialog_links_to_tax_status_page(self):
        """用户列表页的编辑弹窗（users.html）也必须显示税务身份入口。"""
        with self.module.app.app_context():
            self.module.db().execute(
                "update users set role='user' where id=?", (self.worker_id,))
            self.module.db().commit()
        page = self.http.get("/users")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f"/users/{self.worker_id}/tax-status", page.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
