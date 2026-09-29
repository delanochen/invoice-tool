"""「现场工作」菜单入口的打开方式契约。

需求（用户提出）：「在非现场工作的界面点击『现场工作』菜单，会弹出新的页面，
这不像 APP 的操作，最好改一下。」—— 即在管理系统（工作区外壳）里点「现场工作」，
不应另开一个浏览器标签页，而应整页直接进入现场工作页（像 APP 一样「点进去就是那个界面」）。

现场工作页（``/field/``）是**独立的手机端 PWA 页面**，不是 ERP 页面：
它自带 ``<header class="fw-header">``、``<nav class="bottom-tabs">``、相机 / 定位 / 装到桌面，
不 extends base.html、也没有 ``.main``。因此它既不该塞进工作区 iframe，
也不该再 ``window.open(..., '_blank')``。

修法：顶部菜单中的 ``/field`` 交还浏览器原生导航；iframe 内工单详情页中的
``/field`` 链接则由工作区外壳调用 ``location.assign`` 执行顶层导航。仅仅 ``return``
会让链接继续在 iframe 内打开，现场工作页没有 ``.main``，会被误判为加载失败。

本测试钉两层：
1. 源码契约 —— 不再出现针对 /field 的 ``window.open(..., '_blank')``，且两处特判仍存在（防误删）；
2. 反向对照 —— 若有人把 ``_blank`` 改回去，断言必须 FAIL。

注意：``/reports/field-photos``（工单照片台账）**不属于** /field 特判范围，
仍应在工作区 iframe 里当普通页面打开；本测试同时钉住这一点（正则不能误伤它）。
"""

import re
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent
STATIC = REPO_DIR / "static"
WORKSPACE_JS = STATIC / "workspace.js"


class FieldWorkNavigationSourceTest(unittest.TestCase):
    """源码契约：/field 不再另开浏览器标签，而是原生整页导航。"""

    @classmethod
    def setUpClass(cls):
        cls.js = WORKSPACE_JS.read_text(encoding="utf-8")

    def test_no_blank_open_for_field_anywhere(self):
        """整个 workspace.js 里不能再有「对 /field 调 window.open(..., '_blank')」。"""
        # 抓所有 window.open(...) 调用，确认没有一条是在 /field 分支里
        for match in re.finditer(r"window\.open\([^;]*\);", self.js):
            snippet = match.group(0)
            self.assertNotIn(
                "'_blank'", snippet,
                msg="workspace.js 仍存在 window.open(..., '_blank')：%s" % snippet,
            )

    def _field_guard_lines(self):
        """取出所有包含 /field 特判正则的源码行。"""
        return [ln for ln in self.js.splitlines() if "/field" in ln and "test(" in ln]

    def test_topnav_field_link_returns_natively(self):
        """顶部菜单里 /field 走原生导航：正则命中后直接 return，不 preventDefault、不 window.open。"""
        literal = "/^\\/field\\/?$/"
        self.assertIn(literal, self.js, msg="topnav 的 /field 正则未找到")
        line = next(ln for ln in self._field_guard_lines() if literal in ln)
        self.assertIn("return;", line, msg="topnav 的 /field 特判应直接 return：%s" % line)
        self.assertNotIn("window.open", line, msg="topnav 的 /field 不应再 window.open：%s" % line)
        self.assertNotIn("preventDefault", line, msg="topnav 的 /field 应交还原生导航：%s" % line)

    def test_iframe_link_field_is_promoted_to_top_level_navigation(self):
        """iframe 内页的 /field 必须由工作区外壳执行顶层导航。"""
        literal = "/^\\/field\\/?(?:[?#]|$)/"
        self.assertIn(literal, self.js, msg="iframe 的 /field 正则未找到")
        line = next(ln for ln in self._field_guard_lines() if literal in ln)
        self.assertIn("event.preventDefault()", line, msg="iframe 的 /field 应阻止 iframe 原生导航：%s" % line)
        self.assertIn("location.assign(url)", line, msg="iframe 的 /field 应由外壳执行顶层导航：%s" % line)
        self.assertNotIn("window.open", line, msg="iframe 的 /field 不应再 window.open：%s" % line)

    def test_order_photo_action_is_a_link_the_workspace_can_intercept(self):
        """工单详情的拍照入口不能再用 onclick 按钮绕过工作区链接处理。"""
        html = (REPO_DIR / "templates" / "service_order_detail.html").read_text(encoding="utf-8")
        self.assertRegex(
            html,
            r'<a class="erp-btn" href="\{\{ url_for\(\'field_work\', order_id=order\.id\) \}\}">工单拍照</a>',
        )
        self.assertNotRegex(html, r'<button[^>]+field_work[^>]*>工单拍照</button>')

    def test_field_photos_ledger_not_treated_as_field(self):
        """工单照片台账 /reports/field-photos 不属于 /field 特判，regex 不会误伤它。"""
        # 把源码里用到的 /field 正则取出来，用台账 URL 反证不匹配
        for literal in ("/^\\/field\\/?(?:[?#]|$)/", "/^\\/field\\/?$/"):
            self.assertIn(literal, self.js, msg="预期的 /field 正则未找到：%s" % literal)

        pattern = re.compile(r"^/field/?(?:[?#]|$)")
        self.assertTrue(pattern.search("/field"), "/field 应匹配")
        self.assertTrue(pattern.search("/field/"), "/field/ 应匹配")
        self.assertFalse(
            pattern.search("/reports/field-photos"),
            "/reports/field-photos 不应匹配 /field 特判（否则台账会被整页跳出工作区）",
        )
        self.assertFalse(pattern.search("/reports/field-repairs"), "/reports/field-repairs 不应匹配")

    def test_field_work_page_is_standalone_not_workspace_embedded(self):
        """现场工作页是独立页面：不 extends base.html、无 .main（所以不能塞 iframe）。"""
        html = (REPO_DIR / "templates" / "field_work.html").read_text(encoding="utf-8")
        self.assertNotIn("{% extends", html)
        self.assertIn('class="bottom-tabs"', html)
        self.assertNotIn('class="main"', html)


if __name__ == "__main__":
    unittest.main()
