"""Legacy-compatible invoice_create helpers; explicit runtime dependencies."""

def create_invoice_from_form(save_token=None, *, IntegrityError, abort, can_access_client, copy_customer_reimbursement_attachments_to_invoice, copy_mileage_proofs_to_invoice, db, finish_invoice_save_token, g, invoice_items_from_form, invoice_number_exists, invoice_totals, log_action, money, next_invoice_number, now, posted_client_id, posted_service_order_id, recognize_invoice_if_accounting_enabled, request, save_uploaded_attachment, service_order_active_invoice, to_float, uploaded_attachments_from_request):
    client_id = posted_client_id()
    if not can_access_client(client_id):
        abort(403)
    service_order_id = posted_service_order_id(require_start_date=True, required=True)
    source_reimbursement = None
    reimbursement_id = request.form.get("customer_reimbursement_id", "")
    if reimbursement_id:
        if not reimbursement_id.isdigit():
            raise ValueError("工单结算来源无效。")
        source_reimbursement = db().execute(
            "select * from customer_reimbursements where id = ?",
            (int(reimbursement_id),),
        ).fetchone()
        if not source_reimbursement or source_reimbursement["service_order_id"] != service_order_id:
            raise ValueError("工单结算与所选工单不匹配。")
        if source_reimbursement["status"] != "approved":
            raise ValueError("工单结算审核通过后才能生成发票。")
        if source_reimbursement["invoice_id"]:
            raise ValueError("这份工单结算已经生成过发票。")
        if service_order_active_invoice(service_order_id):
            raise ValueError("这个工单已经有关联发票，不能重复生成。")
        source_order = db().execute(
            "select client_id from service_orders where id = ?",
            (service_order_id,),
        ).fetchone()
        if source_order and source_order["client_id"] and client_id != source_order["client_id"]:
            raise ValueError("发票客户必须与工单关联客户一致。")
    invoice_number = request.form.get("invoice_number", "").strip()
    if not invoice_number:
        invoice_number = next_invoice_number()
    if invoice_number_exists(invoice_number):
        invoice_number = next_invoice_number()
    item_rows = invoice_items_from_form()
    status = "completed"
    cursor = None
    for _ in range(30):
        try:
            cursor = db().execute(
                """
                insert into invoices (
                    invoice_number, client_id, service_order_id, issue_date, due_date, currency, notes, status, created_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_number,
                    client_id,
                    service_order_id,
                    request.form.get("issue_date"),
                    request.form.get("due_date"),
                    request.form.get("currency", "USD"),
                    request.form.get("notes", "").strip(),
                    status,
                    g.user["id"],
                    now(),
                ),
            )
            break
        except IntegrityError:
            invoice_number = next_invoice_number()
    if cursor is None:
        raise RuntimeError("Unable to generate a unique invoice number.")
    invoice_id = cursor.lastrowid
    if save_token:
        finish_invoice_save_token(save_token, invoice_id)
    for project, amount in item_rows:
        db().execute(
            """
            insert into invoice_items (invoice_id, project_id, description, amount, tax_rate)
            values (?, ?, ?, ?, ?)
            """,
            (invoice_id, project["id"], project["name"], to_float(amount), project["tax_rate"]),
        )
    recognize_invoice_if_accounting_enabled(invoice_id)
    for uploaded in uploaded_attachments_from_request():
        if uploaded and uploaded.filename:
            save_uploaded_attachment(invoice_id, uploaded)
    if source_reimbursement:
        copy_customer_reimbursement_attachments_to_invoice(invoice_id, source_reimbursement["id"])
        copy_mileage_proofs_to_invoice(invoice_id, service_order_id)
    if source_reimbursement:
        db().execute(
            "update customer_reimbursements set invoice_id = ? where id = ? and invoice_id is null",
            (invoice_id, source_reimbursement["id"]),
        )
    invoice_summary = f"金额：{money(invoice_totals(invoice_id)['total'], request.form.get('currency', 'USD'))}"
    log_action("create", "invoice", invoice_id, invoice_number, invoice_summary)
    db().commit()
    return invoice_id
