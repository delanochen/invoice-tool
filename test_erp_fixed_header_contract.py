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
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
USERS = ROOT / "templates" / "users.html"
LEDGER = ROOT / "templates" / "employee_ledger.html"   # 标准范式参照
CSS = ROOT / "static" / "erp-ui.css"
GRID_JS = ROOT / "static" / "system-grid.js"


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
