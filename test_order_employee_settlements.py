import json
import unittest
from unittest.mock import patch

import tests_pg

tests_pg.activate()

import app as app_module  # noqa: E402
from invoice_tool.order_employee_settlements import employee_expense_block_reason  # noqa: E402


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
