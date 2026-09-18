"""v0.1.244 regression tests: main-site PWA manifest + service worker.

Problem: the phone home-screen icon opened the site as a plain web page —
every tap stacked another browser tab, and unfinished work got lost among
duplicates. A real manifest (display=standalone) + installable service worker
makes the home-screen icon resume a single app window instead.
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from test_ai_daily_report_phase9 import Phase9TestBase  # noqa: E402


class TestPwaEndpoints(Phase9TestBase):
    def test_manifest_served_with_standalone_display(self):
        resp = self.client.get("/manifest.webmanifest")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["scope"], "/")
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["id"], "/")
        self.assertEqual(data["short_name"], "Prasinos")
        sizes = {icon["sizes"] for icon in data["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        for icon in data["icons"]:
            self.assertTrue(icon["src"].startswith("/static/"))
        self.assertEqual(resp.headers.get("Cache-Control"), "no-cache")

    def test_service_worker_served_with_scope_header(self):
        resp = self.client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("javascript", resp.headers.get("Content-Type", ""))
        self.assertEqual(resp.headers.get("Service-Worker-Allowed"), "/")
        body = resp.get_data(as_text=True)
        self.assertIn("addEventListener", body)
        # passthrough: must NOT cache responses
        self.assertNotIn("caches.open", body)


class TestPwaTemplateContract(unittest.TestCase):
    def test_base_html_wires_manifest_and_meta(self):
        html = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("app_manifest", html)
        self.assertIn("apple-mobile-web-app-capable", html)
        self.assertIn("mobile-web-app-capable", html)
        self.assertIn("apple-touch-icon", html)
        self.assertIn("app_service_worker", html)
        self.assertIn("serviceWorker", html)

    def test_service_worker_file_exists(self):
        sw = (PROJECT_ROOT / "static" / "app-sw.js").read_text(encoding="utf-8")
        self.assertIn("addEventListener(\"fetch\"", sw)
        self.assertIn("fetch(event.request)", sw)


if __name__ == "__main__":
    unittest.main()
