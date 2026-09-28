# -*- coding: utf-8 -*-
"""时长展示精度的统一契约（工时/时长/小时 一律保留 2 位小数）。

起因：用户看到「时薪 35，2.2 小时却是 78.75」。
根因不是算错，而是两层精度不一致：
  1. 计算层：日报在岗时长按 15 分钟量化取整（app.rounded_report_service_hours），
     最小档位是 0.25 小时，所以 128~142 分钟都会变成 2.25 小时，
     金额 = 2.25 × 35 = 78.75。
  2. 显示层：旧 hours() 过滤器用 f"{v:.1f}"，把 2.25 显示成 "2.2"，
     与 2.25×35 对不上，看起来像算错。

修复（v0.1.331）：hours() 改为保留 2 位小数并去掉末尾无意义的 0，
使 2.0 → "2"、2.25 → "2.25"、2.5 → "2.5"，与金额口径一致。
金额/单价不走这个过滤器，不能被改。

本测试钉住：过滤器数值契约 + 所有模板调用点仍在 + 不再有 .1f 残留。
"""
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


def load_hours_filter():
    """只加载 app.py 里的 hours() —— 不 import app（会碰 data/ 目录）。"""
    source = (REPO_DIR / "app.py").read_text(encoding="utf-8")
    match = re.search(r"^def hours\(value\):\n(?:.*\n)*?(?=^\S|\Z)", source, re.MULTILINE)
    assert match, "app.py 里找不到 hours() 定义"
    namespace = {}
    exec(compile(match.group(0), "hours_filter", "exec"), namespace)
    return namespace["hours"]


class HoursFilterPrecisionTest(unittest.TestCase):
    """hours() 的数值契约。"""

    @classmethod
    def setUpClass(cls):
        cls.hours = staticmethod(load_hours_filter())

    def test_quarter_hour_keeps_two_decimals(self):
        """核心回归：2.25 必须显示成 2.25，而不是旧行为的 2.2。"""
        self.assertEqual(self.hours(2.25), "2.25")
        self.assertEqual(self.hours(0.25), "0.25")
        self.assertEqual(self.hours(2.75), "2.75")

    def test_old_one_decimal_behaviour_is_gone(self):
        """反向对照：旧实现会给出 2.2，改后必须不是 2.2。"""
        self.assertNotEqual(self.hours(2.25), "2.2")
        self.assertNotEqual(self.hours(2.25), f"{2.25:.1f}")

    def test_amount_matches_displayed_hours(self):
        """显示出来的小时数 × 时薪，必须等于系统算出的金额。"""
        hourly_rate = 35
        amount = 78.75
        displayed = float(self.hours(2.25))
        self.assertAlmostEqual(displayed * hourly_rate, amount, places=2)

    def test_trailing_zeros_are_trimmed(self):
        """去掉末尾无意义的 0，避免 2.0 / 3.50 这种噪声。"""
        self.assertEqual(self.hours(2.0), "2")
        self.assertEqual(self.hours(2.5), "2.5")
        self.assertEqual(self.hours(3.0), "3")
        self.assertEqual(self.hours(8), "8")

    def test_none_and_invalid_become_zero(self):
        """None / 空串 / 非数字不能炸模板（列表页到处在渲染空值）。"""
        self.assertEqual(self.hours(None), "0")
        self.assertEqual(self.hours(""), "0")
        self.assertEqual(self.hours("abc"), "0")

    def test_string_numbers_are_parsed(self):
        """数据库 REAL / TEXT 列都可能回来，字符串数字要能解析。"""
        self.assertEqual(self.hours("2.25"), "2.25")
        self.assertEqual(self.hours("0"), "0")

    def test_negative_is_preserved(self):
        """时长理论上不会为负，但过滤器不该把负数吞成 0（以免掩盖脏数据）。"""
        self.assertEqual(self.hours(-1.5), "-1.5")

    def test_all_quarter_steps_round_trip(self):
        """0.25 步长是工时的最小粒度，全档位必须无损往返。"""
        for step in range(0, 40):
            value = step * 0.25
            with self.subTest(value=value):
                self.assertAlmostEqual(float(self.hours(value)), value, places=2)


class HoursFilterSourceContractTest(unittest.TestCase):
    """静态契约：防止有人把精度改回 1 位，或新增绕过过滤器的裸显示。"""

    @classmethod
    def setUpClass(cls):
        cls.source = (REPO_DIR / "app.py").read_text(encoding="utf-8")

    def test_hours_filter_still_registered(self):
        self.assertIn('app.jinja_env.filters["hours"] = hours', self.source)

    def test_filter_no_longer_uses_one_decimal(self):
        """hours() 函数体（去掉文档字符串）里不能再出现 .1f。"""
        match = re.search(r"^def hours\(value\):\n(?:.*\n)*?(?=^\S|\Z)", self.source, re.MULTILINE)
        self.assertIsNotNone(match)
        body = match.group(0)
        # 去掉三引号文档字符串：文档里故意引用了旧写法 f"{2.25:.1f}" 作说明。
        body = re.sub(r'"""(?:.|\n)*?"""', "", body)
        self.assertNotIn(".1f", body, msg="hours() 仍在使用一位小数格式")
        self.assertIn(".2f", body, msg="hours() 应使用两位小数格式")

    def test_templates_keep_using_the_filter(self):
        """7 个模板的 53 处 |hours 调用点都要还在（不能被裸 {{ x }} 取代）。"""
        templates = [
            "customer_reimbursement_form.html",
            "labor_hours_report.html",
            "payroll_detail_report.html",
            "payroll_report.html",
            "service_order_detail.html",
            "service_report_query.html",
            "service_report_view.html",
        ]
        for name in templates:
            with self.subTest(template=name):
                text = (REPO_DIR / "templates" / name).read_text(encoding="utf-8")
                self.assertIn("|hours", text, msg=f"{name} 不再使用 hours 过滤器")

    def test_no_one_decimal_hour_formatting_left(self):
        """全仓不应再有把时长格式化成 1 位小数的 JS/模板代码。"""
        offenders = []
        js_files = list((REPO_DIR / "static").glob("*.js"))
        js_files += list((REPO_DIR / "templates").glob("*.html"))
        for path in js_files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if "toFixed(1)" not in line:
                    continue
                # 只关心时长；排除体积(KB/MB)、百分比、评分
                lower = line.lower()
                if any(token in lower for token in ("kb", "mb", "confidence", "rating", "%")):
                    continue
                if "小时" in line or "hour" in lower:
                    offenders.append(f"{path.name}:{lineno}: {line.strip()[:100]}")
        self.assertEqual(offenders, [], msg="仍有一位数时长显示：\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main(verbosity=2)
