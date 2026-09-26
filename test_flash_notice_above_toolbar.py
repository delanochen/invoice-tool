"""ERP / 全站页面里的服务端提示（.flash-stack）必须浮在顶部常驻栏之上，并且让开它。

背景（两轮用户报障）：
  · v0.1.309 前：提示是 fixed top:16 right:16 z-index:10，ERP 页面顶部有常驻 sticky 工具栏
    （erp-ui.css：z-index 12）→ 提示层级低 2 级，整块被工具栏渐变背景盖住，只剩一圈边框
    （「报销审核通过」后的现象）。
  · v0.1.309 把 top 写死成 60px —— 但工具栏高度是变量（窗口变窄会换行、标题长短不同、
    页面之间也不同），在「员工等级」这类页面上又压住了工具栏那排按钮（用户第二次报障）。

所以现在的做法是「量一次、写成变量」：
  static/flash-notice.js  量出顶部常驻栏的真实高度 → 写到 :root 的 --flash-top
  styles.css  base        .flash-stack { position: fixed; top: var(--flash-top, 16px); z-index: 45 }
  styles.css  ≤1100px     .flash-stack { position: sticky; top: var(--flash-top, 0) }
  erp-ui.css  >1100px     body:has(.erp-app) .flash-stack { top: var(--flash-top, 60px) }  ← 兜底

真渲染证据见仓库根的 _flash_all_probe.py（全站逐页 × 多宽度 × 滚动后复测）与
_flash_probe.py（报销详情页走真实审核流程）。这里用静态契约把结论钉住，
防止以后调样式时又退回写死的 top 或过低的 z-index（真渲染那步太重，不进单测）。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ERP_CSS = (ROOT / "static" / "erp-ui.css").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
BASE_HTML = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
FLASH_JS = (ROOT / "static" / "flash-notice.js").read_text(encoding="utf-8")

FLASH_SELECTOR = ".flash-stack"
FLASH_JS_NAME = "flash-notice.js"
FLASH_VAR = "--flash-top"


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
    return [(media, body) for media, sel, body in rules if sel == selector]


def _base_rule():
    """styles.css 里 .flash-stack 的第一条规则（基础定位）。"""
    found = _rules(STYLES_RULES, FLASH_SELECTOR)
    assert found, "styles.css 里找不到 .flash-stack 基础规则"
    return found[0][1]


class FlashNoticeAboveToolbarTest(unittest.TestCase):
    # ---------------------------------------------------------------- 层级
    def test_notice_z_index_beats_every_top_bar(self):
        """提示层级必须高于所有「贴顶常驻栏」：erp-toolbar 12、erp-status 10、
        erp-summary 9、窄屏 erp-nav 40。低一级就会被整条盖住。"""
        flash_z = int(_declaration(_base_rule(), "z-index"))
        bars = {}
        for _, sel, body in ERP_RULES:
            z = _declaration(body, "z-index")
            if not z:
                continue
            for name in ("erp-toolbar", "erp-status", "erp-summary", "erp-nav", "erp-body"):
                if name in sel and "erp-detail" not in sel:
                    bars[name] = max(bars.get(name, 0), int(z))
        self.assertTrue(bars, "erp-ui.css 里没找到常驻栏的 z-index")
        self.assertGreater(flash_z, max(bars.values()),
                           f"提示 z-index={flash_z} 不高于常驻栏 {bars}")

    def test_notice_z_index_still_below_drawer_and_topbar(self):
        """但也别越界：.erp-detail 抽屉（60/61）与顶栏（100）要在提示之上。"""
        flash_z = int(_declaration(_base_rule(), "z-index"))
        drawer_zs = [int(z) for _, sel, body in ERP_RULES
                     if ".erp-detail" in sel and (z := _declaration(body, "z-index"))]
        topbar_zs = [int(z) for _, sel, body in STYLES_RULES + ERP_RULES
                     if sel == ".topbar" and (z := _declaration(body, "z-index"))]
        self.assertTrue(drawer_zs, "没找到 .erp-detail 的 z-index")
        self.assertLess(flash_z, min(drawer_zs), "提示盖住了抽屉")
        for top_z in topbar_zs:
            self.assertLess(flash_z, top_z, "提示盖住了顶栏")

    # ---------------------------------------------------------------- 位置
    def test_base_top_reads_the_measured_variable(self):
        """top 必须是 var(--flash-top, …)：写死值在工具栏换行变高的页面上必然失手。"""
        top = _declaration(_base_rule(), "top")
        self.assertIsNotNone(top)
        self.assertIn(FLASH_VAR, top, f"top={top!r} 没读 --flash-top")
        fallback = re.search(r"var\(\s*" + re.escape(FLASH_VAR) + r"\s*,\s*(\d+)px\s*\)", top)
        self.assertIsNotNone(fallback, f"top={top!r} 没有 px 兜底值")
        self.assertGreaterEqual(int(fallback.group(1)), 8)

    def test_erp_fallback_clears_toolbar_height(self):
        """erp-ui.css 的兜底（脚本还没跑时）至少 40px —— 实测桌面工具栏底边约 51px，
        16px 会正好落在工具栏中间，就是用户截图那个样子。"""
        bodies = [body for _, body in _rules(ERP_RULES, "body:has(.erp-app) .flash-stack")]
        self.assertTrue(bodies, "erp-ui.css 里缺少 ERP 页面的兜底规则")
        fallbacks = []
        for body in bodies:
            top = _declaration(body, "top") or ""
            match = re.search(r"var\(\s*" + re.escape(FLASH_VAR) + r"\s*,\s*(\d+)px\s*\)", top)
            self.assertIsNotNone(match, f"ERP 兜底的 top={top!r} 没读 --flash-top 或没有兜底值")
            fallbacks.append(int(match.group(1)))
        self.assertGreaterEqual(max(fallbacks), 40, f"兜底 top={max(fallbacks)}px 会压在工具栏上")

    def test_erp_fallback_scoped_to_desktop_only(self):
        """兜底必须限定桌面断点：≤1100px 那条 sticky 规则要能接管。"""
        medias = [media for media, sel, _ in ERP_RULES
                  if sel == "body:has(.erp-app) .flash-stack"]
        self.assertTrue(medias, "规则没放在任何 @media 里")
        for media in medias:
            self.assertIn("min-width", media or "", f"媒体查询没限定最小宽度：{media!r}")

    def test_mobile_banner_sticks_instead_of_scrolling_under_the_bar(self):
        """≤1100px 的页面内横幅必须是 sticky：static 时页面一滚动就被吸顶工具栏整条盖住。"""
        found = [(media, body) for media, sel, body in STYLES_RULES
                 if sel == FLASH_SELECTOR and media and "max-width" in media]
        self.assertTrue(found, "styles.css 里窄屏 .flash-stack 规则没了")
        media, body = found[0]
        self.assertIn("sticky", _declaration(body, "position") or "",
                      f"窄屏规则是 {_declaration(body, 'position')!r}，滚动后会被工具栏盖住")
        self.assertIn(FLASH_VAR, _declaration(body, "top") or "")

    def test_base_rule_still_fixed_overlay(self):
        """基础定位没被改坏：普通页面仍是右上角浮层。"""
        self.assertIn("fixed", _declaration(_base_rule(), "position") or "")

    # ---------------------------------------------------------------- 测量脚本
    def test_measure_script_is_loaded_on_every_page(self):
        self.assertIn(FLASH_JS_NAME, BASE_HTML,
                      "base.html 没加载 flash-notice.js —— --flash-top 永远没人写")
        self.assertIn("defer", BASE_HTML.split(FLASH_JS_NAME)[1][:120],
                      "脚本应带 defer（不阻塞解析）")

    def test_measure_script_writes_the_variable(self):
        self.assertIn(FLASH_VAR, FLASH_JS)
        self.assertIn("setProperty", FLASH_JS)
        self.assertRegex(FLASH_JS, r"addEventListener\(\s*'resize'",
                         "窗口尺寸变了要重新量（工具栏会换行）")

    def test_measure_script_clears_the_bar_bottom_not_only_its_height(self):
        """必须让开顶栏的**底边**而不是它的高度：顶栏不一定贴着视口最顶（<main> 有内边距，
        ERP 页实测 header 在 y=20..51、高度只有 31px），只让开高度会让提示正好压在按钮上
        —— 全站审计里 15 个页面各压住 1~7 个按钮，就是这条取错了。"""
        self.assertRegex(FLASH_JS, r"Math\.max\(\s*rect\.height\s*,\s*rect\.bottom\s*\)",
                         "没有用 max(高度, 底边) 取让开量")

    def test_measure_script_ignores_bottom_bars_and_full_screen_overlays(self):
        """只认「贴顶 + 通栏 + 高度正常」的栏：底部固定条（top:auto）、
        inset:0 的遮罩/抽屉（高度接近视口）都不该被算成顶栏。"""
        self.assertRegex(FLASH_JS, r"parseFloat\(\s*style\.top\s*\)",
                         "用 parseFloat 读 top，top:auto 得 NaN 才能被排除")
        self.assertRegex(FLASH_JS, r"top\s*>\s*24", "没有排除 top 很大的浮层")
        self.assertRegex(FLASH_JS, r"rect\.height\s*>\s*240", "没有排除全屏遮罩/抽屉")
        self.assertIn("innerWidth * 0.6", FLASH_JS.replace("window.", ""),
                      "没有要求「通栏」（宽度过半）")


if __name__ == "__main__":
    unittest.main()
