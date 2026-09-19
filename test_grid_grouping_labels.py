"""v0.1.247 regression: profitability detail grid must support grouped subtotals.

The 项目利润 page's 「展开利润明细」 table exposes 收入/成本/利润/客户单价
columns and 类别/项目 dimensions. system-grid.js only offers the grouping
toolbar (一级/二级分组 + 小计) when a column matches the monetary regex, and
only offers dimensions matching the dimension regex — neither contained the
profitability labels, so the table had no 分类汇总 at all.
"""

import pathlib
import re
import unittest

GRID = pathlib.Path(__file__).parent / "static" / "system-grid.js"


def extract_regex(source: str, name: str) -> re.Pattern:
    match = re.search(rf"const {name} = /(.+?)/;", source)
    assert match, f"{name} regex not found"
    return re.compile(match.group(1))


class GridRegexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = GRID.read_text(encoding="utf-8")
        cls.monetary = extract_regex(cls.source, "monetary")
        cls.dimension = extract_regex(cls.source, "dimension")

    def test_profitability_money_columns(self):
        for label in ["收入", "成本", "利润", "客户单价", "员工/实际成本单价"]:
            self.assertTrue(self.monetary.fullmatch(label), f"monetary should match {label}")

    def test_profitability_dimension_columns(self):
        for label in ["工单", "员工", "类别", "项目", "日期"]:
            self.assertTrue(self.dimension.search(label), f"dimension should match {label}")

    def test_existing_labels_still_recognised(self):
        for label in ["金额", "明细金额", "合计工资", "Amount", "Line Total"]:
            self.assertTrue(self.monetary.fullmatch(label), f"monetary should still match {label}")
        for label in ["姓名", "站点", "客户", "报销编号"]:
            self.assertTrue(self.dimension.search(label), f"dimension should still match {label}")

    def test_non_money_columns_not_matched(self):
        for label in ["数量", "利润率", "状态", "分摊"]:
            self.assertFalse(self.monetary.fullmatch(label), f"monetary must not match {label}")


class DialogBackdropFixTest(unittest.TestCase):
    """v0.1.247: 编辑用户等弹窗不得因点击内部留白而关闭（仅真正点遮罩才关）。"""

    def test_backdrop_close_checks_content_rect(self):
        js = (pathlib.Path(__file__).parent / "static" / "modal-forms.js").read_text(encoding="utf-8")
        self.assertIn("getBoundingClientRect", js)
        self.assertIn("insideContent", js)


if __name__ == "__main__":
    unittest.main()
