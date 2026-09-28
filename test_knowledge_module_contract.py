import unittest

import app as app_module
from flask import url_for


class KnowledgeModuleRouteContractTest(unittest.TestCase):
    EXPECTED = {
        "knowledge_base": ("/knowledge-base", {"GET", "POST"}, {}),
        "preview_knowledge_document": (
            "/knowledge-base/17/preview", {"GET"}, {"document_id": 17}
        ),
        "download_knowledge_document": (
            "/knowledge-base/17/download", {"GET"}, {"document_id": 17}
        ),
        "edit_knowledge_document": (
            "/knowledge-base/17/edit", {"POST"}, {"document_id": 17}
        ),
        "upload_knowledge_document_version": (
            "/knowledge-base/17/versions", {"POST"}, {"document_id": 17}
        ),
        "preview_knowledge_document_version": (
            "/knowledge-base/versions/23/preview", {"GET"}, {"version_id": 23}
        ),
        "download_knowledge_document_version": (
            "/knowledge-base/versions/23/download", {"GET"}, {"version_id": 23}
        ),
        "delete_knowledge_document": (
            "/knowledge-base/17/delete", {"POST"}, {"document_id": 17}
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
        self.assertFalse(any(endpoint.startswith("knowledge.") for endpoint in endpoints))

    def test_legacy_helpers_remain_importable_from_app(self):
        for name in (
            "extract_knowledge_pdf_text",
            "save_knowledge_pdf_upload",
            "knowledge_expiry_from_form",
            "knowledge_document_path",
            "knowledge_document_or_404",
            "knowledge_version_or_404",
            "knowledge_version_path",
        ):
            self.assertTrue(callable(getattr(app_module, name)), name)


if __name__ == "__main__":
    unittest.main()
