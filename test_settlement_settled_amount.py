"""回归：工单结算「结算来源金额调整」（一条报销明细部分计入客户结算）。

业务规则（2026-10-17 用户拍板，v0.1.398）：
  - 员工报销金额是员工侧事实，任何情况下都不动；员工付款单与工资结算同样不动。
  - `customer_reimbursement_expense_links.settled_amount` 表达「这笔报销在这份
    客户结算里计入多少」。NULL = 未调整 = 全额（历史行全部落在这一档）。
  - 差额 = 报销金额 - 客户结算金额，由计算得出，不重复存储。
  - 允许 0（客户明确不承担），但 0 与「部分计入」都必须填写原因。
  - 利润表客户收入取 settled_amount；显式金额不再乘住宿上限比例（避免二次扣减）。
  - 整笔不计入必须走「未勾选 → 提交前提醒 → 确认（写 ignore + 原因）」流程，
    确认结果落库后利润表收入为 0；只靠「没勾选」不能推出客户不承担。

连接 PostgreSQL invoice_test（见 tests_pg.py）。
"""

import importlib.util
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import profitability
import settlement_review
import tests_pg

REPO_DIR = Path(__file__).resolve().parent
ORDER_NUMBER = "SO-SETTLED-ADJ"
DAY = "2026-09-10"
RANGE = ("2026-09-01", "2026-09-30")
NOW = "2026-09-10T00:00:00"
EMAIL = "settled-adj@example.invalid"
MAPPING = [
    ("MRO Supplies配件及耗材费", "other", "MRO Supplies配件及耗材费"),
    ("Accommodation/Lodging住宿费", "lodging", "Travel Expenses Reimbursement"),
    ("Parking Charge停车费", "parking", "Travel Expenses Reimbursement"),
]


class SettlementSettledAmountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_settled_adj_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.api = vars(cls.module)

        with cls.module.app.app_context():
            db = cls.module.db()
            name = db.execute("select current_database()").fetchone()[0]
            if name != "invoice_test":
                raise RuntimeError(f"refusing to run against {name!r}")
            cls._purge(db)

            cls.uid = db.execute(
                "insert into users (name, email, password_hash, role, created_at, "
                "is_active, region_code, country_code, address, default_language, "
                "phone, phone_verified, preferred_communication_language, "
                "communication_languages) values "
                "('Settled Adj', ?, 'x', 'admin', ?, 1, 'US', 'US', '', 'zh', '', 0, 'zh', 'zh') returning id",
                (EMAIL, NOW),
            ).fetchone()["id"]

            cls.project_ids = {}
            for project_name, field, invoice_project in MAPPING:
                db.execute(
                    "insert into expense_settlement_invoice_map "
                    "(expense_project_name, settlement_field, invoice_project_name, created_at) "
                    "values (?,?,?,?) on conflict (expense_project_name) do nothing",
                    (project_name, field, invoice_project, NOW),
                )
                row = db.execute("select id from projects where name = ?", (project_name,)).fetchone()
                cls.project_ids[project_name] = (
                    row["id"] if row
                    else db.execute(
                        "insert into projects (name, created_at) values (?,?) returning id",
                        (project_name, NOW),
                    ).fetchone()["id"]
                )

            cls.order_id = db.execute(
                "insert into service_orders (order_number, client_name, site_address, "
                "client_order_number, status, created_by, created_at, geocode_status, "
                "region_code, country_code) values (?,?,?,?,'in_progress',?,?,"
                "'pending','US','US') returning id",
                (ORDER_NUMBER, "Settled Adj Client", "1 Adj St", "PO-ADJ", cls.uid, NOW),
            ).fetchone()["id"]
            db.commit()

    @classmethod
    def _purge(cls, db):
        old = db.execute(
            "select id from service_orders where order_number = ?", (ORDER_NUMBER,)
        ).fetchone()
        if old:
            order_id = old["id"]
            db.execute(
                "delete from customer_reimbursement_expense_ignores where "
                "customer_reimbursement_id in (select id from customer_reimbursements "
                "where service_order_id = ?)",
                (order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_expense_links where "
                "customer_reimbursement_id in (select id from customer_reimbursements "
                "where service_order_id = ?)",
                (order_id,),
            )
            db.execute("delete from customer_reimbursements where service_order_id = ?", (order_id,))
            db.execute(
                "delete from expense_items where expense_id in "
                "(select id from expenses where service_order_id = ?)",
                (order_id,),
            )
            db.execute("delete from expenses where service_order_id = ?", (order_id,))
            db.execute("delete from service_orders where id = ?", (order_id,))
        db.execute("delete from users where email = ?", (EMAIL,))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.module.app.app_context():
            db = cls.module.db()
            cls._purge(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "delete from customer_reimbursement_expense_ignores where "
                "customer_reimbursement_id in (select id from customer_reimbursements "
                "where service_order_id = ?)",
                (self.order_id,),
            )
            db.execute(
                "delete from customer_reimbursement_expense_links where "
                "customer_reimbursement_id in (select id from customer_reimbursements "
                "where service_order_id = ?)",
                (self.order_id,),
            )
            db.execute("delete from customer_reimbursements where service_order_id = ?", (self.order_id,))
            db.execute("delete from expenses where service_order_id = ?", (self.order_id,))
            db.commit()

    # ───────────────────────────── 工具 ─────────────────────────────

    def add_expense(self, number, project_name, amount, vehicle=None):
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                "insert into expenses (service_order_id, expense_number, project, "
                "expense_date, amount, currency, status, created_by, created_at, "
                "updated_at, project_id, payout_status, beneficiary_id) "
                "values (?,?,?,?,?,'USD','approved',?,?,?,?,'paid',?) returning id",
                (self.order_id, number, project_name, DAY, amount, self.uid, NOW, NOW,
                 self.project_ids[project_name], self.uid),
            ).fetchone()["id"]
            item_id = db.execute(
                "insert into expense_items (expense_id, project_id, project, amount, "
                "description, sort_order, fuel_vehicle_type, line_key) "
                "values (?,?,?,?,'adj',0,?,?) returning id",
                (expense_id, self.project_ids[project_name], project_name, amount,
                 vehicle, f"k-{number}"),
            ).fetchone()["id"]
            db.commit()
            return item_id

    def make_settlement(self, person_nights=0, cap_rate=0):
        with self.module.app.app_context():
            db = self.module.db()
            series_id = db.execute(
                "insert into customer_reimbursement_series (service_order_id, created_at) "
                "values (?,?) returning id",
                (self.order_id, NOW),
            ).fetchone()["id"]
            reimbursement_id = db.execute(
                "insert into customer_reimbursements (series_id, service_order_id, file_name, "
                "stored_filename, created_by, created_at, status, lodging_person_nights, "
                "lodging_cap_rate_snapshot, expense_selection_mode) "
                "values (?,?,'f','f',?,?,'draft',?,?,'manual_review') returning id",
                (series_id, self.order_id, self.uid, NOW, person_nights, cap_rate),
            ).fetchone()["id"]
            db.commit()
            return reimbursement_id

    def candidates(self, reimbursement_id):
        with self.module.app.app_context():
            rows = settlement_review.settlement_expense_candidates(
                self.api, self.order_id, reimbursement_id
            )
            return {row["expense_item_id"]: row for row in rows}

    def save_selection(self, reimbursement_id, selected_ids, settled=None, reasons=None):
        """走真实的 replace_expense_links（含校验），不是直接写 SQL。"""
        candidate_map = self.candidates(reimbursement_id)
        with self.module.app.test_request_context():
            self.module.g.user = {"id": self.uid}
            settlement_review.replace_expense_links(
                self.api,
                reimbursement_id,
                candidate_map,
                set(selected_ids),
                settled_amounts=settled or {},
                adjustment_reasons=reasons or {},
            )
            self.module.db().commit()

    def linked_rows(self, reimbursement_id):
        with self.module.app.app_context():
            rows = settlement_review.linked_approved_expense_rows(
                self.api, reimbursement_id, self.order_id
            )
            return {row["expense_item_id"]: row for row in rows}

    def profit_line(self, expense_item_id):
        with self.module.app.app_context():
            lines = profitability._expense_lines(self.api, RANGE[0], RANGE[1], self.order_id)
        for line in lines:
            if line["source_line_id"] == expense_item_id:
                return line
        self.fail(f"没有为报销明细 {expense_item_id} 生成利润行")

    def link_snapshot(self, reimbursement_id, expense_item_id):
        with self.module.app.app_context():
            return self.module.db().execute(
                "select settled_amount, adjustment_reason from "
                "customer_reimbursement_expense_links "
                "where customer_reimbursement_id = ? and expense_item_id = ?",
                (reimbursement_id, expense_item_id),
            ).fetchone()

    # ───────────────────── 1. 历史全额（NULL）─────────────────────

    def test_null_settled_amount_keeps_full_revenue(self):
        """未填结算金额（历史行）必须等价于全额，收入 = 成本，利润 0。"""
        item_id = self.add_expense("EX-ADJ-1", "MRO Supplies配件及耗材费", 9800.0)
        reimbursement_id = self.make_settlement()
        self.save_selection(reimbursement_id, [item_id])

        snapshot = self.link_snapshot(reimbursement_id, item_id)
        self.assertIsNone(snapshot["settled_amount"], "全额计入应存 NULL，便于筛出被调减的行")
        linked = self.linked_rows(reimbursement_id)[item_id]
        self.assertEqual(float(linked["amount"]), 9800.0)
        self.assertEqual(float(linked["full_amount"]), 9800.0)

        line = self.profit_line(item_id)
        self.assertEqual(float(line["revenue"]), 9800.0)
        self.assertEqual(float(line["profit"]), 0.0)

    # ───────────────────── 2. 部分计入 ─────────────────────

    def test_partial_settlement_reduces_revenue_only(self):
        """9800 里客户只认 8000：收入 8000、成本 9800、亏损 1800，报销原额不变。"""
        item_id = self.add_expense("EX-ADJ-2", "MRO Supplies配件及耗材费", 9800.0)
        reimbursement_id = self.make_settlement()
        self.save_selection(
            reimbursement_id, [item_id],
            settled={item_id: Decimal("8000")},
            reasons={item_id: "甲方不承担工具、服装费用"},
        )

        snapshot = self.link_snapshot(reimbursement_id, item_id)
        self.assertEqual(float(snapshot["settled_amount"]), 8000.0)
        self.assertEqual(snapshot["adjustment_reason"], "甲方不承担工具、服装费用")

        linked = self.linked_rows(reimbursement_id)[item_id]
        self.assertEqual(float(linked["amount"]), 8000.0, "结算取客户结算金额")
        self.assertEqual(float(linked["full_amount"]), 9800.0, "报销原额必须原样保留")

        with self.module.app.app_context():
            original = self.module.db().execute(
                "select amount from expense_items where id = ?", (item_id,)
            ).fetchone()["amount"]
        self.assertEqual(float(original), 9800.0, "员工报销金额不得被改动")

        line = self.profit_line(item_id)
        self.assertEqual(float(line["revenue"]), 8000.0)
        self.assertEqual(float(line["cost"]), 9800.0)
        self.assertEqual(float(line["profit"]), -1800.0)

    # ───────────────────── 3. 零元计入 ─────────────────────

    def test_zero_settlement_means_company_bears_all(self):
        """客户结算金额填 0（明确不承担）：收入 0、亏损全额，仍保留关联。"""
        item_id = self.add_expense("EX-ADJ-3", "MRO Supplies配件及耗材费", 1200.0)
        reimbursement_id = self.make_settlement()
        self.save_selection(
            reimbursement_id, [item_id],
            settled={item_id: Decimal("0")},
            reasons={item_id: "甲方明确不承担"},
        )
        linked = self.linked_rows(reimbursement_id)[item_id]
        self.assertEqual(float(linked["amount"]), 0.0)
        self.assertEqual(float(linked["full_amount"]), 1200.0)

        line = self.profit_line(item_id)
        self.assertEqual(float(line["revenue"]), 0.0)
        self.assertEqual(float(line["profit"]), -1200.0)

    # ───────────────────── 4. 校验 ─────────────────────

    def test_settlement_amount_validation(self):
        item_id = self.add_expense("EX-ADJ-4", "MRO Supplies配件及耗材费", 500.0)
        reimbursement_id = self.make_settlement()
        candidate_map = self.candidates(reimbursement_id)

        def attempt(settled, reasons):
            with self.module.app.test_request_context():
                self.module.g.user = {"id": self.uid}
                settlement_review.replace_expense_links(
                    self.api, reimbursement_id, candidate_map, {item_id},
                    settled_amounts=settled, adjustment_reasons=reasons,
                )
                self.module.db().commit()

        with self.assertRaises(ValueError):
            attempt({item_id: Decimal("600")}, {item_id: "超额"})
        with self.assertRaises(ValueError):
            attempt({item_id: Decimal("-1")}, {item_id: "负数"})
        with self.assertRaises(ValueError):
            attempt({item_id: Decimal("400")}, {})
        # 未调减时原因可以为空
        attempt({item_id: Decimal("500")}, {})
        with self.module.app.app_context():
            count = self.module.db().execute(
                "select count(*) as count from customer_reimbursement_expense_links "
                "where customer_reimbursement_id = ?", (reimbursement_id,),
            ).fetchone()["count"]
        self.assertEqual(count, 1)

    # ───────────────────── 5. 重复保存 ─────────────────────

    def test_repeated_save_keeps_adjustment(self):
        """保存是先删后建：第二次保存仍带同一金额与原因，结果必须不变。"""
        item_id = self.add_expense("EX-ADJ-5", "MRO Supplies配件及耗材费", 9800.0)
        reimbursement_id = self.make_settlement()
        for _ in range(2):
            self.save_selection(
                reimbursement_id, [item_id],
                settled={item_id: Decimal("8000")},
                reasons={item_id: "甲方不承担"},
            )
        snapshot = self.link_snapshot(reimbursement_id, item_id)
        self.assertEqual(float(snapshot["settled_amount"]), 8000.0)
        self.assertEqual(snapshot["adjustment_reason"], "甲方不承担")
        self.assertEqual(len(self.linked_rows(reimbursement_id)), 1, "重复保存不得产生重复行")

    # ───────────────────── 6. 明确不承担 vs 未确认 ─────────────────────

    def test_unselected_expense_keeps_estimate_until_marked_ignored(self):
        """未勾选且未确认时不做收入结论；确认不计入（ignore）才归零。"""
        item_id = self.add_expense("EX-ADJ-6", "Parking Charge停车费", 300.0)
        reimbursement_id = self.make_settlement()

        # 还没走完确认：没 link、没 ignore → 不推断客户不承担，仍按成本确认预计收入
        line = self.profit_line(item_id)
        self.assertEqual(float(line["revenue"]), 300.0)
        self.assertEqual(line["client_rate_source"], "pass_through_at_cost")

        # 明确不承担：写 ignore 标记 → 收入归零，亏损 300
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into customer_reimbursement_expense_ignores "
                "(customer_reimbursement_id, expense_item_id, reason, ignored_by, ignored_at) "
                "values (?,?,'甲方不认',?,?)",
                (reimbursement_id, item_id, self.uid, NOW),
            )
            db.commit()
        line = self.profit_line(item_id)
        self.assertEqual(float(line["revenue"]), 0.0)
        self.assertEqual(float(line["profit"]), -300.0)
        self.assertEqual(line["client_rate_source"], "client_rejected")

    # ───────────────────── 7. 住宿不二次折算 ─────────────────────

    def test_explicit_lodging_amount_is_not_discounted_twice(self):
        """显式填的住宿结算金额已按上限处理过，不能再乘一次上限比例。"""
        item_id = self.add_expense("EX-ADJ-7", "Accommodation/Lodging住宿费", 1000.0)
        # 1 人晚 × 800 上限：未显式填写时收入应折到 800
        reimbursement_id = self.make_settlement(person_nights=1, cap_rate=800)
        self.save_selection(reimbursement_id, [item_id])
        self.assertEqual(float(self.profit_line(item_id)["revenue"]), 800.0)

        # 显式填 800（已按上限处理）：收入必须就是 800，不能变成 640
        self.save_selection(
            reimbursement_id, [item_id],
            settled={item_id: Decimal("800")},
            reasons={item_id: "按合同上限结算"},
        )
        self.assertEqual(float(self.profit_line(item_id)["revenue"]), 800.0)


if __name__ == "__main__":
    unittest.main()
