"""Contract tests for the bounded payment-terms extraction."""
import ast
from datetime import date
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import app as app_module
from flask import url_for


class PaymentTermsModuleContractTest(unittest.TestCase):
    def test_routes_preserve_endpoint_url_and_methods(self):
        expected = {
            "payment_terms": ("/settings/payment-terms", {"GET", "POST"}, {}),
            "edit_payment_term": ("/settings/payment-terms/17/edit", {"POST"}, {"payment_term_id": 17}),
            "toggle_payment_term": ("/settings/payment-terms/17/toggle", {"POST"}, {"payment_term_id": 17}),
            "recalculate_payment_term_invoices": ("/settings/payment-terms/recalculate-invoices", {"POST"}, {}),
        }
        for endpoint, (url, methods, values) in expected.items():
            rules = list(app_module.app.url_map.iter_rules(endpoint))
            self.assertEqual(len(rules), 1, endpoint)
            self.assertEqual(rules[0].methods - {"HEAD", "OPTIONS"}, methods)
            with app_module.app.test_request_context():
                self.assertEqual(url_for(endpoint, **values), url)
            view = getattr(app_module, endpoint)
            self.assertIs(view, app_module.app.view_functions[endpoint])
            self.assertEqual(view.__module__, "invoice_tool.payment_terms.routes")

    def test_legacy_helper_entry_points_remain_callable(self):
        for name in (
            "payment_term_rows", "default_payment_term_id", "posted_payment_term_id",
            "shifted_month", "calculate_payment_due_date", "payment_term_description",
            "payment_term_form_values", "eligible_invoice_due_date_changes",
        ):
            self.assertTrue(callable(getattr(app_module, name)), name)

    def test_posted_default_resolves_patched_legacy_helper(self):
        with app_module.app.test_request_context(method="POST", data={}):
            with patch.object(app_module, "default_payment_term_id", return_value=42) as resolver:
                self.assertEqual(app_module.posted_payment_term_id(), 42)
                resolver.assert_called_once_with()

    def test_due_date_resolves_patched_month_shifter(self):
        term = {"rule_type": "monthly_cutoff", "cutoff_day": 20, "due_day": 19,
                "before_due_months": 1, "after_due_months": 2}
        with patch.object(app_module, "shifted_month", return_value=(2030, 2)) as resolver:
            self.assertEqual(app_module.calculate_payment_due_date("2026-08-21", term), date(2030, 2, 19))
            resolver.assert_called_once_with(2026, 8, 2)

    def test_eligibility_uses_patched_db_and_due_date_function(self):
        connection = Mock()
        row = {"id": 17, "issue_date": "2026-08-21", "due_date": "2026-09-20"}
        connection.execute.return_value.fetchall.return_value = [row]
        with patch.object(app_module, "db", return_value=connection):
            with patch.object(app_module, "calculate_payment_due_date", return_value=date(2030, 1, 1)) as resolver:
                result = app_module.eligible_invoice_due_date_changes()
        self.assertEqual(result, [{"invoice": row, "new_due_date": "2030-01-01"}])
        resolver.assert_called_once_with(row["issue_date"], row)

    def test_route_uses_patched_form_validator_and_rolls_back(self):
        connection = Mock()
        with app_module.app.test_request_context("/settings/payment-terms", method="POST", data={}):
            with patch.object(app_module, "db", return_value=connection):
                with patch.object(app_module, "payment_term_form_values", side_effect=ValueError("invalid")) as validator:
                    response = app_module.payment_terms.__wrapped__()
        self.assertEqual(response.status_code, 302)
        validator.assert_called_once_with()
        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()

    def test_domain_modules_do_not_import_root_app(self):
        folder = Path(__file__).resolve().parent / "invoice_tool" / "payment_terms"
        for path in folder.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(alias.name == "app" for alias in node.names), path.name)
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "app", path.name)
