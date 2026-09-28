import unittest

import app as app_module
from flask import url_for


class CustomerCatalogModuleRouteContractTest(unittest.TestCase):
    EXPECTED = {
        "clients": ("/clients", {"GET", "POST"}, {}),
        "edit_client": (
            "/clients/17/edit", {"GET", "POST"}, {"client_id": 17}
        ),
        "delete_client": ("/clients/17/delete", {"POST"}, {"client_id": 17}),
        "owners": ("/owners", {"GET", "POST"}, {}),
        "edit_owner": ("/owners/19/edit", {"POST"}, {"owner_id": 19}),
        "delete_owner": ("/owners/19/delete", {"POST"}, {"owner_id": 19}),
        "manufacturers": ("/manufacturers", {"GET", "POST"}, {}),
        "edit_manufacturer": (
            "/manufacturers/23/edit", {"POST"}, {"manufacturer_id": 23}
        ),
        "delete_manufacturer": (
            "/manufacturers/23/delete", {"POST"}, {"manufacturer_id": 23}
        ),
        "buyers": ("/buyers", {"GET", "POST"}, {}),
        "import_buyers": ("/buyers/import", {"POST"}, {}),
        "edit_buyer": (
            "/buyers/29/edit", {"GET", "POST"}, {"buyer_id": 29}
        ),
        "delete_buyer": ("/buyers/29/delete", {"POST"}, {"buyer_id": 29}),
        "work_order_types": ("/work-order-types", {"GET", "POST"}, {}),
        "edit_work_order_type": (
            "/work-order-types/31/edit", {"GET", "POST"}, {"type_id": 31}
        ),
        "delete_work_order_type": (
            "/work-order-types/31/delete", {"POST"}, {"type_id": 31}
        ),
        "projects": ("/projects", {"GET", "POST"}, {}),
        "edit_project": (
            "/projects/37/edit", {"GET", "POST"}, {"project_id": 37}
        ),
        "delete_project": (
            "/projects/37/delete", {"POST"}, {"project_id": 37}
        ),
        "countries": ("/countries", {"GET", "POST"}, {}),
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
        self.assertFalse(any(endpoint.startswith("customers.") for endpoint in endpoints))
        self.assertFalse(any(endpoint.startswith("catalog.") for endpoint in endpoints))

    def test_legacy_helpers_remain_importable_from_app(self):
        for name in (
            "normalized_project_name",
            "project_name_key",
            "is_mro_alias_project_name",
            "is_mro_project_name",
            "merge_mro_project_aliases",
            "merge_duplicate_projects",
            "project_name_exists",
            "country_rows",
            "country_by_code",
            "country_translations",
            "country_from_form",
            "next_client_number",
            "next_buyer_number",
            "next_owner_number",
            "next_manufacturer_number",
            "unknown_owner",
            "owner_options",
            "manufacturer_options",
            "manufacturer_from_form",
            "manufacturer_by_name",
            "normalize_import_header",
            "get_or_create_owner_by_name",
            "country_from_import_value",
            "imported_buyer_rows",
            "import_buyers_from_file",
        ):
            self.assertTrue(callable(getattr(app_module, name)), name)

    def test_domain_endpoints_are_registered_once(self):
        endpoint_counts = {}
        rule_counts = {}
        for rule in app_module.app.url_map.iter_rules():
            endpoint_counts[rule.endpoint] = endpoint_counts.get(rule.endpoint, 0) + 1
            signature = (str(rule), tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
            rule_counts[signature] = rule_counts.get(signature, 0) + 1
        for endpoint in self.EXPECTED:
            self.assertEqual(endpoint_counts[endpoint], 1, endpoint)
        for endpoint in self.EXPECTED:
            rule = next(
                rule for rule in app_module.app.url_map.iter_rules()
                if rule.endpoint == endpoint
            )
            signature = (str(rule), tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
            self.assertEqual(rule_counts[signature], 1, endpoint)


if __name__ == "__main__":
    unittest.main()
