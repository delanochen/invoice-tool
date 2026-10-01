"""Phase 6C：CPA Policy Decision Package 回归测试。

覆盖用户列出的验收点（只读决策包 + 纯内存模拟）：
 1 三个 decision package 数字正确      2 simulate review→accountable
 3 simulate review→taxable             4 simulate keep review
 5 无效 target 报错                    6 缺失/已 superseded 组件报错
 7 空选择报错                          8 模拟零写入（快照比对）
 9 里程佐证盘点（evidence/起终点）     10 Reporting basis 数字
11 Identity readiness + checklist     12 页面权限（employee 403）
13 导出权限（manager 403）            14 XLSX 5 sheets + 非申报标记
15 XLSX 无 SSN/TIN/bank               16 页面无 Apply 按钮
17 Question for CPA 存在              18 Payment/Batch/Ledger 零 mutation

铁律：
- 模拟绝不写库（不 INSERT review、不 UPDATE 组件/付款单）；
- Total Recorded before == after，不闭合即模拟失败；
- 页面第一版只有 Export，没有 Apply。
"""
import importlib.util
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import tests_pg

ROOT = Path(__file__).resolve().parent

CLEANUP_TABLES = (
    "messages", "employee_payment_tax_reviews", "employee_payment_components",
    "payment_order_events", "payment_order_sources",
    "employee_payment_orders", "employee_payment_batches",
    "worker_tax_status_history", "service_report_mileage_evidence",
    "payroll_component_tax_config",
    "expense_attachments", "expense_items", "expenses",
    "service_orders", "audit_logs", "users", "projects", "settings",
)


class TaxPolicyDecisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("policy_decision_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="policy-decision-secret")
        cls.module.app.template_folder = str(ROOT / "templates")
        cls.module.app.static_folder = str(ROOT / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in CLEANUP_TABLES:
                db.execute(f"delete from {table}")
            self.admin_id = self._insert_user(db, "Admin", "admin")
            self.finance_id = self._insert_user(db, "Finance", "finance")
            self.manager_id = self._insert_user(db, "Manager", "manager")
            self.employee_id = self._insert_user(db, "Pedro", "employee")
            db.commit()
        self.http = self.module.app.test_client()
        self._login(self.admin_id)

    def tearDown(self):
        with self.module.app.app_context():
            db = self.module.db()
            for table in CLEANUP_TABLES:
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
                 gross="100.00", status="draft", paid_at=None, batch_id=None):
        with self.module.app.app_context():
            db = self.module.db()
            payment_id = db.execute(
                """
                insert into employee_payment_orders
                (payment_number,employee_id,payment_type,status,currency,gross_amount,
                 advance_offset,other_adjustment,net_amount,taxable_compensation_total,
                 accountable_reimbursement_total,tax_review_required_total,source_type,
                 batch_id,paid_at,created_at,updated_at,created_by)
                values (?,?,?,?,'USD',?,'0','0',?,'0','0','0','manual',?,?,?,?,?)
                """,
                (number, employee_id, payment_type, status, gross, gross,
                 batch_id, paid_at, self.module.now(), self.module.now(), self.admin_id),
            ).lastrowid
            db.commit()
            return payment_id

    def _component(self, payment_id, employee_id, *, code="base_pay",
                   amount="100.00", tax_category="taxable_compensation",
                   tax_status="1099", substantiated=None, source_type="payroll_generation",
                   source_id=None, service_date="2026-08-05", daily_report_id=None):
        with self.module.app.app_context():
            db = self.module.db()
            component_id = db.execute(
                """
                insert into employee_payment_components
                (payment_order_id,employee_id,component_code,component_name,amount,
                 service_date,work_order_id,source_type,source_id,daily_report_id,
                 tax_category,tax_status_snapshot,substantiated,review_status,created_at)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (payment_id, employee_id, code, code, amount, service_date, None,
                 source_type, source_id, daily_report_id, tax_category, tax_status,
                 None if substantiated is None else bool(substantiated),
                 "review_required" if tax_category == "tax_review_required" else "confirmed",
                 self.module.now()),
            ).lastrowid
            db.commit()
            return component_id

    def _service_report(self, worker_id):
        with self.module.app.app_context():
            db = self.module.db()
            order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,"
                "client_order_number,start_date,created_by,created_at)"
                " values ('SO-6C-1','Client','Site','C1','2026-08-01',?,?)",
                (self.admin_id, self.module.now()),
            ).lastrowid
            report_id = db.execute(
                "insert into service_reports (service_order_id,report_date,"
                "total_service_hours,travel_hours,public_transport_hours,driving_miles,"
                "created_by,created_at,updated_at)"
                " values (?,?,0,0,0,0,?,?,?)",
                (order_id, "2026-08-05", worker_id,
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
            return report_id

    def _evidence(self, report_id, worker_id, *, status="success",
                  origin="110A", destination="220B", miles=None):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                """
                insert into service_report_mileage_evidence
                (report_id,worker_user_id,route_fingerprint,origin_address,
                 destination_address,status,reported_miles,generated_at)
                values (?,?,?,?,?,?,?,?)
                """,
                (report_id, worker_id, f"fp-{report_id}", origin, destination,
                 status, miles, self.module.now()),
            )
            db.commit()

    def _seed_standard(self, *, with_paid=True):
        """标准 seed：1 taxable paid 200 + 1 taxable unpaid 100 + 3 review。"""
        if with_paid:
            paid = self._payment(self.employee_id, number="SL-2608-0101",
                                 gross="200.00", status="paid", paid_at="2026-09-15 10:00")
            self._component(paid, self.employee_id, code="base_pay", amount="200.00")
        unpaid = self._payment(self.employee_id, number="SL-2608-0102", gross="100.00")
        self._component(unpaid, self.employee_id, code="base_pay", amount="100.00")
        report_id = self._service_report(self.employee_id)
        mileage_pay = self._payment(self.employee_id, number="SL-2608-0103", gross="60.00")
        mileage = self._component(mileage_pay, self.employee_id,
                                  code="self_drive_allowance", amount="60.00",
                                  tax_category="tax_review_required", substantiated=False,
                                  service_date="2026-08-05", daily_report_id=report_id)
        meal_pay = self._payment(self.employee_id, number="SL-2608-0104", gross="50.00")
        meal = self._component(meal_pay, self.employee_id, code="meal_allowance",
                               amount="50.00", tax_category="tax_review_required",
                               service_date="2026-08-06")
        expense_pay = self._payment(self.employee_id, payment_type="expense",
                                    number="ER-2608-0105", gross="41.65")
        expense = self._component(expense_pay, self.employee_id, code="expense_item",
                                  amount="41.65", tax_category="tax_review_required",
                                  source_type="expense_item", source_id=9001,
                                  service_date="2026-08-18")
        return {"mileage": mileage, "meal": meal, "expense": expense,
                "report_id": report_id}

    def _packages(self):
        with self.module.app.app_context():
            return self.module.tax_policy_decision_packages("2026")

    def _payment_snapshot(self):
        with self.module.app.app_context():
            db = self.module.db()
            tables = ("employee_payment_batches", "employee_payment_orders",
                      "employee_payment_components", "employee_payment_tax_reviews",
                      "payment_order_sources")
            return {
                t: db.execute(
                    f"select count(*), coalesce(sum(id),0),"
                    f" md5(coalesce(string_agg(q::text, '|' order by id),'')) from {t} q"
                ).fetchone()[:3]
                for t in tables
            }

    # ------------------------------------------------------------------
    # 1：决策包数字
    # ------------------------------------------------------------------
    def test_decision_packages_numbers(self):
        seeded = self._seed_standard()
        data = self._packages()
        self.assertEqual(data["generated_note"],
                         "Internal Tax Policy Decision Package — Not an IRS Filing")
        before = data["totals_before"]
        self.assertEqual(str(before["service_compensation"]), "300.00")
        self.assertEqual(str(before["review_required"]), "151.65")
        self.assertEqual(str(before["total_recorded"]), "451.65")
        self.assertEqual(str(before["potential_1099_candidate"]), "300.00")

        mileage = data["decisions"]["mileage"]["current"]
        self.assertEqual(mileage["count"], 1)
        self.assertEqual(str(mileage["amount"]), "60.00")
        self.assertEqual(mileage["employee_count"], 1)
        self.assertEqual(mileage["work_order_count"], 0)
        self.assertEqual(mileage["daily_report_count"], 1)
        meal = data["decisions"]["meal_allowance"]["current"]
        self.assertEqual(str(meal["amount"]), "50.00")
        expense = data["decisions"]["expense_documentation"]["current"]
        self.assertEqual(str(expense["amount"]), "41.65")
        self.assertEqual(expense["attachments"][0]["attachment_count"], 0)

        # 三包 × 三选项，全部闭合
        for decision in data["decisions"].values():
            self.assertEqual(len(decision["options"]), 3)
            for option in decision["options"]:
                sim = option["simulation"]
                self.assertNotIn("error", sim)
                self.assertEqual(str(sim["before"]["total_recorded"]), "451.65")
                self.assertEqual(str(sim["after"]["total_recorded"]), "451.65")
            self.assertTrue(decision.get("question_for_cpa"))

    # ------------------------------------------------------------------
    # 2-4：simulate 三方向
    # ------------------------------------------------------------------
    def test_simulation_review_to_accountable(self):
        seeded = self._seed_standard()
        sim = self._simulate([seeded["mileage"]], "accountable_reimbursement")
        self.assertEqual(str(sim["before"]["review_required"]), "151.65")
        self.assertEqual(str(sim["after"]["review_required"]), "91.65")
        self.assertEqual(str(sim["after"]["reimbursements"]), "60.00")
        self.assertEqual(str(sim["after"]["service_compensation"]), "300.00")
        self.assertEqual(str(sim["after"]["potential_1099_candidate"]), "300.00")
        self.assertEqual(str(sim["after"]["total_recorded"]), "451.65")
        self.assertEqual(str(sim["affected_amount"]), "60.00")

    def test_simulation_review_to_taxable(self):
        seeded = self._seed_standard()
        sim = self._simulate([seeded["meal"]], "taxable_compensation")
        self.assertEqual(str(sim["after"]["review_required"]), "101.65")
        self.assertEqual(str(sim["after"]["service_compensation"]), "350.00")
        self.assertEqual(str(sim["after"]["potential_1099_candidate"]), "350.00")
        self.assertEqual(str(sim["after"]["total_recorded"]), "451.65")

    def test_simulation_keep_review_is_noop(self):
        seeded = self._seed_standard()
        sim = self._simulate([seeded["expense"]], "tax_review_required")
        for key in ("service_compensation", "reimbursements", "review_required",
                    "potential_1099_candidate", "total_recorded"):
            self.assertEqual(str(sim["before"][key]), str(sim["after"][key]))

    def _simulate(self, component_ids, target):
        with self.module.app.app_context():
            return self.module.tax_policy_simulation(component_ids, target)

    # ------------------------------------------------------------------
    # 5-7：模拟参数校验
    # ------------------------------------------------------------------
    def test_simulation_rejects_invalid_target(self):
        with self.assertRaises(ValueError):
            self._simulate([1], "never_reportable")

    def test_simulation_rejects_missing_or_superseded(self):
        with self.assertRaises(ValueError):
            self._simulate([999999], "taxable_compensation")
        seeded = self._seed_standard()
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update employee_payment_components set superseded_at=? where id=?",
                       (self.module.now(), seeded["meal"]))
            db.commit()
        with self.assertRaises(ValueError):
            self._simulate([seeded["meal"]], "taxable_compensation")

    def test_simulation_rejects_empty_selection(self):
        with self.assertRaises(ValueError):
            self._simulate([], "taxable_compensation")

    # ------------------------------------------------------------------
    # 8/18：模拟零写入
    # ------------------------------------------------------------------
    def test_simulation_and_packages_are_read_only(self):
        self._seed_standard()
        before = self._payment_snapshot()
        data = self._packages()
        for decision in data["decisions"].values():
            for option in decision["options"]:
                self.assertNotIn("error", option["simulation"])
        after = self._payment_snapshot()
        self.assertEqual(before, after)

    # ------------------------------------------------------------------
    # 9：里程佐证盘点
    # ------------------------------------------------------------------
    def test_mileage_evidence_summary(self):
        seeded = self._seed_standard()
        # 无佐证：miles 不可靠、miles_total=0
        data = self._packages()
        summary = data["decisions"]["mileage"]["current"]
        self.assertFalse(summary["miles_reliable"])
        self.assertEqual(str(summary["miles_total"]), "0.00")
        self.assertEqual(summary["evidence_rows"], 0)
        # 补佐证：origin/destination 齐全、reported_miles=42 → miles 可靠
        self._evidence(seeded["report_id"], self.employee_id, miles=42)
        data = self._packages()
        summary = data["decisions"]["mileage"]["current"]
        self.assertTrue(summary["miles_reliable"])
        self.assertEqual(str(summary["miles_total"]), "42.00")
        self.assertEqual(summary["origin_destination_reports"], 1)
        self.assertEqual(summary["evidence_rows"], 1)

    # ------------------------------------------------------------------
    # 10：Reporting basis
    # ------------------------------------------------------------------
    def test_reporting_basis_decision(self):
        self._seed_standard()
        data = self._packages()
        rb = data["reporting_basis"]
        self.assertEqual(rb["service_date"]["count"], 5)
        self.assertEqual(str(rb["service_date"]["amount"]), "451.65")
        self.assertEqual(rb["payment_date"]["count"], 1)
        self.assertEqual(str(rb["payment_date"]["amount"]), "200.00")
        self.assertEqual(rb["payment_date_unresolved"]["count"], 4)
        self.assertEqual(str(rb["payment_date_unresolved"]["amount"]), "251.65")
        self.assertTrue(rb["question_for_cpa"])

    # ------------------------------------------------------------------
    # 11：Identity readiness + checklist
    # ------------------------------------------------------------------
    def test_identity_readiness_and_checklist(self):
        self._seed_standard()
        data = self._packages()
        identity = data["identity_readiness"]
        # 测试库 company_* 未配置 → payer 全 missing
        self.assertEqual(identity["payer"]["legal_name"], "missing")
        self.assertEqual(identity["payer"]["ein"], "missing")
        self.assertEqual(identity["recipient"]["tin_storage"], "not_implemented")
        self.assertEqual(identity["recipient"]["legal_name"], "available")
        checklist = {item["item"]: item for item in data["readiness_checklist"]}
        self.assertEqual(checklist["Tax status complete"]["status"], "ready")
        self.assertEqual(checklist["Classification review resolved"]["status"],
                         "pending_decision")
        self.assertEqual(checklist["Reporting basis confirmed"]["status"],
                         "pending_decision")
        self.assertEqual(checklist["Filing method confirmed"]["status"],
                         "not_implemented")
        # 配置 payer 后变 available（只查存在性，不读值）
        with self.module.app.app_context():
            db = self.module.db()
            for key in ("company_name", "company_ein", "company_address"):
                db.execute("insert into settings (key,value) values (?,?)",
                           (key, f"secret-{key}-value"))
            db.commit()
        data = self._packages()
        for state in data["identity_readiness"]["payer"].values():
            self.assertEqual(state, "available")
        # 敏感值绝不进输出结构
        self.assertNotIn("secret-company-ein-value", repr(data))

    # ------------------------------------------------------------------
    # 12/13：权限
    # ------------------------------------------------------------------
    def test_page_permissions(self):
        self._seed_standard()
        for user_id, expected in ((self.admin_id, 200), (self.finance_id, 200),
                                  (self.manager_id, 200), (self.employee_id, 403)):
            self._login(user_id)
            response = self.http.get("/finance/policy-decisions")
            self.assertEqual(response.status_code, expected, f"user {user_id}")

    def test_export_permissions(self):
        self._seed_standard()
        for user_id, expected in ((self.admin_id, 200), (self.finance_id, 200),
                                  (self.manager_id, 403), (self.employee_id, 403)):
            self._login(user_id)
            response = self.http.get("/finance/policy-decisions/export.xlsx")
            self.assertEqual(response.status_code, expected, f"user {user_id}")

    # ------------------------------------------------------------------
    # 14/15/17：XLSX + 问题文案
    # ------------------------------------------------------------------
    def test_export_xlsx_structure_and_no_sensitive_data(self):
        self._seed_standard()
        response = self.http.get("/finance/policy-decisions/export.xlsx")
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(__import__("io").BytesIO(response.data))
        names = [n for n in archive.namelist() if n.startswith("xl/worksheets/")]
        self.assertEqual(len(names), 5)
        all_xml = b"".join(archive.read(n) for n in archive.namelist())
        self.assertIn("Internal Tax Policy Decision Package — Not an IRS Filing".encode(),
                      all_xml)
        # 敏感字段只可能出现在单元格文本；OOXML 样板（reporting 等）不算。
        # "tin_storage"（就绪检查项）含 tin 但不是 TIN 值 → 词边界匹配。
        cell_text_lower = all_xml.decode("utf-8", "replace").lower()
        cell_texts = "|".join(re.findall(r"<t>(.*?)</t>", cell_text_lower))
        for pattern in (r"\bssn\b", r"social security", r"\btin\b",
                        r"bank_account", r"routing number"):
            self.assertIsNone(re.search(pattern, cell_texts),
                              f"XLSX 不应包含敏感标记 {pattern}")
        # scenario sheet：Before 行 + 每包 3 选项 + Question
        texts = cell_texts
        self.assertIn("before", texts)
        self.assertIn("question for cpa", texts)
        self.assertIn("service_compensation_v1", texts)

    def test_questions_present_on_page(self):
        self._seed_standard()
        response = self.http.get("/finance/policy-decisions")
        page = response.data
        self.assertIn(b"How should these contractor mileage payments", page)
        self.assertIn(b"Should the fixed meal allowance", page)
        self.assertIn(b"Can this expense be treated as reimbursement", page)
        self.assertIn(b"Which date basis should control the year", page)

    # ------------------------------------------------------------------
    # 16：无 Apply 按钮
    # ------------------------------------------------------------------
    def test_page_has_no_apply(self):
        self._seed_standard()
        response = self.http.get("/finance/policy-decisions")
        # 无 Apply 表单/按钮（本页面只读；"apply" 仅可能出现在基础框架脚本里，
        # 因此检查本页专属的 action 链接与提交目标都不存在）。
        self.assertNotIn(b"/apply", response.data)
        self.assertNotIn(b"apply_tax", response.data)
        # 本页不提交任何税务表单（POST 端点只属于 4A 复核详情页）。
        self.assertNotIn(b"tax-review/", response.data)
        self.assertIn(b"Export Decision Package", response.data)


if __name__ == "__main__":
    unittest.main()
