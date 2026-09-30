"""ERP 网格「表头固定 + 数据区内部滚动」的结构契约（v0.1.348）。

用户诉求：「表格滚动能不能表头不动、表格内部动？」

根因不在 CSS 手脚，而在**结构少了一半**：表格被 system-grid.js 镜像成 Tabulator 后，
是否「吃满容器高度」完全取决于源表有没有 `data-grid-height`：

- 有 `data-grid-height="100%"` → Tabulator 拿到高度 → `.tabulator-tableholder`
  内部滚动，`.tabulator-header` 是它的兄弟节点，天然不随数据滚动（表头固定）；
- 没有 → Tabulator 按内容长高，滚动落到外层包裹元素上，**表头跟着一起滚走**。

用户管理页此前就是后者（外层还是老的 `.table-scroll`）。真渲染实测（60 行数据，
1400×760 视口）：修复前滚动容器 = `.table-scroll`（clientH 406 / scrollH 1825），
滚动 240px 后表头位移 **-240px**；修复后容器 = `.tabulator-tableholder`
（clientH 347 / scrollH 1768），表头位移 **0.0px**，与员工往来账（标准范式）完全一致。

契约（改这几处必须同步本文件）：
- templates/users.html：容器 `.erp-grid-wrap`（吃掉剩余高度）+ 源表 `data-grid-height="100%"`；
- static/erp-ui.css 的 `.erp-grid-wrap` / `.system-grid` / `.tabulator` 三条高度规则；
- static/system-grid.js 的 `source.dataset.gridHeight` 透传。

## v0.1.349：批量推广到全站（不再只钉 users 一页）

同一模式（有 erp-grid 但无 data-grid-height）的页面共 20 个，本次按同一范式处理 19 个、
共 22 张表；唯一例外是 invoice_detail 的附件清单表 —— 它挂在 `.erp-detail-scroll` 内，
整块详情本来就是一整块滚动区，套 100% 会和详情滚动与打印规则打架，保持原样。

真渲染前后对照（60 行数据 / 1400×760，见文末 ERP_CONTRACT_PAGES）：

| 页面 | 滚动容器 前→后 | 表头位移 前→后 |
|---|---|---|
| work_order_types | table-scroll → tabulator-tableholder | -200.0 → **0.0** |
| projects         | table-scroll → tabulator-tableholder |  -44.0 → **0.0** |
| company_info     | table-scroll → tabulator-tableholder |  -17.0 → **0.0** |

其余页面数据没到溢出量级，量不出滚动，但**表格高度从内容高度（74～398px）涨到面板
剩余高度（321～476px）** —— 这就是高度链已打通的证据，数据一多自然走内部滚动。

改造中查明的两个结构事实（改这类页面前必读）：
1. 高度链根节点是 `.erp-app`（自带 `height: calc(100vh - var(--erp-offset,128px))`），
   与是否加载 erp-report.js 无关（那个脚本只是把默认的 128px 换成实测值）。19 页都有
   `.erp-app` + `.erp-body`，所以没有出现「加了 100% 反而塌陷」的页面。
2. `.erp-panel { display:none }` / `.active { display:block }`：没有 `active`、又没有被
   erp-report.js 管理的面板**永远不显示**。finance_overview（3 段只剩 1 段）与
   asset_detail（4 段只剩 1 段）的 section 全是裸 `erp-panel`，它们的表格此前根本
   看不见（既有 bug，不在本版本修）。
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
USERS = ROOT / "templates" / "users.html"
LEDGER = ROOT / "templates" / "employee_ledger.html"   # 标准范式参照
CSS = ROOT / "static" / "erp-ui.css"
GRID_JS = ROOT / "static" / "system-grid.js"

#: v0.1.349 批量改造的 19 个页面（含 users / employee_ledger 两个已改造工时长的参照页）。
#: invoice_detail **刻意不在此列**：它的 erp-grid 挂在 .erp-detail-scroll 里的
#: .attachment-panel 下，那一整块详情本来就是一个滚动区，按「整页滚动」设计，
#: 钉住高度反而会与详情滚动 / 打印规则冲突。
ERP_CONTRACT_PAGES = [
    "asset_detail", "assets", "bank_accounts", "bank_transactions", "buyers", "clients",
    "company_info", "countries", "dashboard", "employee_advances", "employee_ledger",
    "expense_processing", "finance_overview", "knowledge_base", "manufacturers", "owners",
    "payment_terms", "projects", "service_order_detail", "users", "work_order_types",
]


def grid_table_tags(text):
    """取出源表里所有参与网格化的 <table> 开标签。"""
    return re.findall(r"<table[^>]*\berp-grid\b[^>]*>", text)


def preceding_wrapper(text, table_tag):
    """表前最近的 <div>/<section> 开标签 —— 真正的滚动容器候选。"""
    candidates = re.findall(r"<(?:div|section)[^>]*>", text[: text.index(table_tag)])
    return candidates[-1] if candidates else ""


class UsersGridFixedHeaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = USERS.read_text(encoding="utf-8")
        cls.ledger = LEDGER.read_text(encoding="utf-8")

    def test_users_table_wraps_in_grid_wrap(self):
        """容器必须是 .erp-grid-wrap —— 它负责把剩余高度交给 system-grid。"""
        self.assertIn('<div class="erp-grid-wrap">', self.html)
        self.assertNotIn('class="table-scroll"', self.html,
                         "老的 .table-scroll 包裹层不会给网格高度，滚动会落在它身上（表头跟着滚）")

    def test_users_table_declares_grid_height(self):
        """源表必须显式声明高度，否则 Tabulator 退回「按内容长高」。"""
        match = re.search(r"<table[^>]*\berp-grid\b[^>]*>", self.html)
        self.assertIsNotNone(match, "找不到源表开标签")
        self.assertIn('data-grid-height="100%"', match.group(0))

    def test_ledger_keeps_same_pattern(self):
        """参照页（员工往来账）保持同一范式；它变了说明范式本身被改过。"""
        self.assertIn('<div class="erp-grid-wrap">', self.ledger)
        self.assertIn('data-grid-height="100%"', self.ledger)


class GridHeightPlumbingTest(unittest.TestCase):
    """全站扫描：任何 erp-grid 表格都必须同时具备容器 + 高度声明（v0.1.349）。"""

    def test_every_grid_declares_height(self):
        for name in ERP_CONTRACT_PAGES:
            with self.subTest(page=name):
                text = (ROOT / "templates" / f"{name}.html").read_text(encoding="utf-8")
                tags = grid_table_tags(text)
                self.assertTrue(tags, f"{name}: 找不到 erp-grid 源表")
                for tag in tags:
                    self.assertIn('data-grid-height="100%"', tag,
                                  f"{name}: 源表缺 data-grid-height，Tabulator 会退回按内容长高（表头跟着滚）")

    def test_every_grid_sits_in_grid_wrap(self):
        """.table-scroll 是老的外部滚容器，它会让滚动落在外面，表头跟着走。"""
        for name in ERP_CONTRACT_PAGES:
            with self.subTest(page=name):
                text = (ROOT / "templates" / f"{name}.html").read_text(encoding="utf-8")
                self.assertNotIn('class="table-scroll"', text,
                                 f"{name}: 仍存在 .table-scroll 包裹层")
                for tag in grid_table_tags(text):
                    wrapper = preceding_wrapper(text, tag)
                    self.assertIn("erp-grid-wrap", wrapper,
                                  f"{name}: 表格外层不是 .erp-grid-wrap（实际={wrapper[:60]}）")

    def test_every_page_has_height_chain_root(self):
        """高度链根：没有 .erp-app（100vh - offset）就给不出确定高度，100% 会失效。"""
        for name in ERP_CONTRACT_PAGES:
            with self.subTest(page=name):
                text = (ROOT / "templates" / f"{name}.html").read_text(encoding="utf-8")
                self.assertTrue(re.search(r'class="[^"]*\berp-app\b', text), f"{name}: 缺 .erp-app 外壳")
                self.assertIn("erp-body", text, f"{name}: 缺 .erp-body")

    def test_invoice_detail_attachment_grid_stays_fluid(self):
        """反向钉住例外：发票详情附件表故意不套固定高度。"""
        text = (ROOT / "templates" / "invoice_detail.html").read_text(encoding="utf-8")
        for tag in grid_table_tags(text):
            self.assertNotIn("data-grid-height", tag,
                             "附件清单在 .erp-detail-scroll 里，整块详情是一体的滚动区，不要钉高度")
    @classmethod
    def setUpClass(cls):
        cls.js = GRID_JS.read_text(encoding="utf-8")
        cls.css = CSS.read_text(encoding="utf-8")

    def test_system_grid_passes_height_to_tabulator(self):
        """opt-in 透传：没有这段，data-grid-height 写了也没用。"""
        self.assertIn("source.dataset.gridHeight", self.js)
        self.assertIn("...(gridHeight ? {height:gridHeight", self.js)

    def test_container_and_grid_take_full_height(self):
        """高度链三段：wrap 吃剩余高度、system-grid 与 .tabulator 逐级 flex:1。"""
        self.assertRegex(self.css, r"\.erp-grid-wrap\s*\{[^}]*flex:\s*1 1 auto")
        self.assertRegex(self.css, r"\.erp-app \.system-grid\s*\{[^}]*flex:\s*1 1 auto")
        self.assertRegex(self.css, r"\.erp-app \.system-grid \.tabulator\s*\{[^}]*flex:\s*1 1 auto")


if __name__ == "__main__":
    unittest.main()
