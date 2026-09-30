"""Phase 0：app.py Payroll 抽取后的边界契约。

这一阶段的工资重构被定义为「纯重构」，验收标准里最容易被后续改动悄悄破坏的有三条，
这里各钉一颗钉子：

1. 依赖方向 —— invoice_tool/payroll 任何一类举个都不能反向 import app；
   一旦有人图省事写下 ``from app import db``，文件虽然拆了，架构其实没有拆，
   而且会立刻变成循环 import。
2. 路由契约 —— 七个工资路由的 URL / endpoint / methods 必须与抽取前完全一致。
   endpoint 名就是权限映射的键，改名等于悄悄改权限。
3. 根模块兼容 —— 既有调用方（scripts/pg_rehearsal_checks.py、employee_finance、
   历史测试）都是通过 app.<name> 拿这些服务的，名字必须继续挂在根模块上。
"""
import unittest
from pathlib import Path

import app


ROOT = Path(__file__).resolve().parent
PAYROLL_PACKAGE = ROOT / "invoice_tool" / "payroll"

EXPECTED_ROUTES = [
    ("/payroll/subsidies", "payroll_subsidies", True),
    ("/reports/labor-hours", "labor_hours_report", False),
    ("/reports/payroll", "payroll_report", False),
    ("/reports/payroll-details", "payroll_detail_report", False),
    ("/payroll/calendar", "payroll_calendar", False),
    ("/payroll/calendar/batch", "payroll_calendar_batch", False),
    ("/payroll/calendar/export.xlsx", "payroll_calendar_export", False),
]

EXPOSED_NAMES = [
    "split_report_labor_hours", "labor_report_entries", "payroll_rows_for_range",
    "payroll_rows_for_period", "payroll_row_export", "payroll_payslip_payload",
    "payroll_period_dates", "payroll_calendar_weeks", "payroll_cycle_start_date",
    "payroll_historical_paid_date", "payroll_periods_for_month", "payroll_batch_payload",
    "current_payroll_period_start", "effective_payroll_worker_id", "historical_payroll_period",
    "payroll_subsidies", "labor_hours_report", "payroll_report", "payroll_detail_report",
    "payroll_calendar", "payroll_calendar_batch", "payroll_calendar_export",
]


class PayrollModuleBoundaryTest(unittest.TestCase):
    def test_payroll_package_does_not_import_app(self):
        offenders = []
        for path in sorted(PAYROLL_PACKAGE.glob("*.py")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith(("import app", "from app import", "from app.")):
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
        self.assertEqual([], offenders, "payroll 包不允许反向 import app，依赖必须通过 api 注入")

    def test_payroll_routes_keep_their_contract(self):
        registered = {}
        for rule in app.app.url_map.iter_rules():
            methods = {method for method in rule.methods if method not in ("HEAD", "OPTIONS")}
            registered.setdefault(str(rule), []).append((rule.endpoint, methods))
        for url, endpoint, allows_post in EXPECTED_ROUTES:
            self.assertIn(url, registered, f"路由不见了: {url}")
            matches = registered[url]
            self.assertEqual(1, len(matches), f"同一 URL 有多个注册: {url}")
            actual_endpoint, actual_methods = matches[0]
            self.assertEqual(endpoint, actual_endpoint, f"endpoint 名变了（会影响权限映射）: {url}")
            self.assertIn("GET", actual_methods)
            self.assertEqual(allows_post, ("POST" in actual_methods), f"method 集合变了: {url}")

    def test_root_module_still_exposes_payroll_api(self):
        missing = [name for name in EXPOSED_NAMES if not callable(getattr(app, name, None))]
        self.assertEqual([], missing, "根模块 app.py 必须继续暴露这些名字（外部调用方依赖）")


if __name__ == "__main__":
    unittest.main()
