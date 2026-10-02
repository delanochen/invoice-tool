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

from collections import defaultdict
from datetime import date, timedelta

from rate_engine import employee_rate

from .tax import money

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
    DEFAULT_YEAR_BASIS,
    SUMMARY_BUCKET_CATEGORY,
    SUMMARY_BUCKET_REVIEW,
    TAX_CATEGORIES,
    YEAR_BASIS_PAYMENT,
    YEAR_BASIS_SERVICE,
    build_payment_components,
    compensation_bucket_label,
    effective_tax_category,
    expense_purpose_ok,
    normalize_summary_bucket,
    normalize_year_basis,
    potential_1099_reportable,
    review_reason_key,
    review_reason_label,
    tax_status_label,
    w2_candidate_wages,
)

# 报销侧组件的 provenance（新建 ER / 历史 ER 回填），复核原因按同一套口径推导。
from .tax import (
    EXPENSE_BACKFILL_SOURCE_TYPE,
    EXPENSE_CLASSIFICATION_CURRENT,
    EXPENSE_CLASSIFICATION_LEGACY_BACKFILL,
    EXPENSE_COMPONENT_SOURCE_TYPES,
    POTENTIAL_1099_RULE_VERSION,
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

    def _mileage_review_context(rows):
        """Phase 6B Queue A：为 self_drive 组件补充**实时**里程佐证状态。

        只读展示，绝不据此改分类——快照里的 substantiated 保持原样，
        佐证后来补齐也必须由授权人员人工复核（候选名单见 queue 摘要）。
        """
        todo = [row for row in rows
                if row.get("component_code") == "self_drive_allowance"
                and row.get("daily_report_id")]
        for row in rows:
            row.setdefault("mileage_evidence_status", None)
            row.setdefault("mileage_evidence_origin", "")
            row.setdefault("mileage_evidence_destination", "")
        if not todo:
            return
        ids = sorted({int(row["daily_report_id"]) for row in todo})
        marks = ",".join("?" * len(ids))
        evidence_rows = api["db"]().execute(
            f"""select report_id, status, origin_address, destination_address
                from service_report_mileage_evidence
                where report_id in ({marks}) order by id""",
            ids,
        ).fetchall()
        by_report = defaultdict(list)
        for evidence in evidence_rows:
            by_report[int(evidence["report_id"])].append(evidence)
        for row in todo:
            evidence = by_report.get(int(row["daily_report_id"])) or []
            statuses = {entry["status"] for entry in evidence}
            if not evidence:
                row["mileage_evidence_status"] = "no_evidence"
            elif "success" in statuses:
                row["mileage_evidence_status"] = "evidence_success"
            else:
                row["mileage_evidence_status"] = "evidence_failed"
            latest = evidence[-1] if evidence else None
            if latest is not None:
                row["mileage_evidence_origin"] = latest["origin_address"] or ""
                row["mileage_evidence_destination"] = latest["destination_address"] or ""

    def _tax_review_rows(filters=None):
        """复核工作台列表：只查生效快照（superseded_at is null）。

        SQL 负责结构化筛选；「原因」依赖报销上下文（凭证 / 业务用途）在 Python 侧
        推导后再过滤，避免把推导逻辑复制一份进 SQL 造成两套真相。
        """
        from .tax import money
        filters = dict(filters or {})
        # Decision 4：复核队列也必须走统一读层 —— 否则 159 条 duplicate 会以
        # 「待复核」的名义留在队列里，金额还会被重复计入 totals。
        clauses = ["c.superseded_at is null", api["excluded_component_clause"]("c")]
        params = api["excluded_component_params"]()
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
        _mileage_review_context(rows)
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
        """付款单的组件 + 原始/有效两套税务合计（页面展示用，改库一律不发生）。

        Decision 3/4：replacement SL 名下只有 correction-generated 组件，被保留的
        冻结组件仍挂在原 SL 上，所以这里必须走统一读层（按 effectivePaymentOrder
        归集，并排除 duplicate_superseded）而不是「自己订单的组件」。
        Allocation 表为空时结果逐分等于旧行为。
        """
        from .tax import money
        rows = [dict(row) for row in api["db"]().execute(
            f"""
            with {_latest_review_cte()},
            {api['correction_component_cte']()}
            select c.*, coalesce(lr.new_tax_category, c.tax_category) as effective_tax_category,
                   lr.reviewed_at as last_reviewed_at, lr.reason as last_review_reason,
                   u.name as last_reviewed_by_name, so.order_number, so.client_name
            from effective_components c
            left join latest_review lr on lr.component_id = c.id
            left join users u on u.id = lr.reviewed_by
            left join service_orders so on so.id = c.work_order_id
            where c.effective_payment_order_id = ?
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

    # ------------------------------------------------------------------
    # Phase 4B：Annual Tax Summary（年度税务汇总 + drill-down）。
    #
    # 纯 reporting / read model：不写员工往来账、不改付款单 / 组件 / 身份 / 报销 / 工资。
    # 分组维度固定为 employee + tax_year + tax_status_snapshot —— 同一人同年同时出现
    # 1099 与 W2 时必须拆成两个 section，绝不用员工「当前身份」覆盖历史快照。
    # 分类一律用 effective（最新 review 优先），绝不用付款单上的原始 tax totals。
    # ------------------------------------------------------------------

    def _payment_date_expr():
        """实际付款日（四级优先；解析不出就是 NULL = 已记录未付款）。

        1. 银行交易（按 batch 匹配，退而求其次按 order 匹配）→ transaction_date /
           posted_date；作废批次一律不算付款
        2. 批次已签发 → batch.issued_at（再退 reconciled_at）
        3. 未进批次但付款单已付 → order.paid_at
        4. 都没有 → NULL

        绝不把 NULL 当成已付款：页面必须显示 Recorded，不能显示 Paid。
        """
        return """
            case
              when b.id is not null and b.status <> 'void' and bt.id is not null
                   and coalesce(bt.transaction_date, '') <> ''
                   then left(bt.transaction_date, 10)
              when b.id is not null and b.status <> 'void' and bt.id is not null
                   and coalesce(bt.posted_date, '') <> ''
                   then left(bt.posted_date, 10)
              when b.id is not null and b.status <> 'void'
                   and coalesce(b.issued_at, '') <> ''
                   then left(b.issued_at, 10)
              when b.id is not null and b.status <> 'void'
                   and coalesce(b.reconciled_at, '') <> ''
                   then left(b.reconciled_at, 10)
              when coalesce(o.paid_at, '') <> '' then left(o.paid_at, 10)
              else null end
        """

    def _annual_base_cte():
        """年度汇总与 drill-down 共用的 base：生效快照 + 有效分类 + 付款日。

        Decision 4：base 一律建在统一读层 `effective_components` 上。
        - 被 `duplicate_superseded` 的冻结组件不再进任何年度合计；
        - `payment_order_id`（原始父单）保留用于溯源，但payment/批次/付款日等
          显示维度取 `effective_payment_order_id`，否则被纠偏的工资会永远显示成
          一张作废 draft 单（无付款日、状态 superseded）。
        """
        return f"""
            with {_latest_review_cte()},
            {api['correction_component_cte']()},
            base as (
                select c.id as component_id, c.payment_order_id,
                       c.effective_payment_order_id,
                       c.allocation_disposition, c.allocation_group_id,
                       c.employee_id, c.component_code, c.component_name, c.amount,
                       c.quantity, c.unit,
                       c.unit_rate, c.service_date, c.work_order_id, c.source_type,
                       c.source_id, c.daily_report_id, c.tax_category, c.tax_status_snapshot,
                       c.substantiated, c.review_status, c.created_at,
                       coalesce(lr.new_tax_category, c.tax_category) as effective_tax_category,
                       lr.review_id, lr.reviewed_at as last_reviewed_at,
                       lr.reason as last_review_reason, lr.reviewed_by as last_reviewed_by,
                       ru.name as last_reviewed_by_name,
                       o.payment_number, o.payment_type, o.status as payment_status,
                       o.paid_at, o.batch_id,
                       b.batch_number, b.status as batch_status,
                       b.issued_at as batch_issued_at, b.reconciled_at as batch_reconciled_at,
                       u.name as employee_name,
                       so.order_number, so.client_name,
                       {_payment_date_expr()} as payment_date
                from effective_components c
                join employee_payment_orders o on o.id = c.effective_payment_order_id
                join users u on u.id = c.employee_id
                left join employee_payment_batches b on b.id = o.batch_id
                left join lateral (
                    select bt.id, bt.transaction_date, bt.posted_date
                    from bank_transactions bt
                    where bt.matched_batch_id = b.id
                       or (b.id is null and bt.matched_payment_order_id = o.id)
                    order by bt.transaction_date, bt.id
                    limit 1
                ) bt on true
                left join service_orders so on so.id = c.work_order_id
                left join latest_review lr on lr.component_id = c.id
                left join users ru on ru.id = lr.reviewed_by
                where 1 = 1
            )
        """
        # superseded_at 过滤已由 effective_components 承担（连同工资侧的
        # duplicate_superseded），这里不能再加一层，否则等于两处各自解释分配层。

    def _annual_year_expr(basis):
        """年度口径表达式（唯一来源，杜绝把口径散落硬编码到各处 SQL）。"""
        if basis == YEAR_BASIS_PAYMENT:
            return "nullif(left(payment_date, 4), '')"
        return "nullif(left(coalesce(service_date, ''), 4), '')"

    def _annual_filters(filters=None, default_year=None):
        """规范化年度汇总筛选参数（口径必须显式，非法值抛错）。"""
        filters = dict(filters or {})
        include_review = filters.get("include_review")
        if include_review is None:
            include_review = True
        return {
            "year": (str(filters.get("year") or "").strip() or str(default_year or "").strip()),
            "year_basis": normalize_year_basis(filters.get("year_basis")),
            "employee_id": str(filters.get("employee_id") or "").strip(),
            "tax_status": str(filters.get("tax_status") or "").strip(),
            "category": normalize_summary_bucket(filters.get("category")),
            "payment_status": str(filters.get("payment_status") or "").strip(),
            "work_order_id": str(filters.get("work_order_id") or "").strip(),
            "include_review": str(include_review).lower() not in ("0", "false", "no", ""),
        }

    def _annual_where(normalized, *, with_year=True):
        clauses = ["1 = 1"]
        params = []
        year_expr = _annual_year_expr(normalized["year_basis"])
        if with_year:
            # payment-date 口径下解析不出付款日的组件不属于任何年度（单独报告）。
            if normalized["year_basis"] == YEAR_BASIS_PAYMENT:
                clauses.append(f"{year_expr} is not null")
            if normalized["year"]:
                clauses.append(f"{year_expr} = ?")
                params.append(normalized["year"])
        if normalized["employee_id"]:
            clauses.append("employee_id = ?")
            params.append(int(normalized["employee_id"]))
        if normalized["tax_status"] == "NULL":
            clauses.append("tax_status_snapshot is null")
        elif normalized["tax_status"]:
            clauses.append("tax_status_snapshot = ?")
            params.append(normalized["tax_status"])
        if normalized["category"]:
            clauses.append("effective_tax_category = ?")
            params.append(SUMMARY_BUCKET_CATEGORY[normalized["category"]])
        if normalized["payment_status"]:
            clauses.append("payment_status = ?")
            params.append(normalized["payment_status"])
        if normalized["work_order_id"]:
            clauses.append("work_order_id = ?")
            params.append(int(normalized["work_order_id"]))
        if not normalized["include_review"]:
            clauses.append("effective_tax_category <> ?")
            params.append(SUMMARY_BUCKET_CATEGORY[SUMMARY_BUCKET_REVIEW])
        return " and ".join(clauses), params

    def _annual_tax_summary(filters=None, default_year=None):
        """按 employee + 年度 + 身份快照分组的年度税务汇总（只读）。

        三个桶（compensation / reimbursement / review）与 Total Recorded Amount
        必须闭合；闭合性在返回值里给出 closure_ok，页面与测试都能直接断言。
        """
        from .tax import money
        normalized = _annual_filters(filters, default_year)
        db = api["db"]()
        where, params = _annual_where(normalized)
        year_expr = _annual_year_expr(normalized["year_basis"])
        sql = f"""
            {_annual_base_cte()}
            select employee_id, max(employee_name) as employee_name,
                   coalesce(tax_status_snapshot, '') as tax_status_key,
                   {year_expr} as tax_year,
                   coalesce(sum(case when effective_tax_category = 'taxable_compensation'
                                     then amount else 0 end), 0) as compensation_amount,
                   coalesce(sum(case when effective_tax_category = 'accountable_reimbursement'
                                     then amount else 0 end), 0) as reimbursement_amount,
                   coalesce(sum(case when effective_tax_category = 'tax_review_required'
                                     then amount else 0 end), 0) as review_amount,
                   coalesce(sum(case when effective_tax_category = 'tax_review_required'
                                     then 1 else 0 end), 0) as review_count,
                   coalesce(sum(amount), 0) as total_amount,
                   count(*) as component_count,
                   coalesce(sum(case when payment_date is not null then amount else 0 end), 0)
                       as paid_amount,
                   coalesce(sum(case when payment_date is not null then 1 else 0 end), 0)
                       as paid_count,
                   coalesce(sum(case when payment_type = 'salary' then 1 else 0 end), 0)
                       as salary_count,
                   coalesce(sum(case when payment_type = 'expense' then 1 else 0 end), 0)
                       as expense_count
            from base
            where {where}
            group by employee_id, coalesce(tax_status_snapshot, ''), {year_expr}
            order by employee_name, tax_status_key, tax_year
        """
        rows = []
        for row in db.execute(sql, params).fetchall():
            entry = dict(row)
            status = entry.pop("tax_status_key") or None
            compensation = money(entry["compensation_amount"])
            reimbursement = money(entry["reimbursement_amount"])
            review = money(entry["review_amount"])
            total = money(entry["total_amount"])
            paid = money(entry["paid_amount"])
            entry.update({
                "tax_status": status,
                "tax_status_label": tax_status_label(status),
                "compensation_label": compensation_bucket_label(status),
                "compensation_amount": compensation,
                "reimbursement_amount": reimbursement,
                "review_amount": review,
                "review_count": int(entry["review_count"]),
                "total_amount": total,
                "paid_amount": paid,
                "unpaid_amount": total - paid,
                "paid_count": int(entry["paid_count"]),
                "component_count": int(entry["component_count"]),
                "salary_count": int(entry["salary_count"]),
                "expense_count": int(entry["expense_count"]),
                # 候选值，不是最终 filing amount；规则可配置，见 tax.py。
                "potential_1099_amount": potential_1099_reportable(
                    compensation=compensation, reimbursement=reimbursement,
                    review_required=review),
                "w2_candidate_wages": w2_candidate_wages(
                    compensation=compensation, review_required=review),
            })
            rows.append(entry)

        totals = {key: money(0) for key in (
            "compensation_amount", "reimbursement_amount", "review_amount",
            "total_amount", "paid_amount")}
        totals["component_count"] = 0
        totals["review_count"] = 0
        for entry in rows:
            for key in ("compensation_amount", "reimbursement_amount", "review_amount",
                        "total_amount", "paid_amount"):
                totals[key] = totals[key] + entry[key]
            totals["component_count"] += entry["component_count"]
            totals["review_count"] += entry["review_count"]
        totals["unpaid_amount"] = totals["total_amount"] - totals["paid_amount"]
        totals["potential_1099_amount"] = potential_1099_reportable(
            compensation=totals["compensation_amount"],
            reimbursement=totals["reimbursement_amount"],
            review_required=totals["review_amount"])
        # 闭合：三桶相加必须等于记录总额（否则页面要显式报警，不能悄悄上线）。
        bucket_sum = (totals["compensation_amount"] + totals["reimbursement_amount"]
                      + totals["review_amount"])
        totals["closure_ok"] = bucket_sum == totals["total_amount"]

        excluded_where, excluded_params = _annual_where(normalized, with_year=False)
        excluded = db.execute(
            f"""
            {_annual_base_cte()}
            select count(*) as n, coalesce(sum(amount), 0) as amt
            from base where {excluded_where} and {year_expr} is null
            """,
            excluded_params,
        ).fetchone()
        return {
            "rows": rows,
            "totals": totals,
            "filters": normalized,
            "options": _annual_tax_summary_options(),
            "excluded": {
                "count": int(excluded["n"] or 0),
                "amount": money(excluded["amt"] or 0),
                "reason": ("付款日无法解析（未付款 / 无银行流水）"
                           if normalized["year_basis"] == YEAR_BASIS_PAYMENT
                           else "缺少服务日期"),
            },
        }

    def _annual_tax_summary_options():
        db = api["db"]()
        employees = [dict(row) for row in db.execute(
            """select distinct u.id, u.name from users u
               join employee_payment_components c on c.employee_id = u.id
               where c.superseded_at is null order by u.name"""
        ).fetchall()]
        service_years = [row["year"] for row in db.execute(
            """select distinct left(service_date, 4) as year
               from employee_payment_components
               where superseded_at is null and coalesce(service_date, '') <> ''
               order by year desc"""
        ).fetchall() if row["year"]]
        payment_years = [row["year"] for row in db.execute(
            f"""
            {_annual_base_cte()}
            select distinct left(payment_date, 4) as year from base
            where payment_date is not null order by year desc
            """
        ).fetchall() if row["year"]]
        statuses = [row["status"] for row in db.execute(
            """select distinct tax_status_snapshot as status
               from employee_payment_components where superseded_at is null
               order by 1"""
        ).fetchall() if row["status"]]
        payment_statuses = [row["status"] for row in db.execute(
            """select distinct o.status from employee_payment_orders o
               join employee_payment_components c on c.payment_order_id = o.id
               where c.superseded_at is null order by 1"""
        ).fetchall() if row["status"]]
        work_orders = [dict(row) for row in db.execute(
            """select distinct so.id, so.order_number, so.client_name
               from employee_payment_components c
               join service_orders so on so.id = c.work_order_id
               where c.superseded_at is null
               order by so.order_number limit 500"""
        ).fetchall()]
        return {
            "employees": employees,
            "service_years": service_years,
            "payment_years": payment_years,
            "tax_statuses": statuses,
            "payment_statuses": payment_statuses,
            "work_orders": work_orders,
        }

    def _annual_tax_summary_components(filters=None, default_year=None, limit=5000):
        """年度汇总 drill-down：金额可一路追到 component（只读）。"""
        from .tax import money
        normalized = _annual_filters(filters, default_year)
        where, params = _annual_where(normalized)
        rows = [dict(row) for row in api["db"]().execute(
            f"""
            {_annual_base_cte()}
            select * from base where {where}
            order by employee_name, coalesce(service_date, '') desc,
                     coalesce(payment_date, '') desc, component_id desc
            limit ?
            """,
            params + [int(limit)],
        ).fetchall()]
        _annotate(rows)
        totals = {key: money(0) for key in (
            "amount", "taxable_compensation", "accountable_reimbursement",
            "tax_review_required", "paid_amount")}
        for row in rows:
            amount = money(row["amount"])
            totals["amount"] = totals["amount"] + amount
            totals[row["effective_tax_category"]] = (
                totals[row["effective_tax_category"]] + amount)
            if row.get("payment_date"):
                totals["paid_amount"] = totals["paid_amount"] + amount
        return {
            "rows": rows,
            "totals": totals,
            "filters": normalized,
            "options": _annual_tax_summary_options(),
        }

    def _tax_review_queue_summaries(default_year=None):
        """Phase 6B：三个快捷复核队列（A 里程佐证 / B 餐补政策 / C 报销凭证）。

        只读摘要；不判断、不改分类。Queue B 额外给出当前 config 快照与
        「CPA POLICY DECISION REQUIRED」所需口径；Queue A 额外给出
        「佐证已补齐但 effective 仍 review」的人工复核候选数。
        """
        from .tax import money
        db = api["db"]()
        year = str(default_year or "").strip()
        rows = db.execute(
            f"""
            with {_latest_review_cte()}
            select c.component_code, c.source_type, c.amount, c.employee_id,
                   c.service_date, c.daily_report_id,
                   coalesce(lr.new_tax_category, c.tax_category) as effective_tax_category
            from employee_payment_components c
            left join latest_review lr on lr.component_id = c.id
            where c.superseded_at is null
              and coalesce(lr.new_tax_category, c.tax_category) = 'tax_review_required'
            """
        ).fetchall()

        def summarize(match):
            picked = [row for row in rows if match(row)]
            dates = [row["service_date"] for row in picked if row["service_date"]]
            return {
                "count": len(picked),
                "amount": money(sum((row["amount"] or 0) for row in picked)),
                "employee_count": len({row["employee_id"] for row in picked}),
                "earliest_service_date": min(dates) if dates else "",
                "latest_service_date": max(dates) if dates else "",
            }

        queue_a = summarize(
            lambda row: row["component_code"] == "self_drive_allowance")
        queue_b = summarize(lambda row: row["component_code"] == "meal_allowance")
        queue_c = summarize(
            lambda row: row["source_type"] in EXPENSE_COMPONENT_SOURCE_TYPES)
        # Queue A 候选：佐证现在已 success、但 effective 仍是 review（绝不自动改）。
        evidence_lookup = _mileage_evidence_lookup()
        candidates = []
        for row in rows:
            if row["component_code"] != "self_drive_allowance" or not row["daily_report_id"]:
                continue
            if evidence_lookup(row["employee_id"], [row["daily_report_id"]]):
                candidates.append(row)
        queue_a["verified_candidates"] = {
            "count": len(candidates),
            "amount": money(sum((row["amount"] or 0) for row in candidates)),
        }
        meal_config = db.execute(
            """select display_name, default_tax_category, requires_substantiation,
                      effective_from, effective_to
               from payroll_component_tax_config
               where component_code='meal_allowance' and is_active=1
               order by effective_from desc limit 1"""
        ).fetchone()
        return {
            "year": year,
            "queue_a_mileage": queue_a,
            "queue_b_meal": queue_b,
            "queue_c_expense": queue_c,
            "meal_config": dict(meal_config) if meal_config else None,
        }

    def _closing_exceptions():
        """Phase 6B：Closing Exceptions（6A 盘点的产品化，只读，不自动修复）。"""
        from .tax import money
        db = api["db"]()
        out = []

        def add(severity, issue_type, label, count, amount, detail=""):
            out.append({"severity": severity, "issue_type": issue_type,
                        "label": label, "count": int(count),
                        "amount": money(amount or 0), "detail": str(detail or "")})

        row = db.execute(
            f"""
            with {_latest_review_cte()}
            select count(*) as n, coalesce(sum(c.amount), 0) as a
            from employee_payment_components c
            left join latest_review lr on lr.component_id = c.id
            where c.superseded_at is null
              and coalesce(lr.new_tax_category, c.tax_category) = 'tax_review_required'
            """
        ).fetchone()
        add("HIGH", "unresolved_tax_review", "未解决税务复核", row["n"], row["a"])

        cancelled = db.execute(
            """
            select o.payment_number, o.gross_amount
            from employee_payment_orders o
            where o.status = 'cancelled' and not exists (
                select 1 from employee_payment_components c
                where c.payment_order_id = o.id and c.superseded_at is null)
            order by o.id
            """
        ).fetchall()
        add("HIGH", "cancelled_source_missing",
            "Excluded cancelled payment with missing source",
            len(cancelled), sum((r["gross_amount"] or 0) for r in cancelled),
            "；".join(f"{r['payment_number']} ${r['gross_amount']}" for r in cancelled) or "—")

        row = db.execute(
            f"{_annual_base_cte()} select count(*) as n, coalesce(sum(amount), 0) as a"
            " from base where payment_date is null").fetchone()
        add("MEDIUM", "missing_payment_date", "缺少可解析付款日", row["n"], row["a"])

        row = db.execute(
            "select count(*) as n, coalesce(sum(net_amount), 0) as a"
            " from employee_payment_orders where batch_id is null and status <> 'void'"
        ).fetchone()
        add("MEDIUM", "order_without_batch", "付款单未入批次", row["n"], row["a"])

        bad_batches = db.execute(
            """
            select count(*) as n from (
                select b.id
                from employee_payment_batches b
                left join employee_payment_orders o on o.batch_id = b.id
                group by b.id, b.total_amount, b.payment_count
                having coalesce(sum(o.net_amount), 0) <> b.total_amount
                    or count(o.id) <> b.payment_count) q
            """
        ).fetchone()
        add("HIGH" if bad_batches["n"] else "INFO", "batch_integrity_mismatch",
            "批次闭合校验失败", bad_batches["n"], 0)

        bad_orders = db.execute(
            """
            select count(*) as n from (
                select o.id
                from employee_payment_orders o
                join employee_payment_components c on c.payment_order_id = o.id
                    and c.superseded_at is null
                group by o.id
                having coalesce(sum(c.amount), 0) <> o.gross_amount) q
            """
        ).fetchone()
        add("HIGH" if bad_orders["n"] else "INFO", "component_mismatch",
            "组件与付款单金额不一致", bad_orders["n"], 0)

        orphan = db.execute(
            """
            select count(*) as n, coalesce(sum(c.amount), 0) as a
            from employee_payment_components c
            left join expense_items i on i.id = c.source_id
            where c.superseded_at is null
              and c.source_type in ('expense_item', 'historical_expense_recompute')
              and i.id is null
            """
        ).fetchone()
        add("HIGH" if orphan["n"] else "INFO", "component_source_missing",
            "报销组件缺少来源快照", orphan["n"], orphan["a"])

        row = db.execute(
            "select count(*) as n, coalesce(sum(total_amount), 0) as a"
            " from employee_payment_batches where status = 'issued'").fetchone()
        add("INFO", "batch_not_reconciled", "批次已签发未对账", row["n"], row["a"])

        row = db.execute(
            "select count(*) as n from users u where (u.email is null or u.email = '')"
            " and exists (select 1 from employee_payment_orders o where o.employee_id = u.id)"
        ).fetchone()
        add("INFO", "employee_email_missing", "员工缺少 Email（仅信息项）", row["n"], 0)

        order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
        out.sort(key=lambda item: (order.get(item["severity"], 3), -item["amount"]))
        return out

    def _cpa_workpaper(filters=None, default_year=None):
        """Phase 6B：CPA Workpaper（内部工作底稿，绝不是 IRS 申报文件）。只读。

        主表复用 Annual Tax Summary（员工 + 年度 + 身份快照），
        Recorded / Paid 严格分开；Potential 1099 继续走
        POTENTIAL_1099_RULE_VERSION（service_compensation_v1），Preliminary。
        """
        data = _annual_tax_summary(filters, default_year)
        data["exceptions"] = _closing_exceptions()
        data["rule_version"] = POTENTIAL_1099_RULE_VERSION
        data["not_filing_note"] = "Internal CPA Workpaper — Not an IRS Filing"
        data["year_basis_note"] = (
            "Reporting year and filing treatment must be confirmed before final tax filing.")
        return data

    # ------------------------------------------------------------------
    # Phase 6C：CPA Policy Decision Preparation。
    # 只读决策包 + 纯内存 scenario 模拟；绝不写库、绝不执行 review、
    # 绝不生成 1099。Total Recorded 闭合失败即模拟失败。
    # ------------------------------------------------------------------
    _POLICY_TARGETS = ("taxable_compensation", "accountable_reimbursement",
                       "tax_review_required")

    def _effective_category_totals(component_ids=None):
        """按 effective category 的只读汇总（全库或指定组件集合）。"""
        from .tax import money
        db = api["db"]()
        rows = []
        if component_ids is None:
            rows = db.execute(f"""
                with {_latest_review_cte()}
                select coalesce(lr.new_tax_category, c.tax_category) as cat,
                       count(*) as n, coalesce(sum(c.amount), 0) as a
                from employee_payment_components c
                left join latest_review lr on lr.component_id = c.id
                where c.superseded_at is null
                group by 1
            """).fetchall()
        else:
            ids = sorted({int(i) for i in component_ids})
            if ids:
                marks = ",".join("?" * len(ids))
                rows = db.execute(f"""
                    with {_latest_review_cte()}
                    select coalesce(lr.new_tax_category, c.tax_category) as cat,
                           count(*) as n, coalesce(sum(c.amount), 0) as a
                    from employee_payment_components c
                    left join latest_review lr on lr.component_id = c.id
                    where c.superseded_at is null and c.id in ({marks})
                    group by 1
                """, ids).fetchall()
        amounts = {key: money(0) for key in _POLICY_TARGETS}
        counts = {key: 0 for key in _POLICY_TARGETS}
        for row in rows:
            if row["cat"] in amounts:
                amounts[row["cat"]] = amounts[row["cat"]] + money(row["a"] or 0)
                counts[row["cat"]] += int(row["n"] or 0)
        return {"amounts": amounts, "counts": counts}

    def _scenario_view(totals):
        """scenario 口径视图（service_compensation_v1：potential = taxable）。"""
        amounts = totals["amounts"]
        return {
            "service_compensation": amounts["taxable_compensation"],
            "reimbursements": amounts["accountable_reimbursement"],
            "review_required": amounts["tax_review_required"],
            "potential_1099_candidate": amounts["taxable_compensation"],
            "total_recorded": money(sum(amounts.values(), money(0))),
            "component_count": sum(totals["counts"].values()),
            "counts": dict(totals["counts"]),
        }

    def _simulate_tax_policy_decision(component_ids, target_category):
        """纯内存模拟：把 affected 组件的 effective 改为 target 后的 before/after。

        只 SELECT，不 INSERT/UPDATE 任何表（不写 review、不碰组件/付款单）。
        Total Recorded before != after 即模拟失败。
        """
        ids = sorted({int(i) for i in (component_ids or [])})
        if not ids:
            raise ValueError("no components selected for simulation")
        if target_category not in _POLICY_TARGETS:
            raise ValueError("invalid target tax category: %r" % (target_category,))
        db = api["db"]()
        marks = ",".join("?" * len(ids))
        affected = db.execute(f"""
            with {_latest_review_cte()}
            select c.id, c.amount, coalesce(lr.new_tax_category, c.tax_category) as effective
            from employee_payment_components c
            left join latest_review lr on lr.component_id = c.id
            where c.superseded_at is null and c.id in ({marks})
        """, ids).fetchall()
        if len(affected) != len(ids):
            raise ValueError(
                "simulation failed: some components not found or superseded")
        before_totals = _effective_category_totals()
        after_amounts = {key: money(value)
                         for key, value in before_totals["amounts"].items()}
        after_counts = dict(before_totals["counts"])
        for row in affected:
            amount = money(row["amount"] or 0)
            current = row["effective"]
            if current in after_amounts:
                after_amounts[current] = after_amounts[current] - amount
                after_counts[current] -= 1
            after_amounts[target_category] = after_amounts[target_category] + amount
            after_counts[target_category] += 1
        after_totals = {"amounts": after_amounts, "counts": after_counts}
        before = _scenario_view(before_totals)
        after = _scenario_view(after_totals)
        if before["total_recorded"] != after["total_recorded"]:
            raise ValueError(
                "simulation failed: total recorded does not close "
                "(%s != %s)" % (before["total_recorded"], after["total_recorded"]))
        return {
            "affected_count": len(ids),
            "affected_amount": money(sum((money(r["amount"] or 0) for r in affected),
                                         money(0))),
            "target_category": target_category,
            "before": before,
            "after": after,
        }

    def _queue_component_rows(component_code=None, expense_only=False):
        """拉取 effective=review 的队列组件明细（含 evidence 汇总所需字段）。"""
        db = api["db"]()
        rows = db.execute(f"""
            with {_latest_review_cte()}
            select c.id, c.component_code, c.source_type, c.amount, c.employee_id,
                   c.service_date, c.work_order_id, c.daily_report_id, c.source_id,
                   u.name as employee_name,
                   coalesce(lr.new_tax_category, c.tax_category) as effective_tax_category
            from employee_payment_components c
            join users u on u.id = c.employee_id
            left join latest_review lr on lr.component_id = c.id
            where c.superseded_at is null
              and coalesce(lr.new_tax_category, c.tax_category) = 'tax_review_required'
        """).fetchall()
        if expense_only:
            return [dict(r) for r in rows
                    if r["source_type"] in EXPENSE_COMPONENT_SOURCE_TYPES]
        return [dict(r) for r in rows if r["component_code"] == component_code]

    def _mileage_evidence_summary(mileage_rows):
        """Decision A 佐证盘点：日报 driving_miles / 里程佐证 / 起终点存在性。"""
        db = api["db"]()
        report_ids = sorted({int(r["daily_report_id"]) for r in mileage_rows
                             if r["daily_report_id"]})
        worker_ids = sorted({int(r["employee_id"]) for r in mileage_rows})
        summary = {
            "daily_report_count": len(report_ids),
            "driving_miles_present": 0,
            "evidence_rows": 0,
            "evidence_success_rows": 0,
            "evidence_miles_total": money(0),
            "origin_destination_reports": 0,
            "miles_reliable": False,
            "miles_total": money(0),
        }
        if not report_ids or not worker_ids:
            return summary
        rmarks = ",".join("?" * len(report_ids))
        wmarks = ",".join("?" * len(worker_ids))
        params = [*report_ids, *worker_ids]
        row = db.execute(
            f"""select count(*) as n from service_report_workers
                where report_id in ({rmarks}) and user_id in ({wmarks})
                  and driving_miles is not null""", params).fetchone()
        summary["driving_miles_present"] = int(row["n"] or 0)
        row = db.execute(
            f"""select count(*) as n,
                       coalesce(sum(coalesce(reported_miles, one_way_miles, 0)), 0) as m
                from service_report_mileage_evidence
                where report_id in ({rmarks}) and worker_user_id in ({wmarks})""",
            params).fetchone()
        summary["evidence_rows"] = int(row["n"] or 0)
        summary["evidence_miles_total"] = money(row["m"] or 0)
        row = db.execute(
            f"""select count(*) as n from service_report_mileage_evidence
                where report_id in ({rmarks}) and worker_user_id in ({wmarks})
                  and status = 'success'""", params).fetchone()
        summary["evidence_success_rows"] = int(row["n"] or 0)
        row = db.execute(
            f"""select count(distinct report_id) as n
                from service_report_mileage_evidence
                where report_id in ({rmarks}) and worker_user_id in ({wmarks})
                  and coalesce(origin_address, '') <> ''
                  and coalesce(destination_address, '') <> ''""", params).fetchone()
        summary["origin_destination_reports"] = int(row["n"] or 0)
        summary["miles_reliable"] = summary["evidence_success_rows"] > 0
        summary["miles_total"] = (summary["evidence_miles_total"]
                                  if summary["miles_reliable"] else money(0))
        return summary

    def _component_tax_config_snapshot(component_code):
        row = api["db"]().execute(
            """select component_code, display_name, default_tax_category,
                      requires_substantiation, effective_from, effective_to
               from payroll_component_tax_config
               where component_code=? and is_active=1
               order by effective_from desc limit 1""", (component_code,)).fetchone()
        return dict(row) if row else None

    def _expense_attachment_state(expense_rows):
        """Decision C 凭证状态（只计数，不读内容）。"""
        db = api["db"]()
        states = []
        for row in expense_rows:
            item_id = row.get("source_id")
            state = {"component_id": row["id"], "amount": money(row["amount"] or 0),
                     "attachment_count": 0, "expense_number": "", "expense_date": ""}
            if item_id:
                detail = db.execute(
                    """select e.expense_number, e.expense_date, i.line_key
                       from expense_items i join expenses e on e.id = i.expense_id
                       where i.id = ?""", (item_id,)).fetchone()
                if detail:
                    state["expense_number"] = detail["expense_number"]
                    state["expense_date"] = detail["expense_date"] or ""
                    att = db.execute(
                        """select count(*) as n from expense_attachments
                           where expense_id = (select expense_id from expense_items
                                               where id = ?)
                             and expense_item_key = ?""",
                        (item_id, detail["line_key"])).fetchone()
                    state["attachment_count"] = int(att["n"] or 0)
            states.append(state)
        return states

    def _policy_options(ids, prefix, labels):
        """三选项 scenario（只算影响，绝不执行）。"""
        out = []
        for key, (label, target) in labels:
            try:
                sim = _simulate_tax_policy_decision(ids, target)
            except ValueError as exc:
                sim = {"error": str(exc)}
            out.append({"key": "%s%d" % (prefix, key), "label": label,
                        "target_category": target, "simulation": sim})
        return out

    def _policy_decision_packages(default_year=None):
        """Phase 6C：三个 Decision Package + Decision D + readiness checklist。

        全只读；scenario 为纯内存模拟；Question for CPA 不替 CPA 回答。
        """
        db = api["db"]()
        before = _scenario_view(_effective_category_totals())
        mileage_rows = _queue_component_rows("self_drive_allowance")
        meal_rows = _queue_component_rows("meal_allowance")
        expense_rows = _queue_component_rows(expense_only=True)
        mileage_ids = [r["id"] for r in mileage_rows]
        meal_ids = [r["id"] for r in meal_rows]
        expense_ids = [r["id"] for r in expense_rows]

        def group(rows):
            dates = [r["service_date"] for r in rows if r["service_date"]]
            return {
                "count": len(rows),
                "amount": money(sum((money(r["amount"] or 0) for r in rows), money(0))),
                "employee_count": len({r["employee_id"] for r in rows}),
                "earliest_service_date": min(dates) if dates else "",
                "latest_service_date": max(dates) if dates else "",
                "work_order_count": len({r["work_order_id"] for r in rows
                                         if r["work_order_id"]}),
            }

        mileage = group(mileage_rows)
        mileage.update(_mileage_evidence_summary(mileage_rows))
        mileage["tax_config"] = _component_tax_config_snapshot("self_drive_allowance")
        meal = group(meal_rows)
        meal["tax_config"] = _component_tax_config_snapshot("meal_allowance")
        expense = group(expense_rows)
        expense["attachments"] = _expense_attachment_state(expense_rows)

        keep = ("Continue under tax review", "tax_review_required")
        reimb = ("Reclassify to accountable_reimbursement",
                 "accountable_reimbursement")
        comp = ("Treat as compensation (taxable)", "taxable_compensation")
        packages = {
            "mileage": {
                "label": "Decision A — Mileage Reimbursement",
                "component_code": "self_drive_allowance",
                "current": mileage,
                "potential_1099_treatment": (
                    "Excluded from Potential 1099 Candidate under "
                    "service_compensation_v1 while effective category is "
                    "tax_review_required."),
                "question_for_cpa": (
                    "How should these contractor mileage payments be treated for "
                    "1099 reporting when mileage substantiation is incomplete?"),
                "options": _policy_options(mileage_ids, "A", [(1, keep), (2, reimb),
                                                              (3, comp)]),
            },
            "meal_allowance": {
                "label": "Decision B — Meal Allowance Policy",
                "component_code": "meal_allowance",
                "current": meal,
                "potential_1099_treatment": (
                    "Excluded from Potential 1099 Candidate under "
                    "service_compensation_v1 while effective category is "
                    "tax_review_required."),
                "question_for_cpa": (
                    "Should the fixed meal allowance be treated as contractor "
                    "compensation, reimbursement, or remain subject to "
                    "documentation review?"),
                "options": _policy_options(meal_ids, "B", [(1, keep), (2, comp),
                                                           (3, reimb)]),
            },
            "expense_documentation": {
                "label": "Decision C — Expense Missing Attachment",
                "component_code": "expense_item",
                "current": expense,
                "potential_1099_treatment": (
                    "Excluded from Potential 1099 Candidate under "
                    "service_compensation_v1 while effective category is "
                    "tax_review_required."),
                "question_for_cpa": (
                    "Can this expense be treated as reimbursement based on "
                    "alternative documentation despite the missing attachment?"),
                "options": _policy_options(expense_ids, "C", [(1, keep), (2, reimb),
                                                              (3, comp)]),
            },
        }

        # Decision D：Reporting Year Basis（不擅自决定）。
        service_totals = _annual_tax_summary({"year_basis": "service_date"}, None)["totals"]
        payment_totals = _annual_tax_summary({"year_basis": "payment_date"}, None)["totals"]
        reporting_basis = {
            "service_date": {"count": service_totals["component_count"],
                             "amount": money(service_totals["total_amount"])},
            "payment_date": {"count": payment_totals["component_count"],
                             "amount": money(payment_totals["total_amount"])},
            "payment_date_unresolved": {
                "count": service_totals["component_count"] - payment_totals["component_count"],
                "amount": money(service_totals["total_amount"]
                                - payment_totals["total_amount"])},
            "question_for_cpa": (
                "Which date basis should control the year in the final 1099 "
                "reporting workflow?"),
        }

        identity = _identity_readiness()
        return {
            "year": str(default_year or ""),
            "generated_note": "Internal Tax Policy Decision Package — Not an IRS Filing",
            "rule_version": POTENTIAL_1099_RULE_VERSION,
            "totals_before": before,
            "decisions": packages,
            "reporting_basis": reporting_basis,
            "identity_readiness": identity,
            "readiness_checklist": _readiness_checklist(
                identity, before["review_required"]),
        }

    def _setting_has_value(key):
        """只检查 settings 值是否存在（非空），绝不返回真实值。"""
        row = api["db"]().execute(
            "select value from settings where key = ?", (key,)).fetchone()
        return bool(row and str(row["value"] or "").strip())

    def _identity_readiness():
        """Payer / Recipient 身份就绪：只做字段存在性检查，不读敏感值。"""
        payer = {
            "legal_name": "available" if _setting_has_value("company_name") else "missing",
            "ein": "available" if _setting_has_value("company_ein") else "missing",
            "address": "available" if _setting_has_value("company_address") else "missing",
        }
        # Recipient 基于 schema 字段存在性：users.name / users.address 已有；
        # TIN / SSN 目前没有任何存储字段 → not_implemented（Phase 7 再设计）。
        recipient = {
            "legal_name": "available",
            "tin_storage": "not_implemented",
            "address": "available",
        }
        return {"payer": payer, "recipient": recipient,
                "note": "Field existence check only — no EIN/TIN/SSN values are read."}

    def _readiness_checklist(identity, review_required_amount):
        """1099 readiness checklist（明确不做百分比评分）。"""
        db = api["db"]()
        null_status = db.execute(
            """select count(*) as n from employee_payment_components
               where superseded_at is null
                 and (tax_status_snapshot is null or tax_status_snapshot = '')"""
        ).fetchone()["n"]
        payer_ok = all(v == "available" for v in identity["payer"].values())
        recipient_ok = all(v == "available" for v in identity["recipient"].values())
        return [
            {"item": "Tax status complete",
             "status": "ready" if null_status == 0 else "missing_data",
             "detail": "Components with NULL tax status: %d" % null_status},
            {"item": "Classification review resolved",
             "status": "pending_decision" if review_required_amount else "ready",
             "detail": "Outstanding review amount: %s" % review_required_amount},
            {"item": "Reporting basis confirmed", "status": "pending_decision",
             "detail": "Decision D — service-date vs payment-date basis"},
            {"item": "Payer identity available",
             "status": "ready" if payer_ok else "missing_data",
             "detail": "; ".join("%s=%s" % (k, v)
                                 for k, v in identity["payer"].items())},
            {"item": "Recipient identity available",
             "status": "ready" if recipient_ok else "missing_data",
             "detail": "; ".join("%s=%s" % (k, v)
                                 for k, v in identity["recipient"].items())},
            {"item": "Filing method confirmed", "status": "not_implemented",
             "detail": "Out of scope for Phase 6 (future Phase 7)"},
        ]

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
        # Phase 4B：Annual Tax Summary（只读 reporting，绝不写任何业务表）
        "annual_tax_summary": _annual_tax_summary,
        "annual_tax_summary_options": _annual_tax_summary_options,
        "annual_tax_summary_components": _annual_tax_summary_components,
        # Phase 6B：Resolution Queue + CPA Workpaper（只读；复核动作仍走 4A）
        "tax_review_queue_summaries": _tax_review_queue_summaries,
        "closing_exceptions": _closing_exceptions,
        "cpa_workpaper": _cpa_workpaper,
        # Phase 6C：CPA Policy Decision Package（只读决策包 + 纯内存模拟）
        "tax_policy_decision_packages": _policy_decision_packages,
        "simulate_tax_policy_decision": _simulate_tax_policy_decision,
    }
