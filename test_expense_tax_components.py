"""Phase 3A：报销审核通过生成 ER 时冻结 employee_payment_components 的回归测试。

覆盖：单 item 凭证齐全 → accountable / 多 item sum==gross / 缺凭证 → review /
缺业务用途 → review / 缺工单（纯函数）/ 缺税务身份 → review + NULL 快照 /
1099 快照 / 重复 ensure 不重复插入 / 退回重开 supersede / cancel 保持冻结 /
金额不一致整体回滚 / 往来账批次不受影响 / 老报销流程无回归 / 分类纯函数矩阵。
"""
import importlib.util
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def dec(value):
    """database.py 读回 numeric 会转 float，比较前统一 Decimal(str(x))。"""
    return value if isinstance(value, Decimal) else Decimal(str(value))


class ExpenseTaxComponentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("expense_tax_component_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="expense-tax-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "messages", "employee_payment_components", "payment_order_events", "payment_order_sources",
                "employee_payment_orders", "worker_tax_status_history",
                "expense_attachments", "expense_items", "expenses",
                "service_orders", "audit_logs", "users", "projects",
            ):
                db.execute(f"delete from {table}")
            self.admin_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Admin','exp-admin@test.invalid','unused','admin',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.worker_id = db.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at)"
                " values ('Claimant','claimant@test.invalid','unused','employee',1,?)",
                (self.module.now(),),
            ).lastrowid
            self.order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,client_order_number,"
                "start_date,created_by,created_at) values ('SO-EXP-1','Client','Site','C1','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            self.project_id = db.execute(
                "insert into projects (name,default_amount,tax_rate,is_active,created_at,project_type)"
                " values ('Hotel',0,0,1,?,'expense')",
                (self.module.now(),),
            ).lastrowid
            db.commit()
        self.http = self.module.app.test_client()
        self._login(self.admin_id)

    def tearDown(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "messages", "employee_payment_components", "payment_order_events", "payment_order_sources",
                "employee_payment_orders", "worker_tax_status_history",
                "expense_attachments", "expense_items", "expenses",
                "service_orders", "audit_logs", "users", "projects",
            ):
                db.execute(f"delete from {table}")
            db.commit()

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

    def _expense(self, *, status="submitted", description="客户现场维修住宿",
                 business_purpose="工单现场作业需要的住宿支出", reviewed_by=None,
                 amount=None, expense_date="2026-08-05"):
        """默认：Phase 3D 新数据口径——显式 business_purpose。

        需要验证「缺业务用途」的用例必须显式传 business_purpose=None；
        description / reviewed_by 自 3D 起不再能替代业务用途。
        金额在插入 item 后按合计维护。"""
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                """
                insert into expenses (service_order_id, expense_number, project_id, project,
                    expense_date, amount, currency, description, status, created_by,
                    created_at, updated_at, beneficiary_id, business_purpose, reviewed_by)
                values (?, 'EX-T-1', ?, 'Hotel', ?, 0, 'USD', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (self.order_id, self.project_id, expense_date, description, status,
                 self.worker_id, self.module.now(), self.module.now(), self.worker_id,
                 business_purpose, reviewed_by),
            ).lastrowid
            db.commit()
            return expense_id

    def _item(self, expense_id, amount, line_key="line-1", description="房间费"):
        with self.module.app.app_context():
            db = self.module.db()
            item_id = db.execute(
                "insert into expense_items (expense_id, line_key, project_id, project, amount,"
                " description, sort_order) values (?,?,?,?,?,?,?)",
                (expense_id, line_key, self.project_id, "Hotel", amount, description, 0),
            ).lastrowid
            total = db.execute(
                "select coalesce(sum(amount),0) from expense_items where expense_id=?",
                (expense_id,)).fetchone()[0]
            db.execute("update expenses set amount=? where id=?", (total, expense_id))
            db.commit()
            return item_id

    def _receipt(self, expense_id, line_key):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into expense_attachments (expense_id, original_filename, stored_filename,"
                " content_type, uploaded_by, uploaded_at, expense_item_key)"
                " values (?,?,?,?,?,?,?)",
                (expense_id, "receipt.pdf", "stored-receipt.pdf", "application/pdf",
                 self.worker_id, self.module.now(), line_key),
            )
            db.commit()

    def _approve(self, expense_id):
        response = self.http.post(f"/expenses/{expense_id}/approve")
        return response

    def _reset_workflow(self, expense_id):
        return self.http.post(
            "/expense-processing/action",
            data={"expense_id": expense_id, "action": "reset_workflow"})

    def _payment(self):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select * from employee_payment_orders order by id desc limit 1").fetchone()
            return dict(row) if row else None

    def _components(self, payment_id, superseded=False):
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select * from employee_payment_components where payment_order_id=?"
                " and superseded_at is {} order by id".format("not null" if superseded else "null"),
                (payment_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------

    def test_single_item_with_receipt_and_purpose_is_accountable(self):
        """单 item + 凭证 + 业务用途 → accountable_reimbursement + 1099 快照。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        item_id = self._item(expense_id, "120.50")
        self._receipt(expense_id, "line-1")
        response = self._approve(expense_id)
        self.assertEqual(response.status_code, 302)
        payment = self._payment()
        self.assertIsNotNone(payment)
        self.assertEqual(dec(payment["gross_amount"]), Decimal("120.50"))
        lines = self._components(payment["id"])
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["component_code"], "expense_item")
        self.assertEqual(line["component_name"], "Hotel")
        self.assertEqual(dec(line["amount"]), Decimal("120.50"))
        self.assertEqual(line["service_date"], "2026-08-05")
        self.assertEqual(line["work_order_id"], self.order_id)
        self.assertEqual(line["source_type"], "expense_item")
        self.assertEqual(line["source_id"], item_id)
        self.assertIsNone(line["daily_report_id"])
        self.assertEqual(line["tax_category"], "accountable_reimbursement")
        self.assertEqual(line["tax_status_snapshot"], "1099")
        self.assertEqual(line["substantiated"], 1)
        self.assertEqual(line["review_status"], "confirmed")
        self.assertEqual(dec(payment["accountable_reimbursement_total"]), Decimal("120.50"))
        self.assertEqual(dec(payment["taxable_compensation_total"]), Decimal("0"))
        self.assertEqual(dec(payment["tax_review_required_total"]), Decimal("0"))

    def test_multi_items_component_sum_equals_gross(self):
        """多 item：1 item = 1 component，sum == gross，三合计闭合。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        for index, amount in enumerate(("60.00", "33.33", "16.67"), start=1):
            self._item(expense_id, amount, line_key=f"line-{index}")
            self._receipt(expense_id, f"line-{index}")
        self._approve(expense_id)
        payment = self._payment()
        self.assertEqual(dec(payment["gross_amount"]), Decimal("110.00"))
        lines = self._components(payment["id"])
        self.assertEqual(len(lines), 3)
        self.assertEqual(sum(dec(line["amount"]) for line in lines), Decimal("110.00"))
        self.assertEqual(
            dec(payment["accountable_reimbursement_total"]), Decimal("110.00"))
        self.assertEqual(
            dec(payment["taxable_compensation_total"])
            + dec(payment["accountable_reimbursement_total"])
            + dec(payment["tax_review_required_total"]),
            dec(payment["gross_amount"]))

    def test_missing_receipt_is_review_required(self):
        """缺 item 凭证 → tax_review_required + substantiated=False。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        self._item(expense_id, "80.00")
        self._approve(expense_id)
        payment = self._payment()
        lines = self._components(payment["id"])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["tax_category"], "tax_review_required")
        self.assertEqual(lines[0]["substantiated"], 0)
        self.assertEqual(lines[0]["review_status"], "review_required")
        self.assertEqual(lines[0]["tax_status_snapshot"], "1099")
        self.assertEqual(dec(payment["tax_review_required_total"]), Decimal("80.00"))

    def test_missing_business_purpose_is_review_required(self):
        """Phase 3D：缺 business_purpose → review（即便有 description/reviewed_by）。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense(status="approved", business_purpose=None,
                                   reviewed_by=self.admin_id)
        self._item(expense_id, "50.00")
        self._receipt(expense_id, "line-1")
        with self.module.app.test_request_context("/"):
            from flask import g
            g.user = {"id": self.admin_id, "name": "Admin"}
            payment_id = self.module.ensure_expense_payment_order(self.module.__dict__, expense_id)
            self.module.db().commit()
        self.assertIsNotNone(payment_id)
        lines = self._components(payment_id)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["tax_category"], "tax_review_required")
        self.assertEqual(lines[0]["substantiated"], 0)

    def test_missing_work_order_is_review_required_pure(self):
        """缺工单关联（结构化判定，纯函数矩阵）。"""
        from invoice_tool.payroll.tax import classify_expense_item
        category, substantiated = classify_expense_item(
            amount_ok=True, receipt_ok=True, purpose_ok=True,
            work_order_ok=False, tax_status="1099")
        self.assertEqual(category, "tax_review_required")
        self.assertEqual(substantiated, False)

    def test_missing_tax_status_is_review_with_null_snapshot(self):
        """缺税务身份 → review + snapshot NULL，证据充分时 substantiated=True。"""
        expense_id = self._expense()
        self._item(expense_id, "40.00")
        self._receipt(expense_id, "line-1")
        self._approve(expense_id)
        payment = self._payment()
        lines = self._components(payment["id"])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["tax_category"], "tax_review_required")
        self.assertIsNone(lines[0]["tax_status_snapshot"])
        self.assertEqual(lines[0]["substantiated"], 1)
        self.assertEqual(dec(payment["tax_review_required_total"]), Decimal("40.00"))

    def test_classify_never_returns_taxable(self):
        """Expense 侧永不自动产生 taxable_compensation（纯函数矩阵）。"""
        from itertools import product
        from invoice_tool.payroll.tax import classify_expense_item
        for amount_ok, receipt_ok, purpose_ok, work_order_ok, tax_status in product(
                (True, False), repeat=5):
            category, _ = classify_expense_item(
                amount_ok=amount_ok, receipt_ok=receipt_ok, purpose_ok=purpose_ok,
                work_order_ok=work_order_ok, tax_status=tax_status)
            self.assertIn(category, ("accountable_reimbursement", "tax_review_required"))

    def test_repeat_ensure_does_not_duplicate_components(self):
        """重复 ensure：已有生效快照 → 幂等跳过。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        self._item(expense_id, "30.00")
        self._receipt(expense_id, "line-1")
        self._approve(expense_id)
        payment = self._payment()
        first = self._components(payment["id"])
        self.assertEqual(len(first), 1)
        with self.module.app.test_request_context("/"):
            from flask import g
            g.user = {"id": self.admin_id, "name": "Admin"}
            again = self.module.ensure_expense_payment_order(
                self.module.__dict__, expense_id)
            self.module.db().commit()
        self.assertEqual(again, payment["id"])
        self.assertEqual(len(self._components(payment["id"])), 1)

    def test_return_reopen_supersedes_old_components(self):
        """退回 → reset 取消 ER → 改明细重新通过：同一 ER 重开，
        旧快照置 superseded_at 退役（内容不变），新快照生效。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        self._item(expense_id, "70.00")
        self._receipt(expense_id, "line-1")
        self._approve(expense_id)
        payment = self._payment()
        old_lines = self._components(payment["id"])
        self.assertEqual(len(old_lines), 1)
        # reset：ER 取消，快照保持冻结（superseded_at 仍为 NULL）
        response = self._reset_workflow(expense_id)
        self.assertEqual(response.status_code, 302)
        frozen = self._components(payment["id"])
        self.assertEqual(len(frozen), 1)
        self.assertIsNone(frozen[0]["superseded_at"])
        # 改明细：加一行 + 提高总额，重新走审核
        self._item(expense_id, "25.00", line_key="line-2")
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update expenses set status='submitted' where id=?", (expense_id,))
            db.commit()
        self._approve(expense_id)
        payment_after = self._payment()
        self.assertEqual(payment_after["id"], payment["id"])  # 复用同一 ER
        self.assertEqual(dec(payment_after["gross_amount"]), Decimal("95.00"))
        live = self._components(payment["id"])
        self.assertEqual(len(live), 2)
        self.assertEqual(sum(dec(line["amount"]) for line in live), Decimal("95.00"))
        superseded = self._components(payment["id"], superseded=True)
        self.assertEqual(len(superseded), 1)
        self.assertEqual(dec(superseded[0]["amount"]), Decimal("70.00"))  # 原始内容不变
        self.assertIsNotNone(superseded[0]["superseded_at"])

    def test_cancel_keeps_components_frozen(self):
        """cancel ER：组件快照原样保留，不做任何改写。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        self._item(expense_id, "55.00")
        self._receipt(expense_id, "line-1")
        self._approve(expense_id)
        payment = self._payment()
        with self.module.app.test_request_context("/"):
            from flask import g
            g.user = {"id": self.admin_id, "name": "Admin"}
            self.module.cancel_expense_payment_order(self.module.__dict__, expense_id)
            self.module.db().commit()
        with self.module.app.app_context():
            status = self.module.db().execute(
                "select status from employee_payment_orders where id=?",
                (payment["id"],)).fetchone()["status"]
        self.assertEqual(status, "cancelled")
        self.assertEqual(len(self._components(payment["id"])), 1)
        self.assertEqual(len(self._components(payment["id"], superseded=True)), 0)

    def test_amount_mismatch_rolls_back_whole_approval(self):
        """明细合计 != expense.amount → 硬校验失败 → 整体回滚，无 ER 落库。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        self._item(expense_id, "40.00")
        self._receipt(expense_id, "line-1")
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update expenses set amount='99.99' where id=?", (expense_id,))
            db.commit()
        response = self._approve(expense_id)
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            db = self.module.db()
            self.assertEqual(
                db.execute("select count(*) from employee_payment_orders").fetchone()[0], 0)
            self.assertEqual(
                db.execute("select count(*) from employee_payment_components").fetchone()[0], 0)
            status = db.execute("select status from expenses where id=?",
                                (expense_id,)).fetchone()["status"]
        self.assertEqual(status, "submitted")  # 整体回滚：审核状态未生效

    def test_employee_ledger_and_batches_unaffected(self):
        """审批后：往来账页面正常、批次为 0、ledger 不受组件影响。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        self._item(expense_id, "66.00")
        self._receipt(expense_id, "line-1")
        self._approve(expense_id)
        payment = self._payment()
        page = self.http.get("/finance/employee-ledger")
        self.assertEqual(page.status_code, 200)
        self.assertIn(payment["payment_number"], page.get_data(as_text=True))
        with self.module.app.app_context():
            batches = self.module.db().execute(
                "select count(*) from employee_payment_batches").fetchone()[0]
        self.assertEqual(batches, 0)

    def test_existing_expense_behavior_no_regression(self):
        """老流程无回归：状态/payout/来源桥接/付款单列表不变。"""
        self._seed_tax_status(self.worker_id, [("1099", "2026-01-01", None)])
        expense_id = self._expense()
        self._item(expense_id, "88.00")
        self._receipt(expense_id, "line-1")
        self._approve(expense_id)
        with self.module.app.app_context():
            db = self.module.db()
            expense = db.execute("select * from expenses where id=?", (expense_id,)).fetchone()
            self.assertEqual(expense["status"], "approved")
            self.assertEqual(expense["payout_status"], "pending")
            source = db.execute(
                "select * from payment_order_sources where source_type='expense' and source_id=?",
                (expense_id,)).fetchone()
            self.assertIsNotNone(source)
        payment = self._payment()
        page = self.http.get("/employee-payments")
        self.assertEqual(page.status_code, 200)
        self.assertIn(payment["payment_number"], page.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
