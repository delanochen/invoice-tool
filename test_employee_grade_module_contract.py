import unittest

import app as app_module
from flask import url_for


class EmployeeGradeModuleRouteContractTest(unittest.TestCase):
    EXPECTED = {
        "employee_grades": ("/employee-grades", {"GET", "POST"}, {}),
        "set_employee_grade_state": (
            "/employee-grades/17/state", {"POST"}, {"grade_id": 17}
        ),
        "delete_employee_grade": (
            "/employee-grades/17/delete", {"POST"}, {"grade_id": 17}
        ),
        "update_employee_grade_members": (
            "/employee-grades/17/members", {"POST"}, {"grade_id": 17}
        ),
        "employee_rate_versions": (
            "/employee-grades/17/rates", {"GET", "POST"}, {"grade_id": 17}
        ),
        "delete_employee_rate_version": (
            "/employee-grades/17/rates/23/delete",
            {"POST"},
            {"grade_id": 17, "version_id": 23},
        ),
        "edit_employee_rate_version": (
            "/employee-grades/17/rates/23/edit",
            {"POST"},
            {"grade_id": 17, "version_id": 23},
        ),
        "employee_rate_version_state": (
            "/employee-grades/17/rates/23/state",
            {"POST"},
            {"grade_id": 17, "version_id": 23},
        ),
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

    def test_legacy_helpers_remain_importable_from_app(self):
        for name in (
            "employee_grade_usage",
            "employee_grade_panel",
            "render_employee_grades_page",
            "is_member_ajax_request",
            "employee_grade_member_fragments",
        ):
            self.assertTrue(callable(getattr(app_module, name)), name)

    def test_grade_endpoints_and_rules_are_unique(self):
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


if __name__ == "__main__":
    unittest.main()
