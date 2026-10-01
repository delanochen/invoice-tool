"""Phase 5B：Payment Statement PDF 回归测试。

覆盖用户列出的 20 项验收点：
 1 单 SL PDF            2 单 ER PDF            3 SL+ER mixed PDF
 4 multiple orders       5 Total Payment 闭合    6 review_pending warning
 7 effective 生效        8 original snapshot 不变 9 missing component fallback
10 void batch PDF       11 reconciled batch PDF  12 中文员工姓名
13 filename sanitize    14 admin 下载            15 manager 下载
16 finance 下载          17 employee self 下载    18 employee 他人 403
19 下载前后 DB 零变化     20 response 头/签名/非空

铁律（改这几处必须同步本文件）：
- PDF 业务数据只来自 payment_statement_payload()，render 层不查库、不重算；
- 批次不闭合仍生成但显式 DATA INTEGRITY WARNING，绝不静默调金额；
- 下载路由权限与 HTML Statement 完全同口径，员工 self-only；
- 下载零写入：不碰批次/付款单/组件/复核/往来账/对账，也不记下载事件。
"""
import importlib.util
import io
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from flask import g

import employee_finance
import tests_pg
from employee_finance import (
    payment_statement_pdf_filename,
    render_payment_statement_pdf,
)

ROOT = Path(__file__).resolve().parent

CLEAN_TABLES = (
    "messages", "employee_payment_tax_reviews", "employee_payment_components",
    "payment_order_events", "payment_order_sources", "employee_payment_orders",
    "employee_payment_batches", "worker_tax_status_history", "expense_attachments",
    "expense_items", "expenses", "service_orders", "audit_logs", "users", "projects",
)


def api_of(module):
    return {
        "db": module.db,
        "normalized_role": module.normalized_role,
        "has_action_permission": module.has_action_permission,
        "payment_tax_components": module.payment_tax_components,
        "now": module.now,
    }


class PaymentStatementPdfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("statement_pdf_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="statement-pdf-secret")
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
                " values ('SO-PDF-1','Client','Site','C1','2026-09-01',?,?)",
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
    # 播种辅助（与 5A test_payment_statement.py 同口径）
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

    def _expense(self, *, number="EX-PDF-1"):
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

    def _batch(self, employee_id, total="100.00", count=1, status="issued",
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

    def _pdf_url(self, batch_id):
        return f"/finance/payment-batches/{batch_id}/statement.pdf"

    def _download(self, batch_id):
        return self.http.get(self._pdf_url(batch_id))

    def _pdf_text(self, response):
        """用 pypdf 抽取 PDF 文本（断言业务内容，而非渲染细节）。"""
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(response.data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def _snapshot(self):
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

    def _seed_salary(self, *, number="SL-2609-0001", amount="100.00", employee_id=None):
        employee_id = employee_id or self.employee_id
        order_id = self._order(employee_id, "salary", number, amount)
        self._component(order_id, employee_id, amount=amount)
        return order_id

    # ------------------------------------------------------------------
    # 1 单 SL PDF
    # ------------------------------------------------------------------
    def test_single_salary_pdf(self):
        order_id = self._seed_salary(number="SL-2609-0001", amount="100.00")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        response = self._download(batch_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"%PDF-"))
        text = self._pdf_text(response)
        self.assertIn("Payment Statement", text)
        self.assertIn("SL-2609-0001", text)
        self.assertIn("2026-09-01~2026-09-14", text)  # pay period
        self.assertIn("Subtotal", text)
        self.assertIn("$100.00", text)
        self.assertIn("Total Payment Amount", text)
        # 不出现传统 paystub 栏位
        self.assertNotIn("Net Pay", text)
        self.assertNotIn("Deductions", text)

    # ------------------------------------------------------------------
    # 2 单 ER PDF
    # ------------------------------------------------------------------
    def test_single_expense_pdf(self):
        expense_id = self._expense()
        order_id = self._order(self.employee_id, "expense", "ER-2609-0001", "80.00",
                               source_type="expense", source_id=expense_id,
                               source_number="ER-2609-0001")
        self._component(order_id, self.employee_id, code="expense_item", name="Hotel",
                        amount="80.00", tax_category="accountable_reimbursement")
        batch_id = self._batch(self.employee_id, total="80.00", count=1)
        self._attach(batch_id, [order_id])

        response = self._download(batch_id)
        self.assertEqual(response.status_code, 200)
        text = self._pdf_text(response)
        self.assertIn("Expense Reimbursements", text)
        self.assertIn("ER-2609-0001", text)
        self.assertIn("EX-PDF-1", text)
        self.assertIn("房间费", text)
        self.assertIn("$80.00", text)

    # ------------------------------------------------------------------
    # 3 mixed SL + ER PDF
    # ------------------------------------------------------------------
    def test_mixed_salary_and_expense_pdf(self):
        expense_id = self._expense(number="EX-PDF-2")
        sl = self._order(self.employee_id, "salary", "SL-2609-0002", "120.00")
        er = self._order(self.employee_id, "expense", "ER-2609-0002", "80.00",
                         source_type="expense", source_id=expense_id,
                         source_number="ER-2609-0002")
        self._component(sl, self.employee_id, amount="120.00")
        self._component(er, self.employee_id, code="expense_item", name="Hotel",
                        amount="80.00", tax_category="accountable_reimbursement")
        batch_id = self._batch(self.employee_id, total="200.00", count=2)
        self._attach(batch_id, [sl, er])

        response = self._download(batch_id)
        text = self._pdf_text(response)
        self.assertIn("Earnings / Compensation", text)
        self.assertIn("Expense Reimbursements", text)
        self.assertIn("SL-2609-0002", text)
        self.assertIn("ER-2609-0002", text)
        # 5 Total 闭合：SL 120 + ER 80 = 200
        self.assertIn("$120.00", text)
        self.assertIn("$80.00", text)
        self.assertIn("$200.00", text)

    # ------------------------------------------------------------------
    # 4 multiple orders（两张 SL 同批次）
    # ------------------------------------------------------------------
    def test_multiple_salary_orders(self):
        sl1 = self._order(self.employee_id, "salary", "SL-2609-0003", "120.00")
        sl2 = self._order(self.employee_id, "salary", "SL-2609-0004", "60.00")
        self._component(sl1, self.employee_id, amount="120.00")
        self._component(sl2, self.employee_id, code="overtime_pay", name="Overtime",
                        amount="60.00")
        batch_id = self._batch(self.employee_id, total="180.00", count=2)
        self._attach(batch_id, [sl1, sl2])

        response = self._download(batch_id)
        text = self._pdf_text(response)
        self.assertIn("SL-2609-0003", text)
        self.assertIn("SL-2609-0004", text)
        self.assertIn("$180.00", text)

    # ------------------------------------------------------------------
    # 5 Total Payment 闭合（summary 三行相等关系）
    # ------------------------------------------------------------------
    def test_payment_summary_totals(self):
        expense_id = self._expense(number="EX-PDF-3")
        sl = self._order(self.employee_id, "salary", "SL-2609-0005", "120.00")
        er = self._order(self.employee_id, "expense", "ER-2609-0003", "80.00",
                         source_type="expense", source_id=expense_id,
                         source_number="ER-2609-0003")
        self._component(sl, self.employee_id, amount="120.00")
        self._component(er, self.employee_id, code="expense_item", name="Hotel",
                        amount="80.00", tax_category="accountable_reimbursement")
        batch_id = self._batch(self.employee_id, total="200.00", count=2)
        self._attach(batch_id, [sl, er])

        text = self._pdf_text(self._download(batch_id))
        self.assertIn("Salary / Compensation subtotal", text)
        self.assertIn("Expense Reimbursement subtotal", text)
        self.assertIn("Total Payment Amount", text)
        self.assertIn("$200.00", text)
        # 批次闭合正常时不得出现 DATA INTEGRITY WARNING
        self.assertNotIn("DATA INTEGRITY WARNING", text)

    # ------------------------------------------------------------------
    # 6 review_pending warning
    # ------------------------------------------------------------------
    def test_review_pending_warning_in_pdf(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0006", "100.00")
        self._component(order_id, self.employee_id, amount="100.00",
                        tax_category="tax_review_required")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        response = self._download(batch_id)
        self.assertEqual(response.status_code, 200)
        text = self._pdf_text(response)
        self.assertIn("Tax Classification Review Pending", text)
        self.assertIn("Tax Review Required", text)

    # ------------------------------------------------------------------
    # 7 effective classification 生效（PDF 显示 Original → Effective）
    # ------------------------------------------------------------------
    def test_effective_classification_shown_in_pdf(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0007", "100.00")
        component_id = self._component(order_id, self.employee_id, amount="100.00",
                                       tax_category="tax_review_required")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])
        self._review(component_id, "accountable_reimbursement",
                     previous="tax_review_required")

        text = self._pdf_text(self._download(batch_id))
        # 复核过的行显示 original → effective
        self.assertIn("Tax review required", text)
        self.assertIn("Reimbursement (accountable)", text)
        self.assertIn("→", text)
        # 分类脚注必须存在
        self.assertIn(
            "Tax classifications reflect the current effective classification", text)

    # ------------------------------------------------------------------
    # 8 original snapshot 不变（PDF 生成不碰组件快照/复核链）
    # ------------------------------------------------------------------
    def test_original_snapshot_unchanged(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0008", "100.00")
        component_id = self._component(order_id, self.employee_id, amount="100.00",
                                       tax_category="tax_review_required")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])
        self._review(component_id, "accountable_reimbursement",
                     previous="tax_review_required")

        self.assertEqual(self._download(batch_id).status_code, 200)
        with self.module.app.app_context():
            db = self.module.db()
            component = db.execute(
                "select tax_category, amount from employee_payment_components where id=?",
                (component_id,),
            ).fetchone()
            self.assertEqual(component["tax_category"], "tax_review_required")
            self.assertEqual(component["amount"], Decimal("100.00"))
            review = db.execute(
                "select new_tax_category from employee_payment_tax_reviews where id=?",
                (db.execute("select min(id) as id from employee_payment_tax_reviews")
                    .fetchone()["id"],),
            ).fetchone()
            self.assertEqual(review["new_tax_category"], "accountable_reimbursement")

    # ------------------------------------------------------------------
    # 9 missing component fallback
    # ------------------------------------------------------------------
    def test_missing_component_fallback_in_pdf(self):
        order_id = self._order(self.employee_id, "salary", "SL-2609-0009", "150.00")
        batch_id = self._batch(self.employee_id, total="150.00", count=1)
        self._attach(batch_id, [order_id])

        text = self._pdf_text(self._download(batch_id))
        self.assertIn("Component detail unavailable", text)
        self.assertIn("$150.00", text)

    # ------------------------------------------------------------------
    # 10 void batch PDF（仍可生成，状态显示作废）
    # ------------------------------------------------------------------
    def test_void_batch_pdf(self):
        order_id = self._seed_salary(number="SL-2609-0010")
        batch_id = self._batch(self.employee_id, total="100.00", count=1,
                               status="void", voided_at="2026-09-30T10:00:00-05:00")
        self._attach(batch_id, [order_id])

        response = self._download(batch_id)
        self.assertEqual(response.status_code, 200)
        text = self._pdf_text(response)
        self.assertIn("已作废", text)
        self.assertIn("Voided Date", text)

    # ------------------------------------------------------------------
    # 11 reconciled batch PDF
    # ------------------------------------------------------------------
    def test_reconciled_batch_pdf(self):
        order_id = self._seed_salary(number="SL-2609-0011")
        batch_id = self._batch(self.employee_id, total="100.00", count=1,
                               status="reconciled",
                               reconciled_at="2026-09-30T12:00:00-05:00")
        self._attach(batch_id, [order_id])

        text = self._pdf_text(self._download(batch_id))
        self.assertIn("已对账", text)
        self.assertIn("Reconciled Date", text)

    # ------------------------------------------------------------------
    # 12 中文员工姓名
    # ------------------------------------------------------------------
    def test_chinese_employee_name(self):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update users set name=? where id=?", ("陈亦珹", self.employee_id))
            db.commit()
        order_id = self._seed_salary(number="SL-2609-0012")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        response = self._download(batch_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"%PDF-"))
        text = self._pdf_text(response)
        self.assertIn("陈亦珹", text)

    # ------------------------------------------------------------------
    # 13 filename sanitization
    # ------------------------------------------------------------------
    def test_filename_sanitization(self):
        # 常规：批次 + 员工名
        self.assertEqual(
            payment_statement_pdf_filename("PB-2610-0001", "李力"),
            "Payment_Statement_PB-2610-0001_李力.pdf",
        )
        # 非法字符全部替换，Unicode 保留
        self.assertEqual(
            payment_statement_pdf_filename("PB-2610-0001", '陈亦珹/\\:*?"<>|'),
            "Payment_Statement_PB-2610-0001_陈亦珹---------.pdf",
        )
        # 控制字符
        self.assertEqual(
            payment_statement_pdf_filename("PB-2610-0001", "a\x00b\x1fc"),
            "Payment_Statement_PB-2610-0001_a-b-c.pdf",
        )
        # 员工名为空 / 全部被剥掉 → 回退批次号
        self.assertEqual(
            payment_statement_pdf_filename("PB-2610-0001", ""),
            "Payment_Statement_PB-2610-0001.pdf",
        )
        self.assertEqual(
            payment_statement_pdf_filename("PB-2610-0001", "  ..  "),
            "Payment_Statement_PB-2610-0001.pdf",
        )
        # 全空兜底
        self.assertEqual(
            payment_statement_pdf_filename("", ""),
            "Payment_Statement.pdf",
        )

    # ------------------------------------------------------------------
    # 14/15/16/17 admin / manager / finance / employee self 下载
    # ------------------------------------------------------------------
    def test_admin_manager_finance_self_can_download(self):
        order_id = self._seed_salary(number="SL-2609-0013")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        for user_id in (self.admin_id, self.manager_id, self.finance_id):
            self._login(user_id)
            self.assertEqual(self._download(batch_id).status_code, 200, f"user {user_id}")
        self._login(self.employee_id)
        self.assertEqual(self._download(batch_id).status_code, 200)

    # ------------------------------------------------------------------
    # 18 employee 下载他人 → 403
    # ------------------------------------------------------------------
    def test_employee_cannot_download_other_statement(self):
        order_id = self._seed_salary(number="SL-2609-0014")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        self._login(self.other_id)
        self.assertEqual(self._download(batch_id).status_code, 403)

    # ------------------------------------------------------------------
    # 19 PDF 下载前后 DB 零变化
    # ------------------------------------------------------------------
    def test_download_writes_nothing(self):
        expense_id = self._expense(number="EX-PDF-4")
        sl = self._order(self.employee_id, "salary", "SL-2609-0015", "120.00")
        er = self._order(self.employee_id, "expense", "ER-2609-0004", "80.00",
                         source_type="expense", source_id=expense_id,
                         source_number="ER-2609-0004")
        self._component(sl, self.employee_id, amount="120.00")
        self._component(er, self.employee_id, code="expense_item", name="Hotel",
                        amount="80.00", tax_category="tax_review_required")
        batch_id = self._batch(self.employee_id, total="200.00", count=2)
        self._attach(batch_id, [sl, er])

        before = self._snapshot()
        for _ in range(2):  # 连续下载两次都不允许产生任何写入
            self.assertEqual(self._download(batch_id).status_code, 200)
        self.assertEqual(self._snapshot(), before)

    # ------------------------------------------------------------------
    # 20 response 头 / bytes / 签名
    # ------------------------------------------------------------------
    def test_pdf_response_headers(self):
        order_id = self._seed_salary(number="SL-2609-0016")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        response = self._download(batch_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")
        disposition = response.headers["Content-Disposition"]
        self.assertIn("attachment", disposition)
        self.assertIn("Payment_Statement_PB-2609-0001_Worker.pdf", disposition)
        self.assertTrue(response.data)
        self.assertTrue(response.data.startswith(b"%PDF-"))

    # ------------------------------------------------------------------
    # 附加：批次不闭合 → DATA INTEGRITY WARNING 但仍生成
    # ------------------------------------------------------------------
    def test_batch_mismatch_shows_integrity_warning(self):
        order_id = self._seed_salary(number="SL-2609-0017", amount="100.00")
        batch_id = self._batch(self.employee_id, total="99.99", count=1)
        self._attach(batch_id, [order_id])

        response = self._download(batch_id)
        self.assertEqual(response.status_code, 200)
        text = self._pdf_text(response)
        self.assertIn("DATA INTEGRITY WARNING", text)
        self.assertIn("$99.99", text)
        self.assertIn("$100.00", text)

    # ------------------------------------------------------------------
    # 附加：渲染层纯函数 —— 同一 payload 输出相同内容（ReportLab 每次写入
    # 随机文档 ID，bytes 不可能逐字节相等，因此比较页数与抽取文本）。
    # ------------------------------------------------------------------
    def test_render_is_pure_function_of_payload(self):
        import pypdf
        order_id = self._seed_salary(number="SL-2609-0018")
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        self._attach(batch_id, [order_id])

        with self.module.app.test_request_context("/"):
            g.user = {"id": self.admin_id, "name": "Admin", "role": "admin"}
            payload = employee_finance.payment_statement_payload(
                api_of(self.module), batch_id)
        first = render_payment_statement_pdf(payload, company_name="Test Co",
                                             generated_at="2026-10-01 10:00")
        second = render_payment_statement_pdf(payload, company_name="Test Co",
                                              generated_at="2026-10-01 10:00")
        for pdf_bytes in (first, second):
            self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

        def page_texts(data):
            reader = pypdf.PdfReader(io.BytesIO(data))
            return len(reader.pages), [page.extract_text() or "" for page in reader.pages]

        self.assertEqual(page_texts(first), page_texts(second))
        self.assertIn("SL-2609-0018", page_texts(first)[1][0])


if __name__ == "__main__":
    unittest.main()
