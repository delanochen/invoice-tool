"""Regression contracts for the mobile ERP fixed-shell scrolling model."""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestErpMobileScrollContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (PROJECT_ROOT / "static" / "erp-ui.css").read_text(encoding="utf-8")
        cls.script = (PROJECT_ROOT / "static" / "erp-report.js").read_text(encoding="utf-8")

    def test_mobile_erp_shell_keeps_data_inside_the_viewport(self):
        self.assertIn(".erp-app:not(.erp-app--flow)", self.css)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr)", self.css)
        self.assertIn(".erp-app:not(.erp-app--flow) .erp-body", self.css)
        self.assertIn(".erp-app:not(.erp-app--flow) .erp-cards", self.css)
        self.assertIn("overflow-y: auto", self.css)
        self.assertIn("overscroll-behavior: contain", self.css)
        self.assertIn("-webkit-overflow-scrolling: touch", self.css)

    def test_flow_pages_remain_an_explicit_document_scroll_exception(self):
        self.assertIn(".erp-app--flow,", self.css)
        self.assertIn("height: auto", self.css)
        self.assertIn("overflow: visible", self.css)
        self.assertIn(".erp-app:not(.erp-app--flow)", self.css)

    def test_viewport_fitting_uses_visual_viewport_without_touch_suppression(self):
        self.assertIn("window.visualViewport", self.script)
        self.assertIn("visualViewport?.height", self.script)
        self.assertIn("visualViewport?.addEventListener('resize'", self.script)
        self.assertIn("visualViewport?.addEventListener('scroll'", self.script)
        self.assertNotIn("addEventListener('touchmove'", self.script)
        self.assertNotIn('addEventListener("touchmove"', self.script)
        self.assertNotIn("touch-action: none", self.css)


if __name__ == "__main__":
    unittest.main()
