"""用户管理页导出契约（v0.1.347）。

用户诉求：「用户管理 没有导出功能」。

全站已有通用导出：按钮 `data-erp-action="export"` → erp-report.js 派发 →
report-export.js 从 system-grid 镜像取当前可见行（含筛选/排序/分组小计）→
POST /reports/export-visible.xlsx 换 xlsx。用户页此前两样都缺：
- /users 不在 base.html 的 automatic_report_export 白名单里（没有 page-header，
  即使加载了 report-export.js 也无处自动插按钮）；
- 页面没加载 erp-report.js，`data-erp-action` 没人派发，按钮点了没反应。

契约（改这几处必须同步本文件）：
- 工具栏有一个 `data-erp-action="export"` 按钮；
- 页面同时加载 report-export.js（下载实现）与 erp-report.js（动作派发）；
- `window.reportExportConfig` 必须在 report-export.js **之前**赋值 ——
  它在顶层就把 config 读进模块内常量，之后赋值新对象不会生效；
- config.url 指向 export_visible_report 端点。
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
TEMPLATE = ROOT / "templates" / "users.html"
BASE = ROOT / "templates" / "base.html"
APP = ROOT / "app.py"


class UsersExportContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TEMPLATE.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")
        cls.app = APP.read_text(encoding="utf-8")

    def test_toolbar_has_export_button(self):
        self.assertIn('data-erp-action="export"', self.html)
        # 按钮必须在顶部工具栏里（ERP 外壳的 .erp-toolbar），而不是塞进弹窗或选择工具条
        toolbar = self.html.split('<header class="erp-toolbar', 1)[1].split("</header>", 1)[0]
        self.assertIn('data-erp-action="export"', toolbar)

    def test_page_loads_export_and_report_scripts(self):
        self.assertIn("report-export.js", self.html)
        self.assertIn("erp-report.js", self.html)

    def test_export_config_precedes_report_export_script(self):
        """config 必须早于 report-export.js —— 顺序反了导出会用默认标题/空 url。"""
        # 用 <script src> 定位：注释里也会提到文件名，直接 index("report-export.js") 会命中注释
        config_at = self.html.index("window.reportExportConfig")
        script_at = self.html.index("<script src=\"{{ url_for('static', filename='report-export.js'")
        self.assertLess(config_at, script_at, "reportExportConfig 必须先赋值再加载 report-export.js")

    def test_export_config_points_to_endpoint(self):
        block = self.html.split("window.reportExportConfig", 1)[1].split("</script>", 1)[0]
        self.assertIn("url_for('export_visible_report')", block)
        self.assertIn('title: "用户管理"', block)

    def test_endpoint_exists(self):
        """导出端点必须存在且只接受 POST（数据来自客户端请求体）。"""
        self.assertRegex(self.app, r'@app\.post\("/reports/export-visible\.xlsx"\)')

    def test_users_not_in_automatic_whitelist(self):
        """users 走模板自管导出，不在 base.html 白名单里（避免重复加载脚本）。

        白名单只负责「有 page-header 的老页面」自动插按钮；users.html 是 ERP 外壳，
        没有 page-header，靠本页模板显式给按钮 + 自管 config。
        """
        whitelist = self.base.split("automatic_report_export =", 1)[1].split("%}", 1)[0]
        self.assertNotIn('"users"', whitelist)


class UsersExportPayloadTest(unittest.TestCase):
    """导出的取数逻辑在 system-grid / report-export 里，这里钉住它们的配合点。"""

    @classmethod
    def setUpClass(cls):
        cls.grid = (ROOT / "static" / "system-grid.js").read_text(encoding="utf-8")
        cls.report = (ROOT / "static" / "report-export.js").read_text(encoding="utf-8")

    def test_grid_exposes_payload_for_source_table(self):
        """report-export.js 靠 window.systemGrids.payload(源表) 取分组后的行。"""
        self.assertIn("payload:source=>instances.get(source)?.export()", self.grid)

    def test_report_export_prefers_grid_payload(self):
        self.assertIn("window.systemGrids?.payload(table)", self.report)

    def test_report_export_skips_dialog_tables(self):
        """用户页有大量 dialog 内的工单授权表，误取它们会导出空壳。"""
        self.assertIn("!table.closest(\"dialog\")", self.report)


if __name__ == "__main__":
    unittest.main()
