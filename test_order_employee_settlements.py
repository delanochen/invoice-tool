import json
import sqlite3
import unittest
from unittest.mock import patch

import tests_pg

tests_pg.activate()

import app as app_module  # noqa: E402
from invoice_tool.order_employee_settlements import (  # noqa: E402
    employee_expense_block_reason,
    order_employee_settlement_rows,
)


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _FixedInvoiceConnection:
    def execute(self, sql, params=()):
        if "from quotation_revisions" in sql:
            return _Cursor({
                "id": 7,
                "total": 12000,
                "snapshot_json": json.dumps({
                    "settlement_mode": "fixed",
                    "gross_total": "12000.00",
                    "discount_amount": "500.00",
                }),
            })
        if "from projects" in sql:
            return _Cursor({"id": 11, "name": "Technical Services", "tax_rate": 8.25})
        raise AssertionError(sql)


class _ExpenseBlockConnection:
    def __init__(self, agreement):
        self.agreement = agreement

    def execute(self, sql, params=()):
        self.params = params
        return _Cursor(self.agreement)


class OrderEmployeeSettlementRulesTest(unittest.TestCase):
    def test_employee_actuals_reuse_report_duration_and_include_public_transport(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            create table users (id integer primary key, name text);
            create table user_service_orders (service_order_id integer, user_id integer);
            create table service_reports (
                id integer primary key, service_order_id integer, total_service_hours real,
                arrival_time text, departure_time text
            );
            create table service_report_workers (
                report_id integer, user_id integer, travel_hours real,
                public_transport_hours real, driving_miles real
            );
            create table service_order_employee_settlements (
                service_order_id integer, employee_id integer, settlement_mode text,
                fixed_amount real, currency text, settlement_date text, status text, notes text
            );
            insert into users values (1, '高阳'), (2, '陈亦城');
            insert into service_reports values (10, 7, 0, '08:00', '16:00');
            insert into service_report_workers values
                (10, 1, 2, 1, 100), (10, 2, 3, 0.5, 100);
            """
        )
        calls = []

        def duration(report, worker_count):
            calls.append((report["arrival_time"], report["departure_time"], worker_count))
            return 8

        rows = order_employee_settlement_rows(connection, 7, duration)
        by_id = {row["employee_id"]: row for row in rows}
        self.assertEqual(by_id[1]["actual_hours"], 8)
        self.assertEqual(by_id[1]["travel_hours"], 3)
        self.assertEqual(by_id[2]["travel_hours"], 3.5)
        self.assertEqual(by_id[2]["actual_miles"], 100)
        self.assertEqual(calls, [("08:00", "16:00", 2), ("08:00", "16:00", 2)])

    def test_fixed_customer_quote_creates_exactly_one_technical_services_line(self):
        reimbursement = {
            "id": 3,
            "settlement_basis": "quotation",
            "quotation_revision_id": 7,
        }
        with patch.object(app_module, "db", return_value=_FixedInvoiceConnection()):
            rows = app_module.customer_reimbursement_invoice_items(reimbursement)
        self.assertEqual(rows, [{"project_id": 11, "amount": 11500.0, "tax_rate": 8.25}])

    def test_fixed_employee_is_blocked_from_expense(self):
        connection = _ExpenseBlockConnection({"id": 9, "settlement_mode": "fixed"})
        reason = employee_expense_block_reason(connection, 4, 6)
        self.assertIn("一口价全包", reason)
        self.assertEqual(connection.params, (4, 6))

    def test_actual_employee_can_submit_expense(self):
        self.assertEqual(employee_expense_block_reason(_ExpenseBlockConnection(None), 4, 6), "")


if __name__ == "__main__":
    unittest.main()
