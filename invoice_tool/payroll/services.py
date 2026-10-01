"""工资 / 工时的数据库服务层。

这一层负责取数（SQL）与组装，计算部分交给 :mod:`calculations`。

依赖方向：app → PayrollService → db / rate_engine / settings。
本模块**不允许** ``import app``：所有来自 app 的东西（``db``、``get_setting``、
``payroll_subsidy_settings``、节假日判定……）都通过 ``api`` 取。

``api`` 是 app 模块的 ``globals()``（live dict），因此所有依赖都在**调用时**解析。
这既避免了循环 import，也保留了既有的可测性：测试仍然可以
``patch.object(app, "labor_report_entries", ...)`` 并让工资汇总看到替换后的实现。
"""
from __future__ import annotations

from datetime import date, timedelta

from rate_engine import employee_rate

from .calculations import (
    aggregate_payroll_rows,
    payroll_calendar_weeks,
    payroll_component_rows,
    payroll_payslip_payload,
    payroll_period_dates,
    payroll_row_export,
    split_report_labor_hours,
)
from .tax import build_payment_components


def build_payroll_services(api):
    """返回一组工资服务函数，键名与 app.py 里对外暴露的名字一致。"""
    def _split_report_labor_hours(report, worker_count=1):
        return split_report_labor_hours(
            report,
            worker_count,
            duration_resolver=api["report_duration_hours"],
            holiday_resolver=api["is_us_weekend_or_holiday"],
        )

    def _labor_report_entries(date_from="", date_to="", worker_id="", date_mode="actual"):
        if date_mode == "attendance":
            report_date_expr = "date(coalesce(service_reports.actual_work_date, service_reports.report_date))"
        elif date_mode == "report":
            report_date_expr = "date(service_reports.report_date)"
        else:
            report_date_expr = "coalesce(service_reports.actual_work_date, service_reports.report_date)"
        clauses = ["1 = 1"]
        params = []
        if date_from:
            clauses.append(f"{report_date_expr} >= ?")
            params.append(date_from)
        if date_to:
            clauses.append(f"{report_date_expr} <= ?")
            params.append(date_to)
        if worker_id and str(worker_id).isdigit():
            clauses.append("users.id = ?")
            params.append(int(worker_id))
        rows = api["db"]().execute(
            f"""
        with worker_counts as (
            select report_id, count(*) as worker_count
            from service_report_workers
            group by report_id
        )
        select service_reports.id as report_id,
               service_reports.report_date,
               {report_date_expr} as actual_work_date,
               {report_date_expr} as attendance_date,
               service_reports.total_service_hours,
               service_reports.travel_hours,
               service_reports.public_transport_hours,
               service_report_workers.driving_miles as worker_driving_miles,
               service_report_workers.travel_mode as worker_travel_mode,
               service_report_workers.travel_hours as worker_travel_hours,
               service_report_workers.public_transport_hours as worker_public_transport_hours,
               service_reports.arrival_time,
               service_reports.departure_time,
               service_orders.id as service_order_id,
               service_orders.order_number,
               service_orders.client_name,
               coalesce(owners.name, buyers.owner) as buyer_owner,
               users.id as worker_id,
               users.name as worker_name,
               employee_grades.grade_name,
               employee_grades.base_salary,
               employee_grades.meal_daily_amount,
               employee_grades.car_allowance_method,
               employee_grades.car_mileage_rate,
               employee_grades.rental_driving_hourly_rate,
               employee_grades.standard_hourly_rate,
               employee_grades.transport_hourly_rate,
               employee_grades.overtime_hourly_rate,
               employee_grades.holiday_hourly_rate,
               coalesce(worker_counts.worker_count, 1) as worker_count
        from service_reports
        join service_orders on service_orders.id = service_reports.service_order_id
        join service_report_workers on service_report_workers.report_id = service_reports.id
        join users on users.id = service_report_workers.user_id
        left join employee_grades on employee_grades.id = users.employee_grade_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join owners on owners.id = buyers.owner_id
        left join worker_counts on worker_counts.report_id = service_reports.id
        where {" and ".join(clauses)}
        order by actual_work_date desc, users.name, service_orders.order_number
        """,
            params,
        ).fetchall()
        entries = []
        for row in rows:
            hours = api["split_report_labor_hours"](row, row["worker_count"])
            entry = dict(row)
            entry.update(hours)
            entry["work_hours"] = hours["standard_hours"] + hours["overtime_hours"] + hours["holiday_hours"]
            entries.append(entry)
        return entries

    def _range_inputs(period_start, period_end, worker_id="", date_mode="attendance", detail=False):
        """payroll_rows_for_range 与组件明细共用的取数（工时行 + 撰稿行 + 补贴参数）。"""
        subsidy_settings = api["payroll_subsidy_settings"]()
        rows = api["labor_report_entries"](period_start.isoformat(), period_end.isoformat(), worker_id, date_mode=date_mode)

        def rate_resolver(worker_id, work_date, rate_type):
            return employee_rate(api["db"](), worker_id, work_date, rate_type)["rate"]

        date_expr = "date(service_reports.report_date)" if date_mode == "report" else "date(coalesce(service_reports.actual_work_date, service_reports.report_date))"
        clauses = [f"{date_expr} >= ?", f"{date_expr} <= ?", "service_reports.report_writer_id is not null"]
        params = [period_start.isoformat(), period_end.isoformat()]
        if worker_id and str(worker_id).isdigit():
            clauses.append("users.id = ?")
            params.append(int(worker_id))
        writer_rows = api["db"]().execute(
            f"""
        select users.id as worker_id, users.name as worker_name,
               employee_grades.grade_name, employee_grades.base_salary,
               employee_grades.meal_daily_amount, employee_grades.standard_hourly_rate,
               employee_grades.car_allowance_method, employee_grades.car_mileage_rate,
               employee_grades.rental_driving_hourly_rate,
               employee_grades.transport_hourly_rate, employee_grades.overtime_hourly_rate,
               employee_grades.holiday_hourly_rate, count(service_reports.id) as report_writing_count
               {f', {date_expr} as attendance_date, service_reports.service_order_id, service_orders.order_number, service_orders.client_name, service_reports.id as report_id' if detail else ''}
        from service_reports join users on users.id = service_reports.report_writer_id
        join service_orders on service_orders.id = service_reports.service_order_id
        left join employee_grades on employee_grades.id = users.employee_grade_id
        where {" and ".join(clauses)}
        group by users.id, employee_grades.id {', service_reports.id, service_orders.id' if detail else ''}
        """, params,
        ).fetchall()
        return rows, writer_rows, subsidy_settings, rate_resolver

    def _payroll_rows_for_range(period_start, period_end, pay_date, worker_id="", date_mode="attendance", detail=False):
        rows, writer_rows, subsidy_settings, rate_resolver = _range_inputs(
            period_start, period_end, worker_id, date_mode=date_mode, detail=detail)
        payroll_rows, totals = aggregate_payroll_rows(
            rows,
            writer_rows,
            subsidy_settings,
            period_start,
            detail=detail,
            rate_resolver=rate_resolver,
        )
        return {"rows": payroll_rows, "totals": totals, "period_start": period_start,
                "period_end": period_end, "pay_date": pay_date, "subsidy_settings": subsidy_settings}

    def _payroll_rows_for_period(period_start, worker_id=""):
        period_end, pay_date = api["payroll_period_dates"](period_start)
        return api["payroll_rows_for_range"](period_start, period_end, pay_date, worker_id)

    def _current_payroll_period_start(today=None):
        today = today or date.today()
        try:
            cycle_start = date.fromisoformat(api["get_setting"]("payroll_cycle_start", "2026-07-06"))
        except ValueError:
            cycle_start = date(2026, 7, 6)
        if today < cycle_start:
            return cycle_start
        elapsed_periods = (today - cycle_start).days // 14
        return cycle_start + timedelta(days=elapsed_periods * 14)

    def _effective_payroll_worker_id():
        return "" if api["normalized_role"]() in {"admin", "manager", "finance"} else str(api["g"].user["id"])

    def _payroll_cycle_start_date():
        try:
            return date.fromisoformat(api["get_setting"]("payroll_cycle_start", "2026-07-06"))
        except ValueError:
            return date(2026, 7, 6)

    def _payroll_historical_paid_date():
        try:
            return date.fromisoformat(api["get_setting"]("payroll_historical_paid_date", "2026-07-05"))
        except ValueError:
            return date(2026, 7, 5)

    def _historical_payroll_period(worker_id=""):
        cycle_start = api["payroll_cycle_start_date"]()
        period_end = min(cycle_start - timedelta(days=1), api["payroll_historical_paid_date"]())
        clauses = ["date(service_reports.report_date) <= ?"]
        params = [period_end.isoformat()]
        if worker_id and str(worker_id).isdigit():
            clauses.append("users.id = ?")
            params.append(int(worker_id))
        row = api["db"]().execute(
            f"""
        select min(date(service_reports.report_date)) as period_start
        from service_reports
        join service_report_workers on service_report_workers.report_id = service_reports.id
        join users on users.id = service_report_workers.user_id
        where {" and ".join(clauses)}
        """,
            params,
        ).fetchone()
        if not row or not row["period_start"]:
            return None
        period_start = date.fromisoformat(row["period_start"])
        if period_start > period_end:
            return None
        return {
            "batch_type": "historical",
            "label": "历史工资补发",
            "period_start": period_start,
            "period_end": period_end,
            "pay_date": api["payroll_historical_paid_date"](),
            "status": "paid",
        }

    def _payroll_batch_payload(period_start, batch_type="regular"):
        if batch_type == "historical":
            period = api["historical_payroll_period"](api["effective_payroll_worker_id"]())
            if not period or period["period_start"] != period_start:
                api["abort"](404)
            batch = api["payroll_rows_for_range"](
                period["period_start"],
                period["period_end"],
                period["pay_date"],
                api["effective_payroll_worker_id"](),
                date_mode="report",
            )
            label = period["label"]
            status = "paid"
        else:
            batch = api["payroll_rows_for_period"](period_start, api["effective_payroll_worker_id"]())
            label = "工资发放"
            status = "paid" if batch["pay_date"] <= date.today() else "scheduled"
        return {
            "batch_type": batch_type,
            "label": label,
            "period_start": batch["period_start"].isoformat(),
            "period_end": batch["period_end"].isoformat(),
            "pay_date": batch["pay_date"].isoformat(),
            "status": status,
            "rows": [payroll_row_export(row) for row in batch["rows"]],
            "payslips": [payroll_payslip_payload(row) for row in batch["rows"]],
            "totals": {key: round(value, 2) for key, value in batch["totals"].items()},
        }

    # ------------------------------------------------------------------
    # 税务分类（Phase 2A）：组件明细 + 分类判定，供生成付款单时冻结快照。
    # ------------------------------------------------------------------
    def _component_tax_config_lookup():
        """payroll_component_tax_config 按 code + 日期取生效版本（缺失返回 None）。"""
        def lookup(component_code, day_iso):
            return api["db"]().execute(
                "select * from payroll_component_tax_config where component_code=? and is_active=1"
                " and effective_from<=? and (effective_to is null or effective_to>=?)"
                " order by effective_from desc limit 1",
                (component_code, day_iso, day_iso),
            ).fetchone()
        return lookup

    def _worker_tax_status_lookup():
        """worker_tax_status_history 按 service_date 查当时身份；查不到返回 None，禁止猜。"""
        def lookup(employee_id, day_iso):
            row = api["db"]().execute(
                "select tax_status from worker_tax_status_history where employee_id=?"
                " and effective_from<=? and (effective_to is null or effective_to>=?)"
                " order by effective_from desc limit 1",
                (employee_id, day_iso, day_iso),
            ).fetchone()
            return row["tax_status"] if row else None
        return lookup

    def _mileage_evidence_lookup():
        """self_drive 佐证判定：该员工这些日报是否全部有 status='success' 的里程佐证。"""
        def lookup(worker_id, report_ids):
            ids = sorted({int(r) for r in report_ids})
            if not ids:
                return False
            marks = ",".join("?" * len(ids))
            row = api["db"]().execute(
                "select count(distinct report_id) as hits from service_report_mileage_evidence"
                f" where worker_user_id=? and status='success' and report_id in ({marks})",
                [worker_id, *ids],
            ).fetchone()
            return row["hits"] == len(ids)
        return lookup

    def _payroll_payment_components(period_start, worker_id, gross_amount):
        """生成工资付款单用的组件快照（已分类 + 硬校验），返回 (rows, totals)。"""
        period_end, _pay_date = api["payroll_period_dates"](period_start)
        entries, writer_rows, subsidy_settings, rate_resolver = _range_inputs(
            period_start, period_end, worker_id, detail=True)
        raw_rows = payroll_component_rows(entries, writer_rows, subsidy_settings, period_start, rate_resolver)
        return build_payment_components(
            raw_rows,
            employee_id=int(worker_id),
            gross_amount=gross_amount,
            payment_order_id=None,
            config_lookup=_component_tax_config_lookup(),
            tax_status_lookup=_worker_tax_status_lookup(),
            mileage_evidence_lookup=_mileage_evidence_lookup(),
            period_end=period_end,
        )

    def _offline_salary_payment_components(employee_id, gross_amount, period_end):
        """线下约定工资无法按组件拆分：单行 offline_salary，交由分类落 review。"""
        raw_rows = [{
            "component_code": "offline_salary", "amount": float(gross_amount),
            "quantity": 1.0, "unit": "period", "unit_rate": float(gross_amount),
            "service_date": None, "work_order_id": None, "order_number": "",
            "report_ids": set(), "daily_report_id": None,
        }]
        return build_payment_components(
            raw_rows,
            employee_id=int(employee_id),
            gross_amount=gross_amount,
            payment_order_id=None,
            config_lookup=_component_tax_config_lookup(),
            tax_status_lookup=_worker_tax_status_lookup(),
            mileage_evidence_lookup=_mileage_evidence_lookup(),
            period_end=period_end,
        )

    def _payroll_periods_for_month(year, month):
        month_start = date(year, month, 1)
        month_end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
        cycle_start = api["payroll_cycle_start_date"]()
        start = cycle_start
        if month_start > cycle_start + timedelta(days=27):
            periods_before = max(((month_start - cycle_start).days - 27) // 14 - 1, 0)
            start = cycle_start + timedelta(days=periods_before * 14)
        periods = []
        while start <= month_end:
            period_end, pay_date = api["payroll_period_dates"](start)
            if month_start <= pay_date <= month_end:
                periods.append(
                    {
                        "batch_type": "regular",
                        "label": "工资发放",
                        "period_start": start,
                        "period_end": period_end,
                        "pay_date": pay_date,
                        "status": "paid" if pay_date <= date.today() else "scheduled",
                    }
                )
            start += timedelta(days=14)
        historical_period = api["historical_payroll_period"](api["effective_payroll_worker_id"]())
        if historical_period and month_start <= historical_period["pay_date"] <= month_end:
            periods.append(historical_period)
        periods.sort(key=lambda period: (period["pay_date"], period["period_start"], period["batch_type"]))
        return periods

    return {
        "split_report_labor_hours": _split_report_labor_hours,
        "labor_report_entries": _labor_report_entries,
        "payroll_rows_for_range": _payroll_rows_for_range,
        "payroll_rows_for_period": _payroll_rows_for_period,
        "payroll_row_export": payroll_row_export,
        "payroll_payslip_payload": payroll_payslip_payload,
        "payroll_period_dates": payroll_period_dates,
        "payroll_calendar_weeks": payroll_calendar_weeks,
        "current_payroll_period_start": _current_payroll_period_start,
        "effective_payroll_worker_id": _effective_payroll_worker_id,
        "payroll_cycle_start_date": _payroll_cycle_start_date,
        "payroll_historical_paid_date": _payroll_historical_paid_date,
        "historical_payroll_period": _historical_payroll_period,
        "payroll_batch_payload": _payroll_batch_payload,
        "payroll_periods_for_month": _payroll_periods_for_month,
        "payroll_payment_components": _payroll_payment_components,
        "offline_salary_payment_components": _offline_salary_payment_components,
        "component_tax_config_lookup": _component_tax_config_lookup,
        "worker_tax_status_lookup": _worker_tax_status_lookup,
        "mileage_evidence_lookup": _mileage_evidence_lookup,
    }
