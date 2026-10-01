"""工时拆分与工资聚合的纯计算层。

这一层刻意不 import ``app`` / ``flask`` / ``db``，也就不会和根模块形成循环依赖。
凡是对外部世界的依赖都由 :mod:`services` 注入：

    duration_resolver(report, worker_count) -> float            工时时长
    holiday_resolver(day) -> bool                               是否周末 / 法定假日
    rate_resolver(worker_id, work_date, rate_type) -> float     按工作日取费率

金额口径说明（改动这里必须同步看 scripts/ 下的一次性回填工具）：
组件金额由「逐条工时 × 当日费率」累加得到，本层不做 round，除金额 / 工时
之类的展示口径 helper（``effective_display_rate``）外一律保留原始精度。
"""
from __future__ import annotations

from datetime import date, timedelta


def effective_display_rate(amount, quantity):
    """展示用实际费率：金额 ÷ 数量。数量为 0 时显示 0，而不是等级静态列。"""
    quantity = float(quantity or 0)
    return float(amount or 0) / quantity if quantity else 0.0


def split_report_labor_hours(
    report,
    worker_count=1,
    duration_resolver=None,
    holiday_resolver=None,
):
    """把一张日报的工时拆成 标准 / 加班 / 假日 + 交通工时。

    结算单 seeding（customer_reimbursement_seed_rows）与工资汇总都用它，
    所以它住在计算层，两个 resolver 由调用方注入。
    """
    if duration_resolver is None or holiday_resolver is None:
        raise TypeError("duration_resolver and holiday_resolver are required")
    work_hours = duration_resolver(report, worker_count)
    if "worker_public_transport_hours" in report.keys():
        worker_travel_hours = (
            report["worker_travel_hours"]
            if "worker_travel_hours" in report.keys() else None
        )
        if (
            "worker_travel_mode" in report.keys()
            and (report["worker_travel_mode"] or "legacy") in {"self_drive", "legacy"}
        ):
            worker_travel_hours = 0
        worker_public_hours = report["worker_public_transport_hours"]
        if worker_travel_hours is not None or worker_public_hours is not None:
            travel_hours = float(worker_travel_hours or 0)
            public_transport_hours = float(worker_public_hours or 0)
        else:
            travel_hours = float(report["travel_hours"] or 0) / max(worker_count, 1)
            public_transport_hours = float(report["public_transport_hours"] or 0) / max(worker_count, 1)
    else:
        travel_hours = float(report["travel_hours"] or 0) / max(worker_count, 1)
        public_transport_hours = float(report["public_transport_hours"] or 0) / max(worker_count, 1)
    transport_hours = travel_hours + public_transport_hours
    try:
        work_day = date.fromisoformat(report["actual_work_date"] or report["report_date"])
    except (TypeError, ValueError):
        work_day = None
    result = {
        "transport_hours": transport_hours,  # backwards-compatible payroll total
        "travel_hours": travel_hours,
        "public_transport_hours": public_transport_hours,
    }
    if work_day and holiday_resolver(work_day):
        result.update({"standard_hours": 0, "overtime_hours": 0, "holiday_hours": work_hours})
    else:
        result.update({
            "standard_hours": min(work_hours, 8),
            "overtime_hours": max(work_hours - 8, 0),
            "holiday_hours": 0,
        })
    return result


def _accumulate_buckets(entries, writer_rows, period_start, rate_resolver, detail):
    """aggregate_payroll_rows 与 payroll_component_rows 共用的累加主体。

    聚合键：detail=True 时按 (worker_id, attendance_date, service_order_id)，
    否则只按 worker_id。返回 bucket 字典（插入序 = 首条出现序）。
    """
    payroll = {}

    def ensure_worker(row):
        key = (row["worker_id"], row["attendance_date"], row["service_order_id"]) if detail else row["worker_id"]
        return payroll.setdefault(key, {
            **({"attendance_date": row["attendance_date"], "service_order_id": row["service_order_id"],
                "order_number": row["order_number"], "client_name": row["client_name"], "report_ids": set(),
                "attendance_bucket": False} if detail else {}),
            "worker_id": row["worker_id"], "worker_name": row["worker_name"],
            "grade_name": row["grade_name"] or "未指定",
            "base_salary": float(row["base_salary"] or 0),
            "meal_daily_amount": float(row["meal_daily_amount"] or 0),
            "car_allowance_method": "mileage",
            "car_mileage_rate": float(row["car_mileage_rate"] or 0),
            "rental_driving_hourly_rate": float(
                row["rental_driving_hourly_rate"]
                if row["rental_driving_hourly_rate"] is not None else 15
            ),
            "standard_rate": float(row["standard_hourly_rate"] or 0),
            "transport_rate": float(row["transport_hourly_rate"] or 0),
            "overtime_rate": float(row["overtime_hourly_rate"] or 0),
            "holiday_rate": float(row["holiday_hourly_rate"] or 0),
            # 工资按「工作日当天的等级费率版本」逐条累加（无版本时回退等级静态列）；
            # 上面的 *_rate 只作展示/兜底，不再参与金额计算。
            "pay_standard": 0.0, "pay_overtime": 0.0, "pay_holiday": 0.0,
            "pay_self_drive": 0.0, "pay_following": 0.0, "pay_rental": 0.0,
            "standard_hours": 0, "transport_hours": 0, "overtime_hours": 0,
            "holiday_hours": 0, "attendance_dates": set(), "driving_miles": 0,
            "self_drive_travel_hours": 0, "following_travel_hours": 0,
            "rental_driving_hours": 0, "rental_driving_miles": 0,
            "report_writing_count": 0,
        })

    rate_cache = {}

    def dated_rate(worker_id, work_date, rate_type):
        """按员工+工作日取费率（等级费率版本优先，缺失回退等级静态列），周期内缓存。"""
        key = (worker_id, work_date, rate_type)
        if key not in rate_cache:
            rate_cache[key] = rate_resolver(worker_id, work_date, rate_type)
        return rate_cache[key]

    for row in entries:
        worker = ensure_worker(row)
        if detail:
            worker["report_ids"].add(row["report_id"])
            worker["attendance_bucket"] = True
        work_date = row["attendance_date"] or period_start.isoformat()
        worker_id = row["worker_id"]
        for key in ("standard_hours", "transport_hours", "overtime_hours", "holiday_hours"):
            worker[key] += row[key]
        worker["pay_standard"] += row["standard_hours"] * dated_rate(worker_id, work_date, "regular_hours")
        worker["pay_overtime"] += row["overtime_hours"] * dated_rate(worker_id, work_date, "overtime_hours")
        worker["pay_holiday"] += row["holiday_hours"] * dated_rate(worker_id, work_date, "holiday_hours")
        if row["attendance_date"]:
            worker["attendance_dates"].add(row["attendance_date"])
        travel_mode = row["worker_travel_mode"] or "legacy"
        if travel_mode in {"self_drive", "legacy"}:
            miles = float(row["worker_driving_miles"] or 0)
            worker["driving_miles"] += miles
            worker["self_drive_travel_hours"] += float(row["worker_travel_hours"] or 0)
            worker["pay_self_drive"] += miles * dated_rate(worker_id, work_date, "mileage")
        if travel_mode == "following":
            hours_value = float(row["worker_travel_hours"] or 0)
            worker["following_travel_hours"] += hours_value
            worker["pay_following"] += hours_value * dated_rate(worker_id, work_date, "travel_hours")
        if travel_mode == "rental_drive":
            hours_value = float(row["worker_travel_hours"] or 0)
            worker["rental_driving_hours"] += hours_value
            worker["rental_driving_miles"] += float(row["worker_driving_miles"] or 0)
            worker["pay_rental"] += hours_value * dated_rate(worker_id, work_date, "rental_drive_hours")

    for row in writer_rows:
        if detail:
            worker = ensure_worker(row)
            worker["report_writing_count"] += int(row["report_writing_count"] or 0)
            worker["report_ids"].add(row["report_id"])
        else:
            ensure_worker(row)["report_writing_count"] = int(row["report_writing_count"] or 0)

    return payroll


def aggregate_payroll_rows(
    entries,
    writer_rows,
    subsidy_settings,
    period_start,
    detail=False,
    rate_resolver=None,
):
    """把逐条工时行聚合成工资行。

    这是 ``payroll_rows_for_range`` 的计算主体，抽到本层后既便于独立测试，
    也让后续「按 service_date 输出组件明细」的改造不必再碰 SQL。
    返回 ``(payroll_rows, totals)``。
    """
    if rate_resolver is None:
        raise TypeError("rate_resolver is required")
    payroll = _accumulate_buckets(entries, writer_rows, period_start, rate_resolver, detail)
    payroll_rows = []
    for worker in payroll.values():
        attendance_days = len(worker["attendance_dates"])
        # 金额来自逐条工时 × 当日费率版本（见上面的 pay_* 累加）
        standard_pay = worker["pay_standard"]
        overtime_pay = worker["pay_overtime"]
        holiday_pay = worker["pay_holiday"]
        self_drive_allowance = worker["pay_self_drive"]
        following_allowance = worker["pay_following"]
        rental_driving_allowance = worker["pay_rental"]
        worker["transport_hours"] = worker["following_travel_hours"] + worker["rental_driving_hours"]

        # 工资条的“单价”必须与本周期实际参与计算的等级费率一致，不能继续展示
        # employee_grades 静态列。一个周期可能跨越多个费率版本，因此汇总工资条
        # 显示金额 / 工时（或里程）的加权实际费率；无对应数量时显示 0，避免展示
        # 一个并未参与本期工资计算的旧静态费率。
        worker["standard_rate"] = effective_display_rate(standard_pay, worker["standard_hours"])
        worker["overtime_rate"] = effective_display_rate(overtime_pay, worker["overtime_hours"])
        worker["holiday_rate"] = effective_display_rate(holiday_pay, worker["holiday_hours"])
        worker["car_mileage_rate"] = effective_display_rate(
            self_drive_allowance, worker["driving_miles"]
        )
        worker["transport_rate"] = effective_display_rate(
            following_allowance, worker["following_travel_hours"]
        )
        worker["rental_driving_hourly_rate"] = effective_display_rate(
            rental_driving_allowance, worker["rental_driving_hours"]
        )

        transport_pay = following_allowance + rental_driving_allowance
        car_allowance = self_drive_allowance
        meal_allowance = attendance_days * worker["meal_daily_amount"]
        report_writing_fee = worker["report_writing_count"] * subsidy_settings["report_writing_fee"]
        subsidy_total = car_allowance + meal_allowance + report_writing_fee
        total_pay = worker["base_salary"] + standard_pay + transport_pay + overtime_pay + holiday_pay + subsidy_total
        payroll_rows.append({**worker, "attendance_days": attendance_days, "standard_pay": standard_pay,
            "transport_pay": transport_pay, "overtime_pay": overtime_pay, "holiday_pay": holiday_pay,
            "self_drive_allowance": self_drive_allowance, "following_allowance": following_allowance,
            "rental_driving_allowance": rental_driving_allowance, "car_allowance": car_allowance,
            "meal_allowance": meal_allowance,
            "report_writing_fee": report_writing_fee, "subsidy_total": subsidy_total, "total_pay": total_pay})
    payroll_rows.sort(key=lambda row: row["worker_name"])
    if detail:
        payroll_rows.sort(key=lambda row: (row["worker_name"], row["attendance_date"], row["order_number"]))
        meal_days, base_rows = set(), {}
        for row in payroll_rows:
            base_rows.setdefault(row["worker_id"], row.copy())
            row["total_pay"] -= row["base_salary"]
            row["base_salary"] = 0
            key = (row["worker_id"], row["attendance_date"])
            if row["attendance_days"]:
                if key in meal_days:
                    for field in ("subsidy_total", "total_pay"):
                        row[field] -= row["meal_allowance"]
                    row["meal_allowance"] = 0
                meal_days.add(key)
        for original in base_rows.values():
            if not original["base_salary"]:
                continue
            base = {key: 0 if isinstance(value, (int, float)) else value for key, value in original.items()}
            base.update(worker_id=original["worker_id"], worker_name=original["worker_name"],
                base_salary=original["base_salary"], total_pay=original["base_salary"],
                attendance_date="", service_order_id=None, order_number="", client_name="", report_ids=set())
            payroll_rows.append(base)
    totals = {key: sum(row[key] for row in payroll_rows) for key in (
        "base_salary", "standard_pay", "transport_pay", "overtime_pay", "holiday_pay",
        "self_drive_allowance", "following_allowance", "rental_driving_allowance", "car_allowance",
        "meal_allowance", "report_writing_fee", "subsidy_total", "total_pay")}
    return payroll_rows, totals


# 税务组件明细：code -> (bucket 原始金额键, 数量字段, 单位)。
# 金额全部来自 _accumulate_buckets 的同一套 pay_* 累加器（与工资行同源，
# 禁止另算）；standard_pay 等展示名是 finalize 阶段才生成的，这里不能用。
COMPONENT_QTY_SPECS = (
    ("standard_pay", "pay_standard", "standard_hours", "hours"),
    ("overtime_pay", "pay_overtime", "overtime_hours", "hours"),
    ("holiday_pay", "pay_holiday", "holiday_hours", "hours"),
    ("following_allowance", "pay_following", "following_travel_hours", "hours"),
    ("rental_driving_allowance", "pay_rental", "rental_driving_hours", "hours"),
    ("self_drive_allowance", "pay_self_drive", "driving_miles", "miles"),
)


def payroll_component_rows(entries, writer_rows, subsidy_settings, period_start, rate_resolver):
    """输出按 (员工, service_date, 工单, 组件) 拆分的工资组件明细（原始精度）。

    供生成付款单时冻结 ``employee_payment_components`` 使用：
    与 ``aggregate_payroll_rows(detail=True)`` 共用同一累加器，因此
    ``sum(组件金额)`` 与工资行的 ``total_pay`` 同源同值（金额差异只可能
    来自落库时的分位量化，由调用方做 ROUND_HALF_UP 硬校验）。

    返回 dict 列表，字段：
        worker_id, component_code, amount, quantity, unit, unit_rate,
        service_date, work_order_id, order_number, report_ids,
        meal_daily_amount, base_salary
    金额为 0 的组件不输出；base_salary / meal_allowance 是周期性组件，
    分别落到 worker 首个 bucket 与每个出勤日的首个 bucket。
    """
    if rate_resolver is None:
        raise TypeError("rate_resolver is required")
    payroll = _accumulate_buckets(entries, writer_rows, period_start, rate_resolver, detail=True)
    writing_fee = float(subsidy_settings["report_writing_fee"] or 0)
    rows = []
    worker_meal = {}
    worker_base = {}
    meal_emitted = set()
    for bucket in payroll.values():
        service_date = bucket["attendance_date"] or None
        report_ids = bucket["report_ids"]
        worker_id = bucket["worker_id"]
        if worker_id not in worker_meal:
            # 与周期级工资行一致：meal / base 取该 worker 首个 bucket 的等级值。
            worker_meal[worker_id] = float(bucket["meal_daily_amount"] or 0)
            worker_base[worker_id] = float(bucket["base_salary"] or 0)
        for code, amount_key, qty_key, unit in COMPONENT_QTY_SPECS:
            amount = float(bucket[amount_key] or 0)
            if amount == 0:
                continue
            quantity = float(bucket[qty_key] or 0)
            rows.append({
                "worker_id": worker_id, "component_code": code, "amount": amount,
                "quantity": quantity, "unit": unit,
                "unit_rate": effective_display_rate(amount, quantity),
                "service_date": service_date, "work_order_id": bucket["service_order_id"],
                "order_number": bucket["order_number"], "report_ids": set(report_ids),
                "daily_report_id": next(iter(report_ids)) if len(report_ids) == 1 else None,
            })
        count = int(bucket["report_writing_count"] or 0)
        if count and writing_fee:
            amount = count * writing_fee
            rows.append({
                "worker_id": worker_id, "component_code": "report_writing_fee",
                "amount": amount, "quantity": float(count), "unit": "reports",
                "unit_rate": writing_fee,
                "service_date": service_date, "work_order_id": bucket["service_order_id"],
                "order_number": bucket["order_number"], "report_ids": set(report_ids),
                "daily_report_id": next(iter(report_ids)) if len(report_ids) == 1 else None,
            })
        meal_key = (worker_id, bucket["attendance_date"])
        if (
            bucket["attendance_bucket"] and bucket["attendance_date"]
            and meal_key not in meal_emitted and worker_meal[worker_id]
        ):
            meal_emitted.add(meal_key)
            rows.append({
                "worker_id": worker_id, "component_code": "meal_allowance",
                "amount": worker_meal[worker_id], "quantity": 1.0, "unit": "day",
                "unit_rate": worker_meal[worker_id],
                "service_date": bucket["attendance_date"],
                "work_order_id": bucket["service_order_id"],
                "order_number": bucket["order_number"], "report_ids": set(report_ids),
                "daily_report_id": next(iter(report_ids)) if len(report_ids) == 1 else None,
            })
    for worker_id, base in worker_base.items():
        if base:
            rows.append({
                "worker_id": worker_id, "component_code": "base_salary",
                "amount": base, "quantity": 1.0, "unit": "period", "unit_rate": base,
                "service_date": None, "work_order_id": None, "order_number": "",
                "report_ids": set(), "daily_report_id": None,
            })
    return rows


def payroll_period_dates(period_start):
    period_end = period_start + timedelta(days=13)
    pay_date = period_end + timedelta(days=14)
    return period_end, pay_date


def payroll_row_export(row):
    payload = {
        "员工": row["worker_name"],
        "员工等级": row["grade_name"],
        "基本工资": round(row["base_salary"], 2),
        "出勤天数": row["attendance_days"],
        "里程": round(row["driving_miles"], 1),
        "租车里程": round(row["rental_driving_miles"], 1),
        "标准工时": round(row["standard_hours"], 2),
        "标准工资": round(row["standard_pay"], 2),
        "随行时长": round(row["following_travel_hours"], 2),
        "租车驾驶时长": round(row["rental_driving_hours"], 2),
        "自驾车补": round(row["self_drive_allowance"], 2),
        "随行补贴": round(row["following_allowance"], 2),
        "租车驾驶补贴": round(row["rental_driving_allowance"], 2),
        "加班工时": round(row["overtime_hours"], 2),
        "加班工资": round(row["overtime_pay"], 2),
        "假期工时": round(row["holiday_hours"], 2),
        "假期工资": round(row["holiday_pay"], 2),
        "补贴": round(row["subsidy_total"], 2),
    }
    payload["合计工资"] = round(row["total_pay"], 2)
    return payload


def payroll_payslip_payload(row):
    lines = [
        {"label": "基本工资", "amount": round(row["base_salary"], 2)},
        {"label": "标准工资", "hours": round(row["standard_hours"], 2), "rate": round(row["standard_rate"], 2), "amount": round(row["standard_pay"], 2)},
        {"label": "加班工资", "hours": round(row["overtime_hours"], 2), "rate": round(row["overtime_rate"], 2), "amount": round(row["overtime_pay"], 2)},
        {"label": "假期工资", "hours": round(row["holiday_hours"], 2), "rate": round(row["holiday_rate"], 2), "amount": round(row["holiday_pay"], 2)},
    ]
    if row["self_drive_allowance"] > 0:
        lines.append({
            "label": "自驾车补",
            "miles": round(row["driving_miles"], 1),
            "hours": round(row["self_drive_travel_hours"], 2),
            "rate": round(row["car_mileage_rate"], 2),
            "amount": round(row["self_drive_allowance"], 2),
        })
    if row["following_allowance"] > 0:
        lines.append({"label": "随行补贴", "hours": round(row["following_travel_hours"], 2),
            "rate": round(row["transport_rate"], 2), "amount": round(row["following_allowance"], 2)})
    if row["rental_driving_allowance"] > 0:
        lines.append({"label": "租车驾驶补贴", "miles": round(row["rental_driving_miles"], 1),
            "hours": round(row["rental_driving_hours"], 2), "rate": round(row["rental_driving_hourly_rate"], 2),
            "amount": round(row["rental_driving_allowance"], 2)})
    if row["meal_allowance"] > 0:
        lines.append({"label": "餐补", "days": row["attendance_days"], "rate": round(row["meal_daily_amount"], 2), "amount": round(row["meal_allowance"], 2)})
    if row["report_writing_fee"] > 0:
        lines.append({"label": "报告撰写费", "count": row["report_writing_count"], "rate": round(row["report_writing_fee"] / row["report_writing_count"], 2), "amount": round(row["report_writing_fee"], 2)})
    return {
        "employee": row["worker_name"],
        "grade": row["grade_name"],
        "attendance_days": row["attendance_days"],
        "driving_miles": round(row["driving_miles"], 1),
        "rental_driving_miles": round(row["rental_driving_miles"], 1),
        "lines": lines,
        "total_pay": round(row["total_pay"], 2),
    }


def payroll_calendar_weeks(year, month, periods):
    month_start = date(year, month, 1)
    month_end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    pay_days = {}
    for period in periods:
        pay_days.setdefault(period["pay_date"], []).append(period)
    current = month_start - timedelta(days=month_start.weekday())
    weeks = []
    while current <= month_end or current.weekday() != 0:
        week = []
        for _ in range(7):
            day_periods = pay_days.get(current, [])
            week.append(
                {
                    "date": current,
                    "in_month": current.month == month,
                    "periods": day_periods,
                    "status": "paid" if any(period["status"] == "paid" for period in day_periods) else ("scheduled" if day_periods else ""),
                    "is_today": current == date.today(),
                }
            )
            current += timedelta(days=1)
        weeks.append(week)
    return weeks
