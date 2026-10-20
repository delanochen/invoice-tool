"""Business routes with explicit current-app resolvers; no reverse import."""


def register_report_queries_routes(app, *,
    _cb_resolve_CUSTOMER_REIMBURSEMENT_STATUS_LABELS,
    _cb_resolve_abort,
    _cb_resolve_build_simple_xlsx,
    _cb_resolve_can_view_audit_logs,
    _cb_resolve_can_view_customer_reimbursement,
    _cb_resolve_current_language,
    _cb_resolve_date,
    _cb_resolve_db,
    _cb_resolve_g,
    _cb_resolve_is_external_manager,
    _cb_resolve_login_required,
    _cb_resolve_re,
    _cb_resolve_render_template,
    _cb_resolve_request,
    _cb_resolve_send_file,
):
    @_cb_resolve_login_required()
    def export_visible_report():
        payload = _cb_resolve_request().get_json(silent=True) or {}
        title = str(payload.get("title") or "报表").strip()[:80] or "报表"
        headers = payload.get("headers") or []
        rows = payload.get("rows") or []
        if not isinstance(headers, list) or not headers or len(headers) > 100:
            _cb_resolve_abort()(400)
        if not isinstance(rows, list) or len(rows) > 10000:
            _cb_resolve_abort()(400)
        clean_headers = [str(value or "")[:500] for value in headers]
        clean_rows = []
        for row in rows:
            if not isinstance(row, list):
                _cb_resolve_abort()(400)
            clean_rows.append([str(value or "")[:5000] for value in row[: len(clean_headers)]])
        safe_sheet_name = _cb_resolve_re().sub(r"[\\/*?:\[\]]", " ", title).strip()[:31] or "报表"
        safe_filename = _cb_resolve_re().sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", title).strip("-") or "report"
        workbook = _cb_resolve_build_simple_xlsx()(clean_headers, clean_rows, sheet_name=safe_sheet_name)
        return _cb_resolve_send_file()(
            workbook,
            as_attachment=True,
            download_name=f"{safe_filename}-{_cb_resolve_date().today().isoformat()}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


    @_cb_resolve_login_required()
    def customer_reimbursement_query():
        if not _cb_resolve_can_view_customer_reimbursement()():
            _cb_resolve_abort()(403)
        q = _cb_resolve_request().args.get("q", "").strip()
        status = _cb_resolve_request().args.get("status", "")
        # 工单状态筛选（open/closed）：与结算单自身的 status 是两个独立维度。
        # 默认只看「进行中」的工单——「进行中」是唯一有意义的默认值，
        # 不做筛选会把历史已关闭工单的结算全带进来，数量上淹没当期的。
        # 传 order_status=all（或空字符串以外的 ALL）表示不限。
        order_status = _cb_resolve_request().args.get("order_status", "open")
        if order_status not in ("open", "closed", "all"):
            order_status = "open"
        date_from = _cb_resolve_request().args.get("date_from", "")
        date_to = _cb_resolve_request().args.get("date_to", "")
        order_ids = list(dict.fromkeys(value for value in _cb_resolve_request().args.getlist("order_id") if value))
        sites = list(dict.fromkeys(value for value in _cb_resolve_request().args.getlist("site") if value))
        clauses = ["1 = 1"]
        params = []
        if _cb_resolve_is_external_manager()():
            clauses.append("service_orders.client_id = ?")
            params.append(_cb_resolve_g().user["client_id"])
        options = _cb_resolve_db()().execute(f"select distinct service_orders.id, service_orders.order_number, service_orders.client_name from service_orders join customer_reimbursements on customer_reimbursements.service_order_id = service_orders.id where {' and '.join(clauses)} order by service_orders.order_number desc", params).fetchall()
        for column, values in [("service_orders.id", order_ids), ("service_orders.client_name", sites)]:
            if values:
                clauses.append(f"{column} in ({','.join('?' for _ in values)})")
                params.extend(values)
        if q:
            clauses.append(
                """
            (
                customer_reimbursements.file_name like ?
                or service_orders.order_number like ?
                or service_orders.client_name like ?
                or service_orders.client_order_number like ?
                or coalesce(owners.name, buyers.owner) like ?
                or invoices.invoice_number like ?
            )
            """
            )
            params.extend([f"%{q}%"] * 6)
        if status in _cb_resolve_CUSTOMER_REIMBURSEMENT_STATUS_LABELS():
            clauses.append("customer_reimbursements.status = ?")
            params.append(status)
        else:
            status = ""
        # 0313：结算统计只读当前有效版本。同工单存在当前版本时，被替代的历史版本
        # （superseded）不得进列表、不得进合计；没有当前版本的存量行仍可见。
        clauses.append(
            "(customer_reimbursements.is_current or not exists ("
        "  select 1 from customer_reimbursements current_row"
        "  where current_row.service_order_id = customer_reimbursements.service_order_id"
        "    and current_row.is_current"
        "))"
        )
        if order_status == "open":
            clauses.append("service_orders.status != 'closed'")
        elif order_status == "closed":
            clauses.append("service_orders.status = 'closed'")
        if date_from:
            clauses.append("date(customer_reimbursements.created_at) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("date(customer_reimbursements.created_at) <= ?")
            params.append(date_to)
        rows = _cb_resolve_db()().execute(
            f"""
        select customer_reimbursements.*,
               service_orders.order_number,
               service_orders.client_name,
               service_orders.client_order_number,
               service_orders.country_code,
               service_orders.region_code,
               coalesce(owners.name, buyers.owner) as buyer_owner,
               coalesce(country_local.name, country_zh.name, service_orders.country_code) as country_name,
               coalesce(country_local.region_name, country_zh.region_name, service_orders.region_code) as region_name,
               creators.name as creator_name,
               reviewers.name as reviewer_name,
               invoices.invoice_number
        from customer_reimbursements
        join service_orders on service_orders.id = customer_reimbursements.service_order_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join owners on owners.id = buyers.owner_id
        left join users creators on creators.id = customer_reimbursements.created_by
        left join users reviewers on reviewers.id = customer_reimbursements.reviewed_by
        left join invoices on invoices.id = customer_reimbursements.invoice_id
        left join country_translations country_local
          on country_local.country_code = service_orders.country_code and country_local.language_code = ?
        left join country_translations country_zh
          on country_zh.country_code = service_orders.country_code and country_zh.language_code = 'zh-CN'
        where {" and ".join(clauses)}
        order by customer_reimbursements.created_at desc, customer_reimbursements.id desc
        """,
            [_cb_resolve_current_language()(), *params],
        ).fetchall()
        totals = {
            "labor_total": sum(float(row["labor_total"] or 0) for row in rows),
            "travel_total": sum(float(row["travel_total"] or 0) for row in rows),
            "mileage_total": sum(float(row["mileage_total"] or 0) for row in rows),
            "other_total": sum(float(row["other_total"] or 0) for row in rows),
            "total_amount": sum(float(row["total_amount"] or 0) for row in rows),
        }
        return _cb_resolve_render_template()(
            "customer_reimbursement_query.html",
            rows=rows,
            totals=totals,
            q=q,
            status=status,
            order_status=order_status,
            date_from=date_from,
            date_to=date_to,
            labels=_cb_resolve_CUSTOMER_REIMBURSEMENT_STATUS_LABELS(),
            order_ids=order_ids, sites=sites, order_options=options,
            site_options=[{"client_name": name} for name in sorted({row["client_name"] for row in options if row["client_name"]})],
        )



    AUDIT_LOG_PER_PAGE = 200

    @_cb_resolve_login_required()
    def audit_log_report():
        if not _cb_resolve_can_view_audit_logs()():
            _cb_resolve_abort()(403)
        # 已知业务类型/动作的中文标签；日志里出现但不在表中的值原样显示。
        entity_labels = {
            "service_order": "工单",
            "invoice": "发票",
            "service_report": "工作日报",
            "expense": "报销",
            "customer_reimbursement": "工单结算",
            "customer_reimbursement_attachment": "结算附件",
            "user": "用户",
            "database": "数据库",
            "menu_permissions": "权限设置",
            "ai_assistant": "智能助手",
            "employee_payment": "付款单",
            "bank_account": "银行账户",
        }
        action_labels = {
            "create": "创建",
            "update": "修改",
            "delete": "删除",
            "submit": "提交审核",
            "approve": "审核通过",
            "return": "退回",
            "void": "作废",
            "mark_paid": "核销",
            "unmark_paid": "取消核销",
            "status_change": "调整状态",
            "reimburse": "报销发放",
            "reset": "重置状态",
            "send": "发送邮件",
            "login": "登录",
            "sql": "执行 SQL",
            "query": "查询",
        }
        req = _cb_resolve_request()
        # 四个独立筛选维度：操作者 / 业务类型 / 对象 / 内容，外加动作与日期范围。
        operator = req.args.get("operator", "").strip()
        entity_type = req.args.get("entity_type", "").strip()
        object_q = req.args.get("object", "").strip()
        content = req.args.get("content", "").strip()
        action = req.args.get("action", "").strip()
        date_from = req.args.get("date_from", "")
        date_to = req.args.get("date_to", "")
        clauses = ["1 = 1"]
        params = []
        if operator:
            clauses.append("user_name = ?")
            params.append(operator)
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if object_q:
            clauses.append("(entity_label like ? or cast(entity_id as text) like ?)")
            params.extend([f"%{object_q}%", f"%{object_q}%"])
        if content:
            clauses.append("summary like ?")
            params.append(f"%{content}%")
        if action:
            clauses.append("action = ?")
            params.append(action)
        if date_from:
            clauses.append("date(created_at) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("date(created_at) <= ?")
            params.append(date_to)
        where = " and ".join(clauses)
        total = _cb_resolve_db()().execute(
            f"select count(*) as c from audit_logs where {where}", params
        ).fetchone()["c"]
        try:
            page = int(req.args.get("page", "1"))
        except (TypeError, ValueError):
            page = 1
        if page < 1:
            page = 1
        per_page = AUDIT_LOG_PER_PAGE
        total_pages = max(1, (int(total) + per_page - 1) // per_page)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * per_page
        rows = _cb_resolve_db()().execute(
            f"""
        select * from audit_logs
        where {where}
        order by datetime(created_at) desc, id desc
        limit ? offset ?
        """,
            params + [per_page, offset],
        ).fetchall()
        # 下拉选项来自日志中实际出现的值，避免写死枚举导致筛选"不起作用"。
        operator_options = [
            r["user_name"]
            for r in _cb_resolve_db()().execute(
                "select distinct user_name from audit_logs order by user_name"
            ).fetchall()
        ]
        entity_type_options = [
            r["entity_type"]
            for r in _cb_resolve_db()().execute(
                "select distinct entity_type from audit_logs order by entity_type"
            ).fetchall()
        ]
        action_options = [
            r["action"]
            for r in _cb_resolve_db()().execute(
                "select distinct action from audit_logs order by action"
            ).fetchall()
        ]
        return _cb_resolve_render_template()(
            "audit_logs.html",
            rows=rows,
            operator=operator,
            entity_type=entity_type,
            object_q=object_q,
            content=content,
            action=action,
            date_from=date_from,
            date_to=date_to,
            entity_labels=entity_labels,
            action_labels=action_labels,
            operator_options=operator_options,
            entity_type_options=entity_type_options,
            action_options=action_options,
            page=page,
            total_pages=total_pages,
            total=int(total),
            per_page=per_page,
        )


    app.add_url_rule('/reports/export-visible.xlsx', endpoint='export_visible_report', view_func=export_visible_report, methods=['POST'])
    app.add_url_rule('/reports/customer-reimbursements', endpoint='customer_reimbursement_query', view_func=customer_reimbursement_query, methods=['GET'])
    app.add_url_rule('/reports/audit-logs', endpoint='audit_log_report', view_func=audit_log_report, methods=['GET'])
    return {'export_visible_report': export_visible_report, 'customer_reimbursement_query': customer_reimbursement_query, 'audit_log_report': audit_log_report}
