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
        self.assertEqual(data["start_url"], "/field/")
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


class TestPwaSafeAreaTopbar(unittest.TestCase):
    """v0.1.326: PWA standalone 顶栏被手机状态栏遮挡。

    base.html 声明 apple-mobile-web-app-status-bar-style=black-translucent，
    即状态栏**浮在**网页之上 → 顶栏第一行（品牌 / 语言 / 退出）会钻到时间电量底下。

    v0.1.245 曾给当时的导航容器 `.sidebar` 加过 env(safe-area-inset-top)，
    但 v0.1.294 的 ERP 化重构把导航换成 `.topbar` 后该修复**没有跟着搬**，
    回归因此在无测试覆盖的情况下静默失效了整整 30 多个版本。
    这个测试把契约钉在「当前真实导航容器」上，防止下次重构再丢。
    """

    def setUp(self):
        self.css = (PROJECT_ROOT / "static" / "erp-topnav.css").read_text(encoding="utf-8")

    @staticmethod
    def _rule_block(css, selector):
        """取 selector 那条规则的大括号内容（第一个匹配）。"""
        import re
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css, re.S)
        return m.group(1) if m else ""

    def test_topbar_clears_safe_area(self):
        block = self._rule_block(self.css, ".topbar")
        self.assertTrue(block, ".topbar 规则不存在 —— 导航容器被改名/移除？")
        self.assertIn(
            "env(safe-area-inset-top)", block,
            ".topbar 缺少 env(safe-area-inset-top) 内边距：PWA 独立模式下顶栏会被状态栏遮挡",
        )

    def test_navigation_container_is_topbar_not_sidebar(self):
        """防止「改了 CSS 文件但选择器已不是真实导航容器」这种假修复。"""
        html = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('class="topbar no-print"', html)
        self.assertNotIn('<aside class="sidebar"', html)

    def test_base_html_declares_black_translucent_status_bar(self):
        """前提断言：只有 black-translucent 才需要安全区（否则本修复无意义）。"""
        html = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("black-translucent", html)
        self.assertIn("viewport-fit=cover", html)

    def test_mobile_menu_button_is_not_stretched_to_full_row_height(self):
        block = self._rule_block(self.css, ".topbar-brand")
        self.assertIn(
            "align-items: center", block,
            "移动端菜单按钮会被 flex 默认的 stretch 拉到整行高度，无法与退出按钮对齐",
        )
        import re
        mobile_button_blocks = re.findall(
            r"\.topbar\s+\.mobile-menu-toggle\s*\{([^}]*)\}", self.css, re.S
        )
        declarations = next(
            (candidate for candidate in mobile_button_blocks if "height: 26px" in candidate),
            "",
        )
        self.assertTrue(declarations, "没有找到移动端菜单按钮的固定高度规则")
        self.assertIn("align-self: center", declarations)
        self.assertIn("height: 26px", declarations)
        self.assertIn("min-height: 26px", declarations)


if __name__ == "__main__":
    unittest.main()
