"""v0.1.246 regression: the "查看正式日报" link must use the real Flask route.

The old hardcoded ``/edit_service_report/<id>`` path does not exist (the route
is ``/service-reports/<id>/edit``) so the button 404'd. It also carried
``target="_blank"``, which forced a new browser tab instead of letting the
workspace shell open it as a tab.
"""

import pathlib
import unittest

STATIC = pathlib.Path(__file__).parent / "static" / "ai-daily-report-review.js"


class ViewFormalReportLinkTest(unittest.TestCase):
    def test_link_uses_real_route(self):
        js = STATIC.read_text(encoding="utf-8")
        self.assertIn('"/service-reports/${fs.service_report_id}/edit"', js)

    def test_no_stale_hardcoded_route(self):
        js = STATIC.read_text(encoding="utf-8")
        self.assertNotIn("/edit_service_report/", js)

    def test_link_opens_in_workspace_tab(self):
        js = STATIC.read_text(encoding="utf-8")
        anchor_start = js.index("查看正式日报")
        tag_start = js.rindex("<a ", 0, anchor_start)
        tag_end = js.index(">", tag_start)
        tag = js[tag_start:tag_end]
        self.assertNotIn("target=", tag)


if __name__ == "__main__":
    unittest.main()
