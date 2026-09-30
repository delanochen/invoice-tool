"""Payroll / labor 领域路由与服务注册。

依赖方向遵循项目既有约定：app 负责组装，本包不反向 import app。

    app.py  →  register_payroll_routes(app, globals())  →  PayrollService
                                                        →  db / rate_engine / settings
"""
from .calculations import (
    aggregate_payroll_rows,
    effective_display_rate,
    payroll_calendar_weeks,
    payroll_payslip_payload,
    payroll_period_dates,
    payroll_row_export,
    split_report_labor_hours,
)
from .routes import register_payroll_routes
from .services import build_payroll_services

__all__ = [
    "aggregate_payroll_rows",
    "build_payroll_services",
    "effective_display_rate",
    "payroll_calendar_weeks",
    "payroll_payslip_payload",
    "payroll_period_dates",
    "payroll_row_export",
    "register_payroll_routes",
    "split_report_labor_hours",
]
