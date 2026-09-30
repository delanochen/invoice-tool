"""利润表「缺费率」判定：明确维护成 0 ≠ 没维护（v0.1.341）。

背景（2026-09-29 用户报障）：W1 外籍员工等级**不拿交通补贴**，所以费率版本里
travel_hours / mileage 等条目是**明确填的 0**。旧逻辑用 ``rate <= 0`` 当
「缺费率」的判据，于是这些行全被标成 incomplete，利润表顶部弹「部分行缺费率」
红条 —— 用户实际是维护过的，只是维护成 0。

新口径（用户拍板）：
  - 员工等级静态费率列默认 **NULL**（不再是 0）：NULL = 从没维护过；
  - 明确维护成 0（版本条目为 0，或静态列填 0）是合法口径，**不算缺费率**；
  - 判定只看 rate_engine 返回的 ``source``，不再看数值大小。

本测试连 PostgreSQL invoice_test（见 conftest.py / tests_pg.py）。
"""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import profitability

REPO_DIR = Path(__file__).resolve().parent
ORDER_NUMBER = "SO-RATE-ZERO"
DAY = "2026-09-10"          # 周四，非美国节假日 → 走「标准工时」分支
RANGE = ("2026-09-01", "2026-09-30")
NOW = "2026-09-10T00:00:00"
CLIENT_TRAVEL_RATE = 60.0   # 客户侧交通工时单价（保证客户费率不缺，只考员工侧）

# 三个等级：交通补贴费率分别为「版本里填 0」「静态列填 0」「静态列 NULL（没维护）」
GRADE_ZERO_VERSION = "W1 版本填0"
GRADE_ZERO_STATIC = "W2 静态填0"
GRADE_NULL_STATIC = "W3 没维护"


class ProfitabilityZeroRateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_profit_zero_rate_app", module_path
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
                "('ZeroRate Admin','zero-rate@example.invalid','x','admin',?,1,'US','US',"
                "'','zh','',0,'zh','zh') returning id",
                (NOW,),
            ).fetchone()["id"]

            client_id = db.execute(
                "insert into clients (client_number, name, short_name, country, created_at) "
                "values ('C-ZERO','Zero Rate Client','ZRC','US',?) returning id",
                (NOW,),
            ).fetchone()["id"]
            contract_id = db.execute(
                "insert into contracts (contract_number, client_id, contract_type, title, "
                "status, currency, created_by, created_at, updated_at) "
                "values ('CT-ZERO',?,'service','Zero Rate','active','USD',?,?,?) returning id",
                (client_id, uid, NOW, NOW),
            ).fetchone()["id"]
            version_id = db.execute(
                "insert into contract_rate_versions (contract_id, version_no, effective_from, "
                "effective_to, status, notes, created_by, created_at, updated_at) "
                "values (?,1,'2026-01-01',NULL,'active','',?,?,?) returning id",
                (contract_id, uid, NOW, NOW),
            ).fetchone()["id"]
            for rate_type, rate in (("travel_hours", CLIENT_TRAVEL_RATE),
                                    ("regular_hours", 100.0)):
                db.execute(
                    "insert into contract_rate_items (version_id, rate_type, unit, rate) "
                    "values (?,?,?,?)",
                    (version_id, rate_type, "hour", rate),
                )

            cls.order_id = db.execute(
                "insert into service_orders (order_number, client_name, site_address, "
                "client_order_number, status, created_by, created_at, geocode_status, "
                "region_code, country_code, contract_id) values "
                "(?,'Zero Rate Client','1 Zero St','PO-ZERO','in_progress',?,?,"
                "'pending','US','US',?) returning id",
                (ORDER_NUMBER, uid, NOW, contract_id),
            ).fetchone()["id"]

            report_id = db.execute(
                "insert into service_reports (service_order_id, report_date, "
                "actual_work_date, total_service_hours, travel_hours, "
                "public_transport_hours, driving_miles, created_by, created_at, "
                "updated_at, mileage_billing_method) "
                "values (?,?,?,8,2,0,0,?,?,?,'per_person') returning id",
                (cls.order_id, DAY, DAY, uid, NOW, NOW),
            ).fetchone()["id"]

            # 三个员工各挂一个等级，同一份日报上都是「随行 2 小时交通」
            cls.employees = {}
            for grade_name, employee_name in (
                (GRADE_ZERO_VERSION, "Zero Version"),
                (GRADE_ZERO_STATIC, "Zero Static"),
                (GRADE_NULL_STATIC, "Null Static"),
            ):
                grade_id = db.execute(
                    "insert into employee_grades (grade_name, description, base_salary, "
                    "meal_daily_amount, car_allowance_method, is_active, created_at) "
                    "values (?,?,0,0,'mileage',1,?) returning id",
                    (grade_name, "", NOW),
                ).fetchone()["id"]
                if grade_name == GRADE_ZERO_VERSION:
                    # 版本里把交通工时明确写成 0（W1 外籍员工：不拿交通补贴）
                    vid = db.execute(
                        "insert into employee_rate_versions (employee_grade_id, version_no, "
                        "effective_from, effective_to, status, notes, created_by, "
                        "created_at, updated_at) values (?,1,'2026-01-01',NULL,'active','',?,?,?) "
                        "returning id",
                        (grade_id, uid, NOW, NOW),
                    ).fetchone()["id"]
                    for rate_type, rate in (("travel_hours", 0.0),
                                            ("public_transport_hours", 0.0),
                                            ("mileage", 0.0),
                                            ("regular_hours", 35.0)):
                        db.execute(
                            "insert into employee_rate_items (version_id, rate_type, unit, rate) "
                            "values (?,?,?,?)",
                            (vid, rate_type, "hour" if rate_type != "mileage" else "mile", rate),
                        )
                elif grade_name == GRADE_ZERO_STATIC:
                    # 没有版本，静态交通时薪明确填 0
                    db.execute(
                        "update employee_grades set transport_hourly_rate = 0, "
                        "standard_hourly_rate = 35 where id = ?",
                        (grade_id,),
                    )
                else:
                    # 没有版本，静态交通时薪没维护（NULL）
                    db.execute(
                        "update employee_grades set standard_hourly_rate = 35 where id = ?",
                        (grade_id,),
                    )

                employee_id = db.execute(
                    "insert into users (name, email, password_hash, role, created_at, "
                    "is_active, region_code, country_code, address, default_language, "
                    "phone, phone_verified, preferred_communication_language, "
                    "communication_languages, employee_grade_id) values "
                    "(?,?,'x','worker',?,1,'US','US','','zh','',0,'zh','zh',?) returning id",
                    (employee_name, f"zero-rate-{grade_id}@example.invalid", NOW, grade_id),
                ).fetchone()["id"]
                db.execute(
                    "insert into service_report_workers (report_id, user_id, driving_miles, "
                    "travel_mode, travel_hours, public_transport_hours) "
                    "values (?,?,0,'following',2,0)",
                    (report_id, employee_id),
                )
                cls.employees[grade_name] = employee_id
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    # ------------------------------------------------------------------ 工具

    def travel_line(self, employee_id):
        """取该员工在日报上那条「交通工时」利润行。"""
        with self.module.app.app_context():
            lines = profitability._labor_lines(self.api, RANGE[0], RANGE[1], self.order_id)
        for line in lines:
            if line["employee_id"] == employee_id and line["item_type"] == "travel_hours":
                return line
        self.fail(f"员工 {employee_id} 没有交通工时利润行")

    def employee_rate_entry(self, employee_id):
        with self.module.app.app_context():
            return profitability.employee_rate(
                self.module.db(), employee_id, DAY, "travel_hours"
            )

    # ------------------------------------------------------------------ 用例

    def test_version_zero_travel_rate_is_not_incomplete(self):
        """费率版本里 travel_hours = 0（W1 场景）：已维护，不标缺费率。"""
        line = self.travel_line(self.employees[GRADE_ZERO_VERSION])
        self.assertEqual(line["quantity"], 2.0)
        self.assertEqual(line["employee_rate"], 0.0)
        self.assertEqual(line["employee_rate_source"], "employee_rate_version")
        self.assertEqual(line["client_rate"], CLIENT_TRAVEL_RATE)
        self.assertFalse(line["incomplete"], "维护成 0 的费率不该被标成缺费率")

    def test_static_zero_travel_rate_is_not_incomplete(self):
        """静态交通时薪明确填 0（没建版本）：同样是「维护成 0」，不标缺费率。"""
        entry = self.employee_rate_entry(self.employees[GRADE_ZERO_STATIC])
        self.assertEqual(entry["rate"], 0.0)
        self.assertEqual(entry["source"], "legacy_employee_grade")
        line = self.travel_line(self.employees[GRADE_ZERO_STATIC])
        self.assertFalse(line["incomplete"], "静态列填 0 是维护过，不是缺费率")

    def test_null_static_travel_rate_is_incomplete(self):
        """静态交通时薪是 NULL（从没维护过）：必须标成缺费率。"""
        entry = self.employee_rate_entry(self.employees[GRADE_NULL_STATIC])
        self.assertEqual(entry["rate"], 0.0)
        self.assertEqual(entry["source"], "missing")
        line = self.travel_line(self.employees[GRADE_NULL_STATIC])
        self.assertTrue(line["incomplete"], "没维护的费率必须标成缺费率")

    def test_zero_and_null_share_the_same_rate_number(self):
        """0 与 NULL 取出来的数值都是 0 —— 区别只在 source，数值不能用来判定。"""
        zero = self.employee_rate_entry(self.employees[GRADE_ZERO_STATIC])
        null = self.employee_rate_entry(self.employees[GRADE_NULL_STATIC])
        self.assertEqual(zero["rate"], null["rate"])
        self.assertNotEqual(zero["source"], null["source"])


if __name__ == "__main__":
    unittest.main()
