import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class PaymentTermsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_payment_terms_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            for table in (
                "email_delivery_logs", "invoice_items", "invoices", "service_orders",
                "clients", "audit_logs", "users",
            ):
                connection.execute(f"delete from {table}")
            term = connection.execute(
                "select id from payment_terms where name = '20日截止、次月19日付款'"
            ).fetchone()
            self.term_id = term["id"]
            self.user_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Admin', 'admin@example.com', 'unused', 'admin', 1, '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.employee_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, country_code, created_at)
                values ('Technician', 'tech@example.com', 'unused', 'employee', 1, 'US', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, payment_term_id, created_at)
                values ('99999', 'Payment Term Client', 'PT Client', ?, '2026-08-13T00:00:00')
                """,
                (self.term_id,),
            ).lastrowid
            self.open_order_id = self._insert_order(connection, "SO-OPEN", "open")
            self.closed_order_id = self._insert_order(connection, "SO-CLOSED", "closed")
            self.eligible_invoice_id = self._insert_invoice(connection, "INV-ELIGIBLE", self.open_order_id)
            self.closed_invoice_id = self._insert_invoice(connection, "INV-CLOSED", self.closed_order_id)
            self.sent_invoice_id = self._insert_invoice(connection, "INV-SENT", self.open_order_id)
            connection.execute(
                """
                insert into email_delivery_logs (entity_type, entity_id, recipient, subject, sent_at)
                values ('invoice', ?, 'client@example.com', 'Sent invoice', '2026-08-13T00:00:00')
                """,
                (self.sent_invoice_id,),
            )
            self.paid_invoice_id = self._insert_invoice(connection, "INV-PAID", self.open_order_id, paid=True)
            connection.commit()
        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = self.user_id

    def _insert_order(self, connection, number, status):
        return connection.execute(
            """
            insert into service_orders (
                order_number, client_id, client_name, site_address, client_order_number,
                status, created_by, created_at
            ) values (?, ?, 'Payment Term Client', 'Test Site', ?, ?, ?, '2026-08-13T00:00:00')
            """,
            (number, self.client_id, number, status, self.user_id),
        ).lastrowid

    def _insert_invoice(self, connection, number, order_id, paid=False):
        return connection.execute(
            """
            insert into invoices (
                invoice_number, client_id, service_order_id, issue_date, due_date,
                status, paid_at, created_by, created_at
            ) values (?, ?, ?, '2026-08-21', '2026-09-20', 'completed', ?, ?, '2026-08-13T00:00:00')
            """,
            (number, self.client_id, order_id, "2026-08-30T00:00:00" if paid else None, self.user_id),
        ).lastrowid

    def test_cutoff_boundaries_and_month_end(self):
        term = {
            "rule_type": "monthly_cutoff", "fixed_days": 30, "cutoff_day": 20,
            "due_day": 19, "before_due_months": 1, "after_due_months": 2,
        }
        self.assertEqual(str(self.module.calculate_payment_due_date("2026-08-20", term)), "2026-09-19")
        self.assertEqual(str(self.module.calculate_payment_due_date("2026-08-21", term)), "2026-10-19")
        end_of_month_term = dict(term, due_day=31)
        self.assertEqual(str(self.module.calculate_payment_due_date("2027-01-20", end_of_month_term)), "2027-02-28")

    def test_payment_term_rule_requires_only_relevant_fields(self):
        with self.module.app.test_request_context(
            "/settings/payment-terms", method="POST",
            data={"name": "Fixed", "rule_type": "fixed_days", "fixed_days": "45"},
        ):
            values = self.module.payment_term_form_values()
            self.assertEqual(values["fixed_days"], 45)
            self.assertIsNone(values["cutoff_day"])
        with self.module.app.test_request_context(
            "/settings/payment-terms", method="POST",
            data={"name": "Cutoff", "rule_type": "monthly_cutoff", "cutoff_day": "20"},
        ):
            with self.assertRaisesRegex(ValueError, "请填写到期日"):
                self.module.payment_term_form_values()

    def test_preview_and_recalculation_protect_sent_paid_and_closed_invoices(self):
        response = self.http.get("/settings/payment-terms")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("INV-ELIGIBLE", page)
        self.assertNotIn("INV-CLOSED", page)
        self.assertNotIn("INV-SENT", page)
        self.assertNotIn("INV-PAID", page)
        self.assertIn("2026-10-19", page)

        response = self.http.post("/settings/payment-terms/recalculate-invoices", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.module.app.app_context():
            connection = self.module.db()
            due_dates = {
                row["invoice_number"]: row["due_date"]
                for row in connection.execute("select invoice_number, due_date from invoices").fetchall()
            }
            self.assertEqual(due_dates["INV-ELIGIBLE"], "2026-10-19")
            self.assertEqual(due_dates["INV-CLOSED"], "2026-09-20")
            self.assertEqual(due_dates["INV-SENT"], "2026-09-20")
            self.assertEqual(due_dates["INV-PAID"], "2026-09-20")

    def test_navigation_and_client_assignment_are_visible(self):
        response = self.http.get("/clients")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("账期方案", page)
        self.assertIn("20日截止、次月19日付款", page)
        self.assertIn("系统配置", page)

    def test_shared_report_export_generates_xlsx(self):
        response = self.http.post(
            "/reports/export-visible.xlsx",
            json={
                "title": "测试报表",
                "headers": ["编号", "金额"],
                "rows": [["INV-001", "$100.00"]],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(response.data.startswith(b"PK"))
        response = self.http.get("/service-orders")
        page = response.get_data(as_text=True)
        self.assertIn("report-export.js", page)
        self.assertIn("export-visible.xlsx", page)

    def test_login_page_renders_without_authenticated_session(self):
        with self.http.session_transaction() as session:
            session.clear()
        response = self.http.get("/login?next=/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("登录", response.get_data(as_text=True))

    def test_service_report_worker_rows_and_long_distance_warning(self):
        with self.module.app.app_context():
            self.module.db().execute(
                "update service_orders set start_date = '2026-08-13' where id = ?",
                (self.open_order_id,),
            )
            self.module.db().commit()
        response = self.http.post(
            f"/service-orders/{self.open_order_id}/reports/new",
            data={
                "save_token": "travel-row-test-token",
                "report_date": "2026-08-13",
                "actual_work_date": "2026-08-13",
                "arrival_time_hour": "08", "arrival_time_minute": "00",
                "departure_time_hour": "16", "departure_time_minute": "00",
                "mileage_billing_method": "per_person",
                "worker_user_id": [str(self.employee_id)],
                "worker_travel_mode": ["following"],
                "worker_driving_miles": ["600"],
                "worker_travel_hours": ["8"],
                "worker_public_transport_hours": [""],
                "worker_work_description": ["现场协助"],
                "site_address": "Test Site",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            report = self.module.db().execute(
                "select * from service_reports where service_order_id = ? order by id desc limit 1",
                (self.open_order_id,),
            ).fetchone()
            worker = self.module.db().execute(
                "select * from service_report_workers where report_id = ?", (report["id"],)
            ).fetchone()
            self.assertEqual(report["driving_miles"], 600)
            self.assertEqual(report["travel_hours"], 8)
            self.assertEqual(worker["travel_mode"], "following")
            self.assertEqual(worker["work_description"], "现场协助")
            order = dict(self.module.db().execute(
                "select * from service_orders where id = ?", (self.open_order_id,)
            ).fetchone())
            order["buyer_owner"] = "Test Owner"
            document_bytes = self.module.build_service_report_docx(report, order)
            self.assertTrue(document_bytes.startswith(b"PK"))
            warnings = self.module.excessive_following_mileage_rows(self.open_order_id)
            self.assertEqual(len(warnings), 1)
            grade_id = self.module.db().execute(
                """
                insert into employee_grades (
                    grade_name, car_allowance_method, car_mileage_rate, car_hourly_rate, created_at
                ) values ('Travel Test', 'mileage', 0.5, 10, '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.module.db().execute(
                "update users set employee_grade_id = ? where id = ?",
                (grade_id, self.employee_id),
            )
            self.module.db().commit()
            payroll = self.module.payroll_rows_for_range(
                self.module.date(2026, 8, 6), self.module.date(2026, 8, 19),
                self.module.date(2026, 9, 2), str(self.employee_id),
            )
            self.assertEqual(payroll["rows"][0]["car_allowance"], 80)

    def test_sanhe_service_order_number_warning_in_list_and_calendar(self):
        self.assertEqual(
            self.module.client_order_number_warning(
                "Sanhe Tongfei Refrigeration Co., Ltd.", "SHPG123456789012"
            ),
            "",
        )
        self.assertTrue(
            self.module.client_order_number_warning("三河同飞制冷股份有限公司", "SO-INVALID")
        )
        self.assertEqual(
            self.module.client_order_number_warning("Another Customer", "SO-INVALID"),
            "",
        )
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                "update clients set name = 'Sanhe Tongfei Refrigeration Co., Ltd.' where id = ?",
                (self.client_id,),
            )
            connection.execute(
                "update service_orders set client_order_number = 'SO-INVALID', start_date = '2026-08-13' where id = ?",
                (self.open_order_id,),
            )
            connection.commit()

        list_page = self.http.get("/service-orders").get_data(as_text=True)
        self.assertIn("client-order-number-warning", list_page)
        self.assertIn("SO-INVALID", list_page)
        calendar_page = self.http.get("/service-orders/calendar?year=2026&month=8").get_data(as_text=True)
        self.assertIn("client-order-number-warning", calendar_page)
        self.assertIn("SO-INVALID", calendar_page)


if __name__ == "__main__":
    unittest.main()
