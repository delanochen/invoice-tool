"""Phase 4B：Annual Tax Summary 回归测试。

覆盖用户列出的 16 项验收点：
 1 1099 employee 汇总            2 W2 employee 汇总
 3 同一员工同年 1099 + W2 拆开    4 taxable / reimbursement / review 三类闭合
 5 latest review 决定 effective   6 superseded 排除
 7 service-date year basis       8 payment-date year basis
 9 payment_date NULL             10 未付款仍显示 Recorded 不显示 Paid
11 drill-down 与主汇总金额一致    12 Employee 权限隔离
13 manager / finance / admin 权限 14 review 变化后 summary 更新
15 Payment / Ledger 数据零修改    16 全量基线闭合（生产 1,145 / $214,567.55 在
    上线前用只读 SQL 另行核对，测试库这里验证「不筛任何条件时三桶闭合」）

铁律（改这几处必须同步本文件）：
- 年度汇总只读：不写往来账 / 付款单 / 组件 / 身份 / 报销 / 工资；
- 分组 = employee + tax_year + tax_status_snapshot（同一人同年 1099 与 W2 拆开）；
- 分类一律用 effective（最新 review 优先），绝不用付款单原始 tax totals；
- payment_date 四级解析（银行流水 → 批次 issued_at → order paid_at → NULL）；
- year_basis 是显式参数（service_date / payment_date），非法值抛 ValueError。
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import tests_pg

ROOT = Path(__file__).resolve().parent


class AnnualTaxSummaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("annual_tax_summary_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="annual-tax-secret")
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
                "payment_order_events", "payment_order_sources",
                "employee_payment_orders", "employee_payment_batches",
                "worker_tax_status_history",
                "expense_attachments", "expense_items", "expenses",
                "service_orders", "audit_logs", "users", "projects",
            ):
                db.execute(f"delete from {table}")
            self.admin_id = self._insert_user(db, "Admin", "admin")
            self.finance_id = self._insert_user(db, "Finance", "finance")
            self.manager_id = self._insert_user(db, "Manager", "manager")
            self.employee_id = self._insert_user(db, "Pedro", "employee")
            self.other_id = self._insert_user(db, "Maria", "employee")
            self.project_id = db.execute(
                "insert into projects (name,default_amount,tax_rate,is_active,created_at,project_type)"
                " values ('Hotel',0,0,1,?,'expense')",
                (self.module.now(),),
            ).lastrowid
            self.order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,"
                "client_order_number,start_date,created_by,created_at)"
                " values ('SO-AT-1','Client','Site','C1','2026-08-01',?,?)",
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
                "payment_order_events", "payment_order_sources",
                "employee_payment_orders", "employee_payment_batches",
                "worker_tax_status_history",
                "expense_attachments", "expense_items", "expenses",
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
            (name, f"{name.lower()}.{role}@test.invalid", "unused", role, self.module.now()),
        ).lastrowid

    def _login(self, user_id):
        with self.http.session_transaction() as session:
            session["user_id"] = user_id

    def _payment(self, employee_id, payment_type="salary", number="SL-2608-0001",
                 gross="100.00", status="draft", paid_at=None):
        with self.module.app.app_context():
            db = self.module.db()
            payment_id = db.execute(
                """
                insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,taxable_compensation_total,
                 accountable_reimbursement_total,tax_review_required_total,source_type,
                 paid_at,created_at,updated_at,created_by)
                values (?,?,?,?,'USD',?,'0','0',?,'0','0','0','manual',?,?,?,?)
                """,
                (number, employee_id, payment_type, status, gross, gross,
                 paid_at, self.module.now(), self.module.now(), self.admin_id),
            ).lastrowid
            db.commit()
            return payment_id

    def _component(self, payment_id, employee_id, *, code="base_pay",
                   amount="100.00", tax_category="taxable_compensation",
                   tax_status="1099", substantiated=None, source_type="payroll_generation",
                   source_id=None, service_date="2026-08-05",
                   work_order_id=None, superseded_at=None):
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
                 source_type, source_id, None, tax_category, tax_status,
                 None if substantiated is None else bool(substantiated),
                 "review_required" if tax_category == "tax_review_required" else "confirmed",
                 self.module.now(), superseded_at),
            ).lastrowid
            db.commit()
            return component_id

    def _batch(self, employee_id, *, status="issued", issued_at="2026-01-05T10:00:00-05:00",
               batch_number="PB-2601-0001"):
        with self.module.app.app_context():
            db = self.module.db()
            batch_id = db.execute(
                """
                insert into employee_payment_batches
                (batch_number,employee_id,currency,bank_account_id,payment_method,
                 check_number,total_amount,payment_count,status,issued_at,created_at,updated_at)
                values (?,?,'USD',null,'check','',0,0,?,?,?,?)
                """,
                (batch_number, employee_id, status, issued_at,
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
            return batch_id

    def _attach_batch(self, payment_id, batch_id):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                "update employee_payment_orders set batch_id=? where id=?",
                (batch_id, payment_id),
            )
            db.commit()

    def _review(self, component_id, new_category, reason="Receipt provided and verified."):
        """直接追加一条复核（服务层需要 app context）。"""
        with self.module.app.app_context():
            from flask import g
            g.user = {"id": self.admin_id, "name": "Admin"}
            previous = self.module.record_tax_review(component_id, new_category, reason,
                                                     self.admin_id)
            self.module.db().commit()
            return previous

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------
    def _summary(self, filters=None, default_year=None):
        with self.module.app.app_context():
            return self.module.annual_tax_summary_rows(filters, default_year)

    def _components(self, filters=None, default_year=None):
        with self.module.app.app_context():
            return self.module.annual_tax_summary_component_rows(filters, default_year)

    def _row_for(self, data, employee_id, tax_status):
        found = [row for row in data["rows"]
                 if row["employee_id"] == employee_id and row["tax_status"] == tax_status]
        self.assertEqual(len(found), 1, f"应恰好一个 {tax_status} section：{data['rows']}")
        return found[0]

    # ------------------------------------------------------------------
    # 1 / 4 / 10：1099 汇总、三桶闭合、Recorded ≠ Paid
    # ------------------------------------------------------------------
    def test_1099_summary_buckets_and_closure(self):
        payment = self._payment(self.employee_id, number="SL-2608-0001", gross="150.00")
        self._component(payment, self.employee_id, code="base_pay", amount="100.00",
                        tax_category="taxable_compensation")
        self._component(payment, self.employee_id, code="expense_item", amount="20.00",
                        tax_category="accountable_reimbursement")
        self._component(payment, self.employee_id, code="meal_allowance", amount="30.00",
                        tax_category="tax_review_required")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        row = self._row_for(data, self.employee_id, "1099")
        self.assertEqual(str(row["compensation_amount"]), "100.00")
        self.assertEqual(row["compensation_label"], "Service Compensation")
        self.assertEqual(str(row["reimbursement_amount"]), "20.00")
        self.assertEqual(str(row["review_amount"]), "30.00")
        self.assertEqual(str(row["total_amount"]), "150.00")
        # Potential 1099 第一版 = Service Compensation（候选值）
        self.assertEqual(str(row["potential_1099_amount"]), "100.00")
        # 全部未付款：Recorded = 150，Paid = 0
        self.assertEqual(str(row["paid_amount"]), "0.00")
        self.assertEqual(str(row["unpaid_amount"]), "150.00")
        self.assertTrue(data["totals"]["closure_ok"])
        self.assertEqual(str(data["totals"]["total_amount"]), "150.00")

    # 2：W2 汇总
    def test_w2_summary_uses_candidate_wages(self):
        payment = self._payment(self.employee_id, number="SL-2609-0001")
        self._component(payment, self.employee_id, tax_status="W2",
                        tax_category="taxable_compensation", amount="120.00")
        self._component(payment, self.employee_id, tax_status="W2",
                        tax_category="tax_review_required", amount="10.00")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        row = self._row_for(data, self.employee_id, "W2")
        self.assertEqual(row["compensation_label"], "Taxable Compensation")
        self.assertEqual(str(row["w2_candidate_wages"]), "120.00")
        self.assertEqual(str(row["review_amount"]), "10.00")

    # 3：同一员工同年 1099 + W2 必须拆成两个 section
    def test_same_employee_same_year_1099_and_w2_split(self):
        payment = self._payment(self.employee_id, number="SL-2608-0002")
        self._component(payment, self.employee_id, tax_status="1099", amount="60.00")
        self._component(payment, self.employee_id, tax_status="W2", amount="40.00")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        self.assertEqual(len(data["rows"]), 2)
        row_1099 = self._row_for(data, self.employee_id, "1099")
        row_w2 = self._row_for(data, self.employee_id, "W2")
        self.assertEqual(str(row_1099["total_amount"]), "60.00")
        self.assertEqual(str(row_w2["total_amount"]), "40.00")
        self.assertEqual(str(data["totals"]["total_amount"]), "100.00")

    # 5：latest review 决定 effective category（原始快照不动）
    def test_latest_review_decides_effective_category(self):
        payment = self._payment(self.employee_id)
        component_id = self._component(payment, self.employee_id,
                                       tax_category="tax_review_required", amount="80.00")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        row = self._row_for(data, self.employee_id, "1099")
        self.assertEqual(str(row["review_amount"]), "80.00")
        self._review(component_id, "accountable_reimbursement")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        row = self._row_for(data, self.employee_id, "1099")
        self.assertEqual(str(row["reimbursement_amount"]), "80.00")
        self.assertEqual(str(row["review_amount"]), "0.00")
        self._review(component_id, "taxable_compensation")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        row = self._row_for(data, self.employee_id, "1099")
        self.assertEqual(str(row["compensation_amount"]), "80.00")
        with self.module.app.app_context():
            original = dict(self.module.db().execute(
                "select tax_category, amount, superseded_at from employee_payment_components"
                " where id=?", (component_id,)).fetchone())
        self.assertEqual(original["tax_category"], "tax_review_required")

    # 6：superseded 组件不进任何汇总
    def test_superseded_components_excluded(self):
        payment = self._payment(self.employee_id)
        self._component(payment, self.employee_id, amount="70.00")
        self._component(payment, self.employee_id, amount="999.00",
                        superseded_at="2026-09-01T00:00:00-05:00")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        self.assertEqual(str(data["totals"]["total_amount"]), "70.00")

    # 7 / 8：双日期模型 —— service 与 payment 两种口径
    def test_service_and_payment_year_basis(self):
        payment = self._payment(self.employee_id, status="paid",
                                paid_at="2026-01-05T10:00:00-05:00", number="SL-2512-0099")
        self._component(payment, self.employee_id, service_date="2025-12-31", amount="55.00")
        # service 口径归 2025
        data = self._summary({"year": "2025", "year_basis": "service_date"})
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["tax_year"], "2025")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        self.assertEqual(data["rows"], [])
        # payment 口径归 2026（order paid_at）
        data = self._summary({"year": "2026", "year_basis": "payment_date"})
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["tax_year"], "2026")
        self.assertEqual(str(data["rows"][0]["paid_amount"]), "55.00")
        data = self._summary({"year": "2025", "year_basis": "payment_date"})
        self.assertEqual(data["rows"], [])

    def test_payment_year_basis_uses_batch_issued_at(self):
        payment = self._payment(self.employee_id, number="SL-2601-0001")
        self._component(payment, self.employee_id, service_date="2025-12-20", amount="66.00")
        batch_id = self._batch(self.employee_id, issued_at="2026-01-05T10:00:00-05:00")
        self._attach_batch(payment, batch_id)
        data = self._summary({"year": "2026", "year_basis": "payment_date"})
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["tax_year"], "2026")

    # 9：payment_date NULL（未付款）在 payment 口径下排除并单独报告
    def test_payment_date_null_reported_as_excluded(self):
        payment = self._payment(self.employee_id, status="draft")
        self._component(payment, self.employee_id, service_date="2026-08-05", amount="44.00")
        data = self._summary({"year": "2026", "year_basis": "payment_date"})
        self.assertEqual(data["rows"], [])
        self.assertEqual(data["excluded"]["count"], 1)
        self.assertEqual(str(data["excluded"]["amount"]), "44.00")
        # service 口径下它仍然在（服务日期有值）
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        self.assertEqual(len(data["rows"]), 1)

    def test_invalid_year_basis_rejected(self):
        with self.assertRaises(ValueError):
            self._summary({"year": "2026", "year_basis": "created_at"})

    # 11：drill-down 与主汇总金额一致
    def test_drilldown_matches_summary(self):
        payment = self._payment(self.employee_id, gross="180.00")
        self._component(payment, self.employee_id, code="base_pay", amount="100.00",
                        tax_category="taxable_compensation")
        self._component(payment, self.employee_id, code="expense_item", amount="50.00",
                        tax_category="accountable_reimbursement")
        self._component(payment, self.employee_id, code="meal_allowance", amount="30.00",
                        tax_category="tax_review_required")
        data = self._summary({"year": "2026", "year_basis": "service_date"})
        row = self._row_for(data, self.employee_id, "1099")
        filters = {"year": "2026", "year_basis": "service_date",
                   "employee_id": str(self.employee_id), "tax_status": "1099"}
        all_components = self._components(dict(filters))
        self.assertEqual(str(all_components["totals"]["amount"]), str(row["total_amount"]))
        self.assertEqual(all_components["totals"]["amount"], row["total_amount"])
        for bucket, key in (("compensation", "compensation_amount"),
                            ("reimbursement", "reimbursement_amount"),
                            ("review", "review_amount")):
            part = self._components({**filters, "category": bucket})
            self.assertEqual(str(part["totals"]["amount"]), str(row[key]))

    # 12 / 13：权限隔离
    def test_permissions_isolation(self):
        payment = self._payment(self.employee_id)
        self._component(payment, self.employee_id)
        url = "/finance/annual-tax-summary?year=2026&year_basis=service_date"
        # employee 一律 403
        self._login(self.employee_id)
        self.assertEqual(self.http.get(url).status_code, 403)
        self.assertEqual(
            self.http.get("/finance/annual-tax-summary/export.xlsx?year=2026").status_code, 403)
        # manager 可看不可导出
        self._login(self.manager_id)
        self.assertEqual(self.http.get(url).status_code, 200)
        self.assertEqual(
            self.http.get("/finance/annual-tax-summary/export.xlsx?year=2026").status_code, 403)
        # finance 可看可导出
        self._login(self.finance_id)
        self.assertEqual(self.http.get(url).status_code, 200)
        response = self.http.get("/finance/annual-tax-summary/export.xlsx?year=2026")
        self.assertEqual(response.status_code, 200)
        # admin 可看，钻取页 200
        self._login(self.admin_id)
        self.assertEqual(self.http.get(url).status_code, 200)
        self.assertEqual(self.http.get(
            "/finance/annual-tax-summary/components?year=2026&year_basis=service_date"
        ).status_code, 200)

    # 14：复核后总记录金额不变、各桶变化、总额仍闭合（A review→accountable，B accountable→taxable）
    def test_review_changes_buckets_but_total_and_closure_hold(self):
        payment = self._payment(self.employee_id, gross="150.00")
        comp_a = self._component(payment, self.employee_id, code="meal_allowance",
                                 amount="60.00", tax_category="tax_review_required")
        comp_b = self._component(payment, self.employee_id, code="expense_item",
                                 amount="40.00", tax_category="accountable_reimbursement")
        self._component(payment, self.employee_id, code="base_pay", amount="50.00",
                        tax_category="taxable_compensation")
        filters = {"year": "2026", "year_basis": "service_date"}
        self._review(comp_a, "accountable_reimbursement")
        self._review(comp_b, "taxable_compensation")
        data = self._summary(dict(filters))
        row = self._row_for(data, self.employee_id, "1099")
        self.assertEqual(str(row["compensation_amount"]), "90.00")   # 50 + 40
        self.assertEqual(str(row["reimbursement_amount"]), "60.00")  # 0 + 60
        self.assertEqual(str(row["review_amount"]), "0.00")
        self.assertEqual(str(row["total_amount"]), "150.00")         # 总记录金额不变
        self.assertTrue(data["totals"]["closure_ok"])
        # drill-down 与新分类一致
        parts = self._components({"year": "2026", "year_basis": "service_date",
                                  "employee_id": str(self.employee_id), "tax_status": "1099"})
        self.assertEqual(str(parts["totals"]["amount"]), "150.00")

    # 15：Payment / Ledger 数据零修改（汇总前后逐字段比对）
    def test_payment_data_unchanged_by_summary(self):
        payment = self._payment(self.employee_id, gross="120.00")
        component_id = self._component(payment, self.employee_id, amount="120.00")
        batch_id = self._batch(self.employee_id)
        self._attach_batch(payment, batch_id)

        def snapshot():
            with self.module.app.app_context():
                db = self.module.db()
                return (
                    dict(db.execute("select * from employee_payment_orders where id=?",
                                    (payment,)).fetchone()),
                    dict(db.execute("select * from employee_payment_components where id=?",
                                    (component_id,)).fetchone()),
                    dict(db.execute("select * from employee_payment_batches where id=?",
                                    (batch_id,)).fetchone()),
                )

        before = snapshot()
        self._summary({"year": "2026", "year_basis": "service_date"})
        self._summary({"year": "2026", "year_basis": "payment_date"})
        self._components({"year": "2026", "year_basis": "service_date"})
        after = snapshot()
        self.assertEqual(before, after)

    # 16：不筛任何条件时三桶闭合（生产 1,145 / $214,567.55 上线前另行只读核对）
    def test_no_filter_totals_close(self):
        p1 = self._payment(self.employee_id, number="SL-2608-0003", gross="110.00")
        self._component(p1, self.employee_id, amount="60.00",
                        tax_category="taxable_compensation")
        self._component(p1, self.employee_id, code="meal_allowance", amount="50.00",
                        tax_category="tax_review_required")
        p2 = self._payment(self.other_id, payment_type="expense", number="ER-2608-0001",
                           gross="70.00")
        self._component(p2, self.other_id, code="expense_item", amount="70.00",
                        tax_category="accountable_reimbursement", source_type="expense_item",
                        source_id=1)
        data = self._summary({"year": "", "year_basis": "service_date"})
        totals = data["totals"]
        self.assertTrue(totals["closure_ok"])
        bucket_sum = (totals["compensation_amount"] + totals["reimbursement_amount"]
                      + totals["review_amount"])
        self.assertEqual(bucket_sum, totals["total_amount"])
        self.assertEqual(str(totals["total_amount"]), "180.00")
        self.assertEqual(totals["component_count"], 3)


if __name__ == "__main__":
    unittest.main()
