import unittest
from decimal import Decimal
from pathlib import Path

from flask import Flask, g
from werkzeug.exceptions import Forbidden

from employee_finance import (
    PAYMENT_STATUS_LABELS,
    _can_view_all_payments,
    _require_payment_access,
    payment_amounts,
    payment_transition_target,
)


ROOT = Path(__file__).resolve().parent


class EmployeeFinanceDomainTest(unittest.TestCase):
    def test_payment_visibility_is_self_only_for_employee(self):
        app = Flask(__name__)
        with app.test_request_context():
            g.user = {"id": 7, "role": "employee"}
            api = {"normalized_role": lambda: g.user["role"]}
            self.assertFalse(_can_view_all_payments(api))
            _require_payment_access(api, {"employee_id": 7})
            with self.assertRaises(Forbidden):
                _require_payment_access(api, {"employee_id": 8})

    def test_management_roles_can_view_all_employee_payments(self):
        app = Flask(__name__)
        for role in ("admin", "finance", "manager"):
            with app.test_request_context():
                g.user = {"id": 7, "role": role}
                api = {"normalized_role": lambda: g.user["role"]}
                self.assertTrue(_can_view_all_payments(api))
                _require_payment_access(api, {"employee_id": 8})

    def test_payment_amount_formula(self):
        self.assertEqual(
            payment_amounts("1000.005", "125.00", "-25.25"),
            (Decimal("1000.01"), Decimal("125.00"), Decimal("-25.25"), Decimal("849.76")),
        )

    def test_payment_amount_cannot_be_negative(self):
        with self.assertRaisesRegex(ValueError, "实付金额"):
            payment_amounts("10", "20", "0")
        with self.assertRaisesRegex(ValueError, "不能为负数"):
            payment_amounts("-1", "0", "0")

    def test_required_happy_path_is_explicit(self):
        steps = [
            ("draft", "submit", "pending_review"),
            ("pending_review", "approve", "approved"),
            ("approved", "ready", "pending_payment"),
            ("pending_payment", "mark_paid", "paid"),
            ("paid", "reconcile", "reconciled"),
        ]
        for source, action, target in steps:
            self.assertEqual(payment_transition_target(source, action), target)

    def test_exception_states_are_explicit(self):
        self.assertEqual(payment_transition_target("pending_review", "reject"), "rejected")
        self.assertEqual(payment_transition_target("pending_payment", "fail"), "payment_failed")
        self.assertEqual(payment_transition_target("payment_failed", "retry"), "pending_payment")
        self.assertIsNone(payment_transition_target("draft", "mark_paid"))
        self.assertEqual(
            set(PAYMENT_STATUS_LABELS),
            {"draft", "pending_review", "approved", "pending_payment", "paid", "reconciled",
             "rejected", "cancelled", "payment_failed"},
        )


class PostgreSQLFinanceMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = (ROOT / "migrations" / "postgresql" / "0285-employee-finance-assets.sql").read_text(encoding="utf-8")

    def test_all_required_tables_are_in_one_postgresql_migration(self):
        for table in (
            "employee_payment_orders", "payment_order_sources", "payment_order_events",
            "employee_advances", "employee_advance_applications", "bank_accounts",
            "bank_transactions", "employee_salary_agreements", "assets", "asset_events", "asset_photos",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS attachment_stored_filename", self.sql)

    def test_advance_balance_is_derived_not_mutable(self):
        advance_block = self.sql.split("CREATE TABLE IF NOT EXISTS employee_advances", 1)[1].split(";", 1)[0]
        self.assertNotIn("remaining_balance", advance_block)
        self.assertNotIn("applied_balance", advance_block)
        self.assertIn("entry_type IN ('application','reversal')", self.sql)
        self.assertIn("amount numeric(14,2) NOT NULL CHECK (amount <> 0)", self.sql)

    def test_bank_evidence_is_separate_and_future_sync_fields_exist(self):
        transaction_block = self.sql.split("CREATE TABLE IF NOT EXISTS bank_transactions", 1)[1].split(";", 1)[0]
        self.assertIn("matched_payment_order_id", transaction_block)
        self.assertIn("external_transaction_id", transaction_block)
        self.assertIn("sync_source", transaction_block)
        self.assertIn("sync_status", transaction_block)
        self.assertIn("matched_payment_order_id bigint UNIQUE", transaction_block)

    def test_asset_qr_identity_is_stable(self):
        asset_block = self.sql.split("CREATE TABLE IF NOT EXISTS assets", 1)[1].split(";", 1)[0]
        self.assertIn("stable_id text NOT NULL UNIQUE", asset_block)
        module = (ROOT / "employee_finance.py").read_text(encoding="utf-8")
        self.assertIn('url_for("asset_by_stable_id", stable_id=asset["stable_id"])', module)
        self.assertNotIn('url_for("asset_by_stable_id", employee', module)

    def test_employee_self_service_permission_is_seeded(self):
        self.assertIn("('employee','employee_payments',1", self.sql)
        self.assertIn("('employee','employee_payments','view',1", self.sql)
        module = (ROOT / "employee_finance.py").read_text(encoding="utf-8")
        self.assertIn('clauses.append("p.employee_id=?")', module)
        self.assertIn('payment["employee_id"] != g.user["id"]', module)
        self.assertIn('method_labels=PAYMENT_METHOD_LABELS, can_view_all=can_view_all', module)
        template = (ROOT / "templates" / "employee_payments.html").read_text(encoding="utf-8")
        self.assertIn("{% if can_view_all %}<label>员工", template)
        self.assertIn("{% if has_action_permission('employee_payments','create') %}", template)
        runner = (ROOT / "scripts" / "upgrade_postgresql_0285.py").read_text(encoding="utf-8")
        self.assertIn('employee_payments\' and is_enabled=1', runner)
        self.assertIn('f"{len(TABLES)}|1|1|1"', runner)


if __name__ == "__main__":
    unittest.main()
