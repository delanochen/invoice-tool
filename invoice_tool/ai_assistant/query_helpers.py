"""Legacy-compatible assistant_helpers helpers; explicit runtime dependencies."""

def ai_normalize_status(domain, value):
    raw = str(value or "").strip().lower()
    aliases = {
        "service_orders": {"进行中": "open", "未关闭": "open", "open": "open", "已关闭": "closed", "closed": "closed"},
        "expenses": {
            "草稿": "draft", "保存未提交": "draft", "draft": "draft",
            "待审核": "submitted", "待经理审核": "submitted", "submitted": "submitted",
            "已退回": "returned", "returned": "returned", "已通过": "approved", "approved": "approved",
            "待付款": "pending", "未付款": "pending", "待报销": "pending", "pending": "pending",
            "已付款": "paid", "已报销": "paid", "paid": "paid",
        },
        "invoices": {
            "草稿": "draft", "保存未提交": "draft", "draft": "draft",
            "待审核": "submitted", "待经理审核": "submitted", "submitted": "submitted",
            "已退回": "returned", "returned": "returned", "已完成": "completed", "completed": "completed",
            "作废": "void", "void": "void",
        },
        "settlements": {
            "草稿": "draft", "保存未提交": "draft", "draft": "draft",
            "待审核": "submitted", "待经理审核": "submitted", "submitted": "submitted",
            "已退回": "returned", "returned": "returned", "已通过": "approved", "approved": "approved",
        },
    }
    return aliases.get(domain, {}).get(raw, raw)


def ai_search_business_records(arguments, *, AI_SEARCH_DOMAINS, ai_normalize_status, ai_search_like, can_view_customer_reimbursement, can_view_invoices, db, expense_access_filter, has_action_permission, service_order_access_filters, url_for):
    if not isinstance(arguments, dict):
        return {"error": "查询参数格式不正确。"}
    domain = str(arguments.get("domain") or "").strip()
    if domain not in AI_SEARCH_DOMAINS:
        return {"error": "不支持的数据类型。"}
    query = str(arguments.get("query") or "").strip()
    status = ai_normalize_status(domain, arguments.get("status"))
    date_from = str(arguments.get("date_from") or "").strip()
    date_to = str(arguments.get("date_to") or "").strip()
    clauses, params = [], []

    if domain == "expenses":
        if not has_action_permission("expenses", "view"):
            return {"error": "当前用户没有查看员工报销的权限。"}
        access_clause, access_params = expense_access_filter()
        clauses.append(access_clause)
        params.extend(access_params)
        if query:
            clauses.append("(expenses.expense_number like ? or expenses.project like ? or service_orders.order_number like ?)")
            params.extend([ai_search_like(query)] * 3)
        if status:
            clauses.append("(expenses.status = ? or expenses.payout_status = ?)")
            params.extend([status, status])
        if date_from:
            clauses.append("expenses.expense_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("expenses.expense_date <= ?")
            params.append(date_to)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""
            select expenses.id, expenses.expense_number, expenses.expense_date, expenses.project,
                   expenses.amount, expenses.currency, expenses.status, expenses.payout_status,
                   users.name as submitter, beneficiaries.name as beneficiary, service_orders.order_number
            from expenses join service_orders on service_orders.id = expenses.service_order_id
            join users on users.id = expenses.created_by
            left join users as beneficiaries on beneficiaries.id = coalesce(expenses.beneficiary_id, expenses.created_by)
            where {where} order by expenses.expense_date desc, expenses.id desc limit 25
            """,
            params,
        ).fetchall()
        total = db().execute(
            f"""select count(*) as count, coalesce(sum(expenses.amount), 0) as amount
                from expenses join service_orders on service_orders.id = expenses.service_order_id
                where {where}""",
            params,
        ).fetchone()
        return {
            "summary": {"count": total["count"], "amount": total["amount"]},
            "records": [dict(row) | {"url": url_for("expense_detail", expense_id=row["id"], _external=True)} for row in rows],
        }

    if domain == "service_orders":
        if not has_action_permission("service_orders", "view"):
            return {"error": "当前用户没有查看工单的权限。"}
        access_clauses, access_params = service_order_access_filters("service_orders")
        clauses.extend(access_clauses)
        params.extend(access_params)
        if query:
            clauses.append("(order_number like ? or client_order_number like ? or client_name like ? or site_address like ?)")
            params.extend([ai_search_like(query)] * 4)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if date_from:
            clauses.append("coalesce(start_date, created_at) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("coalesce(start_date, created_at) <= ?")
            params.append(date_to)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""select id, order_number, client_order_number, client_name, site_address, status, start_date
                from service_orders where {where}
                order by coalesce(start_date, created_at) desc, id desc limit 25""",
            params,
        ).fetchall()
        total = db().execute(f"select count(*) as count from service_orders where {where}", params).fetchone()
        return {"count": total["count"], "records": [dict(row) | {"url": url_for("service_order_detail", order_id=row["id"], _external=True)} for row in rows]}

    if domain == "sites":
        if not has_action_permission("buyers", "view"):
            return {"error": "当前用户没有查看站点的权限。"}
        if query:
            clauses.append("(buyer_number like ? or name like ? or detailed_address like ?)")
            params.extend([ai_search_like(query)] * 3)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""select id, buyer_number, name, detailed_address, latitude, longitude, email
                from buyers where {where} order by buyer_number limit 25""",
            params,
        ).fetchall()
        total = db().execute(f"select count(*) as count from buyers where {where}", params).fetchone()
        return {"count": total["count"], "records": [dict(row) | {"url": url_for("buyers", _external=True)} for row in rows]}

    if domain == "invoices":
        if not can_view_invoices():
            return {"error": "当前用户没有查看发票的权限。"}
        if query:
            clauses.append("(invoices.invoice_number like ? or clients.name like ? or service_orders.order_number like ?)")
            params.extend([ai_search_like(query)] * 3)
        if status in {"待核销", "unpaid"}:
            clauses.append("invoices.paid_at is null")
        elif status in {"已核销", "paid"}:
            clauses.append("invoices.paid_at is not null")
        elif status:
            clauses.append("invoices.status = ?")
            params.append(status)
        if date_from:
            clauses.append("invoices.issue_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("invoices.issue_date <= ?")
            params.append(date_to)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""
            select invoices.id, invoices.invoice_number, invoices.issue_date, invoices.due_date,
                   invoices.status, invoices.paid_at, clients.name as client_name,
                   service_orders.order_number, coalesce(sum(invoice_items.amount), 0) as amount
            from invoices join clients on clients.id = invoices.client_id
            left join service_orders on service_orders.id = invoices.service_order_id
            left join invoice_items on invoice_items.invoice_id = invoices.id
            where {where} group by invoices.id, clients.id, service_orders.id
            order by invoices.issue_date desc, invoices.id desc limit 25
            """,
            params,
        ).fetchall()
        total = db().execute(
            f"""select count(distinct invoices.id) as count, coalesce(sum(invoice_items.amount), 0) as amount
                from invoices join clients on clients.id = invoices.client_id
                left join service_orders on service_orders.id = invoices.service_order_id
                left join invoice_items on invoice_items.invoice_id = invoices.id
                where {where}""",
            params,
        ).fetchone()
        return {"summary": {"count": total["count"], "amount": total["amount"]}, "records": [dict(row) | {"url": url_for("invoice_detail", invoice_id=row["id"], _external=True)} for row in rows]}

    if domain == "daily_reports":
        if not has_action_permission("service_reports", "view"):
            return {"error": "当前用户没有查看工作日报的权限。"}
        access_clauses, access_params = service_order_access_filters("service_orders")
        clauses.extend(access_clauses)
        params.extend(access_params)
        if query:
            clauses.append("(service_orders.order_number like ? or service_orders.client_order_number like ? or service_reports.service_description like ?)")
            params.extend([ai_search_like(query)] * 3)
        if date_from:
            clauses.append("coalesce(service_reports.actual_work_date, service_reports.report_date) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("coalesce(service_reports.actual_work_date, service_reports.report_date) <= ?")
            params.append(date_to)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""
            select service_reports.id, service_reports.report_date, service_reports.actual_work_date,
                   service_reports.total_service_hours, service_reports.service_description,
                   service_orders.id as order_id, service_orders.order_number, service_orders.client_order_number
            from service_reports join service_orders on service_orders.id = service_reports.service_order_id
            where {where} order by coalesce(actual_work_date, report_date) desc, service_reports.id desc limit 25
            """,
            params,
        ).fetchall()
        total = db().execute(
            f"""select count(*) as count, coalesce(sum(service_reports.total_service_hours), 0) as service_hours
                from service_reports join service_orders on service_orders.id = service_reports.service_order_id
                where {where}""",
            params,
        ).fetchone()
        return {"summary": {"count": total["count"], "service_hours": total["service_hours"]}, "records": [dict(row) | {"url": url_for("service_order_detail", order_id=row["order_id"], _external=True)} for row in rows]}

    if domain == "settlements":
        if not can_view_customer_reimbursement():
            return {"error": "当前用户没有查看工单结算的权限。"}
        if query:
            clauses.append("(service_orders.order_number like ? or service_orders.client_order_number like ? or service_orders.client_name like ?)")
            params.extend([ai_search_like(query)] * 3)
        if status:
            clauses.append("customer_reimbursements.status = ?")
            params.append(status)
        where = " and ".join(clauses) if clauses else "1 = 1"
        rows = db().execute(
            f"""
            select customer_reimbursements.id, customer_reimbursements.status,
                   customer_reimbursements.total_amount, customer_reimbursements.created_at,
                   service_orders.id as order_id, service_orders.order_number,
                   service_orders.client_order_number, service_orders.client_name
            from customer_reimbursements
            join service_orders on service_orders.id = customer_reimbursements.service_order_id
            where {where} order by customer_reimbursements.created_at desc limit 25
            """,
            params,
        ).fetchall()
        total = db().execute(
            f"""select count(*) as count, coalesce(sum(customer_reimbursements.total_amount), 0) as amount
                from customer_reimbursements
                join service_orders on service_orders.id = customer_reimbursements.service_order_id
                where {where}""",
            params,
        ).fetchone()
        return {"summary": {"count": total["count"], "amount": total["amount"]}, "records": [dict(row) | {"url": url_for("customer_reimbursement_form", order_id=row["order_id"], _external=True)} for row in rows]}

    if not has_action_permission("knowledge_base", "view"):
        return {"error": "当前用户没有查看知识库的权限。"}
    if query:
        clauses.append("(title like ? or category like ? or description like ? or original_filename like ? or search_text like ?)")
        params.extend([ai_search_like(query)] * 5)
    where = " and ".join(clauses) if clauses else "1 = 1"
    rows = db().execute(
        f"""select id, title, category, description, original_filename,
                   substr(search_text, 1, 1200) as text_excerpt, updated_at
            from knowledge_documents where {where}
            order by is_pinned desc, updated_at desc limit 15""",
        params,
    ).fetchall()
    total = db().execute(f"select count(*) as count from knowledge_documents where {where}", params).fetchone()
    return {"count": total["count"], "records": [dict(row) | {"url": url_for("preview_knowledge_document", document_id=row["id"], _external=True)} for row in rows]}


def deepseek_api_error_message(status_code, response_body, *, json):
    api_message = ""
    try:
        error_payload = json.loads(response_body or "{}")
        api_error = error_payload.get("error") if isinstance(error_payload, dict) else None
        if isinstance(api_error, dict):
            api_message = str(api_error.get("message") or "").strip()
        elif api_error:
            api_message = str(api_error).strip()
    except json.JSONDecodeError:
        pass
    friendly = {
        400: "请求参数不被 DeepSeek 接受",
        401: "API Key 无效或已失效",
        402: "DeepSeek 账户余额不足",
        403: "当前 API Key 没有调用权限",
        429: "DeepSeek 请求过于频繁，请稍后重试",
        500: "DeepSeek 服务内部错误",
        503: "DeepSeek 服务暂时繁忙",
    }.get(status_code, f"DeepSeek API 返回 HTTP {status_code}")
    return f"{friendly}：{api_message}" if api_message else friendly


def call_deepseek_chat(messages, settings, include_tools=True, max_tokens=1600, *, AI_ASSISTANT_TOOLS, HTTPError, Request, URLError, deepseek_api_error_message, json, urlopen):
    request_payload = {
        "model": settings["model"],
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if include_tools:
        request_payload["tools"] = AI_ASSISTANT_TOOLS
        request_payload["tool_choice"] = "auto"
    payload = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    _base = (settings.get("base_url") or "https://api.deepseek.com").rstrip("/")
    api_request = Request(
        _base + "/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(api_request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(deepseek_api_error_message(error.code, detail)) from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"服务器无法连接 DeepSeek API：{error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("DeepSeek API 返回了无法解析的数据。") from error
    if not isinstance(result, dict):
        raise RuntimeError("DeepSeek API 没有返回有效的 JSON 对象。")
    choices = result.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise RuntimeError("DeepSeek API 没有返回有效回答。")
    return choices[0]["message"]
