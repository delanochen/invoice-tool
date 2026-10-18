"""Business routes with explicit current-app resolvers; no reverse import."""


def register_settlement_form_routes(app, *,
    _cb_resolve__merge_root_api,
    _cb_resolve_settlement_versions,
    _cb_resolve_abort,
    _cb_resolve_build_customer_reimbursement_pdf,
    _cb_resolve_build_simple_xlsx,
    _cb_resolve_can_create_invoice,
    _cb_resolve_can_manage_customer_reimbursement,
    _cb_resolve_can_view_customer_reimbursement,
    _cb_resolve_contract_rate_missing_summary,
    _cb_resolve_create_customer_reimbursement,
    _cb_resolve_customer_reimbursement_column_totals,
    _cb_resolve_customer_reimbursement_file_path,
    _cb_resolve_customer_reimbursement_gate_error,
    _cb_resolve_customer_reimbursement_item_expense_amount,
    _cb_resolve_customer_reimbursement_items,
    _cb_resolve_customer_reimbursement_items_from_form,
    _cb_resolve_customer_reimbursement_linked_invoice,
    _cb_resolve_customer_reimbursement_lodging_range,
    _cb_resolve_customer_reimbursement_pending_sources,
    _cb_resolve_customer_reimbursement_totals,
    _cb_resolve_customer_reimbursement_workspace_data,
    _cb_resolve_db,
    _cb_resolve_deliver_customer_reimbursement_email,
    _cb_resolve_email_delivery_summary,
    _cb_resolve_ensure_customer_reimbursement_pdf_file,
    _cb_resolve_ensure_customer_reimbursement_pdf_record,
    _cb_resolve_excessive_following_mileage_rows,
    _cb_resolve_flash,
    _cb_resolve_g,
    _cb_resolve_get_customer_reimbursement_attachments,
    _cb_resolve_json,
    _cb_resolve_latest_customer_reimbursement,
    _cb_resolve_log_action,
    _cb_resolve_login_required,
    _cb_resolve_money,
    _cb_resolve_money_decimal,
    _cb_resolve_notify_role,
    _cb_resolve_now,
    _cb_resolve_re,
    _cb_resolve_redirect,
    _cb_resolve_remove_customer_reimbursement_pdf,
    _cb_resolve_render_template,
    _cb_resolve_request,
    _cb_resolve_require_customer_reimbursement,
    _cb_resolve_require_service_order,
    _cb_resolve_require_service_order_start_date,
    _cb_resolve_resolve_contract_rates,
    _cb_resolve_save_customer_reimbursement_items,
    _cb_resolve_save_customer_reimbursement_uploads,
    _cb_resolve_send_file,
    _cb_resolve_service_order_active_invoice,
    _cb_resolve_to_float,
    _cb_resolve_update_customer_reimbursement_totals,
    _cb_resolve_url_for,
):
    @_cb_resolve_login_required()
    def customer_reimbursement_form(order_id):
        if not _cb_resolve_can_view_customer_reimbursement()():
            _cb_resolve_abort()(403)
        order = _cb_resolve_require_service_order()(order_id)
        if _cb_resolve_request().method == "POST" and not _cb_resolve_can_manage_customer_reimbursement()():
            _cb_resolve_abort()(403)
        start_date_redirect = _cb_resolve_require_service_order_start_date()(order)
        if start_date_redirect:
            return start_date_redirect
        reimbursement = _cb_resolve_latest_customer_reimbursement()(order_id)
        if not reimbursement:
            if not _cb_resolve_can_manage_customer_reimbursement()():
                _cb_resolve_flash()("该工单还没有工单结算。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("service_order_detail", order_id=order_id))
            try:
                # Create the draft immediately. Expense selection now lives in the
                # settlement workspace tabs instead of a separate pre-review page.
                reimbursement = _cb_resolve_create_customer_reimbursement()(order, "manual_review")
                _cb_resolve_db()().commit()
            except ValueError as error:
                _cb_resolve_db()().rollback()
                _cb_resolve_flash()(str(error), "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("service_order_detail", order_id=order_id))
        else:
            reimbursement = _cb_resolve_ensure_customer_reimbursement_pdf_record()(reimbursement, order)
            # 方案 A：不再在 GET 期自动重合并覆盖快照（那会导致保存时一闪而过、
            # 且可能把同人异日报销重复计入）。改为只读比对未计入来源，由横幅呈现。
            _cb_resolve_db()().commit()
        # 版本定位：GET 带 version_id 查看同系列任一版本（历史版本只读展示）；
        # POST 带 version_id（表单隐藏域）时作用于该版本——修订版草稿
        # （is_current=false）必须能保存 / 提交 / 审核，否则「沿用现有保存、
        # 确认、审核流程」落空。校验：同工单 + 同系列，跨系列一律不认。
        requested_version_id = (
            _cb_resolve_request().args.get("version_id", type=int) if _cb_resolve_request().method == "GET"
            else _cb_resolve_request().form.get("version_id", type=int)
        )
        if requested_version_id and requested_version_id != reimbursement["id"]:
            candidate = _cb_resolve_db()().execute(
                "select * from customer_reimbursements where id = ? and service_order_id = ?",
                (requested_version_id, order_id),
            ).fetchone()
            if candidate and candidate["series_id"] == reimbursement["series_id"]:
                reimbursement = candidate
        # 保存 / 提交后回到当前正在编辑的版本视图，避免修订版草稿被弹回 V1。
        form_url = _cb_resolve_url_for()("customer_reimbursement_form", order_id=order_id)
        if not reimbursement["is_current"]:
            form_url = _cb_resolve_url_for()(
                "customer_reimbursement_form", order_id=order_id, version_id=reimbursement["id"]
            )
        if _cb_resolve_request().method == "POST":
            try:
                action = _cb_resolve_request().form.get("action", "save")
                if reimbursement["status"] not in {"draft", "returned"}:
                    if action == "generate_pdf":
                        path = _cb_resolve_build_customer_reimbursement_pdf()(
                            reimbursement,
                            order,
                            _cb_resolve_customer_reimbursement_items()(reimbursement["id"]),
                        )
                        return _cb_resolve_send_file()(path, as_attachment=True, download_name=reimbursement["file_name"])
                    if action == "send_email":
                        if reimbursement["status"] != "approved":
                            raise ValueError("工单结算审核通过后才能发送邮件。")
                        gate = _cb_resolve_customer_reimbursement_gate_error()(reimbursement)
                        if gate:
                            _cb_resolve_flash()(gate[1], "error")
                            return _cb_resolve_redirect()(form_url)
                        recipient = _cb_resolve_deliver_customer_reimbursement_email()(reimbursement, order)
                        _cb_resolve_db()().commit()
                        _cb_resolve_flash()(f"工单结算已发送至 {recipient}。", "success")
                        return _cb_resolve_redirect()(form_url)
                    if action == "generate_invoice":
                        if reimbursement["status"] != "approved":
                            raise ValueError("工单结算审核通过后才能生成发票。")
                        if not _cb_resolve_can_create_invoice()():
                            _cb_resolve_abort()(403)
                        gate = _cb_resolve_customer_reimbursement_gate_error()(reimbursement)
                        if gate:
                            _cb_resolve_flash()(gate[1], "error")
                            return _cb_resolve_redirect()(form_url)
                        linked_invoice = _cb_resolve_service_order_active_invoice()(order_id)
                        if linked_invoice:
                            if not reimbursement["invoice_id"]:
                                _cb_resolve_db()().execute(
                                    "update customer_reimbursements set invoice_id = ? where id = ?",
                                    (linked_invoice["id"], reimbursement["id"]),
                                )
                                _cb_resolve_db()().commit()
                            return _cb_resolve_redirect()(_cb_resolve_url_for()("invoice_detail", invoice_id=linked_invoice["id"]))
                        return _cb_resolve_redirect()(
                            _cb_resolve_url_for()(
                                "new_invoice",
                                service_order_id=order["id"],
                                customer_reimbursement_id=reimbursement["id"],
                            )
                        )
                    raise ValueError("只有保存未提交或已退回的工单结算可以修改。")
                if action == "submit":
                    gate = _cb_resolve_customer_reimbursement_gate_error()(reimbursement)
                    if gate:
                        _cb_resolve_flash()(gate[1], "error")
                        return _cb_resolve_redirect()(form_url)
                rows = _cb_resolve_customer_reimbursement_items_from_form()(order_id)
                _cb_resolve_save_customer_reimbursement_items()(reimbursement["id"], rows)
                totals = _cb_resolve_update_customer_reimbursement_totals()(reimbursement["id"], rows)
                _cb_resolve_save_customer_reimbursement_uploads()(reimbursement["id"])
                next_status = "submitted" if action == "submit" else reimbursement["status"]
                _cb_resolve_db()().execute(
                    """
                update customer_reimbursements
                set status = ?, return_reason = null,
                    expense_transfer_cutoff_at = case
                        when ? = 'submitted' then coalesce(expense_transfer_cutoff_at, ?)
                        else expense_transfer_cutoff_at
                    end
                where id = ?
                """,
                    (next_status, next_status, _cb_resolve_now()(), reimbursement["id"]),
                )
                reimbursement = _cb_resolve_db()().execute("select * from customer_reimbursements where id = ?", (reimbursement["id"],)).fetchone()
                _cb_resolve_remove_customer_reimbursement_pdf()(reimbursement)
                if action == "generate_pdf":
                    _cb_resolve_build_customer_reimbursement_pdf()(reimbursement, order, _cb_resolve_customer_reimbursement_items()(reimbursement["id"]))
                _cb_resolve_log_action()(
                    "update",
                    "customer_reimbursement",
                    reimbursement["id"],
                    reimbursement["file_name"],
                    f"金额：{_cb_resolve_money()(totals['total_amount'])}",
                )
                _cb_resolve_db()().commit()
                if action == "submit":
                    _cb_resolve_notify_role()(
                        ["admin", "manager"],
                        "新工单结算待审核",
                        (
                            f"{_cb_resolve_g().user['name']}提交了工单 {order['order_number']} 的工单结算，"
                        f"金额 {_cb_resolve_money()(totals['total_amount'])}。"
                        ),
                        form_url,
                        exclude_user_ids={_cb_resolve_g().user["id"]},
                    )
                    _cb_resolve_log_action()(
                        "submit",
                        "customer_reimbursement",
                        reimbursement["id"],
                        reimbursement["file_name"],
                        f"金额：{_cb_resolve_money()(totals['total_amount'])}",
                    )
                    _cb_resolve_db()().commit()
                    _cb_resolve_flash()("工单结算已提交经理审核。", "success")
                    return _cb_resolve_redirect()(form_url)
                if action == "generate_pdf":
                    return _cb_resolve_send_file()(
                        _cb_resolve_customer_reimbursement_file_path()(reimbursement),
                        as_attachment=True,
                        download_name=reimbursement["file_name"],
                    )
                if action == "send_email":
                    if reimbursement["status"] != "approved":
                        raise ValueError("工单结算审核通过后才能发送邮件。")
                    try:
                        recipient = _cb_resolve_deliver_customer_reimbursement_email()(reimbursement, order)
                    except (ValueError, RuntimeError) as error:
                        _cb_resolve_flash()(str(error), "error")
                        return _cb_resolve_redirect()(form_url)
                    _cb_resolve_db()().commit()
                    _cb_resolve_flash()(f"工单结算已发送至 {recipient}。", "success")
                    return _cb_resolve_redirect()(form_url)
                if action == "generate_invoice":
                    if reimbursement["status"] != "approved":
                        raise ValueError("工单结算审核通过后才能生成发票。")
                    if not _cb_resolve_can_create_invoice()():
                        _cb_resolve_abort()(403)
                    linked_invoice = _cb_resolve_service_order_active_invoice()(order_id)
                    if linked_invoice:
                        if not reimbursement["invoice_id"]:
                            _cb_resolve_db()().execute(
                                "update customer_reimbursements set invoice_id = ? where id = ?",
                                (linked_invoice["id"], reimbursement["id"]),
                            )
                            _cb_resolve_db()().commit()
                        return _cb_resolve_redirect()(_cb_resolve_url_for()("invoice_detail", invoice_id=linked_invoice["id"]))
                    return _cb_resolve_redirect()(
                        _cb_resolve_url_for()(
                            "new_invoice",
                            service_order_id=order["id"],
                            customer_reimbursement_id=reimbursement["id"],
                        )
                    )
                _cb_resolve_flash()("工单结算已保存。", "success")
                return _cb_resolve_redirect()(form_url)
            except ValueError as error:
                _cb_resolve_db()().rollback()
                _cb_resolve_flash()(str(error), "error")
                return _cb_resolve_redirect()(form_url)
        items = _cb_resolve_customer_reimbursement_items()(reimbursement["id"])
        items = [dict(item) for item in items]
        for item in items:
            try:
                item["expense_sources"] = _cb_resolve_json().loads(item.get("auto_expense_sources") or '{}')
            except (ValueError, TypeError):
                item["expense_sources"] = {}
        # 为每个来源补充附件信息和报销单链接
        all_source_keys = []
        for item in items:
            for field_sources in item["expense_sources"].values():
                for source in field_sources:
                    if source.get("expense_id") and source.get("line_key"):
                        all_source_keys.append((source["expense_id"], source["line_key"]))
        source_attachments = {}
        if all_source_keys:
            placeholders = ",".join("(?,?)" for _ in all_source_keys)
            params = [val for pair in all_source_keys for val in pair]
            attachment_rows = _cb_resolve_db()().execute(
                f"select id, expense_id, expense_item_key, original_filename, content_type from expense_attachments "
            f"where (expense_id, expense_item_key) in ({placeholders})",
                params,
            ).fetchall()
            for att in attachment_rows:
                key = (att["expense_id"], att["expense_item_key"])
                source_attachments.setdefault(key, []).append({
                    "id": att["id"],
                    "original_filename": att["original_filename"],
                    "content_type": att["content_type"],
                })
        for item in items:
            for field_sources in item["expense_sources"].values():
                for source in field_sources:
                    source["expense_url"] = _cb_resolve_url_for()("expense_detail", expense_id=source["expense_id"]) if source.get("expense_id") else None
                    key = (source.get("expense_id"), source.get("line_key"))
                    source["attachments"] = source_attachments.get(key, [])

        linked_invoice = _cb_resolve_customer_reimbursement_linked_invoice()(reimbursement, order_id)
        if linked_invoice and reimbursement["invoice_id"] != linked_invoice["id"]:
            _cb_resolve_db()().execute(
                "update customer_reimbursements set invoice_id = ? where id = ?",
                (linked_invoice["id"], reimbursement["id"]),
            )
            _cb_resolve_db()().commit()
            reimbursement = _cb_resolve_db()().execute("select * from customer_reimbursements where id = ?", (reimbursement["id"],)).fetchone()
        reviewer = _cb_resolve_db()().execute(
            "select name from users where id = ?",
            (reimbursement["reviewed_by"],),
        ).fetchone() if reimbursement["reviewed_by"] else None
        # 客户费率一律来自合同费率版本：给 JS 的兜底单价按工单开始日期解析（缺失传 0），
        # 行级单价已随明细行的 data-*-rate 传入；缺失项在页面顶部横幅醒目提示
        js_rates, _ = _cb_resolve_resolve_contract_rates()(order_id, order["start_date"])
        missing_rate_summary = _cb_resolve_contract_rate_missing_summary()(
            order_id, [item.get("project_date") for item in items] or [order["start_date"]]
        )
        workspace = _cb_resolve_customer_reimbursement_workspace_data()(order_id, reimbursement["id"], items)
        _form_totals = dict(_cb_resolve_customer_reimbursement_totals()(
            items,
            reimbursement["mro_supplies_total"],
            reimbursement["rental_fuel_total"],
        ))
        # 报价单折扣快照：工单结算展示 折扣前应收 / 折扣 / 最终应收
        _form_totals["gross_amount"] = float(reimbursement["gross_amount"] or 0)
        _form_totals["discount_amount"] = float(reimbursement["discount_amount"] or 0)
        # 版本列表 / 在处理中的修订版（settlement_revisions 模块函数第一个参数是 api dict）
        version_rows = _cb_resolve_settlement_versions()(_cb_resolve__merge_root_api()(), order_id)
        pending_revision = next(
            (
                version for version in version_rows
                if not version["is_current"] and version["status"] != "superseded"
            ),
            None,
        )
        return _cb_resolve_render_template()(
            "customer_reimbursement_form.html",
            order=order,
            reimbursement=reimbursement,
            items=items,
            totals=_form_totals,
            js_rates={
                "standard": js_rates["standard_rate"],
                "transport": js_rates["transport_rate"],
                "publicTransport": js_rates["public_transport_rate"],
                "overtime": js_rates["overtime_rate"],
                "holiday": js_rates["holiday_rate"],
                "mileage": js_rates["mileage_rate"],
            },
            missing_rate_summary=missing_rate_summary,
            attachments=_cb_resolve_get_customer_reimbursement_attachments()(reimbursement["id"]),
            settlement_versions=version_rows,
            pending_revision=pending_revision,
            linked_invoice=linked_invoice,
            reviewer=reviewer,
            email_delivery=_cb_resolve_email_delivery_summary()("customer_reimbursement", reimbursement["id"]),
            excessive_following_mileage=_cb_resolve_excessive_following_mileage_rows()(order_id),
            lodging_range=_cb_resolve_customer_reimbursement_lodging_range()(order_id),
            column_totals=_cb_resolve_customer_reimbursement_column_totals()(items),
            # 可编辑 = 有管理权限 + 状态是草稿/已退回。修订版草稿 is_current=false
            # 但必须能编辑（「沿用现有保存、确认、审核流程」）；被替代的历史版本
            # 状态是 superseded，天然落在只读分支，不需要再按 is_current 拦。
            can_edit=(
                _cb_resolve_can_manage_customer_reimbursement()()
                and reimbursement["status"] in {"draft", "returned"}
            ),
            pending_sources=_cb_resolve_customer_reimbursement_pending_sources()(reimbursement),
            workspace=workspace,
        )



    @_cb_resolve_login_required()
    def download_customer_reimbursement(reimbursement_id):
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(reimbursement_id)
        reimbursement, path = _cb_resolve_ensure_customer_reimbursement_pdf_file()(reimbursement, order)
        _cb_resolve_db()().commit()
        return _cb_resolve_send_file()(path, as_attachment=True, download_name=reimbursement["file_name"])


    @_cb_resolve_login_required()
    def download_customer_reimbursement_excel(reimbursement_id):
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(reimbursement_id)
        items = _cb_resolve_customer_reimbursement_items()(reimbursement_id)
        headers = ["序号", "姓名", "项目日期", "标准工时", "交通工时", "公共交通工时", "加班工时", "假期工时", "人工费", "住宿费", "机票费", "行李费", "租车费", "燃油费", "停车费", "出租车费", "里程费", "其他", "合计"]
        rows = []
        for index, item in enumerate(items, 1):
            item = dict(item)
            expenses = [_cb_resolve_customer_reimbursement_item_expense_amount()(item, key)
                        for key in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi")]
            rows.append([index, item["worker_name"], item["project_date"], item["standard_hours"],
                         item["transport_hours"], item["public_transport_hours"], item["overtime_hours"], item["holiday_hours"],
                         item["labor_total"], *expenses, item["mileage_total"],
                         _cb_resolve_customer_reimbursement_item_expense_amount()(item, "other"), item["total"]])
        # 合计行：逐列求和；末列取快照 total_amount（租车油费不在明细行，已计入合计）。
        total_row = ["合计", "", "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        for item in items:
            total_row[3] += _cb_resolve_to_float()(item["standard_hours"])
            total_row[4] += _cb_resolve_to_float()(item["transport_hours"])
            total_row[5] += _cb_resolve_to_float()(item["public_transport_hours"])
            total_row[6] += _cb_resolve_to_float()(item["overtime_hours"])
            total_row[7] += _cb_resolve_to_float()(item["holiday_hours"])
            total_row[8] += float(_cb_resolve_money_decimal()(item["labor_total"]))
            for offset, key in enumerate(("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi")):
                total_row[9 + offset] += float(_cb_resolve_money_decimal()(_cb_resolve_customer_reimbursement_item_expense_amount()(item, key)))
            total_row[16] += float(_cb_resolve_money_decimal()(item["mileage_total"]))
            total_row[17] += float(_cb_resolve_money_decimal()(_cb_resolve_customer_reimbursement_item_expense_amount()(item, "other")))
        total_row[18] = float(_cb_resolve_money_decimal()(reimbursement["total_amount"]))
        rows.append(total_row)
        rows.append([
            "说明：租车油费不在明细行，已计入合计；MRO 耗材已计入「其他」列；合计为工单结算快照总额。",
        ] + [""] * 18)
        workbook = _cb_resolve_build_simple_xlsx()(headers, rows, sheet_name="工单结算")
        return _cb_resolve_send_file()(workbook, as_attachment=True, download_name="_".join(_cb_resolve_re().sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(value)).strip(" .") for value in (order["order_number"], order["client_order_number"], "工单结算.xlsx") if value), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


    @_cb_resolve_login_required()
    def preview_customer_reimbursement(reimbursement_id):
        reimbursement, order = _cb_resolve_require_customer_reimbursement()(reimbursement_id)
        reimbursement = _cb_resolve_ensure_customer_reimbursement_pdf_record()(reimbursement, order)
        path = _cb_resolve_build_customer_reimbursement_pdf()(
            reimbursement,
            order,
            _cb_resolve_customer_reimbursement_items()(reimbursement_id),
        )
        _cb_resolve_db()().commit()
        return _cb_resolve_send_file()(
            path,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=reimbursement["file_name"],
            conditional=True,
        )


    app.add_url_rule('/service-orders/<int:order_id>/customer-reimbursement', endpoint='customer_reimbursement_form', view_func=customer_reimbursement_form, methods=['GET', 'POST'])
    app.add_url_rule('/customer-reimbursements/<int:reimbursement_id>/download', endpoint='download_customer_reimbursement', view_func=download_customer_reimbursement, methods=['GET'])
    app.add_url_rule('/customer-reimbursements/<int:reimbursement_id>/download.xlsx', endpoint='download_customer_reimbursement_excel', view_func=download_customer_reimbursement_excel, methods=['GET'])
    app.add_url_rule('/customer-reimbursements/<int:reimbursement_id>/preview', endpoint='preview_customer_reimbursement', view_func=preview_customer_reimbursement, methods=['GET'])
    return {'customer_reimbursement_form': customer_reimbursement_form, 'download_customer_reimbursement': download_customer_reimbursement, 'download_customer_reimbursement_excel': download_customer_reimbursement_excel, 'preview_customer_reimbursement': preview_customer_reimbursement}
