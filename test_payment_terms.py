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


if __name__ == "__main__":
    unittest.main()
