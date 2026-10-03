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

    def test_type_is_groupable_dimension(self):
        """v0.1.346：类型是最常用的分组维度（员工往来账要按「工资 / 员工报销」分组）。"""
        self.assertTrue(self.dimension.search("类型"))

    def test_existing_labels_still_recognised(self):
        for label in ["金额", "明细金额", "合计工资", "Amount", "Line Total"]:
            self.assertTrue(self.monetary.fullmatch(label), f"monetary should still match {label}")
        for label in ["姓名", "站点", "客户", "报销编号"]:
            self.assertTrue(self.dimension.search(label), f"dimension should still match {label}")

    def test_non_money_columns_not_matched(self):
        for label in ["数量", "利润率", "状态", "分摊"]:
            self.assertFalse(self.monetary.fullmatch(label), f"monetary must not match {label}")


class GridMoneyOptInTest(unittest.TestCase):
    """v0.1.346：金额列也可以由源表 <th data-grid-money> 显式声明。

    分组入口（一级 / 二级分组 + 小计 / 合计）只在「表里有金额列」时出现，
    而金额列默认靠列名白名单识别 —— 员工往来账的「应付 / 抵扣 / 实付」不在名单里，
    不声明就整张表没有分组能力。
    """

    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).parent

    def test_grid_honours_money_attribute(self):
        js = (self.root / "static" / "system-grid.js").read_text(encoding="utf-8")
        self.assertIn("hasAttribute('data-grid-money')", js)
        self.assertIn("monetary.test(label) ||", js)

    def test_employee_ledger_declares_money_columns(self):
        html = (self.root / "templates" / "employee_ledger.html").read_text(encoding="utf-8")
        for label in ["应付", "抵扣", "其他调整", "实付"]:
            self.assertIn(f'<th data-grid-money>{label}</th>', html, f"{label} 必须声明为金额列")
        # 维度列本身不是金额列，别误标（标了就不能当分组维度）
        for label in ["员工", "类型", "状态"]:
            self.assertNotIn(f'<th data-grid-money>{label}</th>', html)

    def test_employee_payments_declares_money_columns(self):
        """v0.1.359：员工付款中心的付款单表要和员工往来账一样能按员工 / 类型分组小计。

        该表列名是「应付 / 借款抵扣 / 其他调整 / 实付」，全部不在 monetary 白名单里，
        不声明 data-grid-money 就整张表没有分组入口 / 小计行（实测 selects=0）。
        """
        html = (self.root / "templates" / "employee_payments.html").read_text(encoding="utf-8")
        for label in ["应付", "借款抵扣", "其他调整", "实付"]:
            self.assertIn(f'<th data-grid-money>{label}</th>', html, f"{label} 必须声明为金额列")
        for label in ["员工", "类型", "状态", "创建时间"]:
            self.assertNotIn(f'<th data-grid-money>{label}</th>', html)
        # 合计行只留表尾一行；默认 both 会在表头上方再压一行黄底合计，与底部汇总栏重复
        self.assertIn('data-grid-calcs="bottom"', html)


class DialogBackdropFixTest(unittest.TestCase):
    """v0.1.247: 编辑用户等弹窗不得因点击内部留白而关闭（仅真正点遮罩才关）。"""

    def test_backdrop_close_checks_content_rect(self):
        js = (pathlib.Path(__file__).parent / "static" / "modal-forms.js").read_text(encoding="utf-8")
        self.assertIn("getBoundingClientRect", js)
        self.assertIn("insideContent", js)


if __name__ == "__main__":
    unittest.main()
