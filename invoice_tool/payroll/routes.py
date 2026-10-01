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

    return {
        "payroll_subsidies": payroll_subsidies,
        "labor_hours_report": labor_hours_report,
        "payroll_report": payroll_report,
        "payroll_detail_report": payroll_detail_report,
        "payroll_calendar": payroll_calendar,
        "payroll_calendar_batch": payroll_calendar_batch,
        "payroll_calendar_export": payroll_calendar_export,
        "worker_tax_status_history": worker_tax_status_history,
    }
