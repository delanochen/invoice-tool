"""工资相关页面路由。

从 app.py 平移过来的六个路由（外加 /payroll/subsidies）。逐个接受项：

- URL、endpoint 名、HTTP method、query/form 参数一律不变
  （endpoint 由函数名决定，改函数名会连带改掉权限映射，所以名字必须原样保留）
- 权限判定不变：仍然调用 app 里的 ``can_view_labor_payroll_reports`` /
  ``has_action_permission`` / ``can_manage_employee_grades``
- 模板、返回内容、XLSX 文件名不变

所有 flask / app 里的东西都通过 ``api`` 在**调用时**取得，所以本模块不 import app，
也不 import flask —— 既避免循环依赖，也让既有测试仍能
``patch.object(app, "render_template", ...)`` 观察到路由内部行为。
"""
from __future__ import annotations

from datetime import date, timedelta

from .tax import (
    DEFAULT_YEAR_BASIS,
    POTENTIAL_1099_RULE_NOTE,
    SUMMARY_BUCKET_OPTIONS,
    TAX_CATEGORIES,
    W2_CANDIDATE_RULE_NOTE,
    YEAR_BASIS_NOTES,
    YEAR_BASIS_OPTIONS,
    review_reason_options,
)

# Phase 4A：复核工作台的展示字典（纯展示，判定一律走 tax.py 的结构化函数）。
TAX_CATEGORY_LABELS = {
    "taxable_compensation": "Taxable Compensation",
    "accountable_reimbursement": "Accountable Reimbursement",
    "tax_review_required": "Tax Review Required",
}
TAX_CATEGORY_OPTIONS = [(key, TAX_CATEGORY_LABELS[key]) for key in TAX_CATEGORIES]
# SL = 工资付款单 / ER = 报销付款单（与 employee_finance.PAYMENT_NUMBER_PREFIXES 一致）。
TAX_REVIEW_PAYMENT_TYPES = [("salary", "SL 工资"), ("expense", "ER 报销")]
TAX_STATUS_OPTIONS = [("1099", "1099"), ("W2", "W2"), ("NULL", "NULL（缺身份）")]
TAX_REVIEW_REVIEWED_OPTIONS = [("unreviewed", "未复核"), ("reviewed", "已复核")]

# Phase 4B：付款单状态（Recorded ≠ Paid，年度汇总必须让「已记录未付款」看得见）。
PAYMENT_STATUS_LABELS = {
    "draft": "草稿",
    "pending_review": "待审核",
    "approved": "已审核",
    "pending_payment": "待付款",
    "paid": "已付款",
    "reconciled": "已对账",
    "rejected": "已拒绝",
    "cancelled": "已取消",
    "payment_failed": "付款失败",
}


def register_payroll_routes(app, api):

    @app.route("/payroll/subsidies", methods=["GET", "POST"])
    @api["login_required"]
    def payroll_subsidies():
        if not api["can_manage_employee_grades"]():
            api["abort"](403)
        request = api["request"]
        if request.method == "POST":
            values = {
                "payroll_cycle_start": request.form.get("cycle_start") or "2026-07-06",
                "payroll_report_writing_fee": max(api["to_float"](request.form.get("report_writing_fee")), 0),
                "payroll_lodging_limit": max(api["to_float"](request.form.get("lodging_limit")), 0),
            }
            try:
                date.fromisoformat(values["payroll_cycle_start"])
            except ValueError:
                values["payroll_cycle_start"] = "2026-07-06"
            for key, value in values.items():
                api["set_setting"](key, str(value))
            api["log_action"]("update", "payroll_subsidies", None, "薪酬补贴参数", "修改薪酬补贴参数")
            api["db"]().commit()
            api["flash"]("补贴参数已保存。", "success")
            return api["redirect"](api["url_for"]("payroll_subsidies"))
        return api["render_template"]("payroll_subsidies.html", settings=api["payroll_subsidy_settings"]())

    @app.route("/reports/labor-hours")
    @api["login_required"]
    def labor_hours_report():
        if not api["can_view_labor_payroll_reports"]():
            api["abort"](403)
        request = api["request"]
        g = api["g"]
        date_from = request.args.get("date_from", "")
        date_to = request.args.get("date_to", "")
        worker_id = request.args.get("worker_id", "")
        can_filter_workers = api["normalized_role"]() in {"admin", "manager", "finance"}
        effective_worker_id = worker_id if can_filter_workers else str(g.user["id"])
        workers = api["db"]().execute(
            """
        select id, name
        from users
        where role in ('manager', 'finance', 'employee', 'external_employee')
        order by name
        """
        ).fetchall() if can_filter_workers else []
        rows = api["labor_report_entries"](date_from, date_to, effective_worker_id)
        totals = {
            "work_hours": sum(row["work_hours"] for row in rows),
            "standard_hours": sum(row["standard_hours"] for row in rows),
            "transport_hours": sum(row["transport_hours"] for row in rows),
            "overtime_hours": sum(row["overtime_hours"] for row in rows),
            "holiday_hours": sum(row["holiday_hours"] for row in rows),
        }
        return api["render_template"](
            "labor_hours_report.html",
            rows=rows,
            totals=totals,
            workers=workers,
            worker_id=int(effective_worker_id) if str(effective_worker_id).isdigit() else "",
            can_filter_workers=can_filter_workers,
            date_from=date_from,
            date_to=date_to,
        )

    @app.route("/reports/payroll")
    @api["login_required"]
    def payroll_report():
        if not api["can_view_labor_payroll_reports"]():
            api["abort"](403)
        request = api["request"]
        g = api["g"]
        try:
            period_start = date.fromisoformat(request.args['period_start']) if request.args.get('period_start') else api["current_payroll_period_start"]()
            period_end = date.fromisoformat(request.args['period_end']) if request.args.get('period_end') else api["payroll_period_dates"](period_start)[0]
            if period_end < period_start:
                api["abort"](400, description='结束日期不能早于开始日期。')
            pay_date = period_end + timedelta(days=14)
        except (ValueError, OverflowError):
            api["abort"](400, description='请选择有效的开始日期和结束日期。')
        can_filter_workers = api["normalized_role"]() in {'admin', 'manager', 'finance'}
        worker_id = request.args.get('worker_id', '').strip() if can_filter_workers else str(g.user['id'])
        if worker_id and not worker_id.isdigit():
            api["abort"](400, description='请选择有效的人员。')
        workers = api["db"]().execute("""select id, name from users
        where role in ('admin', 'manager', 'finance', 'employee', 'external_employee', 'internal', 'user')
        order by name""").fetchall() if can_filter_workers else []
        payroll_batch = api["payroll_rows_for_range"](period_start, period_end, pay_date, worker_id)
        return api["render_template"](
            "payroll_report.html",
            workers=workers, worker_id=worker_id, can_filter_workers=can_filter_workers,
            rows=payroll_batch["rows"],
            totals=payroll_batch["totals"],
            period_start=payroll_batch["period_start"].isoformat(),
            period_end=payroll_batch["period_end"].isoformat(),
            pay_date=payroll_batch["pay_date"].isoformat(),
            subsidy_settings=payroll_batch["subsidy_settings"],
        )

    @app.route("/reports/payroll-details")
    @api["login_required"]
    def payroll_detail_report():
        if not api["has_action_permission"]("payroll_report", "view"):
            api["abort"](403)
        request = api["request"]
        g = api["g"]
        try:
            start = date.fromisoformat(request.args.get("date_from") or api["current_payroll_period_start"]().isoformat())
            end = date.fromisoformat(request.args.get("date_to") or api["payroll_period_dates"](start)[0].isoformat())
            if end < start:
                raise ValueError()
        except (ValueError, OverflowError):
            api["abort"](400, description="请选择有效的开始日期和结束日期。")
        can_filter_workers = api["normalized_role"]() in {"admin", "manager", "finance"}
        worker_ids = request.args.getlist("worker_id") if can_filter_workers else [str(g.user["id"])]
        order_ids, sites = request.args.getlist("order_id"), request.args.getlist("site")
        if any(not value.isdigit() for value in worker_ids + order_ids):
            api["abort"](400)
        batch = api["payroll_rows_for_range"](start, end, end, "" if can_filter_workers else str(g.user["id"]), detail=True)
        rows = [row for row in batch["rows"] if (not worker_ids or str(row["worker_id"]) in worker_ids)]
        order_options = {row["service_order_id"]: row for row in rows if row["service_order_id"]}
        site_options = [{"client_name": value} for value in sorted({row["client_name"] for row in rows if row["client_name"]})]
        rows = [row for row in rows if (not order_ids or str(row["service_order_id"]) in order_ids)
                and (not sites or row["client_name"] in sites)]
        workers = api["db"]().execute("select id, name from users order by name").fetchall() if can_filter_workers else []
        return api["render_template"]("payroll_detail_report.html", rows=rows,
            date_from=start.isoformat(), date_to=end.isoformat(), workers=workers, worker_ids=worker_ids,
            order_options=[{"id": key, "order_number": value["order_number"]} for key, value in order_options.items()],
            order_ids=order_ids, site_options=site_options, sites=sites, can_filter_workers=can_filter_workers,
            total=sum(row["total_pay"] for row in rows))

    @app.route("/payroll/calendar")
    @api["login_required"]
    def payroll_calendar():
        if not api["can_view_labor_payroll_reports"]():
            api["abort"](403)
        today = date.today()
        try:
            year = int(api["request"].args.get("year", today.year))
            month = int(api["request"].args.get("month", today.month))
            visible_month = date(year, month, 1)
        except ValueError:
            visible_month = date(today.year, today.month, 1)
        periods = api["payroll_periods_for_month"](visible_month.year, visible_month.month)
        previous_month = date(visible_month.year - 1, 12, 1) if visible_month.month == 1 else date(visible_month.year, visible_month.month - 1, 1)
        next_month = date(visible_month.year + 1, 1, 1) if visible_month.month == 12 else date(visible_month.year, visible_month.month + 1, 1)
        return api["render_template"](
            "payroll_calendar.html",
            visible_month=visible_month,
            previous_month=previous_month,
            next_month=next_month,
            weeks=api["payroll_calendar_weeks"](visible_month.year, visible_month.month, periods),
        )

    @app.get("/payroll/calendar/batch")
    @api["login_required"]
    def payroll_calendar_batch():
        if not api["can_view_labor_payroll_reports"]():
            api["abort"](403)
        try:
            period_start = date.fromisoformat(api["request"].args.get("period_start", ""))
        except ValueError:
            api["abort"](400)
        return api["jsonify"](api["payroll_batch_payload"](period_start, api["request"].args.get("batch_type", "regular")))

    @app.get("/payroll/calendar/export.xlsx")
    @api["login_required"]
    def payroll_calendar_export():
        if not api["can_view_labor_payroll_reports"]():
            api["abort"](403)
        try:
            period_start = date.fromisoformat(api["request"].args.get("period_start", ""))
        except ValueError:
            api["abort"](400)
        payload = api["payroll_batch_payload"](period_start, api["request"].args.get("batch_type", "regular"))
        headers = list(payload["rows"][0].keys()) if payload["rows"] else [
            "员工", "员工等级", "基本工资", "出勤天数", "里程", "标准工时", "标准工资",
            "随行时长", "租车驾驶时长", "随行补贴", "租车驾驶补贴", "自驾车补",
            "加班工时", "加班工资", "假期工时", "假期工资", "补贴", "合计工资",
        ]
        rows = [[row.get(header, "") for header in headers] for row in payload["rows"]]
        rows.append([])
        rows.append(["周期", f"{payload['period_start']} 至 {payload['period_end']}"])
        rows.append(["发薪日期", payload["pay_date"]])
        rows.append(["合计工资", payload["totals"]["total_pay"]])
        rows.append([])
        rows.append(["工资条"])
        for payslip in payload["payslips"]:
            rows.append([])
            rows.append(["员工", payslip["employee"], "员工等级", payslip["grade"], "合计工资", payslip["total_pay"]])
            rows.append(["项目", "工时", "单价", "天数", "数量", "里程", "金额"])
            for line in payslip["lines"]:
                rows.append([
                    line.get("label", ""),
                    line.get("hours", ""),
                    line.get("rate", ""),
                    line.get("days", ""),
                    line.get("count", ""),
                    line.get("miles", ""),
                    line.get("amount", ""),
                ])
        workbook = api["build_simple_xlsx"](headers, rows, sheet_name="工资批次")
        filename = f"payroll-{payload['batch_type']}-{payload['pay_date']}.xlsx"
        return api["send_file"](
            workbook,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ------------------------------------------------------------------
    # Phase 2B：员工 W-2 / 1099 税务身份历史。
    # 当前身份一律按日期查 worker_tax_status_history，users 不加单值字段。
    # ------------------------------------------------------------------
    @app.get("/users/<int:user_id>/tax-status")
    @app.post("/users/<int:user_id>/tax-status")
    @api["login_required"]
    def worker_tax_status_history(user_id):
        request = api["request"]
        user = api["db"]().execute(
            "select id, name, role from users where id=?", (user_id,)
        ).fetchone()
        if not user:
            api["abort"](404)
        can_manage = api["can_manage_employee_grades"]()
        if not can_manage and api["g"].user["id"] != user_id:
            api["abort"](403)
        if request.method == "POST":
            if not can_manage:
                api["abort"](403)
            try:
                effective_from = (request.form.get("effective_from", "") or "").strip()
                effective_to = (request.form.get("effective_to", "") or "").strip() or None
                date.fromisoformat(effective_from)
                if effective_to is not None:
                    date.fromisoformat(effective_to)
                api["create_worker_tax_status_entry"](
                    user_id,
                    (request.form.get("tax_status", "") or "").strip(),
                    effective_from,
                    effective_to,
                    (request.form.get("notes", "") or "").strip(),
                    api["g"].user["id"],
                )
                api["db"]().commit()
                api["log_action"]("create", "worker_tax_status", user_id, user["name"],
                                  "新增税务身份 %s %s~%s" % (request.form.get("tax_status"), effective_from, effective_to or ""))
                api["flash"]("税务身份已保存。", "success")
            except ValueError as error:
                api["db"]().rollback()
                api["flash"](str(error), "error")
            return api["redirect"](api["url_for"]("worker_tax_status_history", user_id=user_id))
        entries = api["worker_tax_status_entries"](user_id)
        return api["render_template"](
            "worker_tax_status.html",
            worker=user,
            entries=entries,
            current_status=api["worker_tax_status_current"](user_id),
            today=date.today().isoformat(),
            can_manage=can_manage,
        )

    # ------------------------------------------------------------------
    # Phase 4A：Tax Review 工作台。
    #
    # 统一处理 payroll 与 expense 两侧「当前生效快照」里仍需人工判定的组件。
    # 复核**只追加** employee_payment_tax_reviews：原始组件快照、付款单金额、
    # 往来账 / 批次 / 银行对账一律不动（页面上的 Effective 合计是动态算出来的）。
    # 权限：tax_review.view 看，tax_review.review 才能改分类（默认 admin/finance）。
    # ------------------------------------------------------------------
    REVIEW_ACTIONS = {
        "accountable": "accountable_reimbursement",
        "taxable": "taxable_compensation",
        "keep": "tax_review_required",
    }
    REVIEW_ACTION_LABELS = {
        "accountable": "确认为可报销",
        "taxable": "视为应税报酬",
        "keep": "继续待复核",
    }

    def _review_filters(request, default_year):
        """默认口径：当前税务年度 + 有效分类仍是 tax_review_required。

        「全部」选项的值就是空串，所以只要 URL 里显式带了参数就不套默认，
        用户能真正看到全部记录，而不是被默认值悄悄过滤掉。
        """
        requested = request.args
        effective = requested.get("effective_category")
        if effective is None:
            effective = "tax_review_required"
        year = requested.get("year")
        if year is None:
            year = default_year
        return {
            "year": year or "",
            "employee_id": requested.get("employee_id", "") or "",
            "payment_type": requested.get("payment_type", "") or "",
            "component_code": requested.get("component_code", "") or "",
            "reason": requested.get("reason", "") or "",
            "tax_status": requested.get("tax_status", "") or "",
            "original_category": requested.get("original_category", "") or "",
            "effective_category": effective or "",
            "reviewed": requested.get("reviewed", "") or "",
            "date_from": requested.get("date_from", "") or "",
            "date_to": requested.get("date_to", "") or "",
        }

    @app.get("/finance/tax-review")
    @api["login_required"]
    def tax_review():
        if not api["has_action_permission"]("tax_review", "view"):
            api["abort"](403)
        request = api["request"]
        filters = _review_filters(request, str(date.today().year))
        data = api["tax_review_rows"](filters)
        return api["render_template"](
            "tax_review.html",
            rows=data["rows"],
            totals=data["totals"],
            filters=data["filters"],
            options=data["options"],
            employees=data["options"]["employees"],
            component_codes=data["options"]["component_codes"],
            years=data["options"]["years"],
            reason_options=review_reason_options(),
            category_options=TAX_CATEGORY_OPTIONS,
            category_labels=TAX_CATEGORY_LABELS,
            payment_type_labels=TAX_REVIEW_PAYMENT_TYPES,
            tax_status_options=TAX_STATUS_OPTIONS,
            reviewed_options=TAX_REVIEW_REVIEWED_OPTIONS,
            can_review=api["has_action_permission"]("tax_review", "review"),
            today=date.today().isoformat(),
        )

    @app.get("/finance/tax-review/<int:component_id>")
    @api["login_required"]
    def tax_review_component(component_id):
        if not api["has_action_permission"]("tax_review", "view"):
            api["abort"](403)
        detail = api["tax_review_detail"](component_id)
        if detail is None:
            api["abort"](404)
        return api["render_template"](
            "tax_review_detail.html",
            detail=detail,
            component=detail["component"],
            history=detail["history"],
            category_labels=TAX_CATEGORY_LABELS,
            can_review=api["has_action_permission"]("tax_review", "review"),
        )

    @app.post("/finance/tax-review/<int:component_id>/review")
    @api["login_required"]
    def save_tax_review(component_id):
        if not api["has_action_permission"]("tax_review", "review"):
            api["abort"](403)
        request = api["request"]
        action = (request.form.get("action", "") or "").strip()
        target = REVIEW_ACTIONS.get(action)
        reason = (request.form.get("reason", "") or "").strip()
        try:
            if target is None:
                raise ValueError("未知的复核动作。")
            previous = api["record_tax_review"](
                component_id, target, reason, api["g"].user["id"])
            api["log_action"](
                "review", "tax_review", component_id, "%s → %s" % (previous, target),
                "税务复核 %s → %s：%s" % (previous, target, reason))
            api["db"]().commit()
            api["flash"]("复核已保存：%s。" % REVIEW_ACTION_LABELS[action], "success")
        except ValueError as error:
            api["db"]().rollback()
            api["flash"](str(error), "error")
        return api["redirect"](api["url_for"]("tax_review_component", component_id=component_id))

    # ------------------------------------------------------------------
    # Phase 4B：Annual Tax Summary（年度税务汇总 + drill-down）。
    # 纯只读 reporting：不写往来账、不改付款单 / 组件 / 身份 / 报销 / 工资。
    # 年度口径（service_date / payment_date）由页面显式传入，默认 service_date。
    # ------------------------------------------------------------------
    def annual_filters_from_request(request, default_year):
        args = request.args
        return {
            "year": (args.get("year") or "").strip() or default_year,
            "year_basis": (args.get("year_basis") or "").strip() or DEFAULT_YEAR_BASIS,
            "employee_id": args.get("employee_id") or "",
            "tax_status": args.get("tax_status") or "",
            "category": args.get("category") or "",
            "payment_status": args.get("payment_status") or "",
            "work_order_id": args.get("work_order_id") or "",
            "include_review": args.get("include_review") or "1",
        }

    @app.get("/finance/annual-tax-summary")
    @api["login_required"]
    def annual_tax_summary():
        if not api["has_action_permission"]("annual_tax_summary", "view"):
            api["abort"](403)
        request = api["request"]
        data = api["annual_tax_summary_rows"](
            annual_filters_from_request(request, str(date.today().year)))
        return api["render_template"](
            "annual_tax_summary.html",
            rows=data["rows"],
            totals=data["totals"],
            filters=data["filters"],
            options=data["options"],
            excluded=data["excluded"],
            year_basis_options=YEAR_BASIS_OPTIONS,
            year_basis_notes=YEAR_BASIS_NOTES,
            bucket_options=SUMMARY_BUCKET_OPTIONS,
            tax_status_options=TAX_STATUS_OPTIONS,
            payment_status_labels=PAYMENT_STATUS_LABELS,
            potential_1099_note=POTENTIAL_1099_RULE_NOTE,
            w2_note=W2_CANDIDATE_RULE_NOTE,
            can_export=api["has_action_permission"]("annual_tax_summary", "export"),
            today=date.today().isoformat(),
        )

    @app.get("/finance/annual-tax-summary/components")
    @api["login_required"]
    def annual_tax_summary_components():
        if not api["has_action_permission"]("annual_tax_summary", "view"):
            api["abort"](403)
        request = api["request"]
        data = api["annual_tax_summary_component_rows"](
            annual_filters_from_request(request, str(date.today().year)))
        return api["render_template"](
            "annual_tax_summary_components.html",
            rows=data["rows"],
            totals=data["totals"],
            filters=data["filters"],
            options=data["options"],
            year_basis_options=YEAR_BASIS_OPTIONS,
            year_basis_notes=YEAR_BASIS_NOTES,
            bucket_options=SUMMARY_BUCKET_OPTIONS,
            tax_status_options=TAX_STATUS_OPTIONS,
            payment_status_labels=PAYMENT_STATUS_LABELS,
            category_labels=TAX_CATEGORY_LABELS,
            potential_1099_note=POTENTIAL_1099_RULE_NOTE,
            can_export=api["has_action_permission"]("annual_tax_summary", "export"),
            today=date.today().isoformat(),
        )

    @app.get("/finance/annual-tax-summary/export.xlsx")
    @api["login_required"]
    def annual_tax_summary_export():
        if not api["has_action_permission"]("annual_tax_summary", "export"):
            api["abort"](403)
        request = api["request"]
        data = api["annual_tax_summary_rows"](
            annual_filters_from_request(request, str(date.today().year)))
        headers = ["员工", "税务年度", "身份快照", "Compensation", "Reimbursements",
                   "Tax Review Required", "Total Recorded", "Paid", "Unpaid",
                   "Potential 1099 Reportable", "组件数"]
        rows = []
        for entry in data["rows"]:
            rows.append([
                entry["employee_name"],
                entry["tax_year"] or "",
                entry["tax_status_label"],
                float(entry["compensation_amount"]),
                float(entry["reimbursement_amount"]),
                float(entry["review_amount"]),
                float(entry["total_amount"]),
                float(entry["paid_amount"]),
                float(entry["unpaid_amount"]),
                float(entry["potential_1099_amount"]),
                entry["component_count"],
            ])
        totals = data["totals"]
        rows.append([])
        rows.append(["合计", data["filters"]["year"], data["filters"]["year_basis"],
                     float(totals["compensation_amount"]), float(totals["reimbursement_amount"]),
                     float(totals["review_amount"]), float(totals["total_amount"]),
                     float(totals["paid_amount"]), float(totals["unpaid_amount"]),
                     float(totals["potential_1099_amount"]), totals["component_count"]])
        rows.append(["闭合校验", "三桶合计 = 记录总额" if totals["closure_ok"] else "不闭合！"])
        workbook = api["build_simple_xlsx"](headers, rows, sheet_name="年度税务汇总")
        filename = "annual-tax-summary-%s-%s.xlsx" % (
            data["filters"]["year"] or "all", data["filters"]["year_basis"])
        return api["send_file"](
            workbook,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    return {
        "tax_review": tax_review,
        "tax_review_component": tax_review_component,
        "save_tax_review": save_tax_review,
        "annual_tax_summary": annual_tax_summary,
        "annual_tax_summary_components": annual_tax_summary_components,
        "annual_tax_summary_export": annual_tax_summary_export,
        "payroll_subsidies": payroll_subsidies,
        "labor_hours_report": labor_hours_report,
        "payroll_report": payroll_report,
        "payroll_detail_report": payroll_detail_report,
        "payroll_calendar": payroll_calendar,
        "payroll_calendar_batch": payroll_calendar_batch,
        "payroll_calendar_export": payroll_calendar_export,
        "worker_tax_status_history": worker_tax_status_history,
    }
