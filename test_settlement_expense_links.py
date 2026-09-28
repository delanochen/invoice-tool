"""回归：结算前审核的报销编号链接必须在工作区内开标签，不能弹新窗口。

工作区外壳（static/workspace.js）的点击拦截会显式跳过带 ``target`` 的链接，
让它们走浏览器原生行为 —— 也就是在外部浏览器开一个新窗口。项目的既有约定
（见 test_formal_report_link.py，v0.1.246）是：列表/明细类站内链接一律不带
``target="_blank"``，交给工作区开标签；只有图片/PDF/Word 附件预览、以及外站
链接（Google 地图等）才允许弹窗/下载。

报销候选池的表格会被 system-grid.js 镜像成 Tabulator，镜像按 innerHTML 复制
并转发 ``original.click()``，因此这里必须只校验模板 HTML 属性。
"""

import pathlib
import re
import unittest

TEMPLATE = pathlib.Path(__file__).parent / "templates" / "settlement_expense_review.html"


class SettlementExpenseLinkTest(unittest.TestCase):
    def setUp(self):
        self.html = TEMPLATE.read_text(encoding="utf-8")

    def test_expense_number_has_no_target(self):
        """报销编号必须指向 expense_detail，且不带 target。"""
        anchors = re.findall(
            r"<a\b[^>]*url_for\('expense_detail'[^>]*>", self.html
        )
        self.assertTrue(anchors, "未找到报销编号链接")
        for anchor in anchors:
            self.assertNotIn(
                "target=",
                anchor,
                "报销编号链接带 target 会在工作区外弹新窗口，应交给工作区开标签",
            )

    def test_expense_number_still_linkable(self):
        """去掉 target 后必须是可点的 <a href>，不能退化成纯文本。"""
        self.assertNotRegex(
            self.html,
            r"<td>\s*\{\{\s*row\.expense_number\s*\}\}\s*</td>",
            "报销编号退化成纯文本，审核员无法打开报销明细核对",
        )


if __name__ == "__main__":
    unittest.main()
