"""ERP 页面里的服务端提示（.flash-stack）要浮在常驻工具栏之上，并且让开工具栏。

背景（v0.1.309）：员工报销详情页点「审核通过」→ redirect 回详情页，
成功提示由 base.html 的 .flash-stack 渲染（styles.css：fixed top:16 right:16 z-index:10），
而 ERP 页面顶部另有一条常驻 sticky 工具栏（erp-ui.css：z-index:12）。
两者都贴视口顶部、提示层级低 2 级 → 提示整块被工具栏的渐变背景盖住，
只剩一圈边框露在工具栏下沿之外（用户截图即此现象）。

真渲染证据见仓库根的 _flash_probe.py：
  - 修复前：提示与工具栏垂直重叠 31px，提示矩形内 9 点采样只有 3 点可见，
    挡住它的是 header.erp-toolbar / button.erp-btn
  - 修复后：重叠 0px、9/9 可见；滚动 300px 让工具栏贴到视口顶后仍 0 重叠、9/9 可见
这里用静态契约把结论钉住，防止以后调样式时又退回去（真渲染那步太重，不进单测）。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ERP_CSS = (ROOT / "static" / "erp-ui.css").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

FLASH_OVERRIDE_SELECTOR = "body:has(.erp-app) .flash-stack"


def _scan(css):
    """把 CSS 摊平成 [(media_condition|None, selector, body), ...]（注释先剥掉）。"""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    _walk(css, None, out)
    return out


def _walk(css, media, out):
    i = 0
    while i < len(css):
        brace = css.find("{", i)
        if brace < 0:
            break
        head = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1:j - 1]
        if head.startswith("@media"):
            _walk(body, head[len("@media"):].strip(), out)
        elif not head.startswith("@"):
            for selector in head.split(","):
                out.append((media, selector.strip(), body))
        i = j
    return out


ERP_RULES = _scan(ERP_CSS)
STYLES_RULES = _scan(STYLES_CSS)


def _declaration(body, prop):
    match = re.search(r"(?:^|;)\s*" + re.escape(prop) + r"\s*:\s*([^;]+)", body)
    return match.group(1).strip() if match else None


def _rules(rules, selector):
    return [body for _, sel, body in rules if sel == selector]


def _px(value):
    return int(re.match(r"(-?\d+)px", value or "").group(1))


class FlashNoticeAboveToolbarTest(unittest.TestCase):
    def test_erp_override_targets_all_erp_pages(self):
        """覆盖必须挂在 .erp-app 上（所有 ERP 页面都可能出现提示被工具栏压住）。"""
        self.assertTrue(_rules(ERP_RULES, FLASH_OVERRIDE_SELECTOR),
                        f"erp-ui.css 里缺少 {FLASH_OVERRIDE_SELECTOR} 规则")

    def test_override_scoped_to_desktop_only(self):
        """必须限定在桌面断点内：≤1100px 时提示是 styles.css 里的页面内横幅（position:static），
        无差别覆盖会把它一起顶回 fixed，又会去压工具栏。"""
        medias = [media for media, sel, _ in ERP_RULES if sel == FLASH_OVERRIDE_SELECTOR]
        self.assertTrue(medias, "规则没有放在任何 @media 里")
        for media in medias:
            self.assertIn("min-width", media,
                          f"规则所在媒体查询没限定最小宽度：{media!r}")

    def test_notice_z_index_beats_every_erp_toolbar(self):
        """提示的 z-index 必须高于所有 ERP 工具栏的 z-index（否则被盖住）。"""
        flash_z = max(int(_declaration(body, "z-index"))
                      for body in _rules(ERP_RULES, FLASH_OVERRIDE_SELECTOR)
                      if _declaration(body, "z-index"))
        toolbar_zs = [
            int(z) for _, sel, body in ERP_RULES
            if "erp-toolbar" in sel and (z := _declaration(body, "z-index"))
        ]
        self.assertTrue(toolbar_zs, "没找到工具栏的 z-index 声明")
        self.assertGreater(flash_z, max(toolbar_zs),
                           f"提示 z-index={flash_z} 不高于工具栏 {max(toolbar_zs)}")

    def test_notice_z_index_still_below_drawer(self):
        """但也别越界：.erp-detail 抽屉是 60/61，提示要在它下面（抽屉操作优先）。"""
        flash_z = max(int(_declaration(body, "z-index"))
                      for body in _rules(ERP_RULES, FLASH_OVERRIDE_SELECTOR)
                      if _declaration(body, "z-index"))
        drawer_zs = [int(z) for _, sel, body in ERP_RULES
                     if ".erp-detail" in sel and (z := _declaration(body, "z-index"))]
        self.assertTrue(drawer_zs)
        self.assertLess(flash_z, min(drawer_zs))

    def test_notice_clears_toolbar_height(self):
        """top 要让开工具栏：真渲染实测桌面工具栏底边 51px（未滚动）/ 31px（贴顶滚动后），
        所以 top 至少 40px 才不会压住它那一排按钮。"""
        tops = [_px(_declaration(body, "top"))
                for body in _rules(ERP_RULES, FLASH_OVERRIDE_SELECTOR)
                if _declaration(body, "top")]
        self.assertTrue(tops, "规则里没有 top")
        self.assertGreaterEqual(max(tops), 40, f"top={max(tops)}px 会压在工具栏上")

    def test_base_rule_still_fixed_for_non_erp_pages(self):
        """基础定义没被改坏：普通页面仍是右上角浮层。"""
        bodies = _rules(STYLES_RULES, ".flash-stack")
        self.assertTrue(bodies)
        self.assertIn("fixed", _declaration(bodies[0], "position") or "")

    def test_mobile_keeps_inline_banner(self):
        """≤1100px 的页面内横幅规则必须留着（ERP 覆盖依赖它跳过移动端）。"""
        found = [
            (media, body) for media, sel, body in STYLES_RULES
            if sel == ".flash-stack" and media and "max-width" in media
            and "static" in (_declaration(body, "position") or "")
        ]
        self.assertTrue(found, "styles.css 里移动端 .flash-stack 的 static 规则没了")


if __name__ == "__main__":
    unittest.main()
