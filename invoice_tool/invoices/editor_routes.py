"""Business routes with explicit current-app resolvers; no reverse import."""


def register_invoice_editor_routes(app, *,
    _cb_resolve_IntegrityError,
    _cb_resolve_abort,
    _cb_resolve_calculate_payment_due_date,
    _cb_resolve_can_create_invoice,
    _cb_resolve_claim_invoice_save_token,
    _cb_resolve_create_invoice_from_form,
    _cb_resolve_customer_reimbursement_invoice_items,
    _cb_resolve_date,
    _cb_resolve_db,
    _cb_resolve_flash,
    _cb_resolve_g,
    _cb_resolve_get_invoice_attachments,
    _cb_resolve_is_external_user,
    _cb_resolve_is_manager,
    _cb_resolve_load_invoice,
    _cb_resolve_log_action,
    _cb_resolve_login_required,
    _cb_resolve_next_invoice_number,
    _cb_resolve_payment_term_rows,
    _cb_resolve_redirect,
    _cb_resolve_render_template,
    _cb_resolve_request,
    _cb_resolve_require_customer_reimbursement,
    _cb_resolve_require_service_order,
    _cb_resolve_require_service_order_start_date,
    _cb_resolve_secrets,
    _cb_resolve_service_order_active_invoice,
    _cb_resolve_update_invoice_from_form,
    _cb_resolve_uploaded_attachments_from_request,
    _cb_resolve_url_for,
    _cb_resolve_validate_attachment_uploads,
):
    @_cb_resolve_login_required()
    def new_invoice():
        if not _cb_resolve_can_create_invoice()():
            _cb_resolve_abort()(403)
        requested_order_id = _cb_resolve_request().args.get("service_order_id", "")
        requested_reimbursement_id = _cb_resolve_request().args.get("customer_reimbursement_id", "")
        source_reimbursement = None
        source_order = None
        prefilled_items = []
        if _cb_resolve_request().method == "GET" and not requested_reimbursement_id.isdigit():
            _cb_resolve_flash()("请从审核通过的工单结算生成发票。", "error")
            if requested_order_id.isdigit():
                return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=int(requested_order_id)))
            return _cb_resolve_redirect()(_cb_resolve_url_for()("service_orders"))
        if _cb_resolve_request().method == "GET" and requested_order_id.isdigit():
            requested_order = _cb_resolve_require_service_order()(int(requested_order_id))
            start_date_redirect = _cb_resolve_require_service_order_start_date()(requested_order)
            if start_date_redirect:
                return start_date_redirect
        if _cb_resolve_request().method == "GET" and requested_reimbursement_id.isdigit():
            source_reimbursement, source_order = _cb_resolve_require_customer_reimbursement()(int(requested_reimbursement_id))
            start_date_redirect = _cb_resolve_require_service_order_start_date()(source_order)
            if start_date_redirect:
                return start_date_redirect
            if source_reimbursement["invoice_id"]:
                _cb_resolve_flash()("这份工单结算已经生成过发票。", "success")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("invoice_detail", invoice_id=source_reimbursement["invoice_id"]))
            linked_invoice = _cb_resolve_service_order_active_invoice()(source_order["id"])
            if linked_invoice:
                _cb_resolve_db()().execute(
                    "update customer_reimbursements set invoice_id = ? where id = ?",
                    (linked_invoice["id"], source_reimbursement["id"]),
                )
                _cb_resolve_db()().commit()
                _cb_resolve_flash()("这个工单已经有关联发票。", "success")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("invoice_detail", invoice_id=linked_invoice["id"]))
            if source_reimbursement["status"] != "approved":
                _cb_resolve_flash()("工单结算审核通过后才能生成发票。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=source_order["id"]))
            requested_order_id = str(source_order["id"])
            try:
                prefilled_items = _cb_resolve_customer_reimbursement_invoice_items()(source_reimbursement)
            except ValueError as error:
                _cb_resolve_flash()(str(error), "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("customer_reimbursement_form", order_id=source_order["id"]))
        if _cb_resolve_is_external_user()():
            if not _cb_resolve_g().user["client_id"]:
                _cb_resolve_flash()("请联系管理员绑定客户。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("dashboard"))
            clients_rows = _cb_resolve_db()().execute("select * from clients where id = ?", (_cb_resolve_g().user["client_id"],)).fetchall()
        else:
            clients_rows = _cb_resolve_db()().execute("select * from clients order by client_number").fetchall()
        if not clients_rows:
            _cb_resolve_flash()("请先创建一个客户。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("clients"))
        projects_rows = _cb_resolve_db()().execute(
            "select * from projects where project_type = 'invoice' and is_active = 1 order by name"
        ).fetchall()
        service_orders_rows = _cb_resolve_db()().execute("select * from service_orders where status != 'closed' order by created_at desc, id desc").fetchall()
        if not projects_rows:
            _cb_resolve_flash()("请先由经理或管理员维护项目。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("projects") if _cb_resolve_is_manager()() else _cb_resolve_url_for()("dashboard"))
        if _cb_resolve_request().method == "POST":
            save_token = _cb_resolve_request().form.get("save_token", "")
            if not _cb_resolve_claim_invoice_save_token()(save_token):
                existing = _cb_resolve_db()().execute(
                    "select invoice_id from invoice_save_tokens where token = ?",
                    (save_token,),
                ).fetchone()
                if existing and existing["invoice_id"]:
                    return _cb_resolve_redirect()(_cb_resolve_url_for()("edit_invoice", invoice_id=existing["invoice_id"]))
                _cb_resolve_flash()("该发票正在保存，请稍候。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("new_invoice"))
            uploads = _cb_resolve_uploaded_attachments_from_request()()
            if not _cb_resolve_validate_attachment_uploads()(uploads):
                _cb_resolve_db()().rollback()
                return _cb_resolve_redirect()(_cb_resolve_url_for()("new_invoice"))
            try:
                invoice_id = _cb_resolve_create_invoice_from_form()(save_token=save_token)
            except ValueError as error:
                _cb_resolve_db()().rollback()
                _cb_resolve_flash()(str(error), "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("new_invoice"))
            except _cb_resolve_IntegrityError():
                _cb_resolve_db()().rollback()
                _cb_resolve_flash()("保存发票时发生数据库冲突，请重新提交。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("new_invoice"))
            _cb_resolve_flash()("发票已生成。", "success")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("invoice_detail", invoice_id=invoice_id))
        today = _cb_resolve_date().today()
        selected_client_id = source_order["client_id"] if source_order else clients_rows[0]["id"]
        selected_client = next((row for row in clients_rows if row["id"] == selected_client_id), clients_rows[0])
        selected_term = _cb_resolve_db()().execute(
            "select * from payment_terms where id = ?",
            (selected_client["payment_term_id"],),
        ).fetchone()
        defaults = {
            "invoice_number": _cb_resolve_next_invoice_number()(),
            "issue_date": today.isoformat(),
            "due_date": _cb_resolve_calculate_payment_due_date()(today.isoformat(), selected_term).isoformat(),
            "service_order_id": int(requested_order_id) if requested_order_id.isdigit() else None,
            "customer_reimbursement_id": source_reimbursement["id"] if source_reimbursement else None,
            "client_id": source_order["client_id"] if source_order else None,
        }
        return _cb_resolve_render_template()(
            "invoice_form.html",
            clients=clients_rows,
            projects=projects_rows,
            service_orders=service_orders_rows,
            defaults=defaults,
            form_title="根据工单结算生成发票" if source_reimbursement else "新建发票",
            form_items=prefilled_items,
            is_edit=False,
            attachments=[],
            save_token=_cb_resolve_secrets().token_urlsafe(24),
            payment_terms=_cb_resolve_payment_term_rows()(),
        )


    @_cb_resolve_login_required()
    def edit_invoice(invoice_id):
        invoice, client, items = _cb_resolve_load_invoice()(invoice_id)
        if invoice["status"] != "draft":
            _cb_resolve_flash()("只有管理员调整为草稿的发票可以编辑。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("invoice_detail", invoice_id=invoice_id))
        if invoice["created_by"] != _cb_resolve_g().user["id"] and not _cb_resolve_is_manager()():
            _cb_resolve_abort()(403)
        if _cb_resolve_is_external_user()():
            clients_rows = _cb_resolve_db()().execute("select * from clients where id = ?", (_cb_resolve_g().user["client_id"],)).fetchall()
        else:
            clients_rows = _cb_resolve_db()().execute("select * from clients order by client_number").fetchall()
        projects_rows = _cb_resolve_db()().execute(
            """
        select * from projects
        where project_type = 'invoice' and (is_active = 1 or id in (
            select project_id from invoice_items where invoice_id = ?
        ))
        order by is_active desc, name
        """,
            (invoice_id,),
        ).fetchall()
        service_orders_rows = _cb_resolve_db()().execute("select * from service_orders where status != 'closed' or id = ? order by created_at desc, id desc", (invoice["service_order_id"] or 0,)).fetchall()
        if _cb_resolve_request().method == "POST":
            save_token = _cb_resolve_request().form.get("save_token", "")
            if not _cb_resolve_claim_invoice_save_token()(save_token, invoice_id):
                _cb_resolve_flash()("该发票已经保存，请勿重复提交。", "success")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("edit_invoice", invoice_id=invoice_id))
            uploads = _cb_resolve_uploaded_attachments_from_request()()
            if not _cb_resolve_validate_attachment_uploads()(uploads, invoice_id):
                _cb_resolve_db()().rollback()
                return _cb_resolve_redirect()(_cb_resolve_url_for()("edit_invoice", invoice_id=invoice_id))
            try:
                _cb_resolve_update_invoice_from_form()(invoice_id)
                _cb_resolve_log_action()("update", "invoice", invoice_id, invoice["invoice_number"], "修改发票内容")
            except ValueError as error:
                _cb_resolve_db()().rollback()
                _cb_resolve_flash()(str(error), "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("edit_invoice", invoice_id=invoice_id))
            except _cb_resolve_IntegrityError():
                _cb_resolve_db()().rollback()
                _cb_resolve_flash()("发票编号已经存在，请更换一个发票编号后再保存。", "error")
                return _cb_resolve_redirect()(_cb_resolve_url_for()("edit_invoice", invoice_id=invoice_id))
            _cb_resolve_db()().commit()
            _cb_resolve_flash()("发票已保存。", "success")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("invoice_detail", invoice_id=invoice_id))
        defaults = {
            "id": invoice["id"],
            "invoice_number": invoice["invoice_number"],
            "client_id": invoice["client_id"],
            "created_by": invoice["created_by"],
            "status": invoice["status"],
            "issue_date": invoice["issue_date"],
            "due_date": invoice["due_date"],
            "currency": invoice["currency"],
            "notes": invoice["notes"],
            "service_order_id": invoice["service_order_id"],
        }
        return _cb_resolve_render_template()(
            "invoice_form.html",
            clients=clients_rows,
            projects=projects_rows,
            service_orders=service_orders_rows,
            defaults=defaults,
            form_title="编辑发票",
            form_items=items,
            is_edit=True,
            attachments=_cb_resolve_get_invoice_attachments()(invoice_id),
            save_token=_cb_resolve_secrets().token_urlsafe(24),
            payment_terms=_cb_resolve_payment_term_rows()(),
        )


    app.add_url_rule('/invoices/new', endpoint='new_invoice', view_func=new_invoice, methods=['GET', 'POST'])
    app.add_url_rule('/invoices/<int:invoice_id>/edit', endpoint='edit_invoice', view_func=edit_invoice, methods=['GET', 'POST'])
    return {'new_invoice': new_invoice, 'edit_invoice': edit_invoice}
