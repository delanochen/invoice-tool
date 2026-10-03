"""利润表补贴类成本：餐补 / 复杂日报补贴（v0.1.360）。

背景（2026-10-03 用户报障）：工资里真实发放三类补贴
（employee_payment_components 的 meal_allowance / following_allowance /
report_writing_fee），但利润表以前只认 6 类工时 × 等级费率，餐补和复杂日报
补贴**一行都没有** —— 利润被高估。

口径（与 invoice_tool/payroll/calculations.aggregate_payroll_rows 对齐）：
  - 餐补 = 出勤天数 × employee_grades.meal_daily_amount，同一员工同一工作日
    只算一份（一个人一天可能有多张日报，必须去重，否则重复计成本）；
  - 复杂日报补贴 = 该员工担任 report_writer_id 的日报份数 × 设置里的
    payroll_report_writing_fee。
  - **随行补贴不在这里**：它已经是 travel_hours 的成本（travel_mode=following
    的交通工时 × travel_hours 费率），再补一次就是重复计成本 —— 本测试把它钉住。

两者都不向甲方收费（工单结算单没有对应行），收入恒为 0。

本测试连 PostgreSQL invoice_test（见 conftest.py / tests_pg.py）。
"""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import profitability

REPO_DIR = Path(__file__).resolve().parent
ORDER_NUMBER = "SO-PROFIT-SUB"
DAY_1 = "2026-09-10"          # 周四
DAY_2 = "2026-09-11"          # 周五
RANGE = ("2026-09-01", "2026-09-30")
NOW = "2026-09-10T00:00:00"

MEAL_PER_DAY = 50.0
WRITING_FEE = 20.0
CLIENT_REGULAR_RATE = 100.0
CLIENT_TRAVEL_RATE = 60.0


class ProfitabilitySubsidyCostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_profit_subsidy_app", module_path
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.api = vars(cls.module)

        with cls.module.app.app_context():
            db = cls.module.db()
            name = db.execute("select current_database()").fetchone()[0]
            if name != "invoice_test":
                raise RuntimeError(f"refusing to run against {name!r}")

            uid = db.execute(
                "insert into users (name, email, password_hash, role, created_at, "
                "is_active, region_code, country_code, address, default_language, "
                "phone, phone_verified, preferred_communication_language, "
                "communication_languages) values "
                "('Subsidy Admin','subsidy-admin@example.invalid','x','admin',?,1,'US','US',"
                "'','zh','',0,'zh','zh') returning id",
                (NOW,),
            ).fetchone()["id"]

            client_id = db.execute(
                "insert into clients (client_number, name, short_name, country, created_at) "
                "values ('C-SUB','Subsidy Client','SC','US',?) returning id",
                (NOW,),
            ).fetchone()["id"]
            contract_id = db.execute(
                "insert into contracts (contract_number, client_id, contract_type, title, "
                "status, currency, created_by, created_at, updated_at) "
                "values ('CT-SUB',?,'service','Subsidy','active','USD',?,?,?) returning id",
                (client_id, uid, NOW, NOW),
            ).fetchone()["id"]
            version_id = db.execute(
                "insert into contract_rate_versions (contract_id, version_no, effective_from, "
                "effective_to, status, notes, created_by, created_at, updated_at) "
                "values (?,1,'2026-01-01',NULL,'active','',?,?,?) returning id",
                (contract_id, uid, NOW, NOW),
            ).fetchone()["id"]
            for rate_type, rate in (("regular_hours", CLIENT_REGULAR_RATE),
                                    ("travel_hours", CLIENT_TRAVEL_RATE)):
                db.execute(
                    "insert into contract_rate_items (version_id, rate_type, unit, rate) "
                    "values (?,?,?,?)",
                    (version_id, rate_type, "hour", rate),
                )

            cls.order_id = db.execute(
                "insert into service_orders (order_number, client_name, site_address, "
                "client_order_number, status, created_by, created_at, geocode_status, "
                "region_code, country_code, contract_id) values "
                "(?,'Subsidy Client','1 Subsidy St','PO-SUB','in_progress',?,?,"
                "'pending','US','US',?) returning id",
                (ORDER_NUMBER, uid, NOW, contract_id),
            ).fetchone()["id"]

            # 等级 A：有餐补（50/天）；等级 B：没有餐补（0）
            def make_grade(grade_name, meal_daily_amount):
                return db.execute(
                    "insert into employee_grades (grade_name, description, base_salary, "
                    "meal_daily_amount, car_allowance_method, standard_hourly_rate, "
                    "transport_hourly_rate, is_active, created_at) "
                    "values (?,?,0,?,'mileage',35,20,1,?) returning id",
                    (grade_name, "", meal_daily_amount, NOW),
                ).fetchone()["id"]

            def make_employee(name, grade_id):
                return db.execute(
                    "insert into users (name, email, password_hash, role, created_at, "
                    "is_active, region_code, country_code, address, default_language, "
                    "phone, phone_verified, preferred_communication_language, "
                    "communication_languages, employee_grade_id) values "
                    "(?,?,'x','worker',?,1,'US','US','','zh','',0,'zh','zh',?) returning id",
                    (name, f"subsidy-{grade_id}-{name}@example.invalid", NOW, grade_id),
                ).fetchone()["id"]

            def make_report(day, writer_id):
                return db.execute(
                    "insert into service_reports (service_order_id, report_date, "
                    "actual_work_date, total_service_hours, travel_hours, "
                    "public_transport_hours, driving_miles, report_writer_id, "
                    "created_by, created_at, updated_at, mileage_billing_method) "
                    "values (?,?,?,8,2,0,0,?,?,?,?,'per_person') returning id",
                    (cls.order_id, day, day, writer_id, uid, NOW, NOW),
                ).fetchone()["id"]

            def attach_worker(report_id, employee_id, travel_mode):
                db.execute(
                    "insert into service_report_workers (report_id, user_id, driving_miles, "
                    "travel_mode, travel_hours, public_transport_hours) "
                    "values (?,?,0,?,2,0)",
                    (report_id, employee_id, travel_mode),
                )

            cls.writer = make_employee("Subsidy Writer", make_grade("G-MEAL", MEAL_PER_DAY))
            cls.follower = make_employee("Subsidy Follower", make_grade("G-NO-MEAL", 0))

            # 同一天两张日报（都归 writer 撰写）→ 餐补按天去重只应算一份
            cls.day1_report_a = make_report(DAY_1, cls.writer)
            cls.day1_report_b = make_report(DAY_1, cls.writer)
            # 第二天一张日报，撰写人是 follower → writer 只有 2 份撰写费
            cls.day2_report = make_report(DAY_2, cls.follower)

            for report_id in (cls.day1_report_a, cls.day1_report_b, cls.day2_report):
                attach_worker(report_id, cls.writer, "self_drive")
                attach_worker(report_id, cls.follower, "following")
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        """补贴单价写在 settings 里，每个测试重新落一次（快照恢复后可能不在）。"""
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("delete from settings where key = 'payroll_report_writing_fee'")
            db.execute(
                "insert into settings (key, value) values ('payroll_report_writing_fee', ?)",
                (str(WRITING_FEE),),
            )
            db.commit()

    # ------------------------------------------------------------------ 工具

    def lines(self):
        with self.module.app.app_context():
            return profitability._labor_lines(self.api, RANGE[0], RANGE[1], self.order_id)

    def lines_of(self, item_type, employee_id=None):
        return [
            line for line in self.lines()
            if line["item_type"] == item_type
            and (employee_id is None or line["employee_id"] == employee_id)
        ]

    # ------------------------------------------------------------------ 用例

    def test_meal_allowance_is_counted_once_per_work_day(self):
        """餐补：按「员工 × 工作日」计，同一天两张日报只算一份。"""
        meal_lines = self.lines_of("meal_allowance", self.writer)
        self.assertEqual(len(meal_lines), 2, "两个工作日应只有两条餐补（同日两张日报去重）")
        self.assertEqual({line["work_date"] for line in meal_lines}, {DAY_1, DAY_2})
        for line in meal_lines:
            self.assertEqual(line["quantity"], 1.0)
            self.assertEqual(line["unit"], "day")
            self.assertEqual(line["employee_rate"], MEAL_PER_DAY)
            self.assertEqual(line["cost"], MEAL_PER_DAY)
            self.assertEqual(line["revenue"], 0.0, "客户不承担餐补，收入恒 0")
            self.assertEqual(line["profit"], -MEAL_PER_DAY)
            self.assertEqual(line["category"], "allowance")
            self.assertFalse(line["incomplete"], "餐补不是合同费率项目，不该标缺费率")

    def test_no_meal_allowance_when_grade_amount_is_zero(self):
        """等级餐补为 0 的员工不产生餐补行。"""
        self.assertEqual(self.lines_of("meal_allowance", self.follower), [])

    def test_report_writing_fee_follows_report_writer(self):
        """复杂日报补贴：只给 report_writer_id 本人，按份数 × 设置单价。"""
        # writer 撰写了 day1 的两张日报 → 2 份；day2 那张的撰写人是 follower → 1 份
        fee_lines = self.lines_of("report_writing_fee", self.writer)
        self.assertEqual(len(fee_lines), 2, "writer 撰写了 2 份日报")
        self.assertEqual(len(self.lines_of("report_writing_fee", self.follower)), 1)
        for line in fee_lines:
            self.assertEqual(line["quantity"], 1.0)
            self.assertEqual(line["unit"], "report")
            self.assertEqual(line["employee_rate"], WRITING_FEE)
            self.assertEqual(line["cost"], WRITING_FEE)
            self.assertEqual(line["revenue"], 0.0)
        self.assertEqual(
            {line["source_id"] for line in fee_lines},
            {self.day1_report_a, self.day1_report_b},
        )

    def test_following_allowance_is_not_double_counted(self):
        """随行补贴已经算在 travel_hours 里，绝不能再出现 following_allowance 行。"""
        self.assertEqual(self.lines_of("following_allowance"), [])
        travel_lines = self.lines_of("travel_hours", self.follower)
        self.assertTrue(travel_lines, "随行员工仍应有 travel_hours 利润行")
        self.assertTrue(all(line["item_type"] == "travel_hours" for line in travel_lines))

    def test_subsidy_cost_lands_in_summary(self):
        """补贴成本进 allowance 分类：餐补 2×50 + 撰稿 3×20 = 160。"""
        with self.module.app.app_context():
            summary = profitability._summary(
                profitability.build_profit_lines(self.api, RANGE[0], RANGE[1], self.order_id)
            )
        allowance = summary["by_category"]["allowance"]
        subsidy_cost = 2 * MEAL_PER_DAY + 3 * WRITING_FEE
        self.assertGreaterEqual(allowance["cost"], subsidy_cost)
        self.assertEqual(allowance["revenue"] - allowance["cost"], allowance["profit"])


if __name__ == "__main__":
    unittest.main()
