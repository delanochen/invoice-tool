import sqlite3
import tempfile
import unittest
from pathlib import Path

from customer_report_logic import (
    migrate_historical_customer_reports,
    normalize_customer_report_choice,
)


REPO_DIR = Path(__file__).resolve().parent


class CustomerReportPayrollTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.connection = sqlite3.connect(Path(self.temp_dir.name) / "test.db")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            create table settings (key text primary key, value text);
            create table users (id integer primary key, name text, email text);
            create table manufacturers (id integer primary key, name text);
            create table buyers (id integer primary key, equipment_manufacturer text);
            create table service_orders (
                id integer primary key, manufacturer_id integer, buyer_id integer
            );
            create table service_reports (
                id integer primary key, service_order_id integer, report_writer_id integer
            );
            """
        )
        self.admin_id = self.connection.execute(
                """
                insert into users (name, email)
                values ('Admin Test', 'admin-test@example.com')
                """
            ).lastrowid
        self.gaoyang_id = self.connection.execute(
                """
                insert into users (name, email)
                values ('高阳', 'gaoyangproduction@gmail.com')
                """
            ).lastrowid
        self.other_employee_id = self.connection.execute(
                """
                insert into users (name, email)
                values ('其他员工', 'other@example.com')
                """
            ).lastrowid
        sun_id = self.connection.execute(
                "insert into manufacturers (name) values ('阳光')"
            ).lastrowid
        other_id = self.connection.execute(
                "insert into manufacturers (name) values ('其它厂家')"
            ).lastrowid
        self.sun_order_id = self._insert_order(self.connection, sun_id)
        self.other_order_id = self._insert_order(self.connection, other_id)
        self.sun_report_id = self._insert_report(self.connection, self.sun_order_id, self.other_employee_id)
        self.other_report_id = self._insert_report(self.connection, self.other_order_id, self.other_employee_id)
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def _insert_order(self, connection, manufacturer_id):
        return connection.execute(
            "insert into service_orders (manufacturer_id) values (?)",
            (manufacturer_id,),
        ).lastrowid

    def _insert_report(self, connection, order_id, writer_id):
        return connection.execute(
            "insert into service_reports (service_order_id, report_writer_id) values (?, ?)",
            (order_id, writer_id),
        ).lastrowid

    def test_historical_reports_are_reclassified_by_manufacturer(self):
        migrate_historical_customer_reports(self.connection, "2026-08-16T00:00:00")
        sun_writer = self.connection.execute(
            "select report_writer_id from service_reports where id = ?", (self.sun_report_id,)
        ).fetchone()["report_writer_id"]
        other_writer = self.connection.execute(
            "select report_writer_id from service_reports where id = ?", (self.other_report_id,)
        ).fetchone()["report_writer_id"]
        marker = self.connection.execute(
            "select value from settings where key = 'service_report_customer_report_v1'"
        ).fetchone()
        self.assertEqual(sun_writer, self.gaoyang_id)
        self.assertIsNone(other_writer)
        self.assertIsNotNone(marker)

    def test_customer_report_choice_controls_required_employee(self):
        self.assertEqual(normalize_customer_report_choice("no", str(self.gaoyang_id)), ("no", ""))
        with self.assertRaisesRegex(ValueError, "必须选择一位填写员工"):
            normalize_customer_report_choice("yes", "")
        self.assertEqual(
            normalize_customer_report_choice("yes", str(self.gaoyang_id)),
            ("yes", str(self.gaoyang_id)),
        )

    def test_form_contains_conditional_customer_report_controls(self):
        template = (REPO_DIR / "templates" / "service_report_form.html").read_text(encoding="utf-8")
        script = (REPO_DIR / "static" / "service-report.js").read_text(encoding="utf-8")
        self.assertIn('name="has_customer_report"', template)
        self.assertIn('name="report_writer_id" id="customerReportWriter"', template)
        self.assertIn("customerReportWriter.required = isRequired", script)
        self.assertIn("customerReportWriter.disabled = !isRequired", script)


if __name__ == "__main__":
    unittest.main()
