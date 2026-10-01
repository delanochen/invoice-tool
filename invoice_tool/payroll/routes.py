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
            queues=api["tax_review_queue_summaries"](str(date.today().year)),
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

    # ------------------------------------------------------------------
    # Phase 6B：CPA Workpaper（内部工作底稿 + Closing Exceptions）。
    # 只读 reporting，复用 Annual Tax Summary 的 read model；绝不写业务表，
    # 绝不生成 1099 / withholding / IRS 文件。
    # ------------------------------------------------------------------
    @app.get("/finance/cpa-workpaper")
    @api["login_required"]
    def cpa_workpaper():
        if not api["has_action_permission"]("annual_tax_summary", "view"):
            api["abort"](403)
        request = api["request"]
        data = api["cpa_workpaper_rows"](
            annual_filters_from_request(request, str(date.today().year)))
        return api["render_template"](
            "cpa_workpaper.html",
            rows=data["rows"],
            totals=data["totals"],
            filters=data["filters"],
            options=data["options"],
            excluded=data["excluded"],
            exceptions=data["exceptions"],
            rule_version=data["rule_version"],
            not_filing_note=data["not_filing_note"],
            year_basis_note=data["year_basis_note"],
            potential_1099_note=POTENTIAL_1099_RULE_NOTE,
            year_basis_options=YEAR_BASIS_OPTIONS,
            year_basis_notes=YEAR_BASIS_NOTES,
            tax_status_options=TAX_STATUS_OPTIONS,
            can_export=api["has_action_permission"]("annual_tax_summary", "export"),
            today=date.today().isoformat(),
        )

    @app.get("/finance/cpa-workpaper/export.xlsx")
    @api["login_required"]
    def cpa_workpaper_export():
        if not api["has_action_permission"]("annual_tax_summary", "export"):
            api["abort"](403)
        request = api["request"]
        data = api["cpa_workpaper_rows"](
            annual_filters_from_request(request, str(date.today().year)))
        generated_at = api["now"]()
        banner = [data["not_filing_note"],
                  "generated_at=%s" % generated_at,
                  "year_basis=%s" % data["filters"]["year_basis"],
                  "reporting_rule_version=%s" % data["rule_version"],
                  data["year_basis_note"]]

        review_data = api["tax_review_rows"]({
            "effective_category": "tax_review_required",
            "year": data["filters"]["year"],
        })
        review_groups = {}
        for row in review_data["rows"]:
            key = (row.get("review_reason") or "other", row["component_code"])
            group = review_groups.setdefault(key, {
                "count": 0, "amount": 0, "employees": set(), "dates": []})
            group["count"] += 1
            group["amount"] += row["amount"] or 0
            group["employees"].add(row["employee_id"])
            if row.get("service_date"):
                group["dates"].append(row["service_date"])
        review_rows = [[key[0], key[1], g["count"], float(g["amount"]),
                        len(g["employees"]),
                        min(g["dates"]) if g["dates"] else "",
                        max(g["dates"]) if g["dates"] else ""]
                       for key, g in sorted(review_groups.items())]

        detail_data = api["annual_tax_summary_component_rows"](
            annual_filters_from_request(request, str(date.today().year)))
        detail_rows = [[
            row.get("employee_name"), row.get("payment_number"),
            row.get("payment_type"), row.get("service_date") or "",
            row.get("payment_date") or "", row.get("order_number") or "",
            row.get("component_name") or row.get("component_code"),
            float(row.get("amount") or 0),
            row.get("tax_category"), row.get("effective_tax_category"),
            row.get("tax_status_snapshot") or "NULL",
            "Paid" if row.get("payment_date") else "Unpaid",
        ] for row in detail_data["rows"]]

        totals = data["totals"]
        employee_rows = [[
            entry["employee_name"], entry["tax_status_label"],
            float(entry["compensation_amount"]), float(entry["reimbursement_amount"]),
            float(entry["review_amount"]), float(entry["total_amount"]),
            float(entry["paid_amount"]), float(entry["potential_1099_amount"]),
            entry["review_count"], float(entry["review_amount"]),
        ] for entry in data["rows"]]
        employee_rows.append([])
        employee_rows.append(["合计", "", float(totals["compensation_amount"]),
                              float(totals["reimbursement_amount"]),
                              float(totals["review_amount"]),
                              float(totals["total_amount"]), float(totals["paid_amount"]),
                              float(totals["potential_1099_amount"]),
                              totals["review_count"], float(totals["review_amount"])])

        exception_rows = [[e["severity"], e["issue_type"], e["label"],
                           e["count"], float(e["amount"]), e["detail"]]
                          for e in data["exceptions"]]

        sheets = [
            ("Employee Summary",
             ["Employee", "Tax Status", "Service Compensation", "Reimbursements",
              "Review Required", "Recorded Total", "Paid Total",
              "Potential 1099 Reportable (Preliminary)",
              "Unresolved Review Count", "Unresolved Review Amount"],
             employee_rows),
            ("Component Detail",
             ["Employee", "Payment", "Type", "Service Date", "Payment Date",
              "Work Order", "Component", "Amount", "Original Category",
              "Effective Category", "Tax Status Snapshot", "Paid/Unpaid"],
             detail_rows),
            ("Outstanding Review",
             ["Reason", "Component Code", "Count", "Amount", "Employee Count",
              "Earliest Service Date", "Latest Service Date"],
             review_rows),
            ("Closing Exceptions",
             ["Severity", "Issue Type", "Label", "Count", "Amount", "Detail"],
             exception_rows),
        ]
        workbook = api["build_multi_sheet_xlsx"](
            [(name, banner + [""] + headers, rows)
             for name, headers, rows in sheets])
        filename = "cpa-workpaper-%s-%s.xlsx" % (
            data["filters"]["year"] or "all", data["filters"]["year_basis"])
        return api["send_file"](
            workbook,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ------------------------------------------------------------------
    # Phase 6C：CPA Policy Decision Package（只读决策包 + 纯内存模拟）。
    # 权限复用 annual_tax_summary（view/export）；第一版只有 Export，
    # 没有 Apply——任何分类变更都必须等 CPA 拍板后走 Phase 6D。
    # ------------------------------------------------------------------
    @app.get("/finance/policy-decisions")
    @api["login_required"]
    def policy_decisions():
        if not api["has_action_permission"]("annual_tax_summary", "view"):
            api["abort"](403)
        data = api["tax_policy_decision_packages"](str(date.today().year))
        return api["render_template"](
            "policy_decisions.html",
            d=data,
            not_filing_note=data["generated_note"],
            rule_version=data["rule_version"],
            potential_1099_note=POTENTIAL_1099_RULE_NOTE,
            can_export=api["has_action_permission"]("annual_tax_summary", "export"),
            today=date.today().isoformat(),
        )

    @app.get("/finance/policy-decisions/export.xlsx")
    @api["login_required"]
    def policy_decisions_export():
        if not api["has_action_permission"]("annual_tax_summary", "export"):
            api["abort"](403)
        data = api["tax_policy_decision_packages"](str(date.today().year))
        generated_at = api["now"]()
        banner = [data["generated_note"],
                  "generated_at=%s" % generated_at,
                  "reporting_rule_version=%s" % data["rule_version"],
                  "Preliminary — subject to CPA review",
                  "No personally identifiable or financial account data is included."]

        before = data["totals_before"]
        exec_rows = [["Before totals (current effective classification)"]]
        exec_rows.append(["Service Compensation", float(before["service_compensation"])])
        exec_rows.append(["Reimbursements", float(before["reimbursements"])])
        exec_rows.append(["Review Required", float(before["review_required"])])
        exec_rows.append(["Total Recorded", float(before["total_recorded"])])
        exec_rows.append(["Potential 1099 Candidate (Preliminary)",
                          float(before["potential_1099_candidate"])])
        exec_rows.append([])
        exec_rows.append(["1099 Readiness Checklist"])
        for item in data["readiness_checklist"]:
            exec_rows.append([item["item"], item["status"], item["detail"]])
        exec_rows.append([])
        exec_rows.append(["Identity Readiness (field existence only)"])
        for side in ("payer", "recipient"):
            for field, state in data["identity_readiness"][side].items():
                exec_rows.append([side.title(), field, state])
        rb = data["reporting_basis"]
        exec_rows.append([])
        exec_rows.append(["Decision D — Reporting Year Basis"])
        exec_rows.append(["Service-date basis", rb["service_date"]["count"],
                          float(rb["service_date"]["amount"])])
        exec_rows.append(["Payment-date basis", rb["payment_date"]["count"],
                          float(rb["payment_date"]["amount"])])
        exec_rows.append(["Payment date unresolved",
                          rb["payment_date_unresolved"]["count"],
                          float(rb["payment_date_unresolved"]["amount"])])

        def detail_sheet(decision, extra_headers, extra_rows):
            current = decision["current"]
            rows = [[current["count"], float(current["amount"]),
                     current["employee_count"],
                     current.get("earliest_service_date", ""),
                     current.get("latest_service_date", ""),
                     current.get("work_order_count", 0)]]
            rows.extend(extra_rows)
            return rows

        def component_rows(rows):
            return [[r["employee_name"], r["service_date"] or "",
                     float(r["amount"] or 0)] for r in rows]

        mileage = data["decisions"]["mileage"]
        mileage_rows = detail_sheet(mileage, [], [])
        meal = data["decisions"]["meal_allowance"]
        meal_rows = detail_sheet(meal, [], [])
        expense = data["decisions"]["expense_documentation"]
        expense_rows = detail_sheet(
            expense, [], [[a.get("expense_number"), a.get("expense_date"),
                           float(a.get("amount") or 0), a.get("attachment_count")]
                          for a in expense["current"].get("attachments", [])])

        scenario_rows = []
        for name, decision in data["decisions"].items():
            scenario_rows.append([decision["label"], "", "", "", "", "", ""])
            scenario_rows.append(["Option", "Target Category", "Service Compensation",
                                  "Reimbursements", "Review Required",
                                  "Potential 1099", "Total Recorded"])
            scenario_rows.append(["Before (current)", "", float(before["service_compensation"]),
                                  float(before["reimbursements"]),
                                  float(before["review_required"]),
                                  float(before["potential_1099_candidate"]),
                                  float(before["total_recorded"])])
            for option in decision["options"]:
                sim = option.get("simulation") or {}
                after = sim.get("after") or {}
                scenario_rows.append([
                    option["key"], option["target_category"],
                    float(after.get("service_compensation") or 0),
                    float(after.get("reimbursements") or 0),
                    float(after.get("review_required") or 0),
                    float(after.get("potential_1099_candidate") or 0),
                    float(after.get("total_recorded") or 0)])
            scenario_rows.append(["Question for CPA", decision["question_for_cpa"]])
            scenario_rows.append([])
        scenario_rows.append(["Decision D — Reporting Year Basis",
                              rb["question_for_cpa"]])

        sheets = [
            ("Executive Summary", ["Item", "Value 1", "Value 2"], exec_rows),
            ("Mileage", ["Count", "Amount", "Employee Count", "Earliest Service Date",
                         "Latest Service Date", "Work Order Count"], mileage_rows),
            ("Meal Allowance", ["Count", "Amount", "Employee Count", "Earliest Service Date",
                                "Latest Service Date", "Work Order Count"], meal_rows),
            ("Expense Documentation", ["Count", "Amount", "Employee Count",
                                       "Earliest Service Date", "Latest Service Date",
                                       "Work Order Count"], expense_rows),
            ("Scenario Comparison",
             ["Option / Item", "Target / Note", "Service Compensation",
              "Reimbursements", "Review Required", "Potential 1099",
              "Total Recorded"], scenario_rows),
        ]
        workbook = api["build_multi_sheet_xlsx"](
            [(name, banner + [""] + headers, rows)
             for name, headers, rows in sheets])
        return api["send_file"](
            workbook,
            as_attachment=True,
            download_name="cpa-tax-policy-decision-package.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    return {
        "tax_review": tax_review,
        "tax_review_component": tax_review_component,
        "save_tax_review": save_tax_review,
        "annual_tax_summary": annual_tax_summary,
        "annual_tax_summary_components": annual_tax_summary_components,
        "annual_tax_summary_export": annual_tax_summary_export,
        "cpa_workpaper": cpa_workpaper,
        "cpa_workpaper_export": cpa_workpaper_export,
        "policy_decisions": policy_decisions,
        "policy_decisions_export": policy_decisions_export,
        "payroll_subsidies": payroll_subsidies,
        "labor_hours_report": labor_hours_report,
        "payroll_report": payroll_report,
        "payroll_detail_report": payroll_detail_report,
        "payroll_calendar": payroll_calendar,
        "payroll_calendar_batch": payroll_calendar_batch,
        "payroll_calendar_export": payroll_calendar_export,
        "worker_tax_status_history": worker_tax_status_history,
    }
