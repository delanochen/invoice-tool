import inspect
import unittest

import app as app_module
from flask import g, url_for


class EmployeeUserModuleRouteContractTest(unittest.TestCase):
    EXPECTED = {
        "users": ("/users", {"GET", "POST"}, {}),
        "edit_user": ("/users/17/edit", {"GET", "POST"}, {"user_id": 17}),
        "update_user_status": ("/users/17/status", {"POST"}, {"user_id": 17}),
        "download_user_attachment": (
            "/user-attachments/23/download", {"GET"}, {"attachment_id": 23}
        ),
        "preview_user_attachment": (
            "/user-attachments/23/preview", {"GET"}, {"attachment_id": 23}
        ),
        "delete_user_attachment": (
            "/user-attachments/23/delete", {"POST"}, {"attachment_id": 23}
        ),
        "delete_user": ("/users/17/delete", {"POST"}, {"user_id": 17}),
    }

    def test_routes_keep_legacy_endpoint_rule_and_methods(self):
        rules_by_endpoint = {}
        for rule in app_module.app.url_map.iter_rules():
            if rule.endpoint in self.EXPECTED:
                rules_by_endpoint.setdefault(rule.endpoint, []).append(rule)
        self.assertEqual(set(rules_by_endpoint), set(self.EXPECTED))
        for endpoint, rules in rules_by_endpoint.items():
            self.assertEqual(len(rules), 1, endpoint)
            rule = rules[0]
            expected_url, expected_methods, values = self.EXPECTED[endpoint]
            with app_module.app.test_request_context():
                self.assertEqual(url_for(endpoint, **values), expected_url)
            self.assertEqual(rule.methods - {"HEAD", "OPTIONS"}, expected_methods)

    def test_routes_have_no_blueprint_endpoint_prefix(self):
        endpoints = {rule.endpoint for rule in app_module.app.url_map.iter_rules()}
        self.assertFalse(any(endpoint.startswith("employees.") for endpoint in endpoints))

    def test_user_endpoints_and_rules_are_unique(self):
        endpoint_counts = {}
        rule_counts = {}
        for rule in app_module.app.url_map.iter_rules():
            endpoint_counts[rule.endpoint] = endpoint_counts.get(rule.endpoint, 0) + 1
            signature = (str(rule), tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
            rule_counts[signature] = rule_counts.get(signature, 0) + 1
        for endpoint in self.EXPECTED:
            self.assertEqual(endpoint_counts[endpoint], 1, endpoint)
            rule = next(
                rule for rule in app_module.app.url_map.iter_rules()
                if rule.endpoint == endpoint
            )
            signature = (str(rule), tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
            self.assertEqual(rule_counts[signature], 1, endpoint)

    def test_permission_mapping_keeps_legacy_requirements(self):
        cases = (
            ("/users", "GET", ("users", "view")),
            ("/users", "POST", ("users", "create")),
            ("/users/17/edit", "POST", ("users", "edit")),
            ("/users/17/status", "POST", ("users", "approve")),
            ("/users/17/delete", "POST", ("users", "delete")),
            ("/user-attachments/23/delete", "POST", ("users", "edit")),
        )
        for path, method, expected in cases:
            with self.subTest(path=path, method=method):
                with app_module.app.test_request_context(path, method=method):
                    g.user = {"id": 999}
                    self.assertEqual(app_module.required_action_for_request(), expected)

    def test_template_context_and_ajax_contract_remain_present(self):
        users_source = inspect.getsource(inspect.unwrap(app_module.users))
        edit_source = inspect.getsource(inspect.unwrap(app_module.edit_user))
        delete_attachment_source = inspect.getsource(
            inspect.unwrap(app_module.delete_user_attachment)
        )
        for expected in (
            '"users.html"',
            "user_attachments=",
            "user_order_ids=",
            "role_options=",
            "can_manage=",
            "can_assign=",
            "can_approve=",
            "countries=",
            "employee_grades=",
        ):
            self.assertIn(expected, users_source)
        for expected in (
            '"user_form.html"',
            "selected_order_ids=",
            "attachments=",
            "role_options=",
            "can_manage=",
            "can_assign=",
            "can_approve=",
            "countries=",
            "employee_grades=",
        ):
            self.assertIn(expected, edit_source)
        self.assertIn('request.headers.get("X-Requested-With") == "XMLHttpRequest"', delete_attachment_source)
        self.assertIn('jsonify({"ok": True, "attachment_id": attachment_id})', delete_attachment_source)

    def test_legacy_helpers_remain_importable_from_app(self):
        for name in (
            "get_user_attachments",
            "assigned_service_order_ids",
            "save_user_service_order_assignments",
            "employee_grade_options",
        ):
            self.assertTrue(callable(getattr(app_module, name)), name)


if __name__ == "__main__":
    unittest.main()
