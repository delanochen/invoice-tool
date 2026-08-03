import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class ServiceOrderMapEmailTest(unittest.TestCase):
    def test_buyer_payload_and_both_map_providers_include_email(self):
        app_source = (REPO_DIR / "app.py").read_text(encoding="utf-8")
        self.assertIn('"email": buyer["email"]', app_source)

        expected_popup_row = '<dt>${t("邮箱")}</dt><dd>${escapeHtml(buyer.email || "-")}</dd>'
        for filename in ("service-order-map.js", "service-order-map-google.js"):
            source = (REPO_DIR / "static" / filename).read_text(encoding="utf-8")
            self.assertIn(expected_popup_row, source, filename)


if __name__ == "__main__":
    unittest.main()
