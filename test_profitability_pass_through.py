"""利润表：代垫类报销的收入口径（v0.1.327）。

业务规则（2026-09-28 用户确认）：
  - 租赁汽车加油费 / 停车费 / 打车费 / 住宿费 是「实报实销（代垫）」：
    客户按实际成本全额补偿，收入 = 成本，利润 = 0。
  - 只有个人／自驾加油费没有收入（客户不承担），利润 = -成本。
  - 住宿费默认同样全额代垫；已结算且超出合同人晚上限的部分仍按上限折算，
    那时上限才是可测量的真实亏损。

修复前：收入只在「报销被手工勾选进工单结算单」之后才确认，没勾选/没结算
一律算 0 收入 —— 于是代垫项目被显示成等额亏损，而同期人工收入却是按合同
费率「预估」的，两个口径不一致。

本测试连 PostgreSQL invoice_test（见 conftest.py / tests_pg.py）。
"""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import profitability

REPO_DIR = Path(__file__).resolve().parent
ORDER_NUMBER = "SO-PROFIT-PT"
DAY = "2026-09-10"
RANGE = ("2026-09-01", "2026-09-30")
NOW = "2026-09-10T00:00:00"

MAPPING = [
    ("Fuel Expenses燃油费", "fuel", "Travel Expenses Reimbursement"),
    ("Parking Charge停车费", "parking", "Travel Expenses Reimbursement"),
    ("Taxi Fare / Ride-Hailing Fare打车费", "taxi", "Travel Expenses Reimbursement"),
    ("Accommodation/Lodging住宿费", "lodging", "Travel Expenses Reimbursement"),
]


class ProfitabilityPassThroughTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_profit_pt_test_app", module_path
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

            old = db.execute(
                "select id from service_orders where order_number = ?", (ORDER_NUMBER,)
            ).fetchone()
            if old:
                oid = old["id"]
                db.execute(
                    "delete from customer_reimbursement_expense_links where "
                    "customer_reimbursement_id in (select id from customer_reimbursements "
                    "where service_order_id = ?)",
                    (oid,),
                )
                db.execute("delete from customer_reimbursements where service_order_id = ?", (oid,))
                db.execute(
                    "delete from expense_items where expense_id in "
                    "(select id from expenses where service_order_id = ?)",
                    (oid,),
                )
                db.execute("delete from expenses where service_order_id = ?", (oid,))
                db.execute("delete from service_orders where id = ?", (oid,))
            db.execute("delete from users where email = 'profit-pt@example.invalid'")
            db.commit()

            uid = db.execute(
                "insert into users (name, email, password_hash, role, created_at, "
                "is_active, region_code, country_code, address, default_language, "
                "phone, phone_verified, preferred_communication_language, "
                "communication_languages) values "
                "('Profit PT','profit-pt@example.invalid','x','admin',?,1,'US','US',"
                "'','zh','',0,'zh','zh') returning id",
                (NOW,),
            ).fetchone()["id"]

            for pname, field, invoice in MAPPING:
                db.execute(
                    "insert into expense_settlement_invoice_map "
                    "(expense_project_name, settlement_field, invoice_project_name, created_at) "
                    "values (?,?,?,?) on conflict (expense_project_name) do nothing",
                    (pname, field, invoice, NOW),
                )

            cls.project_ids = {}
            for pname, _field, _invoice in MAPPING:
                row = db.execute("select id from projects where name = ?", (pname,)).fetchone()
                cls.project_ids[pname] = (
                    row["id"]
                    if row
                    else db.execute(
                        "insert into projects (name, created_at) values (?,?) returning id",
                        (pname, NOW),
                    ).fetchone()["id"]
                )

            cls.order_id = db.execute(
                "insert into service_orders (order_number, client_name, site_address, "
                "client_order_number, status, created_by, created_at, geocode_status, "
                "region_code, country_code) values (?,?,?,?,'in_progress',?,?,"
                "'pending','US','US') returning id",
                (ORDER_NUMBER, "Profit PT Client", "1 PT St", "PO-PT", uid, NOW),
            ).fetchone()["id"]

            def add_expense(number, pname, amount, vehicle):
                eid = db.execute(
                    "insert into expenses (service_order_id, expense_number, project, "
                    "expense_date, amount, currency, status, created_by, created_at, "
                    "updated_at, project_id, payout_status, beneficiary_id) "
                    "values (?,?,?,?,?,'USD','approved',?,?,?,?,'pending',?) returning id",
                    (cls.order_id, number, pname, DAY, amount, uid, NOW, NOW,
                     cls.project_ids[pname], uid),
                ).fetchone()["id"]
                return db.execute(
                    "insert into expense_items (expense_id, project_id, project, amount, "
                    "description, sort_order, fuel_vehicle_type, line_key) "
                    "values (?,?,?,?,'pt',0,?,?) returning id",
                    (eid, cls.project_ids[pname], pname, amount, vehicle, f"k-{number}"),
                ).fetchone()["id"]

            cls.items = {
                "rental_fuel": add_expense("EX-PT-1", "Fuel Expenses燃油费", 100.0, "rental"),
                "personal_fuel": add_expense("EX-PT-2", "Fuel Expenses燃油费", 80.0, "personal"),
                "parking": add_expense("EX-PT-3", "Parking Charge停车费", 45.0, None),
                "taxi": add_expense("EX-PT-4", "Taxi Fare / Ride-Hailing Fare打车费", 30.0, None),
                "lodging": add_expense("EX-PT-5", "Accommodation/Lodging住宿费", 1000.0, None),
            }
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def lines(self):
        with self.module.app.app_context():
            return profitability._expense_lines(self.api, RANGE[0], RANGE[1], self.order_id)

    def line_for(self, key):
        item_id = self.items[key]
        for line in self.lines():
            if line["source_line_id"] == item_id:
                return line
        self.fail(f"没有为 {key} 生成利润行")

    def select_into_settlement(self, keys, person_nights=0, cap_rate=0):
        """把指定报销勾选进一张工单结算单（模拟用户在结算前审核页的操作）。"""
        with self.module.app.app_context():
            db = self.module.db()
            uid = db.execute(
                "select id from users where email = 'profit-pt@example.invalid'"
            ).fetchone()["id"]
            cr_id = db.execute(
                "insert into customer_reimbursements (service_order_id, file_name, "
                "stored_filename, created_by, created_at, status, lodging_person_nights, "
                "lodging_cap_rate_snapshot, expense_selection_mode) "
                "values (?,'f','f',?,?,'draft',?,?,'manual_review') returning id",
                (self.order_id, uid, NOW, person_nights, cap_rate),
            ).fetchone()["id"]
            for key in keys:
                item_id = self.items[key]
                amount = db.execute(
                    "select amount from expense_items where id = ?", (item_id,)
                ).fetchone()["amount"]
                db.execute(
                    "insert into customer_reimbursement_expense_links "
                    "(customer_reimbursement_id, expense_item_id, amount_snapshot, "
                    "project_snapshot, expense_status_snapshot, selected_by, selected_at) "
                    "values (?,?,?,'pt','approved',?,?)",
                    (cr_id, item_id, amount, uid, NOW),
                )
            db.commit()

    # ─────────────────────────── 未进入结算单 ───────────────────────────

    def test_unsettled_pass_through_categories_break_even(self):
        """未结算时，租赁加油/停车/打车/住宿 收入=成本，利润 0。"""
        for key, expected_cost in (
            ("rental_fuel", 100.0),
            ("parking", 45.0),
            ("taxi", 30.0),
            ("lodging", 1000.0),
        ):
            line = self.line_for(key)
            self.assertEqual(line["cost"], expected_cost, key)
            self.assertEqual(line["revenue"], expected_cost, key)
            self.assertEqual(line["profit"], 0.0, f"{key} 利润应为 0")

    def test_personal_fuel_has_no_revenue(self):
        """个人／自驾加油费没有收入，利润 = -成本。"""
        line = self.line_for("personal_fuel")
        self.assertEqual(line["cost"], 80.0)
        self.assertEqual(line["revenue"], 0.0)
        self.assertEqual(line["profit"], -80.0)

    # ─────────────────────────── 已进入结算单 ───────────────────────────

    def test_selected_expenses_still_use_settlement_snapshot(self):
        """已勾选进结算单的，仍以结算快照金额为准（不因本次改动而改变）。"""
        self.select_into_settlement(["rental_fuel", "parking", "taxi"])
        for key in ("rental_fuel", "parking", "taxi"):
            line = self.line_for(key)
            self.assertEqual(line["revenue"], line["cost"], key)
            self.assertEqual(line["profit"], 0.0, key)
            self.assertEqual(line["client_rate_source"], "selected_settlement_expense", key)

    def test_lodging_cap_still_applies_once_settled(self):
        """住宿费已结算且超出合同人晚上限时，收入按上限折算，超出部分是真实亏损。"""
        self.select_into_settlement(["lodging"], person_nights=5, cap_rate=100)
        line = self.line_for("lodging")
        self.assertEqual(line["cost"], 1000.0)
        self.assertEqual(line["revenue"], 500.0)  # 5 人晚 × 100 = 500 上限
        self.assertEqual(line["profit"], -500.0)

    def test_unsettled_lodging_defaults_to_full_cost(self):
        """住宿费未结算时按你说的默认全额代垫，利润 0（不臆造上限亏损）。"""
        line = self.line_for("lodging")
        self.assertEqual(line["revenue"], 1000.0)
        self.assertEqual(line["profit"], 0.0)
        self.assertEqual(line["client_rate_source"], "pass_through_at_cost")


if __name__ == "__main__":
    unittest.main()
