"""Phase 6B：CPA Workpaper + Resolution Queue 回归测试。

覆盖用户列出的 22 项验收点：
 1 Mileage queue（队列摘要 + 实时佐证状态 + 人工复核候选）   2 Meal queue（含 config 摘要）
 3 Expense documentation queue               4 review → accountable
 5 review → taxable                          6 keep review
 7 original snapshot immutable               8 review history 连续
 9 Annual Summary 即时反映 review            10 Total Recorded 不变
11 Potential 1099 candidate 更新             12 Workpaper 员工分组
13 Recorded vs Paid 分开                     14 missing payment date 计数
15 cancelled source 排除                     16 W2/1099 混合就绪
17 Employee 403                              18 Manager 只读（POST review 403）
19 Finance/admin 可 review                   20 XLSX 合计与 UI 一致
21 XLSX 无 SSN/TIN/bank + 非申报标记          22 Payment/Batch/Ledger 零 mutation

铁律：
- Workpaper / Queue 只读；复核动作仍走 Phase 4A（只 INSERT review 行）；
- Recorded ≠ Paid；Potential 1099 是候选值（service_compensation_v1）；
- XLSX 顶部必须带 "Internal CPA Workpaper — Not an IRS Filing"。
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
    "service_orders", "audit_logs", "users", "projects",
)


class CpaWorkpaperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests_pg.activate()
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("cpa_workpaper_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="cpa-workpaper-secret")
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
            self.other_id = self._insert_user(db, "Maria", "employee")
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
    # 播种辅助（与 4B 测试同一套）
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

    def _batch(self, employee_id, *, status="issued", issued_at="2026-01-05T10:00:00-05:00",
               batch_number="PB-2601-0001", total="100.00", count=1):
        with self.module.app.app_context():
            db = self.module.db()
            batch_id = db.execute(
                """
                insert into employee_payment_batches
                (batch_number,employee_id,currency,bank_account_id,payment_method,
                 check_number,total_amount,payment_count,status,issued_at,created_at,updated_at)
                values (?,?,'USD',null,'check','',?,?,?,?,?,?)
                """,
                (batch_number, employee_id, total, count, status, issued_at,
                 self.module.now(), self.module.now()),
            ).lastrowid
            db.commit()
            return batch_id

    def _service_report(self, worker_id):
        """seed 一条最小 service_report（daily_report_id 有 FK，必须真实存在）。"""
        with self.module.app.app_context():
            db = self.module.db()
            order_id = db.execute(
                "insert into service_orders (order_number,client_name,site_address,"
                "client_order_number,start_date,created_by,created_at)"
                " values ('SO-6B-1','Client','Site','C1','2026-08-01',?,?)",
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
                  origin="110A", destination="220B"):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                """
                insert into service_report_mileage_evidence
                (report_id,worker_user_id,route_fingerprint,origin_address,
                 destination_address,status,generated_at)
                values (?,?,?, ?,?,?,?)
                """,
                (report_id, worker_id, f"fp-{report_id}", origin, destination,
                 status, self.module.now()),
            )
            db.commit()

    def _meal_config(self, default_category="tax_review_required"):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute(
                """
                insert into payroll_component_tax_config
                (component_code,display_name,default_tax_category,requires_substantiation,
                 effective_from,effective_to,is_active,created_at,updated_at)
                values ('meal_allowance','Meal Allowance',?,0,'2026-01-01',null,1,?,?)
                """,
                (default_category, self.module.now(), self.module.now()),
            )
            db.commit()

    def _review(self, component_id, new_category, reason="Mileage documentation verified."):
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
    def _workpaper(self, filters=None, default_year=None):
        with self.module.app.app_context():
            return self.module.cpa_workpaper_rows(filters, default_year)

    def _queues(self):
        with self.module.app.app_context():
            return self.module.tax_review_queue_summaries("2026")

    def _row_for(self, data, employee_id, tax_status):
        found = [row for row in data["rows"]
                 if row["employee_id"] == employee_id and row["tax_status"] == tax_status]
        self.assertEqual(len(found), 1, f"应恰好一个 {tax_status} section：{data['rows']}")
        return found[0]

    def _exception(self, data, issue_type):
        found = [e for e in data["exceptions"] if e["issue_type"] == issue_type]
        self.assertTrue(found, f"缺少 exception {issue_type}：{data['exceptions']}")
        return found[0]

    def _payment_snapshot(self):
        with self.module.app.app_context():
            db = self.module.db()
            return {
                t: db.execute(
                    f"select count(*), coalesce(sum(id),0),"
                    f" md5(coalesce(string_agg(q::text, '|' order by id),'')) from {t} q"
                ).fetchone()[:3]
                for t in ("employee_payment_batches", "employee_payment_orders",
                          "employee_payment_components", "payment_order_sources")
            }

    # ------------------------------------------------------------------
    # 1-3：三个 Queue
    # ------------------------------------------------------------------
    def test_queue_a_mileage_summary_and_candidates(self):
        report_id = self._service_report(self.employee_id)
        payment = self._payment(self.employee_id, number="SL-2608-0002", gross="60.00")
        c1 = self._component(payment, self.employee_id, code="self_drive_allowance",
                             amount="60.00", tax_category="tax_review_required",
                             substantiated=False, service_date="2026-08-05",
                             daily_report_id=report_id)
        # 无佐证：no evidence
        queues = self._queues()
        self.assertEqual(queues["queue_a_mileage"]["count"], 1)
        self.assertEqual(str(queues["queue_a_mileage"]["amount"]), "60.00")
        self.assertEqual(queues["queue_a_mileage"]["employee_count"], 1)
        self.assertEqual(queues["queue_a_mileage"]["verified_candidates"]["count"], 0)
        # 佐证后来补齐 → 人工复核候选（effective 不自动变化）
        self._evidence(report_id, self.employee_id)
        queues = self._queues()
        self.assertEqual(queues["queue_a_mileage"]["verified_candidates"]["count"], 1)
        self.assertEqual(str(queues["queue_a_mileage"]["verified_candidates"]["amount"]), "60.00")
        with self.module.app.app_context():
            rows = self.module.tax_review_rows(
                {"effective_category": "tax_review_required", "year": "2026"})["rows"]
        target = [r for r in rows if r["component_id"] == c1][0]
        self.assertEqual(target["mileage_evidence_status"], "evidence_success")
        self.assertEqual(target["mileage_evidence_origin"], "110A")
        # evidence failed 状态
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("delete from service_report_mileage_evidence")
            db.execute(
                "insert into service_report_mileage_evidence"
                " (report_id,worker_user_id,route_fingerprint,status,generated_at)"
                f" values ({report_id},?,'fp','failed',?)", (self.employee_id, self.module.now()))
            db.commit()
        with self.module.app.app_context():
            rows = self.module.tax_review_rows(
                {"effective_category": "tax_review_required", "year": "2026"})["rows"]
        target = [r for r in rows if r["component_id"] == c1][0]
        self.assertEqual(target["mileage_evidence_status"], "evidence_failed")

    def test_queue_b_meal_summary_with_config(self):
        self._meal_config(default_category="tax_review_required")
        payment = self._payment(self.employee_id, number="SL-2608-0003", gross="50.00")
        self._component(payment, self.employee_id, code="meal_allowance",
                        amount="50.00", tax_category="tax_review_required",
                        service_date="2026-08-06")
        queues = self._queues()
        self.assertEqual(queues["queue_b_meal"]["count"], 1)
        self.assertEqual(str(queues["queue_b_meal"]["amount"]), "50.00")
        self.assertEqual(queues["meal_config"]["default_tax_category"], "tax_review_required")
        self.assertEqual(queues["meal_config"]["display_name"], "Meal Allowance")

    def test_queue_c_expense_documentation(self):
        payment = self._payment(self.employee_id, payment_type="expense",
                                number="ER-2608-0004", gross="41.65")
        self._component(payment, self.employee_id, code="expense_item",
                        amount="41.65", tax_category="tax_review_required",
                        source_type="expense_item", source_id=9001,
                        service_date="2026-08-18")
        queues = self._queues()
        self.assertEqual(queues["queue_c_expense"]["count"], 1)
        self.assertEqual(str(queues["queue_c_expense"]["amount"]), "41.65")

    # ------------------------------------------------------------------
    # 4-11：review 动作 → Annual Summary / Workpaper 即时反映
    # ------------------------------------------------------------------
    def _seed_review_target(self, amount="100.00", code="self_drive_allowance"):
        payment = self._payment(self.employee_id, number="SL-2608-0010", gross=amount)
        component_id = self._component(payment, self.employee_id, code=code,
                                       amount=amount, tax_category="tax_review_required",
                                       substantiated=False)
        return component_id

    def _workpaper_row(self, filters=None):
        data = self._workpaper(filters or {"year": "2026", "year_basis": "service_date"})
        return data, self._row_for(data, self.employee_id, "1099")

    def test_review_to_accountable_updates_summary(self):
        component_id = self._seed_review_target("100.00")
        response = self.http.post(f"/finance/tax-review/{component_id}/review",
                                  data={"action": "accountable",
                                        "reason": "Mileage documentation verified."})
        self.assertEqual(response.status_code, 302)
        data, row = self._workpaper_row()
        self.assertEqual(str(row["review_amount"]), "0.00")
        self.assertEqual(row["review_count"], 0)
        self.assertEqual(str(row["reimbursement_amount"]), "100.00")
        self.assertEqual(str(row["total_amount"]), "100.00")
        self.assertEqual(str(row["potential_1099_amount"]), "0.00")
        self.assertTrue(data["totals"]["closure_ok"])

    def test_review_to_taxable_updates_potential_1099(self):
        component_id = self._seed_review_target("100.00")
        self.http.post(f"/finance/tax-review/{component_id}/review",
                       data={"action": "taxable", "reason": "Treat as compensation."})
        data, row = self._workpaper_row()
        self.assertEqual(str(row["review_amount"]), "0.00")
        self.assertEqual(str(row["compensation_amount"]), "100.00")
        self.assertEqual(str(row["potential_1099_amount"]), "100.00")
        self.assertEqual(str(row["total_amount"]), "100.00")

    def test_keep_review_and_original_immutable_and_history_chain(self):
        component_id = self._seed_review_target("100.00")
        # keep：分类不变也允许记一条（0293 后）
        response = self.http.post(f"/finance/tax-review/{component_id}/review",
                                  data={"action": "keep", "reason": "Awaiting CPA."})
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            db = self.module.db()
            snapshot = db.execute(
                "select tax_category from employee_payment_components where id=?",
                (component_id,)).fetchone()
            self.assertEqual(snapshot["tax_category"], "tax_review_required")
            reviews = db.execute(
                "select previous_tax_category, new_tax_category"
                " from employee_payment_tax_reviews where component_id=?"
                " order by id", (component_id,)).fetchall()
            self.assertEqual(reviews[0]["previous_tax_category"], "tax_review_required")
            self.assertEqual(reviews[0]["new_tax_category"], "tax_review_required")
        # 第二次复核：previous = 当前有效分类（审计链连续）
        self._review(component_id, "accountable_reimbursement")
        with self.module.app.app_context():
            db = self.module.db()
            reviews = db.execute(
                "select previous_tax_category, new_tax_category"
                " from employee_payment_tax_reviews where component_id=?"
                " order by id", (component_id,)).fetchall()
            self.assertEqual(reviews[1]["previous_tax_category"], "tax_review_required")
            self.assertEqual(reviews[1]["new_tax_category"], "accountable_reimbursement")
        data, row = self._workpaper_row()
        self.assertEqual(str(row["reimbursement_amount"]), "100.00")
        self.assertEqual(str(row["total_amount"]), "100.00")

    # ------------------------------------------------------------------
    # 12-16：Workpaper 结构
    # ------------------------------------------------------------------
    def test_workpaper_grouping_recorded_paid_split_and_exceptions(self):
        batch_id = self._batch(self.employee_id, total="100.00", count=1)
        payment = self._payment(self.employee_id, number="SL-2608-0011",
                                gross="100.00", status="paid", batch_id=batch_id,
                                paid_at=None)
        self._component(payment, self.employee_id, amount="60.00")
        self._component(payment, self.employee_id, code="meal_allowance",
                        amount="40.00", tax_category="tax_review_required")
        # 另一员工：未入批次 → missing payment date
        payment2 = self._payment(self.other_id, number="SL-2608-0012", gross="20.00")
        self._component(payment2, self.other_id, amount="20.00",
                        service_date="2026-08-07")
        # cancelled 无组件订单
        cancelled = self._payment(self.employee_id, number="ER-2609-0098",
                                  gross="3894.70", status="cancelled")
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update employee_payment_orders set source_id=7001,"
                       " source_type='expense' where id=?", (cancelled,))
            db.commit()
        data, row = self._workpaper_row()
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(str(row["total_amount"]), "100.00")
        # 批次 issued_at → payment_date 解析 → Paid
        self.assertEqual(str(row["paid_amount"]), "100.00")
        self.assertEqual(str(data["totals"]["total_amount"]), "120.00")
        self.assertEqual(str(data["totals"]["paid_amount"]), "100.00")
        # exceptions
        missing = self._exception(data, "missing_payment_date")
        self.assertEqual(missing["count"], 1)
        self.assertEqual(str(missing["amount"]), "20.00")
        cancelled_exc = self._exception(data, "cancelled_source_missing")
        self.assertEqual(cancelled_exc["count"], 1)
        self.assertEqual(str(cancelled_exc["amount"]), "3894.70")
        # cancelled 订单不计入 recorded（无组件）
        self.assertEqual(str(data["totals"]["total_amount"]), "120.00")
        self.assertTrue(data["totals"]["closure_ok"])

    def test_workpaper_mixed_status_ready(self):
        payment = self._payment(self.employee_id, number="SL-2608-0013", gross="100.00")
        self._component(payment, self.employee_id, tax_status="1099", amount="60.00")
        self._component(payment, self.employee_id, tax_status="W2", amount="40.00")
        data = self._workpaper({"year": "2026", "year_basis": "service_date"})
        self.assertEqual(len(data["rows"]), 2)
        row_1099 = self._row_for(data, self.employee_id, "1099")
        row_w2 = self._row_for(data, self.employee_id, "W2")
        self.assertEqual(str(row_1099["potential_1099_amount"]), "60.00")
        self.assertEqual(str(row_w2["w2_candidate_wages"]), "40.00")

    # ------------------------------------------------------------------
    # 17-19：权限
    # ------------------------------------------------------------------
    def test_permissions_employee_manager_finance_admin(self):
        self._seed_review_target("100.00")
        with self.module.app.app_context():
            component_id = self.module.tax_review_rows(
                {"effective_category": "tax_review_required", "year": "2026"}
            )["rows"][0]["component_id"]
        # employee：workpaper / tax-review 全 403
        self._login(self.other_id)
        self.assertEqual(self.http.get("/finance/cpa-workpaper").status_code, 403)
        self.assertEqual(self.http.get("/finance/tax-review").status_code, 403)
        self.assertEqual(self.http.get("/finance/cpa-workpaper/export.xlsx").status_code, 403)
        # manager：可看，不可 review
        self._login(self.manager_id)
        self.assertEqual(self.http.get("/finance/cpa-workpaper").status_code, 200)
        self.assertEqual(self.http.get("/finance/cpa-workpaper/export.xlsx").status_code, 403)
        self.assertEqual(self.http.get("/finance/tax-review").status_code, 200)
        self.assertEqual(self.http.post(
            f"/finance/tax-review/{component_id}/review",
            data={"action": "accountable", "reason": "x"}).status_code, 403)
        # finance：可看可 review 可导出
        self._login(self.finance_id)
        self.assertEqual(self.http.get("/finance/cpa-workpaper").status_code, 200)
        self.assertEqual(self.http.get("/finance/cpa-workpaper/export.xlsx").status_code, 200)
        self.assertEqual(self.http.post(
            f"/finance/tax-review/{component_id}/review",
            data={"action": "keep", "reason": "ok"}).status_code, 302)

    # ------------------------------------------------------------------
    # 20-22：XLSX 与零 mutation
    # ------------------------------------------------------------------
    def test_xlsx_export_sheets_banner_totals_and_no_sensitive_data(self):
        payment = self._payment(self.employee_id, number="SL-2608-0014", gross="150.00")
        self._component(payment, self.employee_id, amount="100.00")
        self._component(payment, self.employee_id, code="meal_allowance",
                        amount="50.00", tax_category="tax_review_required")
        before = self._payment_snapshot()
        response = self.http.get("/finance/cpa-workpaper/export.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        archive = zipfile.ZipFile(__import__("io").BytesIO(response.data))
        sheet_names = re.findall(
            r"<sheet name=\"([^\"]+)\"", archive.read("xl/workbook.xml").decode())
        self.assertEqual(sheet_names, ["Employee Summary", "Component Detail",
                                       "Outstanding Review", "Closing Exceptions"])
        sheet1 = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("Internal CPA Workpaper", sheet1)
        self.assertIn("Not an IRS Filing", sheet1)
        self.assertIn("year_basis=service_date", sheet1)
        self.assertIn("service_compensation_v1", sheet1)
        # 合计行与 UI totals 一致（150.00 recorded / 100.00 compensation）
        self.assertIn("<v>150.00</v>", sheet1)
        self.assertIn("<v>100.00</v>", sheet1)
        # 敏感字段只可能出现在单元格文本里；OOXML 样板（reporting 等）不算。
        all_xml = b"".join(archive.read(n) for n in archive.namelist()).lower()
        cell_texts = b"|".join(re.findall(rb"<t>(.*?)</t>", all_xml)).lower()
        for marker in (b"ssn", b"social security", b"tin:", b"tin=", b"tin ",
                       b"bank_account", b"routing number", b"account number"):
            self.assertNotIn(marker, cell_texts)
        # 零 mutation
        self.assertEqual(self._payment_snapshot(), before)

    def test_workpaper_read_only_zero_mutation(self):
        self._seed_review_target("100.00")
        before = self._payment_snapshot()
        for _ in range(2):
            self.http.get("/finance/cpa-workpaper")
            self.http.get("/finance/cpa-workpaper",
                          query_string={"year": "2026", "year_basis": "payment_date"})
        self.assertEqual(self._payment_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
