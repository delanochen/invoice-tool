"""Phase 2C：历史 SL 组件回填工具回归（在 invoice_test 上完整模拟）。

覆盖：dry-run 不写数据 / --commit 写入 provenance=historical_recompute /
三项合计落库且 sum==gross / 重复运行幂等跳过 / 原金额状态批次关系不变。
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL = ROOT / "scripts" / "backfill_sl_tax_components.py"
PERIOD_START = "2026-08-03"


def dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


class BackfillSlTaxComponentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("backfill_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="backfill-secret")
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
                " values ('Admin','bf-admin@test.invalid','unused','admin',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.grade_id = db.execute(
                "insert into employee_grades (grade_name,base_salary,standard_hourly_rate,"
                "meal_daily_amount,is_active,created_at) values ('BFGrade',500,20,0,1,?)",
                (self.module.now(),),
            ).lastrowid
            self.worker_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,employee_grade_id,created_at)"
                " values ('Worker','bf-worker@test.invalid','unused','employee',1,?,?)",
                (self.grade_id, self.module.now()),
            ).lastrowid
            self.order1 = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,"
                "start_date,created_by,created_at) values ('SO-BF','C','S','C1','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            for code, name, category, requires in (
                ("standard_pay", "标准工资", "taxable_compensation", 0),
                ("overtime_pay", "加班工资", "taxable_compensation", 0),
                ("holiday_pay", "假期工资", "taxable_compensation", 0),
                ("following_allowance", "随行补贴", "taxable_compensation", 0),
                ("rental_driving_allowance", "租车驾驶补贴", "taxable_compensation", 0),
                ("self_drive_allowance", "自驾车补", "accountable_reimbursement", 1),
                ("report_writing_fee", "报告撰写费", "taxable_compensation", 0),
                ("base_salary", "基本工资", "taxable_compensation", 0),
            ):
                db.execute(
                    "insert into payroll_component_tax_config (component_code,display_name,"
                    "default_tax_category,requires_substantiation,effective_from,effective_to,"
                    "is_active,created_at,updated_at) values (?,?,?,?, '2020-01-01', null, 1, ?, ?)",
                    (code, name, category, requires, self.module.now(), self.module.now()),
                )
            report_id = db.execute(
                "insert into service_reports (service_order_id,report_date,actual_work_date,"
                "total_service_hours,created_by,created_at,updated_at)"
                " values (?,'2026-08-03','2026-08-03',8,?,'t','t')",
                (self.order1, self.admin_id)).lastrowid
            db.execute(
                "insert into service_report_workers (report_id,user_id,travel_mode,"
                "public_transport_hours,work_description) values (?,?,'legacy',0,'t')",
                (report_id, self.worker_id))
            db.execute(
                "insert into worker_tax_status_history (employee_id,tax_status,effective_from,"
                "effective_to,notes,created_by,created_at) values (?, 'W2', '2026-01-01', null,"
                " 'test', ?, ?)", (self.worker_id, self.admin_id, self.module.now()))
            db.commit()
        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = self.admin_id

    def _run_tool(self, *flags):
        import os
        result = subprocess.run(
            [sys.executable, str(TOOL), *flags],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def _payment(self):
        with self.module.app.app_context():
            return dict(self.module.db().execute(
                "select * from employee_payment_orders order by id desc limit 1").fetchone())

    def _components(self):
        with self.module.app.app_context():
            return [dict(row) for row in self.module.db().execute(
                "select * from employee_payment_components order by id").fetchall()]

    def test_dry_run_then_commit_then_idempotent(self):
        # 直接插入一张「0291 之前」样式的 SL：有单、无组件（2A 之后路由生成的
        # SL 会当场写快照，所以要绕开路由，手工模拟历史数据形态）。
        with self.module.app.app_context():
            db = self.module.db()
            payment_id = db.execute(
                """
                insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,description,source_type,
                 source_number,source_key,sync_source,sync_status,created_by,created_at,updated_at)
                values ('SL-TEST-0001',?, 'salary','draft','USD',660.00,0,0,660.00,
                        '历史样式工资单','system_payroll','2026-08-03~2026-08-16',
                        'payroll:system_payroll:?','manual','local',?,'t','t')
                """.replace("'payroll:system_payroll:?'", "'payroll:system_payroll:%s'" % self.worker_id),
                (self.worker_id, self.admin_id),
            ).lastrowid
            db.commit()
        payment = self._payment()
        self.assertEqual(dec(payment["gross_amount"]), Decimal("660.00"))
        self.assertEqual(self._components(), [])
        gross_before = dec(payment["gross_amount"])
        status_before = payment["status"]

        # dry-run：不写任何数据
        out = self._run_tool()
        self.assertIn("DRY-RUN", out)
        self.assertIn("verified: 1", out)
        self.assertEqual(self._components(), [])
        self.assertEqual(dec(self._payment()["gross_amount"]), gross_before)

        # 正式写入
        out = self._run_tool("--commit")
        self.assertIn("backfilled: 1", out)
        lines = self._components()
        self.assertTrue(lines)
        self.assertEqual(sum(dec(line["amount"]) for line in lines), gross_before)
        for line in lines:
            self.assertEqual(line["source_type"], "historical_recompute")
            self.assertEqual(line["tax_status_snapshot"], "W2")
            self.assertEqual(line["review_status"], "confirmed")
        payment_after = self._payment()
        self.assertEqual(dec(payment_after["taxable_compensation_total"]),
                         gross_before)  # 全部组件 taxable（无餐补、无里程）
        self.assertEqual(dec(payment_after["accountable_reimbursement_total"]), Decimal("0"))
        self.assertEqual(dec(payment_after["tax_review_required_total"]), Decimal("0"))
        # 金额 / 状态 / 来源完全不变
        self.assertEqual(dec(payment_after["gross_amount"]), gross_before)
        self.assertEqual(payment_after["status"], status_before)
        self.assertEqual(payment_after["source_type"], "system_payroll")
        self.assertIsNone(payment_after["batch_id"])

        # 重复运行：候选为 0，组件不变
        out = self._run_tool("--commit")
        self.assertIn("candidate SL rows: 0", out)
        self.assertEqual(len(self._components()), len(lines))
        self.assertEqual(dec(self._payment()["gross_amount"]), gross_before)


if __name__ == "__main__":
    unittest.main()
