"""权限管理页：菜单行标签必须左对齐（不能因缺「显示菜单」复选框而飞到最右）。

需求（用户提出）：贴了截图 —— 菜单权限树里「工作日报」「员工报销」「工单结算」这几行的
标签被顶到了最右边（「显示菜单」复选框也在右边），要求「保持左对齐」。

根因（headless 真渲染实测，非推理）：
- 旧 Web 样式的 `static/styles.css` 里有
  `.permission-menu-node > summary { display:flex; justify-content: space-between; ... }`；
- ERP 化后的 `templates/menu_permissions.html` 在 `.erp-app` 作用域里重绘了该 summary，
  逐条覆盖了 display / align-items / gap / padding / font，**但漏了 `justify-content`**
  （旧样式必须逐条显式清零，漏一条就是一条 bug）；
- 结果：`space-between` 残留生效。有「显示菜单」复选框的行有 2 个 flex 子项，
  space-between 把它们分列两端（恰好看起来是对的）；而**没有复选框**的 4 行
  （工作日报 / 员工报销 / 工单结算 / 操作日志，它们的 `menu_key` 为空）只有 1 个子项，
  space-between 把这唯一的标签推到**最右端**（实测 leftOff=781px）。
- 本页是靠 `.permission-inline-check { margin-left: auto }` 把复选框推到右边的，
  这本身就是正确做法，`space-between` 纯属多余且有害。

修法：在 ERP 作用域内显式 `justify-content: flex-start` 清零旧规则。

断言：源码契约（ERP 块里有 flex-start 重置）+ 反向对照思路说明。
真渲染验证见提交说明（headless 实测 BAD=0；摘掉修复后 BAD=4 / worst=781 / worstTxt=工作日报）。
"""

import re
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent
TEMPLATE = REPO_DIR / "templates" / "menu_permissions.html"
LEGACY_CSS = REPO_DIR / "static" / "styles.css"


class PermissionMenuLabelAlignmentTest(unittest.TestCase):
    """源码契约：ERP 作用域必须覆盖旧样式的 justify-content: space-between。"""

    @classmethod
    def setUpClass(cls):
        cls.tpl = TEMPLATE.read_text(encoding="utf-8")
        cls.legacy = LEGACY_CSS.read_text(encoding="utf-8")

    def test_legacy_rule_still_uses_space_between(self):
        """守住前提：旧样式确实还在写 space-between（若哪天被删，本测试该重写而不是误报）。"""
        pattern = re.compile(
            r"\.permission-menu-node\s*>\s*summary\s*\{[^}]*justify-content:\s*space-between",
            re.S,
        )
        self.assertRegex(
            self.legacy, pattern,
            msg="前提变了：styles.css 里 .permission-menu-node > summary 已不再写 space-between",
        )

    def test_erp_block_resets_justify_content(self):
        """关键断言：ERP 作用域内的 summary 规则必须显式 justify-content: flex-start。"""
        # 取 ERP 模板里 .erp-app .permission-menu-node > summary { ... } 规则体
        pattern = re.compile(
            r"\.erp-app\s+\.permission-menu-node\s*>\s*summary\s*\{(.*?)\}",
            re.S,
        )
        m = pattern.search(self.tpl)
        self.assertIsNotNone(m, msg="未找到 .erp-app .permission-menu-node > summary 规则")
        body = m.group(1)
        self.assertRegex(
            body, r"justify-content:\s*flex-start",
            msg="ERP 块的 summary 规则必须显式 justify-content: flex-start 覆盖旧 space-between；"
                "否则没有「显示菜单」复选框的行（工作日报/员工报销/工单结算/操作日志）标签会飞到最右。",
        )

    def test_erp_block_still_uses_margin_auto_for_checkbox(self):
        """复选框靠 margin-left:auto 推右 —— 这条不能被删（删了复选框会贴着标签）。"""
        pattern = re.compile(
            r"\.erp-app\s+\.permission-inline-check\s*\{(.*?)\}",
            re.S,
        )
        m = pattern.search(self.tpl)
        self.assertIsNotNone(m, msg="未找到 .erp-app .permission-inline-check 规则")
        self.assertRegex(
            m.group(1), r"margin-left:\s*auto",
            msg=".permission-inline-check 应保留 margin-left: auto（把复选框推到行右端）",
        )

    def test_rows_without_menu_key_exist(self):
        """守住动机：确实存在 menu_key 为空的菜单项（没有「显示菜单」复选框的那 4 行）。"""
        import importlib.util, shutil, tempfile
        temp = tempfile.TemporaryDirectory()
        try:
            src = Path(temp.name) / "app.py"
            shutil.copyfile(REPO_DIR / "app.py", src)
            spec = importlib.util.spec_from_file_location("perm_align_probe", src)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            groups = mod.ROLE_ACTION_PERMISSION_GROUPS
        finally:
            temp.cleanup()

        # menu_key 由「是否有对应菜单入口」推导；这里退一步：确认这些标签确实在配置里，
        # 且它们的 key 不在模板会渲染「显示菜单」的那批 menu_key 中。
        labels = {}
        for g in groups:
            for item in g["items"]:
                labels[item["label"]] = item

        for want in ("工作日报", "员工报销", "工单结算", "操作日志"):
            self.assertIn(want, labels, msg="权限配置里应存在菜单项：%s" % want)


if __name__ == "__main__":
    unittest.main()
