"""Phase 5A：Payment Statement 回归测试。

覆盖用户列出的 16 项验收点：
 1 单 SL batch            2 单 ER batch            3 SL+ER mixed batch
 4 多张 SL/ER 同 batch     5 batch total 闭合        6 component total 闭合
 7 effective review 生效   8 review_required 显示    9 employee self-only
10 employee 不能看他人     11 admin/manager/finance  12 missing component fallback
13 missing email 不影响    14 void batch 行为         15 reconciled 显示状态
16 Payment/Ledger/Batch 零修改

铁律（改这几处必须同步本文件）：
- Statement 是 read model：金额全部来自冻结数据（批次/付款单/组件快照/复核链），
  绝不重新计算，也绝不写库；
- 闭合失败必须显式报 data integrity error，绝不静默补差；
- Statement Number 第一版直接用 batch_number；
- HTML / 以后的 PDF / Email 必须复用 payment_statement_payload()，不允许各写一套查询。
"""
import importlib.util
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from flask import g

import employee_finance
import tests_pg

ROOT = Path(__file__).resolve().parent

CLEAN_TABLES = (
    "messages", "employee_payment_tax_reviews", "employee_payment_components",
    "payment_order_events", "payment_order_sources", "employee_payment_orders",
    "employee_payment_batches", "worker_tax_status_history", "expense_attachments",
    "expense_items", "expenses", "service_orders", "audit_logs", "users", "projects",
)


def api_of(module):
    """与 register_employee_finance_routes 收到的 api 同口径的最小依赖注入。"""
    return {
        "db": module.db,
        "normalized_role": module.normalized_role,
        "has_action_permission": module.has_action_permission,
        "payment_tax_components": module.payment_tax_components,
        "now": module.now,
    }


class PaymentStatementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("statement_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="statement-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in CLEAN_TABLES:
                db.execute(f"delete from {table}")
            self.admin_id = self._insert_user(db, "Admin", "admin")
            self.finance_id = self._insert_user(db, "Finance", "finance")
            self.manager_id = self._insert_user(db, "Manager", "manager")
            self.employee_id = self._insert_user(db, "Worker", "employee")
            self.other_id = self._insert_user(db, "Other", "employee")
            self.project_id = db.execute(
                "insert into projects (name,default_amount,tax_rate,is_active,created_at,project_type)"
                " values ('Hotel',0,0,1,?,'expense')",
                (self.module.now(),),
            ).lastrowid
            self.order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,"
                "client_order_number,start_date,created_by,created_at)"
                " values ('SO-PS-1','Client','Site','C1','2026-09-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            db.commit()
        self.http = self.module.app.test_client()
        self._login(self.admin_id)

    def tearDown(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in CLEAN_TABLES:
                db.execute(f"delete from {table}")
            db.commit()

    # ------------------------------------------------------------------
    # 播种辅助
    # ------------------------------------------------------------------
    def _insert_user(self, db, name, role):
        return db.execute(
            "insert into users (name,email,password_hash,role,is_active,created_at)"
            " values (?,?,?,?,1,?)",
            (name, f"{role}{name}@test.invalid", "unused", role, self.module.now()),
        ).lastrowid

    def _login(self, user_id):
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    def _order(self, employee_id, payment_type="salary", number="SL-2609-0001",
               gross="100.00", status="paid", source_number="2026-09-01~2026-09-14",
               source_type="system_payroll", source_id=None):
        with self.module.app.app_context():
            db = self.module.db()
            order_id = db.execute(
                """insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,taxable_compensation_total,
                 accountable_reimbursement_total,tax_review_required_total,source_type,
                 source_id,source_number,paid_at,created_at,updated_at,created_by)
                values (?,?,?,?,'USD',?,'0','0',?,'0','0','0',?,?,?,?,?,?,?)
                """,
                (number, employee_id, payment_type, status, gross, gross,
                 source_type, source_id, source_number, self.module.now(),
                 self.module.now(), self.module.now(), self.admin_id),
            ).lastrowid
            db.commit()
            return order_id

    def _component(self, order_id, employee_id, *, code="standard_pay", name="Standard Pay",
                   amount="100.00", tax_category="taxable_compensation", tax_status="1099",
                   quantity=None, unit=None, unit_rate=None, service_date="2026-09-05",
                   work_order_id=None, superseded_at=None):
        with self.module.app.app_context():
            db = self.module.db()
            component_id = db.execute(
                """insert into employee_payment_components
                (payment_order_id,employee_id,component_code,component_name,amount,
                 quantity,unit,unit_rate,service_date,work_order_id,source_type,
                 tax_category,tax_status_snapshot,substantiated,review_status,created_at,
                 superseded_at)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (order_id, employee_id, code, name, amount, quantity, unit, unit_rate,
                 service_date, work_order_id, "payroll_generation", tax_category,
                 tax_status, True,
                 "review_required" if tax_category == "tax_review_required" else "confirmed",
                 self.module.now(), superseded_at),
            ).lastrowid
            db.commit()
            return component_id

    def _expense(self, *, number="EX-PS-1"):
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                """insert into expenses (service_order_id, expense_number, project_id, project,
                    expense_date, amount, currency, description, status, created_by,
                    created_at, updated_at, beneficiary_id, business_purpose)
                values (?, ?, ?, 'Hotel', '2026-09-05', '80.00', 'USD', '住宿',
                        'approved', ?, ?, ?, ?, '工单现场作业需要的住宿支出')
                """,
                (self.order_id, number, self.project_id, self.employee_id,
                 self.module.now(), self.module.now(), self.employee_id),
            ).lastrowid
            db.execute(
                "insert into expense_items (expense_id, line_key, project_id, project, amount,"
                " description, sort_order) values (?,?,?,?,?,?,?)",
                (expense_id, "line-1", self.project_id, "Hotel", "80.00", "房间费", 0),
            )
            db.commit()
            return expense_id

    def _batch(self, employee_id, total="200.00", count=2, status="issued",
               number="PB-2609-0001", check_number="CH-1001", method="check",
               reconciled_at=None, voided_at=None):
        with self.module.app.app_context():
            db = self.module.db()
            batch_id = db.execute(
                """insert into employee_payment_batches
                (batch_number,employee_id,currency,payment_method,
                 check_number,total_amount,payment_count,status,notes,issued_by,issued_at,
                 reconciled_by,reconciled_at,voided_by,voided_at,created_at,updated_at)
                values (?,?, 'USD', ?, ?, ?, ?, ?, '', ?, ?, null, ?, null, ?, ?, ?)
                """,
                (number, employee_id, method, check_number, total, count, status,
                 self.admin_id, self.module.now(), reconciled_at, voided_at,
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
            return batch_id

    def _attach(self, batch_id, order_ids):
        with self.module.app.app_context():
            db = self.module.db()
            for order_id in order_ids:
                db.execute(
                    "update employee_payment_orders set batch_id=? where id=?",
                    (batch_id, order_id),
                )
            db.commit()

    def _review(self, component_id, new_category, *, previous=None, reason="Receipt verified."):
        with self.module.app.app_context():
            db = self.module.db()
            review_id = db.execute(
                """insert into employee_payment_tax_reviews
                (component_id,reviewed_by,reviewed_at,previous_tax_category,
                 new_tax_category,reason)
                values (?,?,?,?,?,?)
                """,
                (component_id, self.finance_id, self.module.now(), previous,
                 new_category, reason),
            ).lastrowid
            db.commit()
            return review_id

    def _payload(self, batch_id, *, user_id=None, role="admin"):
        """直调 payload（需 request context + g.user，同 4A 测试口径）。"""
        with self.module.app.test_request_context("/"):
            g.user = {"id": user_id or self.admin_id, "name": "Admin", "role": role}
            return employee_finance.payment_statement_payload(api_of(self.module), batch_id)

    def _statement_url(self, batch_id):
        return f"/finance/payment-batches/{batch_id}/statement"

    def _get(self, batch_id):
        return self.http.get(self._statement_url(batch_id))

    def _snapshot(self):
        """抓批次 / 付款单 / 组件 / 复核全量快照，用于「零修改」断言。"""
        with self.module.app.app_context():
            db = self.module.db()
            return {
                "batches": [tuple(r) for r in db.execute(
                    "select * from employee_payment_batches order by id").fetchall()],
                "orders": [tuple(r) for r in db.execute(
                    "select * from employee_payment_orders order by id").fetchall()],
                "components": [tuple(r) for r in db.execute(
                    "select * from employee_payment_components order by id").fetchall()],
                "reviews": [tuple(r) for r in db.execute(
                    "select * from employee_payment_tax_reviews order by id").fetchall()],
            }

    # ------------------------------------------------------------------
    # 1 单 SL batch / 5 batch 闭合 / 6 组件闭合
    # ------------------------------------------------------------------
    def test_single_salary_batch_closure_and_payload(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0001", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        payload = self._payload(batch_id)
        self.assertTrue(payload["batch_closure_ok"])
        self.assertEqual(payload["orders_net"], Decimal("100.00"))
        self.assertEqual(payload["salary_total"], Decimal("100.00"))
        self.assertEqual(payload["expense_total"], Decimal("0"))
        self.assertEqual(len(payload["salary_orders"]), 1)
        self.assertEqual(len(payload["salary_orders"][0]["components"]), 1)
        self.assertTrue(payload["salary_orders"][0]["component_closure_ok"])
        # Statement Number 第一版直接用 batch_number
        self.assertEqual(payload["statement_number"], "PB-2609-0001")
        # SL 的 pay period 来自 source_number（期间 start~end）
        self.assertEqual(payload["salary_orders"][0]["pay_period"], "2026-09-01~2026-09-14")

        response = self._get(batch_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PB-2609-0001", response.data)

    # ------------------------------------------------------------------
    # 2 单 ER batch（来源报销 + 逐 item）
    # ------------------------------------------------------------------
    def test_single_expense_batch_with_items(self):
        expense_id = self._expense()
        order_id = self._order(self.employee_id, "expense", "ER-2609-0001", "80.00",
                               source_type="expense", source_id=expense_id,
                               source_number="ER-2609-0001")
        self._component(order_id, self.employee_id, code="expense_item", name="Hotel",
                        amount="80.00", tax_category="accountable_reimbursement")
        batch_id = self._batch(self.employee_id, total="80.00", count=1)
        self._attach(batch_id, [order_id])

        payload = self._payload(batch_id)
        self.assertEqual(len(payload["expense_orders"]), 1)
        entry = payload["expense_orders"][0]
        self.assertEqual(entry["expense"]["expense_number"], "EX-PS-1")
        self.assertEqual(len(entry["expense_item_rows"]), 1)
        self.assertEqual(entry["expense_item_rows"][0]["project"], "Hotel")
        self.assertTrue(payload["batch_closure_ok"])

        response = self._get(batch_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"EX-PS-1", response.data)
        self.assertIn("房间费".encode("utf-8"), response.data)

    # ------------------------------------------------------------------
    # 3 SL + ER mixed batch / 4 多张同 batch
    # ------------------------------------------------------------------
    def test_mixed_salary_and_expense_batch(self):
        expense_id = self._expense(number="EX-PS-2")
        sl1 = self._order(self.employee_id, "salary", "SL-2609-0002", "120.00")
        sl2 = self._order(self.employee_id, "salary", "SL-2609-0003", "60.00")
        er1 = self._order(self.employee_id, "expense", "ER-2609-0002", "80.00",
                          source_type="expense", source_id=expense_id,
                          source_number="ER-2609-0002")
        self._component(sl1, self.employee_id, amount="120.00")
        self._component(sl2, self.employee_id, code="overtime_pay", name="Overtime",
                        amount="60.00")
        self._component(er1, self.employee_id, code="expense_item", name="Hotel",
                        amount="80.00", tax_category="accountable_reimbursement")
        batch_id = self._batch(self.employee_id, total="260.00", count=3)
        self._attach(batch_id, [sl1, sl2, er1])

        payload = self._payload(batch_id)
        self.assertEqual(len(payload["salary_orders"]), 2)
        self.assertEqual(len(payload["expense_orders"]), 1)
        self.assertEqual(payload["salary_total"], Decimal("180.00"))
        self.assertEqual(payload["expense_total"], Decimal("80.00"))
        self.assertEqual(payload["orders_net"], Decimal("260.00"))
        self.assertTrue(payload["batch_closure_ok"])
        # 组件汇总小计按 component_code 而不是 component_name
        subtotals = {bucket["component_code"] for bucket
                     in payload["salary_orders"][0]["component_subtotals"]}
        self.assertIn("standard_pay", subtotals)

        response = self._get(batch_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SL-2609-0002", response.data)
        self.assertIn(b"ER-2609-0002", response.data)

    # ------------------------------------------------------------------
    # 5 批次不闭合必须显式报错（不静默补差）
    # ------------------------------------------------------------------
    def test_batch_mismatch_shows_integrity_error(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0004", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="99.99", count=1)  # 差一分
        self._attach(batch_id, [order_id])

        payload = self._payload(batch_id)
        self.assertFalse(payload["batch_closure_ok"])

        response = self._get(batch_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Data integrity error", response.data)

    # ------------------------------------------------------------------
    # 6 组件不闭合必须显式报错
    # ------------------------------------------------------------------
    def test_component_mismatch_shows_integrity_error(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0005", "100.00")
        self._component(order_id, self.employee_id, amount="90.00")  # 差 10
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        payload = self._payload(batch_id)
        self.assertFalse(payload["salary_orders"][0]["component_closure_ok"])

        response = self._get(batch_id)
        self.assertIn(b"Data integrity error", response.data)

    # ------------------------------------------------------------------
    # 7 effective review 生效（原始快照不变）
    # ------------------------------------------------------------------
    def test_effective_category_from_latest_review(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0006", "100.00")
        component_id = self._component(order_id, self.employee_id, amount="100.00",
                                       tax_category="tax_review_required")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])
        self._review(component_id, "accountable_reimbursement",
                     previous="tax_review_required")

        payload = self._payload(batch_id)
        entry = payload["salary_orders"][0]
        self.assertEqual(entry["components"][0]["effective_tax_category"],
                         "accountable_reimbursement")
        # 原始快照不变：original 列仍是 review_required
        self.assertEqual(entry["components"][0]["tax_category"], "tax_review_required")
        self.assertEqual(payload["effective_totals"]["accountable_reimbursement"], Decimal("100.00"))
        self.assertEqual(payload["original_totals"]["tax_review_required"], Decimal("100.00"))
        # 复核后不再 review pending
        self.assertEqual(payload["review_pending"]["count"], 0)

    # ------------------------------------------------------------------
    # 8 review_required 明显提示
    # ------------------------------------------------------------------
    def test_review_pending_banner(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0007", "100.00")
        self._component(order_id, self.employee_id, amount="100.00",
                        tax_category="tax_review_required")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        response = self._get(batch_id)
        self.assertIn(b"Tax Classification Review Pending", response.data)

    # ------------------------------------------------------------------
    # 9/10/11 权限：self-only、他人 403、admin/manager/finance 可看
    # ------------------------------------------------------------------
    def test_employee_can_view_own_statement(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0008", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        self._login(self.employee_id)
        self.assertEqual(self._get(batch_id).status_code, 200)

    def test_employee_cannot_view_other_statement(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0009", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        self._login(self.other_id)
        self.assertEqual(self._get(batch_id).status_code, 403)

    def test_admin_manager_finance_can_view(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0010", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        for user_id in (self.admin_id, self.manager_id, self.finance_id):
            self._login(user_id)
            self.assertEqual(self._get(batch_id).status_code, 200, f"user {user_id}")

    # ------------------------------------------------------------------
    # 12 missing component fallback
    # ------------------------------------------------------------------
    def test_missing_components_fallback(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0011", "150.00")
        batch_id = self._batch(self.employee_id, total="150.00", count=1)
        self._attach(batch_id, [order_id])

        payload = self._payload(batch_id)
        entry = payload["salary_orders"][0]
        self.assertFalse(entry["has_components"])
        self.assertTrue(entry["component_closure_ok"])  # 无快照不参与闭合，也不伪造
        self.assertEqual(entry["net_amount"], Decimal("150.00"))

        response = self._get(batch_id)
        self.assertIn(b"Component detail unavailable", response.data)

    # ------------------------------------------------------------------
    # 13 missing email 不影响页面
    # ------------------------------------------------------------------
    def test_missing_employee_email_still_renders(self):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update users set email='' where id=?", (self.employee_id,))
            db.commit()
        order_id = self._order(self.employee_id, "salary", "SL-2609-0012", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        payload = self._payload(batch_id)
        self.assertEqual(payload["batch"]["employee_email"], "")
        self.assertEqual(self._get(batch_id).status_code, 200)

    # ------------------------------------------------------------------
    # 14 void batch 行为：仍可查看、显示作废状态
    # ------------------------------------------------------------------
    def test_void_batch_still_viewable(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0013", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="100.00", count=1,
                               status="void", voided_at="2026-09-30T10:00:00-05:00")
        self._attach(batch_id, [order_id])

        response = self._get(batch_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("已作废".encode("utf-8"), response.data)
        payload = self._payload(batch_id)
        self.assertEqual(payload["batch"]["status"], "void")
        self.assertEqual(payload["batch"]["voided_at"], "2026-09-30T10:00:00-05:00")

    # ------------------------------------------------------------------
    # 15 reconciled batch 显示对账时间
    # ------------------------------------------------------------------
    def test_reconciled_batch_shows_reconciled(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0014", "100.00")
        self._component(order_id, self.employee_id, amount="100.00")
        batch_id = self._batch(self.employee_id, total="100.00", count=1,
                               status="reconciled",
                               reconciled_at="2026-09-30T12:00:00-05:00")
        self._attach(batch_id, [order_id])

        payload = self._payload(batch_id)
        self.assertEqual(payload["batch"]["status"], "reconciled")
        self.assertEqual(payload["batch"]["reconciled_at"], "2026-09-30T12:00:00-05:00")

        response = self._get(batch_id)
        self.assertIn("已对账".encode("utf-8"), response.data)

    # ------------------------------------------------------------------
    # 16 Payment / Ledger / Batch 数据零修改
    # ------------------------------------------------------------------
    def test_statement_writes_nothing(self):
        expense_id = self._expense(number="EX-PS-3")
        sl = self._order(self.employee_id, "salary", "SL-2609-0015", "120.00")
        er = self._order(self.employee_id, "expense", "ER-2609-0003", "80.00",
                         source_type="expense", source_id=expense_id,
                         source_number="ER-2609-0003")
        self._component(sl, self.employee_id, amount="120.00")
        self._component(er, self.employee_id, code="expense_item", name="Hotel",
                        amount="80.00", tax_category="tax_review_required")
        batch_id = self._batch(self.employee_id, total="200.00", count=2)
        self._attach(batch_id, [sl, er])

        before = self._snapshot()
        # 连续访问两次（页面 + payload 直调）都不允许产生任何写入
        self.assertEqual(self._get(batch_id).status_code, 200)
        self._payload(batch_id)
        self.assertEqual(self._snapshot(), before)


if __name__ == "__main__":
    unittest.main()
