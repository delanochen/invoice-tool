"""Phase 4A：Tax Review 工作台回归测试。

覆盖用户列出的 16 项验收点：
 1 review_required 组件出现在列表      2 superseded 不出现
 3 review → accountable                4 review → taxable
 5 keep review 可保存                  6 reason 必填
 7 第二次复核 previous 取上一次有效分类 8 原 component.tax_category 不变
 9 Effective Category 正确             10 review history 顺序正确
 11 employee 权限隔离                  12 finance / admin / manager 权限正确
 13 payroll mileage reason             14 meal allowance reason
 15 expense missing attachment reason  16 Payment Order 金额零变化

铁律（改这几处必须同步本文件）：
- 组件快照只读：复核只 INSERT employee_payment_tax_reviews，
  绝不 UPDATE employee_payment_components.tax_category；
- previous_tax_category = **当前有效分类**（不是原始快照），审计链才连续；
- 复核不改变付款单任何金额 / 状态 / 往来账 / 批次 / 对账；
- 列表默认口径 = 当前税务年度 + 有效分类仍是 tax_review_required。
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import tests_pg
from invoice_tool.payroll.tax import effective_tax_category

ROOT = Path(__file__).resolve().parent

MIGRATION_0293 = ROOT / "migrations" / "postgresql" / "0293-tax-review-keep.sql"

# 退役时间戳用常量：self.module.now() 要 db()，不能在没有 app context 的测试体里调。
SUPERSEDED_AT = "2026-09-01T00:00:00-05:00"


class TaxReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 0293 放开「复核必须改变分类」的 CHECK（Keep Under Review 也是合法审计记录）。
        # 测试库 schema 快照早于 0293，这里幂等执行迁移本身，顺便验证迁移可跑。
        tests_pg.activate()
        with tests_pg.connection() as conn:
            conn.execute(MIGRATION_0293.read_text(encoding="utf-8"))
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("tax_review_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="tax-review-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "messages", "employee_payment_tax_reviews", "employee_payment_components",
                "payment_order_events", "payment_order_sources", "employee_payment_orders",
                "worker_tax_status_history", "expense_attachments", "expense_items", "expenses",
                "service_orders", "audit_logs", "users", "projects",
            ):
                db.execute(f"delete from {table}")
            self.admin_id = self._insert_user(db, "Admin", "admin")
            self.finance_id = self._insert_user(db, "Finance", "finance")
            self.manager_id = self._insert_user(db, "Manager", "manager")
            self.employee_id = self._insert_user(db, "Worker", "employee")
            self.project_id = db.execute(
                "insert into projects (name,default_amount,tax_rate,is_active,created_at,project_type)"
                " values ('Hotel',0,0,1,?,'expense')",
                (self.module.now(),),
            ).lastrowid
            self.order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,"
                "client_order_number,start_date,created_by,created_at)"
                " values ('SO-TR-1','Client','Site','C1','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            db.commit()
        self.http = self.module.app.test_client()
        self._login(self.admin_id)

    def tearDown(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in (
                "messages", "employee_payment_tax_reviews", "employee_payment_components",
                "payment_order_events", "payment_order_sources", "employee_payment_orders",
                "worker_tax_status_history", "expense_attachments", "expense_items", "expenses",
                "service_orders", "audit_logs", "users", "projects",
            ):
                db.execute(f"delete from {table}")
            db.commit()

    # ------------------------------------------------------------------
    # 播种辅助
    # ------------------------------------------------------------------
    def _insert_user(self, db, name, role):
        return db.execute(
            "insert into users (name,email,password_hash,role,is_active,created_at)"
            " values (?,?,?,?,1,?)",
            (name, f"{role}@test.invalid", "unused", role, self.module.now()),
        ).lastrowid

    def _login(self, user_id):
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    def _payment(self, employee_id, payment_type="salary", number="SL-2608-0001",
                 gross="100.00", status="draft"):
        with self.module.app.app_context():
            db = self.module.db()
            payment_id = db.execute(
                """
                insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,taxable_compensation_total,
                 accountable_reimbursement_total,tax_review_required_total,source_type,
                 created_at,updated_at,created_by)
                values (?,?,?,?,'USD',?,'0','0',?,'0','0','0','manual',?,?,?)
                """,
                (number, employee_id, payment_type, status, gross, gross,
                 self.module.now(), self.module.now(), self.admin_id),
            ).lastrowid
            db.commit()
            return payment_id

    def _component(self, payment_id, employee_id, *, code="self_drive_allowance",
                   amount="60.00", tax_category="tax_review_required",
                   tax_status="1099", substantiated=0, source_type="payroll_generation",
                   source_id=None, service_date="2026-08-05",
                   work_order_id=None, daily_report_id=None, superseded_at=None):
        with self.module.app.app_context():
            db = self.module.db()
            component_id = db.execute(
                """
                insert into employee_payment_components
                (payment_order_id,employee_id,component_code,component_name,amount,
                 service_date,work_order_id,source_type,source_id,daily_report_id,
                 tax_category,tax_status_snapshot,substantiated,review_status,created_at,
                 superseded_at)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (payment_id, employee_id, code, code, amount, service_date, work_order_id,
                 source_type, source_id, daily_report_id, tax_category, tax_status,
                 None if substantiated is None else bool(substantiated),
                 "review_required" if tax_category == "tax_review_required" else "confirmed",
                 self.module.now(), superseded_at),
            ).lastrowid
            db.commit()
            return component_id

    def _expense(self, *, business_purpose="工单现场作业需要的住宿支出"):
        with self.module.app.app_context():
            db = self.module.db()
            expense_id = db.execute(
                """
                insert into expenses (service_order_id, expense_number, project_id, project,
                    expense_date, amount, currency, description, status, created_by,
                    created_at, updated_at, beneficiary_id, business_purpose)
                values (?, 'EX-TR-1', ?, 'Hotel', '2026-08-05', '80.00', 'USD', '住宿',
                        'approved', ?, ?, ?, ?, ?)
                """,
                (self.order_id, self.project_id, self.employee_id, self.module.now(),
                 self.module.now(), self.employee_id, business_purpose),
            ).lastrowid
            db.commit()
            return expense_id

    def _item(self, expense_id, amount="80.00", line_key="line-1"):
        with self.module.app.app_context():
            db = self.module.db()
            item_id = db.execute(
                "insert into expense_items (expense_id, line_key, project_id, project, amount,"
                " description, sort_order) values (?,?,?,?,?,?,?)",
                (expense_id, line_key, self.project_id, "Hotel", amount, "房间费", 0),
            ).lastrowid
            db.commit()
            return item_id

    def _receipt(self, expense_id, line_key="line-1"):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "insert into expense_attachments (expense_id, original_filename, stored_filename,"
                " content_type, uploaded_by, uploaded_at, expense_item_key)"
                " values (?,?,?,?,?,?,?)",
                (expense_id, "receipt.pdf", "stored-receipt.pdf", "application/pdf",
                 self.employee_id, self.module.now(), line_key),
            )
            db.commit()

    def _expense_component(self, payment_id, item_id, *, tax_status="1099",
                           source_type="expense_item", amount="80.00"):
        return self._component(
            payment_id, self.employee_id, code="expense_item", amount=amount,
            tax_category="tax_review_required", tax_status=tax_status, substantiated=0,
            source_type=source_type, source_id=item_id, work_order_id=self.order_id)

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------
    def _reviews(self, component_id):
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select * from employee_payment_tax_reviews where component_id=?"
                " order by reviewed_at asc, id asc",
                (component_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def _component_row(self, component_id):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select * from employee_payment_components where id=?", (component_id,)
            ).fetchone()
            return dict(row) if row else None

    def _payment_row(self, payment_id):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select * from employee_payment_orders where id=?", (payment_id,)
            ).fetchone()
            return dict(row) if row else None

    def _detail(self, component_id):
        """服务层函数要 db()，必须包在 app context 里。"""
        with self.module.app.app_context():
            return self.module.tax_review_detail(component_id)

    def _summary(self, payment_id):
        with self.module.app.app_context():
            return self.module.payment_tax_components(payment_id)

    def _review_post(self, component_id, action, reason):
        return self.http.post(
            f"/finance/tax-review/{component_id}/review",
            data={"action": action, "reason": reason}, follow_redirects=False)

    # ------------------------------------------------------------------
    # 1 / 2：列表口径
    # ------------------------------------------------------------------
    def test_review_required_component_appears_in_default_list(self):
        """默认（当前年度 + 有效分类仍待复核）能看到 review_required 组件。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        page = self.http.get("/finance/tax-review")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("SL-2608-0001", html)
        self.assertIn(str(component_id), html)

    def test_superseded_component_is_not_listed(self):
        """superseded_at 非空 = 已退役快照，默认不进复核列表。"""
        payment_id = self._payment(self.employee_id)
        self._component(payment_id, self.employee_id, superseded_at=SUPERSEDED_AT)
        page = self.http.get("/finance/tax-review")
        self.assertEqual(page.status_code, 200)
        self.assertIn("没有符合条件的组件", page.get_data(as_text=True))

    # ------------------------------------------------------------------
    # 3 / 4 / 5 / 8 / 9：复核结果
    # ------------------------------------------------------------------
    def test_review_to_accountable_keeps_snapshot_intact(self):
        """review → accountable：只追加 review 行，原始快照分类不动。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        response = self._review_post(component_id, "accountable", "Receipt provided and verified.")
        self.assertEqual(response.status_code, 302)
        reviews = self._reviews(component_id)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["previous_tax_category"], "tax_review_required")
        self.assertEqual(reviews[0]["new_tax_category"], "accountable_reimbursement")
        self.assertEqual(reviews[0]["reviewed_by"], self.admin_id)
        # 原始快照永不改写
        self.assertEqual(self._component_row(component_id)["tax_category"], "tax_review_required")
        # 有效分类变了：默认（仍待复核）列表里不再出现
        self.assertIn("没有符合条件的组件",
                      self.http.get("/finance/tax-review").get_data(as_text=True))

    def test_review_to_taxable(self):
        """review → taxable：有效分类变 taxable_compensation。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        self._review_post(component_id, "taxable", "Expense determined to be compensation.")
        reviews = self._reviews(component_id)
        self.assertEqual(reviews[0]["new_tax_category"], "taxable_compensation")
        page = self.http.get("/finance/tax-review?effective_category=taxable_compensation")
        self.assertIn("SL-2608-0001", page.get_data(as_text=True))

    def test_keep_under_review_is_saved(self):
        """保持 tax_review_required 也允许保存（附「仍缺材料」的说明）。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        response = self._review_post(
            component_id, "keep", "Supporting documentation remains unavailable.")
        self.assertEqual(response.status_code, 302)
        reviews = self._reviews(component_id)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["previous_tax_category"], "tax_review_required")
        self.assertEqual(reviews[0]["new_tax_category"], "tax_review_required")
        # 仍然出现在默认待复核列表
        self.assertIn("SL-2608-0001",
                      self.http.get("/finance/tax-review").get_data(as_text=True))

    def test_reason_is_required(self):
        """分类调整必须填原因：空原因不落库。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        response = self._review_post(component_id, "accountable", "   ")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._reviews(component_id), [])
        page = self.http.get(f"/finance/tax-review/{component_id}", follow_redirects=True)
        self.assertIn("复核原因必填", page.get_data(as_text=True))

    def test_second_review_previous_is_last_effective_category(self):
        """第二次复核的 previous 取上一次的**有效分类**，审计链连续。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        self._review_post(component_id, "accountable", "Receipt provided and verified.")
        self._review_post(component_id, "taxable", "后来发现应认定为报酬。")
        reviews = self._reviews(component_id)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0]["previous_tax_category"], "tax_review_required")
        self.assertEqual(reviews[0]["new_tax_category"], "accountable_reimbursement")
        self.assertEqual(reviews[1]["previous_tax_category"], "accountable_reimbursement")
        self.assertEqual(reviews[1]["new_tax_category"], "taxable_compensation")
        # 原始快照两次复核后依然是 tax_review_required
        self.assertEqual(self._component_row(component_id)["tax_category"], "tax_review_required")

    # ------------------------------------------------------------------
    # 10：复核历史顺序
    # ------------------------------------------------------------------
    def test_review_history_order_and_effective_category(self):
        """历史按时间正序（审计链），详情页能看到链条与有效分类。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        self._review_post(component_id, "accountable", "Receipt provided and verified.")
        self._review_post(component_id, "taxable", "Expense determined to be compensation.")
        page = self.http.get(f"/finance/tax-review/{component_id}")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        first = html.index("Receipt provided and verified.")
        second = html.index("Expense determined to be compensation.")
        self.assertLess(first, second)
        self.assertIn("Taxable Compensation", html)
        self.assertEqual(
            effective_tax_category(self._component_row(component_id)["tax_category"],
                                   self._reviews(component_id)[-1]),
            "taxable_compensation")

    # ------------------------------------------------------------------
    # 11 / 12：权限
    # ------------------------------------------------------------------
    def test_employee_cannot_open_tax_review(self):
        """员工看不到税务复核工作台（税务口径是财务内部视图）。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        self._login(self.employee_id)
        self.assertEqual(self.http.get("/finance/tax-review").status_code, 403)
        self.assertEqual(
            self.http.get(f"/finance/tax-review/{component_id}").status_code, 403)

    def test_finance_can_review_and_manager_readonly(self):
        """finance 可看可改；manager 只能看（改分类要 tax_review.review）。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        self._login(self.finance_id)
        self.assertEqual(self.http.get("/finance/tax-review").status_code, 200)
        response = self._review_post(component_id, "accountable", "Business purpose confirmed.")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self._reviews(component_id)), 1)

        self._login(self.manager_id)
        self.assertEqual(self.http.get("/finance/tax-review").status_code, 200)
        manager_component = self._component(payment_id, self.employee_id, amount="10.00")
        response = self._review_post(manager_component, "accountable", "经理不该能改分类")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._reviews(manager_component), [])

    def test_admin_can_review(self):
        """admin 具备 tax_review.review（默认权限组 review: admin + finance）。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id)
        self._review_post(component_id, "accountable", "Receipt provided and verified.")
        self.assertEqual(len(self._reviews(component_id)), 1)

    # ------------------------------------------------------------------
    # 13 / 14 / 15：原因动态推导
    # ------------------------------------------------------------------
    def test_payroll_mileage_reason(self):
        """self_drive + 佐证不足 → Missing mileage evidence。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id,
                                       code="self_drive_allowance", substantiated=0)
        page = self.http.get("/finance/tax-review")
        self.assertIn("Missing mileage evidence", page.get_data(as_text=True))
        detail = self._detail(component_id)
        self.assertEqual(detail["review_reason"], "missing_mileage_evidence")

    def test_meal_allowance_reason(self):
        """meal_allowance → Policy review required（默认政策仍待拍板）。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id,
                                       code="meal_allowance", substantiated=1)
        page = self.http.get("/finance/tax-review")
        self.assertIn("Policy review required", page.get_data(as_text=True))
        detail = self._detail(component_id)
        self.assertEqual(detail["review_reason"], "meal_policy_review")

    def test_expense_missing_attachment_reason(self):
        """报销明细缺凭证 → Missing attachment（凭证后补，原因随之消失）。"""
        payment_id = self._payment(self.employee_id, payment_type="expense",
                                   number="ER-2608-0001", gross="80.00")
        expense_id = self._expense()
        item_id = self._item(expense_id)
        component_id = self._expense_component(payment_id, item_id)
        detail = self._detail(component_id)
        self.assertEqual(detail["review_reason"], "missing_attachment")
        self.assertIn("Missing attachment", self.http.get("/finance/tax-review").get_data(as_text=True))
        # 凭证补齐后，原因不再是「缺凭证」（业务用途齐全 → 只剩缺税务身份之类）
        self._receipt(expense_id)
        self.assertEqual(self._detail(component_id)["review_reason"],
                         "substantiation_issue")

    def test_expense_missing_business_purpose_reason(self):
        """新数据口径：缺 business_purpose → Missing business purpose。"""
        payment_id = self._payment(self.employee_id, payment_type="expense",
                                   number="ER-2608-0002", gross="50.00")
        expense_id = self._expense(business_purpose=None)
        item_id = self._item(expense_id, amount="50.00")
        self._receipt(expense_id)
        component_id = self._expense_component(payment_id, item_id, amount="50.00")
        self.assertEqual(self._detail(component_id)["review_reason"],
                         "missing_business_purpose")

    def test_missing_tax_status_reason(self):
        """税务身份快照 NULL → Missing worker tax status（禁止猜身份）。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id,
                                       code="standard_pay", tax_status=None, substantiated=1)
        self.assertEqual(self._detail(component_id)["review_reason"],
                         "missing_tax_status")

    # ------------------------------------------------------------------
    # 16：金额零变化
    # ------------------------------------------------------------------
    def test_payment_order_amounts_unchanged_after_review(self):
        """复核前后付款单金额 / 状态 / 三项税务合计一字不变。"""
        payment_id = self._payment(self.employee_id, gross="160.00", status="approved")
        component_id = self._component(payment_id, self.employee_id, amount="160.00")
        before = self._payment_row(payment_id)
        self._review_post(component_id, "accountable", "Receipt provided and verified.")
        after = self._payment_row(payment_id)
        for field in ("gross_amount", "advance_offset", "other_adjustment", "net_amount",
                      "status", "taxable_compensation_total",
                      "accountable_reimbursement_total", "tax_review_required_total",
                      "payment_number", "batch_id"):
            self.assertEqual(before[field], after[field], field)
        # 组件金额同样不变（numeric 读回来是 float，按数值比较）
        self.assertEqual(float(self._component_row(component_id)["amount"]), 160.0)

    def test_review_rejects_superseded_component(self):
        """已退役的快照不能再复核（只能复核当前生效快照）。"""
        payment_id = self._payment(self.employee_id)
        component_id = self._component(payment_id, self.employee_id,
                                       superseded_at=SUPERSEDED_AT)
        self._review_post(component_id, "accountable", "不该成功")
        self.assertEqual(self._reviews(component_id), [])

    def test_payment_detail_shows_effective_tax_summary(self):
        """付款单详情展示 Effective 合计（动态算，不写回单据）。"""
        payment_id = self._payment(self.employee_id, gross="160.00")
        component_id = self._component(payment_id, self.employee_id, amount="160.00")
        self._review_post(component_id, "accountable", "Receipt provided and verified.")
        page = self.http.get(f"/employee-payments/{payment_id}")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("税务组件（Effective）", html)
        self.assertIn("Accountable Reimbursement", html)
        self.assertIn("Tax Review Required", html)  # 原始合计仍然保留
        summary = self._summary(payment_id)
        self.assertEqual(str(summary["original_totals"]["tax_review_required"]), "160.00")
        self.assertEqual(str(summary["effective_totals"]["accountable_reimbursement"]), "160.00")


if __name__ == "__main__":
    unittest.main()
