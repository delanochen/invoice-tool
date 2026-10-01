"""Phase 2A：生成 SL 时冻结 employee_payment_components 的回归测试。

覆盖：普通工资 / 加班 / 随行 / 租车驾驶 / 里程佐证有无 / 日报撰写费 / 餐补 /
多工单 / 多 service_date / 跨税务身份周期 / 缺税务身份 / 缺分类规则 /
component sum == gross / 校验失败整体回滚 / 重复生成不重复快照 / ledger 不受影响。
"""
import importlib.util
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
PERIOD_START = "2026-08-03"  # 周一；周期 2026-08-03 ~ 2026-08-16


def dec(value):
    """database.py 读回 numeric 会转 float，比较前统一 Decimal(str(x))。"""
    return value if isinstance(value, Decimal) else Decimal(str(value))

CONFIG_SEED = [
    ("standard_pay", "标准工资", "taxable_compensation", 0),
    ("overtime_pay", "加班工资", "taxable_compensation", 0),
    ("holiday_pay", "假期工资", "taxable_compensation", 0),
    ("following_allowance", "随行补贴", "taxable_compensation", 0),
    ("rental_driving_allowance", "租车驾驶补贴", "taxable_compensation", 0),
    ("self_drive_allowance", "自驾车补", "accountable_reimbursement", 1),
    ("report_writing_fee", "报告撰写费", "taxable_compensation", 0),
    ("base_salary", "基本工资", "taxable_compensation", 0),
    # meal_allowance 故意不 seed：走「缺规则 → tax_review_required」路径。
]


class PayrollTaxComponentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("payroll_tax_component_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="tax-component-secret")
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
                "employee_payment_orders", "employee_salary_agreements", "worker_tax_status_history",
                "service_report_mileage_evidence", "service_report_workers", "service_reports",
                "service_orders", "audit_logs", "users", "employee_grades",
            ):
                db.execute(f"delete from {table}")
            self.admin_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Admin','tax-admin@test.invalid','unused','admin',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.grade_id = db.execute(
                """
                insert into employee_grades (grade_name, base_salary, standard_hourly_rate,
                    transport_hourly_rate, overtime_hourly_rate, holiday_hourly_rate,
                    meal_daily_amount, car_mileage_rate, rental_driving_hourly_rate,
                    is_active, created_at)
                values ('TaxGrade', 500, 20, 10, 30, 40, 15, 0.6, 15, 1, ?)
                """,
                (self.module.now(),),
            ).lastrowid
            self.worker_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,employee_grade_id,created_at)"
                " values ('Worker','worker@test.invalid','unused','employee',1,?,?)",
                (self.grade_id, self.module.now()),
            ).lastrowid
            self.order1 = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,"
                "start_date,created_by,created_at) values ('SO-TAX-1','Client','Site','C1','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            self.order2 = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,"
                "start_date,created_by,created_at) values ('SO-TAX-2','Client','Site','C2','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            for code, name, category, requires in CONFIG_SEED:
                db.execute(
                    "insert into payroll_component_tax_config (component_code,display_name,"
                    "default_tax_category,requires_substantiation,effective_from,effective_to,"
                    "is_active,created_at,updated_at) values (?,?,?,?, '2020-01-01', null, 1, ?, ?)",
                    (code, name, category, requires, self.module.now(), self.module.now()),
                )
            db.commit()
        self.http = self.module.app.test_client()
        self._login(self.admin_id)

    def _login(self, user_id):
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    def _seed_tax_status(self, employee_id, segments):
        """segments: [(tax_status, effective_from, effective_to|None)]"""
        with self.module.app.app_context():
            db = self.module.db()
            for status, start, end in segments:
                db.execute(
                    "insert into worker_tax_status_history (employee_id,tax_status,effective_from,"
                    "effective_to,notes,created_by,created_at) values (?,?,?,?,?,?,?)",
                    (employee_id, status, start, end, "test", self.admin_id, self.module.now()),
                )
            db.commit()

    def _report(self, order_id, work_date, travel_mode="", miles=0, travel_hours=0,
                service_hours=8, writer=True):
        with self.module.app.app_context():
            db = self.module.db()
            report_id = db.execute(
                """
                insert into service_reports (service_order_id, report_date, actual_work_date,
                    total_service_hours, report_writer_id, created_by, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id, work_date, work_date, service_hours,
                 self.worker_id if writer else None, self.admin_id,
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.execute(
                """
                insert into service_report_workers (report_id, user_id, driving_miles,
                    travel_mode, travel_hours, public_transport_hours, work_description)
                values (?, ?, ?, ?, ?, 0, 'test')
                """,
                (report_id, self.worker_id, miles, travel_mode or "legacy", travel_hours),
            )
            db.commit()
            return report_id

    def _add_mileage_evidence(self, report_id):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into service_report_mileage_evidence (report_id,worker_user_id,"
                "route_fingerprint,status,generated_at) values (?,?,'fp-test','success',?)",
                (report_id, self.worker_id, self.module.now()),
            )
            db.commit()

    def _generate(self):
        response = self.http.post(
            "/employee-payments/generate-payroll", data={"period_start": PERIOD_START})
        self.assertEqual(response.status_code, 302)
        return self.http.get(response.headers["Location"])

    def _payment(self):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select * from employee_payment_orders order by id desc limit 1").fetchone()
            return dict(row) if row else None

    def _components(self, payment_id):
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select * from employee_payment_components where payment_order_id=? order by id",
                (payment_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------

    def test_standard_overtime_writer_meal_base_snapshot_and_totals(self):
        """普通工资 + 加班 + 撰写费 + 餐补 + 基本工资；多 service_date；sum==gross。"""
        self._seed_tax_status(self.worker_id, [("W2", "2026-07-01", None)])
        self._report(self.order1, "2026-08-03", service_hours=8, writer=True)
        self._report(self.order2, "2026-08-04", service_hours=10, writer=True)
        self._generate()
        payment = self._payment()
        self.assertEqual(dec(payment["gross_amount"]), Decimal("950.00"))
        lines = self._components(payment["id"])
        by_code = {}
        for line in lines:
            by_code.setdefault(line["component_code"], []).append(line)
        self.assertEqual(
            sorted(by_code), ["base_salary", "meal_allowance", "overtime_pay",
                              "report_writing_fee", "standard_pay"])
        std = {line["service_date"]: line for line in by_code["standard_pay"]}
        self.assertEqual(set(std), {"2026-08-03", "2026-08-04"})
        for line in std.values():
            self.assertEqual(dec(line["amount"]), Decimal("160.00"))
            self.assertEqual(dec(line["quantity"]), Decimal("8"))
            self.assertEqual(line["unit"], "hours")
            self.assertEqual(dec(line["unit_rate"]), Decimal("20.00"))
            self.assertEqual(line["tax_category"], "taxable_compensation")
            self.assertEqual(line["tax_status_snapshot"], "W2")
            self.assertEqual(line["review_status"], "confirmed")
        overtime = by_code["overtime_pay"][0]
        self.assertEqual(dec(overtime["amount"]), Decimal("60.00"))
        self.assertEqual(dec(overtime["quantity"]), Decimal("2"))
        self.assertEqual(dec(overtime["unit_rate"]), Decimal("30.00"))
        meal = {line["service_date"]: line for line in by_code["meal_allowance"]}
        self.assertEqual(set(meal), {"2026-08-03", "2026-08-04"})
        for line in meal.values():
            self.assertEqual(dec(line["amount"]), Decimal("15.00"))
            self.assertEqual(line["tax_category"], "tax_review_required")
            self.assertEqual(line["review_status"], "review_required")
        writer = {line["service_date"]: line for line in by_code["report_writing_fee"]}
        self.assertEqual(set(writer), {"2026-08-03", "2026-08-04"})
        for line in writer.values():
            self.assertEqual(dec(line["amount"]), Decimal("20.00"))
            self.assertIsNotNone(line["daily_report_id"])
        base = by_code["base_salary"][0]
        self.assertEqual(dec(base["amount"]), Decimal("500.00"))
        self.assertIsNone(base["service_date"])
        self.assertEqual(base["unit"], "period")
        # 工单归属正确
        self.assertEqual(std["2026-08-03"]["work_order_id"], self.order1)
        self.assertEqual(std["2026-08-04"]["work_order_id"], self.order2)
        # 三项税务合计与硬校验
        self.assertEqual(dec(payment["taxable_compensation_total"]), Decimal("920.00"))
        self.assertEqual(dec(payment["accountable_reimbursement_total"]), Decimal("0.00"))
        self.assertEqual(dec(payment["tax_review_required_total"]), Decimal("30.00"))
        self.assertEqual(
            dec(payment["taxable_compensation_total"]) + dec(payment["accountable_reimbursement_total"])
            + dec(payment["tax_review_required_total"]),
            dec(payment["gross_amount"]),
        )
        self.assertEqual(sum(dec(line["amount"]) for line in lines), dec(payment["gross_amount"]))

    def test_mileage_with_evidence_is_accountable_without_is_review(self):
        """里程：有佐证 → accountable + substantiated；无佐证 → review + 未证实。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        with_evidence = self._report(self.order1, "2026-08-03", travel_mode="self_drive",
                                     miles=100, travel_hours=0, writer=False)
        self._add_mileage_evidence(with_evidence)
        self._report(self.order2, "2026-08-04", travel_mode="self_drive",
                     miles=50, travel_hours=0, writer=False)
        self._generate()
        payment = self._payment()
        lines = self._components(payment["id"])
        mileage = {line["service_date"]: line for line in lines
                   if line["component_code"] == "self_drive_allowance"}
        self.assertEqual(set(mileage), {"2026-08-03", "2026-08-04"})
        good, bad = mileage["2026-08-03"], mileage["2026-08-04"]
        self.assertEqual(dec(good["amount"]), Decimal("60.00"))
        self.assertEqual(dec(good["quantity"]), Decimal("100"))
        self.assertEqual(good["unit"], "miles")
        self.assertEqual(dec(good["unit_rate"]), Decimal("0.60"))
        self.assertEqual(good["tax_category"], "accountable_reimbursement")
        self.assertTrue(good["substantiated"])
        self.assertEqual(good["review_status"], "confirmed")
        self.assertEqual(dec(bad["amount"]), Decimal("30.00"))
        self.assertEqual(bad["tax_category"], "tax_review_required")
        self.assertFalse(bad["substantiated"])
        self.assertEqual(bad["review_status"], "review_required")
        self.assertEqual(dec(payment["accountable_reimbursement_total"]), Decimal("60.00"))
        self.assertEqual(dec(payment["tax_review_required_total"]), Decimal("60.00"))
        self.assertEqual(dec(payment["taxable_compensation_total"]), Decimal("820.00"))
        self.assertEqual(
            dec(payment["taxable_compensation_total"]) + dec(payment["accountable_reimbursement_total"])
            + dec(payment["tax_review_required_total"]),
            dec(payment["gross_amount"]),
        )

    def test_following_and_rental_allowances_are_taxable(self):
        self._seed_tax_status(self.worker_id, [("W2", "2026-01-01", None)])
        self._report(self.order1, "2026-08-03", travel_mode="following", travel_hours=3,
                     writer=False)
        self._report(self.order2, "2026-08-03", travel_mode="rental_drive", travel_hours=2,
                     writer=False)
        self._generate()
        payment = self._payment()
        lines = self._components(payment["id"])
        following = next(line for line in lines if line["component_code"] == "following_allowance")
        rental = next(line for line in lines if line["component_code"] == "rental_driving_allowance")
        self.assertEqual(dec(following["amount"]), Decimal("30.00"))
        self.assertEqual(dec(following["unit_rate"]), Decimal("10.00"))
        self.assertEqual(following["tax_category"], "taxable_compensation")
        self.assertEqual(dec(rental["amount"]), Decimal("30.00"))
        self.assertEqual(dec(rental["unit_rate"]), Decimal("15.00"))
        self.assertEqual(rental["tax_category"], "taxable_compensation")
        # 同一天两个工单：组件各自带工单 id，餐补只落一行
        self.assertEqual(following["work_order_id"], self.order1)
        self.assertEqual(rental["work_order_id"], self.order2)
        meal = [line for line in lines if line["component_code"] == "meal_allowance"]
        self.assertEqual(len(meal), 1)
        self.assertEqual(dec(payment["tax_review_required_total"]), Decimal("15.00"))

    def test_cross_identity_period_splits_snapshot_by_service_date(self):
        """周期跨 1099→W2 切换日：按 service_date 分别保存身份快照。"""
        self._seed_tax_status(self.worker_id, [
            ("1099", "2026-01-01", "2026-08-03"),
            ("W2", "2026-08-04", None),
        ])
        self._report(self.order1, "2026-08-03", service_hours=8, writer=False)
        self._report(self.order2, "2026-08-04", service_hours=8, writer=False)
        self._generate()
        payment = self._payment()
        lines = self._components(payment["id"])
        by_date = {line["service_date"]: line for line in lines
                   if line["component_code"] == "standard_pay"}
        self.assertEqual(by_date["2026-08-03"]["tax_status_snapshot"], "1099")
        self.assertEqual(by_date["2026-08-04"]["tax_status_snapshot"], "W2")
        for line in by_date.values():
            self.assertEqual(line["tax_category"], "taxable_compensation")

    def test_missing_tax_status_forces_review_and_null_snapshot(self):
        """没有税务身份历史：不许猜 —— snapshot NULL + 全部 review_required。"""
        self._report(self.order1, "2026-08-03", service_hours=8, writer=False)
        self._generate()
        payment = self._payment()
        lines = self._components(payment["id"])
        self.assertTrue(lines)
        for line in lines:
            self.assertIsNone(line["tax_status_snapshot"])
            self.assertEqual(line["tax_category"], "tax_review_required")
        self.assertEqual(payment["taxable_compensation_total"], Decimal("0.00"))
        self.assertEqual(dec(payment["taxable_compensation_total"])
                         + dec(payment["accountable_reimbursement_total"])
                         + dec(payment["tax_review_required_total"]),
                         dec(payment["gross_amount"]))

    def test_missing_tax_config_rule_forces_review(self):
        """缺分类规则（如餐补未配置）→ review_required，不影响其他组件。"""
        self._seed_tax_status(self.worker_id, [("W2", "2026-01-01", None)])
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("delete from payroll_component_tax_config where component_code='standard_pay'")
            db.commit()
        self._report(self.order1, "2026-08-03", service_hours=8, writer=False)
        self._generate()
        payment = self._payment()
        lines = self._components(payment["id"])
        standard = next(line for line in lines if line["component_code"] == "standard_pay")
        self.assertEqual(standard["tax_category"], "tax_review_required")
        self.assertEqual(standard["component_name"], "标准工资")  # 兜底展示名
        meal = next(line for line in lines if line["component_code"] == "meal_allowance")
        self.assertEqual(meal["tax_category"], "tax_review_required")
        base = next(line for line in lines if line["component_code"] == "base_salary")
        self.assertEqual(base["tax_category"], "taxable_compensation")

    def test_component_sum_mismatch_rolls_back_whole_generation(self):
        """组件合计与 gross 不一致：整个生成操作回滚，不留任何单据/快照。"""
        self._seed_tax_status(self.worker_id, [("W2", "2026-01-01", None)])
        self._report(self.order1, "2026-08-03", service_hours=8, writer=False)
        from invoice_tool.payroll import services as payroll_services
        original = payroll_services.build_payment_components

        def tampered(*args, **kwargs):
            rows, totals = original(*args, **kwargs)
            for line in rows:
                if line["component_code"] == "standard_pay":
                    line["amount"] = line["amount"] + Decimal("10.00")  # 绕过构建期校验后破坏合计
            return rows, totals

        with patch.object(payroll_services, "build_payment_components", side_effect=tampered):
            response = self._generate()  # ValueError 被路由捕获 → flash → redirect，不 500
        self.assertEqual(response.status_code, 200)
        with self.module.app.app_context():
            db = self.module.db()
            self.assertEqual(db.execute("select count(*) from employee_payment_orders").fetchone()[0], 0)
            self.assertEqual(
                db.execute("select count(*) from employee_payment_components").fetchone()[0], 0)
            self.assertEqual(
                db.execute("select count(*) from payment_order_sources").fetchone()[0], 0)

    def test_duplicate_generation_does_not_duplicate_components(self):
        self._seed_tax_status(self.worker_id, [("W2", "2026-01-01", None)])
        self._report(self.order1, "2026-08-03", service_hours=8, writer=False)
        self._generate()
        first = self._payment()
        first_lines = self._components(first["id"])
        self._generate()
        with self.module.app.app_context():
            db = self.module.db()
            self.assertEqual(
                db.execute("select count(*) from employee_payment_orders").fetchone()[0], 1)
            self.assertEqual(
                db.execute("select count(*) from employee_payment_components").fetchone()[0],
                len(first_lines))
        self.assertEqual(self._payment()["id"], first["id"])
        self.assertEqual(self._components(first["id"]), first_lines)

    def test_sub_cent_components_are_allocated_to_match_gross(self):
        """亚分原始金额：分位残差确定性摊到原始金额最大的组件，sum 恒等于 gross。

        场景：等级时薪 33.33，两份 0.5h 日报 → 每份 16.665，总额 33.33；
        逐条量化 16.67×2 = 33.34，比 gross 多 1 分 → 摊给原始金额最大的
        base_salary（500.00 → 499.99）。算法/数据漂移仍由 raw 校验拦下，
        这里只验证纯分位分配不阻断生成。
        """
        self._seed_tax_status(self.worker_id, [("W2", "2026-01-01", None)])
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update employee_grades set standard_hourly_rate=33.33 where id=?", (self.grade_id,))
            db.commit()
        self._report(self.order1, "2026-08-03", service_hours=0.5, writer=False)
        self._report(self.order2, "2026-08-04", service_hours=0.5, writer=False)
        self._generate()
        payment = self._payment()
        lines = self._components(payment["id"])
        self.assertEqual(dec(payment["gross_amount"]), Decimal("563.33"))
        self.assertEqual(sum(dec(line["amount"]) for line in lines), dec(payment["gross_amount"]))
        base = next(line for line in lines if line["component_code"] == "base_salary")
        self.assertEqual(dec(base["amount"]), Decimal("499.99"))
        for line in lines:
            if line["component_code"] == "standard_pay":
                self.assertEqual(dec(line["amount"]), Decimal("16.67"))
        self.assertEqual(dec(payment["taxable_compensation_total"]), Decimal("533.33"))
        self.assertEqual(dec(payment["tax_review_required_total"]), Decimal("30.00"))

    def test_offline_salary_agreement_produces_single_review_component(self):
        """线下约定工资：不可分解 → 单行 offline_salary → review_required。"""
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into employee_salary_agreements (employee_id,amount,currency,effective_from,"
                "effective_to,replaces_system_payroll,notes,is_active,created_by,created_at,updated_at)"
                " values (?, 3000, 'USD', '2026-08-01', null, 1, 'test', 1, ?, ?, ?)",
                (self.worker_id, self.admin_id, self.module.now(), self.module.now()),
            )
            db.commit()
        self._seed_tax_status(self.worker_id, [("W2", "2026-01-01", None)])
        self._report(self.order1, "2026-08-03", service_hours=8, writer=False)
        self._generate()
        payment = self._payment()
        self.assertEqual(payment["source_type"], "offline_salary")
        self.assertEqual(dec(payment["gross_amount"]), Decimal("3000.00"))
        lines = self._components(payment["id"])
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["component_code"], "offline_salary")
        self.assertEqual(dec(line["amount"]), Decimal("3000.00"))
        self.assertEqual(line["tax_category"], "tax_review_required")
        self.assertEqual(line["source_type"], "payroll_generation")
        self.assertEqual(
            dec(payment["tax_review_required_total"]), dec(payment["gross_amount"]))

    def test_employee_ledger_and_detail_still_work(self):
        """既有流程不受影响：ledger 列表、付款单详情、来源映射都在。"""
        self._seed_tax_status(self.worker_id, [("W2", "2026-01-01", None)])
        self._report(self.order1, "2026-08-03", service_hours=8, writer=False)
        self._generate()
        payment = self._payment()
        self.assertIn(payment["payment_number"],
                      self.http.get("/employee-payments").get_data(as_text=True))
        detail = self.http.get(f"/employee-payments/{payment['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(payment["payment_number"], detail.get_data(as_text=True))
        with self.module.app.app_context():
            db = self.module.db()
            sources = db.execute(
                "select count(*) from payment_order_sources where payment_order_id=?",
                (payment["id"],)).fetchone()[0]
            self.assertEqual(sources, 1)
            events = db.execute(
                "select count(*) from payment_order_events where payment_order_id=?",
                (payment["id"],)).fetchone()[0]
            self.assertEqual(events, 1)


if __name__ == "__main__":
    unittest.main()
