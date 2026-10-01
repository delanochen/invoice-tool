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
from .tax import (
    TAX_CATEGORIES,
    build_payment_components,
    effective_tax_category,
    expense_purpose_ok,
    review_reason_key,
    review_reason_label,
)

# 报销侧组件的 provenance（新建 ER / 历史 ER 回填），复核原因按同一套口径推导。
from .tax import (
    EXPENSE_BACKFILL_SOURCE_TYPE,
    EXPENSE_CLASSIFICATION_CURRENT,
    EXPENSE_CLASSIFICATION_LEGACY_BACKFILL,
    EXPENSE_COMPONENT_SOURCE_TYPES,
)


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

    def _worker_tax_status_entries(employee_id):
        return api["db"]().execute(
            "select h.*, u.name created_by_name from worker_tax_status_history h"
            " left join users u on u.id=h.created_by where h.employee_id=?"
            " order by h.effective_from desc, h.id desc",
            (employee_id,),
        ).fetchall()

    def _create_worker_tax_status_entry(employee_id, tax_status, effective_from, effective_to,
                                        notes, created_by):
        """新增税务身份有效期（写入前做区间重叠校验）。"""
        from .tax import validate_tax_status_segment
        existing = _worker_tax_status_entries(employee_id)
        validate_tax_status_segment(existing, tax_status, effective_from, effective_to)
        api["db"]().execute(
            "insert into worker_tax_status_history (employee_id,tax_status,effective_from,"
            "effective_to,notes,created_by,created_at) values (?,?,?,?,?,?,?)",
            (employee_id, tax_status, effective_from, effective_to or None,
             notes or None, created_by, api["now"]()),
        )

    def _worker_tax_status_current(employee_id, today=None):
        from datetime import date as _date
        day = _iso_day_of(today) if today else _date.today().isoformat()
        return _worker_tax_status_lookup()(employee_id, day)

    # ------------------------------------------------------------------
    # Phase 4A：Tax Review 工作台。
    #
    # 只读原则：组件快照（employee_payment_components）永不 UPDATE/DELETE，
    # 人工复核只往 employee_payment_tax_reviews 追加一行（previous → new + reason）。
    # 「有效分类」= 最新一条 review 的 new_tax_category，无 review 回退原始快照。
    # ------------------------------------------------------------------

    def _latest_review_cte():
        """每个组件最新一条复核（PG DISTINCT ON；排序与 Python 侧一致）。"""
        return """
            latest_review as (
                select distinct on (component_id) component_id, id as review_id,
                       new_tax_category, previous_tax_category, reason,
                       reviewed_by, reviewed_at
                from employee_payment_tax_reviews
                order by component_id, reviewed_at desc, id desc
            )
        """

    def _tax_review_options():
        db = api["db"]()
        employees = db.execute(
            """select distinct u.id, u.name from users u
               join employee_payment_components c on c.employee_id = u.id
               where c.superseded_at is null order by u.name"""
        ).fetchall()
        codes = db.execute(
            """select distinct component_code from employee_payment_components
               where superseded_at is null order by component_code"""
        ).fetchall()
        years = db.execute(
            """select distinct left(coalesce(service_date, created_at), 4) as year
               from employee_payment_components
               where superseded_at is null and coalesce(service_date, created_at) is not null
               order by year desc"""
        ).fetchall()
        return {
            "employees": [dict(row) for row in employees],
            "component_codes": [row["component_code"] for row in codes],
            "years": [row["year"] for row in years if row["year"]],
        }

    def _expense_contexts(rows):
        """按 component.source_id（= expense_items.id）批量取报销侧判定上下文。

        凭证与业务用途都是**当前**状态（动态推导）：凭证后补 / 用途后填，
        原因会随之变化，这正是复核工作台要看到的。
        """
        ids = [int(row["source_id"]) for row in rows
               if row.get("source_type") in EXPENSE_COMPONENT_SOURCE_TYPES and row.get("source_id")]
        if not ids:
            return {}
        db = api["db"]()
        marks = ",".join("?" * len(ids))
        items = db.execute(
            f"""
            select i.id as item_id, i.line_key, i.expense_id, i.project,
                   i.amount as item_amount, i.description as item_description,
                   e.expense_number, e.expense_date, e.status as expense_status,
                   e.business_purpose, e.description, e.reviewed_by,
                   e.service_order_id as expense_order_id
            from expense_items i join expenses e on e.id = i.expense_id
            where i.id in ({marks})
            """,
            ids,
        ).fetchall()
        if not items:
            return {}
        expense_ids = sorted({int(row["expense_id"]) for row in items})
        marks = ",".join("?" * len(expense_ids))
        attachments = db.execute(
            f"""select expense_id, expense_item_key, count(*) as hits
                from expense_attachments where expense_id in ({marks})
                group by expense_id, expense_item_key""",
            expense_ids,
        ).fetchall()
        hits = {(int(row["expense_id"]), row["expense_item_key"] or ""): int(row["hits"] or 0)
                for row in attachments}
        contexts = {}
        for row in items:
            contexts[int(row["item_id"])] = {
                "expense": row,
                "receipt_ok": bool(hits.get((int(row["expense_id"]), row["line_key"] or ""))),
            }
        return contexts

    def _expense_context(component, contexts):
        """取出某个报销组件可用的上下文（含按 provenance 显式选择用途口径）。"""
        found = contexts.get(int(component["source_id"])) if component.get("source_id") else None
        if not found:
            return None
        mode = (EXPENSE_CLASSIFICATION_LEGACY_BACKFILL
                if component.get("source_type") == EXPENSE_BACKFILL_SOURCE_TYPE
                else EXPENSE_CLASSIFICATION_CURRENT)
        return {
            "receipt_ok": found["receipt_ok"],
            "purpose_ok": expense_purpose_ok(found["expense"], mode),
            "expense": found["expense"],
        }

    def _annotate(rows):
        """给每行补 effective 分类、复核原因与来源上下文（纯推导，不写库）。"""
        contexts = _expense_contexts(rows)
        for row in rows:
            row["reviewed"] = row.get("review_id") is not None
            context = _expense_context(row, contexts) if (
                row.get("source_type") in EXPENSE_COMPONENT_SOURCE_TYPES) else None
            row["expense"] = (context or {}).get("expense")
            reason = review_reason_key(
                row, tax_category=row.get("effective_tax_category"), expense_context=context)
            row["review_reason"] = reason
            row["review_reason_label"] = review_reason_label(reason)
        return rows

    def _tax_review_rows(filters=None):
        """复核工作台列表：只查生效快照（superseded_at is null）。

        SQL 负责结构化筛选；「原因」依赖报销上下文（凭证 / 业务用途）在 Python 侧
        推导后再过滤，避免把推导逻辑复制一份进 SQL 造成两套真相。
        """
        from .tax import money
        filters = dict(filters or {})
        clauses = ["c.superseded_at is null"]
        params = []
        year = (filters.get("year") or "").strip()
        if year:
            clauses.append("left(coalesce(c.service_date, o.created_at), 4) = ?")
            params.append(year)
        employee_id = (filters.get("employee_id") or "").strip()
        if employee_id:
            clauses.append("c.employee_id = ?")
            params.append(int(employee_id))
        payment_type = (filters.get("payment_type") or "").strip()
        if payment_type:
            clauses.append("o.payment_type = ?")
            params.append(payment_type)
        component_code = (filters.get("component_code") or "").strip()
        if component_code:
            clauses.append("c.component_code = ?")
            params.append(component_code)
        tax_status = (filters.get("tax_status") or "").strip()
        if tax_status == "NULL":
            clauses.append("c.tax_status_snapshot is null")
        elif tax_status:
            clauses.append("c.tax_status_snapshot = ?")
            params.append(tax_status)
        original_category = (filters.get("original_category") or "").strip()
        if original_category:
            clauses.append("c.tax_category = ?")
            params.append(original_category)
        date_from = (filters.get("date_from") or "").strip()
        if date_from:
            clauses.append("c.service_date >= ?")
            params.append(date_from)
        date_to = (filters.get("date_to") or "").strip()
        if date_to:
            clauses.append("c.service_date <= ?")
            params.append(date_to)
        reviewed = (filters.get("reviewed") or "").strip()
        if reviewed == "reviewed":
            clauses.append("lr.review_id is not null")
        elif reviewed == "unreviewed":
            clauses.append("lr.review_id is null")

        outer = ["1 = 1"]
        outer_params = []
        effective_category = (filters.get("effective_category") or "").strip()
        if effective_category:
            outer.append("effective_tax_category = ?")
            outer_params.append(effective_category)

        sql = f"""
            with {_latest_review_cte()}
            select * from (
                select c.id as component_id, c.payment_order_id, c.employee_id,
                       c.component_code, c.component_name, c.amount, c.quantity, c.unit,
                       c.unit_rate, c.service_date, c.work_order_id, c.source_type,
                       c.source_id, c.daily_report_id, c.tax_category,
                       c.tax_status_snapshot, c.substantiated, c.review_status,
                       c.created_at,
                       coalesce(lr.new_tax_category, c.tax_category) as effective_tax_category,
                       lr.review_id, lr.reviewed_at as last_reviewed_at,
                       lr.reviewed_by as last_reviewed_by, lr.reason as last_review_reason,
                       o.payment_number, o.payment_type, o.status as payment_status,
                       o.paid_at, u.name as employee_name,
                       so.order_number, so.client_name,
                       ru.name as last_reviewed_by_name
                from employee_payment_components c
                join employee_payment_orders o on o.id = c.payment_order_id
                join users u on u.id = c.employee_id
                left join service_orders so on so.id = c.work_order_id
                left join latest_review lr on lr.component_id = c.id
                left join users ru on ru.id = lr.reviewed_by
                where {" and ".join(clauses)}
            ) base
            where {" and ".join(outer)}
            order by coalesce(service_date, '') desc, component_id desc
            limit ?
        """
        rows = [dict(row) for row in api["db"]().execute(
            sql, params + outer_params + [int(filters.get("limit") or 2000)]
        ).fetchall()]
        _annotate(rows)
        reason_filter = (filters.get("reason") or "").strip()
        if reason_filter:
            rows = [row for row in rows if row["review_reason"] == reason_filter]
        totals = {
            "amount": money(sum((row["amount"] or 0) for row in rows) if rows else 0),
            "taxable_compensation": money(0),
            "accountable_reimbursement": money(0),
            "tax_review_required": money(0),
        }
        for row in rows:
            totals[row["effective_tax_category"]] = (
                totals[row["effective_tax_category"]] + money(row["amount"]))
        return {"rows": rows, "totals": totals, "filters": filters,
                "options": _tax_review_options()}

    def _tax_review_detail(component_id):
        """单条组件复核详情（含来源单据 / 里程佐证 / 复核历史）。"""
        from .tax import money
        db = api["db"]()
        component = db.execute(
            """
            select c.*,
                   coalesce((select r.new_tax_category from employee_payment_tax_reviews r
                             where r.component_id = c.id
                             order by r.reviewed_at desc, r.id desc limit 1),
                            c.tax_category) as effective_tax_category,
                   o.payment_number, o.payment_type, o.status as payment_status,
                   o.gross_amount, o.advance_offset, o.other_adjustment, o.net_amount,
                   o.paid_at, o.description as payment_description,
                   u.name as employee_name,
                   so.order_number, so.client_name, so.site_address
            from employee_payment_components c
            join employee_payment_orders o on o.id = c.payment_order_id
            join users u on u.id = c.employee_id
            left join service_orders so on so.id = c.work_order_id
            where c.id = ?
            """,
            (component_id,),
        ).fetchone()
        if not component:
            return None
        component = dict(component)
        expense = None
        evidence = []
        report = None
        if component["source_type"] in EXPENSE_COMPONENT_SOURCE_TYPES and component["source_id"]:
            expense = db.execute(
                """
                select e.*, i.line_key, i.project as item_project, i.description as item_description,
                       i.amount as item_amount
                from expense_items i join expenses e on e.id = i.expense_id
                where i.id = ?
                """,
                (component["source_id"],),
            ).fetchone()
            if expense:
                attachments = db.execute(
                    """select id, original_filename, uploaded_at from expense_attachments
                       where expense_id = ? and expense_item_key = ? order by id""",
                    (expense["id"], expense["line_key"]),
                ).fetchall()
                expense = dict(expense)
                expense["attachments"] = [dict(row) for row in attachments]
        if component["daily_report_id"]:
            report = db.execute(
                """
                select sr.id, sr.report_date, sr.arrival_time, sr.departure_time,
                       srw.driving_miles, srw.travel_mode, srw.travel_hours,
                       so.order_number, so.site_address
                from service_reports sr
                left join service_report_workers srw
                       on srw.report_id = sr.id and srw.user_id = ?
                left join service_orders so on so.id = sr.service_order_id
                where sr.id = ?
                """,
                (component["employee_id"], component["daily_report_id"]),
            ).fetchone()
            if report:
                report = dict(report)
            evidence = [dict(row) for row in db.execute(
                """
                select id, status, one_way_miles, reported_miles, distance_meters,
                       origin_address, destination_address, trip_type, generated_by, generated_at
                from service_report_mileage_evidence
                where report_id = ? and worker_user_id = ?
                order by id desc
                """,
                (component["daily_report_id"], component["employee_id"]),
            ).fetchall()]
        context = None
        if expense is not None:
            context = _expense_context(component, {int(component["source_id"]): {
                "expense": expense, "receipt_ok": bool(expense.get("attachments"))}})
        reason = review_reason_key(component, tax_category=component["effective_tax_category"],
                                   expense_context=context)
        return {
            "component": component,
            "effective_tax_category": component["effective_tax_category"],
            "review_reason": reason,
            "review_reason_label": review_reason_label(reason),
            "expense": expense,
            "report": report,
            "mileage_evidence": evidence,
            "history": _tax_review_history(component_id),
            "amount": money(component["amount"]),
        }

    def _tax_review_history(component_id):
        """复核历史：按时间正序（审计链：原始 → 第一次 → 第二次……）。"""
        return [dict(row) for row in api["db"]().execute(
            """
            select r.*, u.name as reviewer_name
            from employee_payment_tax_reviews r
            left join users u on u.id = r.reviewed_by
            where r.component_id = ?
            order by r.reviewed_at asc, r.id asc
            """,
            (component_id,),
        ).fetchall()]

    def _record_tax_review(component_id, new_category, reason, reviewer_id):
        """追加一条复核（只读组件，绝不 UPDATE employee_payment_components）。

        previous_tax_category 取**当前有效分类**（不是原始快照值），这样第二次
        复核时审计链是连续的：review → accountable → taxable。
        返回 previous 分类供调用方写审计日志。
        """
        if new_category not in TAX_CATEGORIES:
            raise ValueError("未知的税务分类：%r" % (new_category,))
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("复核原因必填：分类调整必须留下可追溯的说明。")
        db = api["db"]()
        component = db.execute(
            "select * from employee_payment_components where id = ?", (component_id,)
        ).fetchone()
        if not component:
            raise ValueError("找不到该税务组件。")
        if component["superseded_at"]:
            raise ValueError("该组件快照已被新快照取代，不能再复核（请复核当前生效快照）。")
        latest = db.execute(
            """select * from employee_payment_tax_reviews where component_id = ?
               order by reviewed_at desc, id desc limit 1""",
            (component_id,),
        ).fetchone()
        previous = effective_tax_category(component["tax_category"], latest)
        db.execute(
            """insert into employee_payment_tax_reviews
               (component_id, reviewed_by, reviewed_at, previous_tax_category,
                new_tax_category, reason)
               values (?,?,?,?,?,?)""",
            (component_id, reviewer_id, api["now"](), previous, new_category, reason),
        )
        return previous

    def _payment_tax_components(payment_id):
        """付款单的组件 + 原始/有效两套税务合计（页面展示用，改库一律不发生）。"""
        from .tax import money
        rows = [dict(row) for row in api["db"]().execute(
            f"""
            with {_latest_review_cte()}
            select c.*, coalesce(lr.new_tax_category, c.tax_category) as effective_tax_category,
                   lr.reviewed_at as last_reviewed_at, lr.reason as last_review_reason,
                   u.name as last_reviewed_by_name, so.order_number, so.client_name
            from employee_payment_components c
            left join latest_review lr on lr.component_id = c.id
            left join users u on u.id = lr.reviewed_by
            left join service_orders so on so.id = c.work_order_id
            where c.payment_order_id = ? and c.superseded_at is null
            order by c.id
            """,
            (payment_id,),
        ).fetchall()]
        _annotate(rows)
        original = {key: money(0) for key in TAX_CATEGORIES}
        effective = {key: money(0) for key in TAX_CATEGORIES}
        for row in rows:
            amount = money(row["amount"])
            original[row["tax_category"]] = original[row["tax_category"]] + amount
            effective[row["effective_tax_category"]] = (
                effective[row["effective_tax_category"]] + amount)
        return {"rows": rows, "original_totals": original, "effective_totals": effective}

    def _iso_day_of(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

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
        "worker_tax_status_entries": _worker_tax_status_entries,
        "create_worker_tax_status_entry": _create_worker_tax_status_entry,
        "worker_tax_status_current": _worker_tax_status_current,
        # Phase 4A：Tax Review 工作台（只读快照 + 只追加复核）
        "tax_review_rows": _tax_review_rows,
        "tax_review_detail": _tax_review_detail,
        "tax_review_history": _tax_review_history,
        "record_tax_review": _record_tax_review,
        "payment_tax_components": _payment_tax_components,
    }
