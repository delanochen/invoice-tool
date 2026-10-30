"""Late report reconciliation against real PostgreSQL invoice_test only."""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import test_payroll_tax_components as fixture
from invoice_tool.payroll.reconciliation import build_reconciliation_services, is_rounding_difference

class PayrollLateReportsTest(unittest.TestCase):
    setUpClass = classmethod(fixture.PayrollTaxComponentTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PayrollTaxComponentTest.tearDownClass.__func__)
    setUp = fixture.PayrollTaxComponentTest.setUp
    _login = fixture.PayrollTaxComponentTest._login
    _report = fixture.PayrollTaxComponentTest._report
    _generate = fixture.PayrollTaxComponentTest._generate
    _payment = fixture.PayrollTaxComponentTest._payment
    _components = fixture.PayrollTaxComponentTest._components

    def pending(self):
        with self.module.app.app_context():
            return self.module.payroll_reconciliation_rows(fixture.PERIOD_START)

    def confirm(self, row):
        return self.http.post("/employee-payments/payroll-reconciliation/confirm", data={
            "employee_id":row["employee_id"],"period_start":row["period_start"],
            "revision":row["revision"]})

    def seed_late(self, paid=False):
        self._report(self.order1,"2026-08-03")
        self._generate()
        original=self._payment()
        if paid:
            with self.module.app.app_context():
                db=self.module.db()
                db.execute("update employee_payment_orders set status='paid' where id=?",(original["id"],))
                db.commit()
        original_lines=self._components(original["id"])
        report_id=self._report(self.order1,"2026-08-04")
        return original,original_lines,report_id

    def test_paid_sl_is_immutable_and_difference_creates_one_draft(self):
        original,lines,_=self.seed_late(paid=True)
        row=self.pending()[0]
        self.assertEqual(row["difference"],Decimal("195.00"))
        self.assertEqual(row["paid"],Decimal(str(original["gross_amount"])))
        self.assertTrue(row["can_create"],row["blocked_reason"])
        self.assertEqual(self.confirm(row).status_code,302)
        adjustment=self._payment()
        self.assertEqual(adjustment["source_type"],"system_payroll_adjustment")
        self.assertEqual(adjustment["status"],"draft")
        self.assertEqual(Decimal(str(adjustment["gross_amount"])),row["difference"])
        self.assertEqual(self._components(original["id"]),lines)
        self.assertEqual(self.pending(),[])
        self.confirm(row)
        self.assertEqual(self._payment()["id"],adjustment["id"])
        self._generate()
        self.assertEqual(self._payment()["id"],adjustment["id"])

    def test_unpaid_original_stays_intact(self):
        original,lines,_=self.seed_late()
        self.confirm(self.pending()[0])
        self.assertEqual(self._components(original["id"]),lines)
        self.assertEqual(self.pending(),[])

    def test_rounding_difference_threshold_is_two_cents(self):
        self.assertTrue(is_rounding_difference(Decimal("0.00")))
        self.assertTrue(is_rounding_difference(Decimal("0.02")))
        self.assertTrue(is_rounding_difference(Decimal("-0.02")))
        self.assertFalse(is_rounding_difference(Decimal("0.03")))
        self.assertFalse(is_rounding_difference(Decimal("-0.03")))

    def test_negative_difference_requires_review_and_cannot_create(self):
        report=self._report(self.order1,"2026-08-03")
        self._generate()
        original=self._payment()
        with self.module.app.app_context():
            db=self.module.db()
            db.execute("update service_reports set total_service_hours=4 where id=?",(report,))
            db.commit()
        row=self.pending()[0]
        self.assertLess(row["difference"],0)
        self.assertEqual(row["status"],"negative_review")
        self.assertEqual(row["status_label"],"负差额待审核")
        self.assertFalse(row["can_create"])
        self.confirm(row)
        self.assertEqual(self._payment()["id"],original["id"])

    def test_stale_confirmation_is_rejected(self):
        original,_,_=self.seed_late()
        row=self.pending()[0]
        self._report(self.order1,"2026-08-05")
        self.confirm(row)
        self.assertEqual(self._payment()["id"],original["id"])

    def test_new_worker_in_previously_generated_period_is_detected(self):
        self._report(self.order1,"2026-08-03")
        self._generate()
        report=self._report(self.order2,"2026-08-04",writer=False)
        with self.module.app.app_context():
            db=self.module.db()
            worker=db.execute("insert into users(name,email,password_hash,role,is_active,employee_grade_id,created_at) values('New Worker','new@test.invalid','unused','employee',1,?,?)",(self.grade_id,self.module.now())).lastrowid
            db.execute("update service_report_workers set user_id=? where report_id=?",(worker,report))
            db.commit()
        row=next(row for row in self.pending() if row["employee_id"]==worker)
        self.assertEqual(row["recorded"],0)
        self.assertEqual(row["status"],"unbilled")
        self.assertEqual(row["status_label"],"尚未入单")
        self.assertFalse(row["can_create"])
        before_id=self._payment()["id"]
        self.confirm(row)
        self.assertEqual(self._payment()["id"],before_id)
        self.assertTrue(any(row["employee_id"]==worker for row in self.pending()))

    def test_notifications_deduplicate_and_include_actor(self):
        self.seed_late()
        with self.module.app.app_context():
            db=self.module.db()
            _,notify=build_reconciliation_services(self.module.__dict__)
            notify("Report Author","补录日报")
            db.commit()
            count=db.execute("select count(*) as n from messages where title='工资变动待核对'").fetchone()["n"]
            self.assertGreater(count,0)
            notify("Report Author","补录日报")
            db.commit()
            self.assertEqual(db.execute("select count(*) as n from messages where title='工资变动待核对'").fetchone()["n"],count)
            body=db.execute("select body from messages where title='工资变动待核对' order by id desc limit 1").fetchone()["body"]
            self.assertIn("Report Author",body)
            self.assertIn("195.00",body)

    def test_employee_cannot_view_or_confirm_other_payroll(self):
        self.seed_late()
        row=self.pending()[0]
        self._login(self.worker_id)
        self.assertEqual(self.http.get("/employee-payments/payroll-reconciliation").status_code,403)
        self.assertEqual(self.confirm(row).status_code,403)

    def test_queue_payment_center_and_calendar_render(self):
        self.seed_late()
        page=self.http.get("/employee-payments/payroll-reconciliation")
        self.assertEqual(page.status_code,200)
        self.assertIn("确认生成补发草稿",page.get_data(as_text=True))
        page=self.http.get("/employee-payments")
        self.assertEqual(page.status_code,200)
        self.assertIn("工资差额核对",page.get_data(as_text=True))
        page=self.http.get("/payroll/calendar?year=2026&month=8")
        self.assertEqual(page.status_code,200)
        self.assertIn("工资有变动",page.get_data(as_text=True))

    def test_unlinked_manual_sl_blocks_automatic_supplement(self):
        self.seed_late()
        self.http.post("/employee-payments/create",data={
            "employee_id":self.worker_id,"payment_type":"salary","gross_amount":"100"})
        row=self.pending()[0]
        self.assertFalse(row["can_create"])
        self.assertIn("手工SL",row["blocked_reason"])

    def test_failed_component_insert_rolls_back_entire_adjustment(self):
        original,_,_=self.seed_late()
        row=self.pending()[0]
        from database import PostgreSQLConnection
        execute=PostgreSQLConnection.execute
        def fail(conn,sql,params=None):
            if "insert into employee_payment_components" in sql.lower():
                raise ValueError("component test failure")
            return execute(conn,sql,params)
        with patch.object(PostgreSQLConnection,"execute",fail):
            self.confirm(row)
        self.assertEqual(self._payment()["id"],original["id"])

    def test_stale_unpaid_sl_cannot_be_submitted(self):
        original,_,_=self.seed_late()
        self.http.post(f"/employee-payments/{original['id']}/transition",data={"action":"submit"})
        with self.module.app.app_context():
            state=self.module.db().execute("select status from employee_payment_orders where id=?",(original["id"],)).fetchone()["status"]
        self.assertEqual(state,"draft")
        self.confirm(self.pending()[0])
        self.http.post(f"/employee-payments/{original['id']}/transition",data={"action":"submit"})
        with self.module.app.app_context():
            state=self.module.db().execute("select status from employee_payment_orders where id=?",(original["id"],)).fetchone()["status"]
        self.assertEqual(state,"pending_review")

    def test_report_save_hook_only_notifies_new_revision(self):
        original,_,report=self.seed_late()
        self.confirm(self.pending()[0])
        from flask import redirect
        def edit(report_id):
            with self.module.app.app_context():
                db=self.module.db()
                db.execute("update service_reports set total_service_hours=10 where id=?",(report_id,))
                db.commit()
            return redirect(f"/service-reports/{report_id}/edit")
        with patch.dict(self.module.app.view_functions,{"edit_service_report":edit}):
            self.http.post(f"/service-reports/{report}/edit")
            self.http.post(f"/service-reports/{report}/edit")
        with self.module.app.app_context():
            records=self.module.db().execute("select * from messages where title='工资变动待核对'").fetchall()
        self.assertEqual(len(records),1)
        self.assertIn("Admin",records[0]["body"])
        self.assertIn("2026-08-04",records[0]["body"])

    def test_calendar_payload_has_all_four_reconciliation_amounts(self):
        self.seed_late(paid=True)
        page=self.http.get("/payroll/calendar/batch?period_start=2026-08-03")
        self.assertEqual(page.status_code,200)
        row=page.get_json()["reconciliation"][0]
        self.assertEqual(row["difference"],195.0)
        self.assertEqual(row["expected"]-row["recorded"],195.0)
        self.assertGreater(row["paid"],0)

    def test_batch_gate_blocks_changed_payroll(self):
        original,_,_=self.seed_late()
        with self.module.app.app_context():
            reason=self.module.payment_batch_block_reason(original)
        self.assertIn("差额",reason)
