"""Legacy-compatible invoice_create helpers; explicit runtime dependencies."""


def _requested_reimbursement_ids(request):
    """合并开票：`customer_reimbursement_ids`（逗号分隔）优先，其次单个 id。"""
    raw = (request.form.get("customer_reimbursement_ids") or "").strip()
    ids = []
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                raise ValueError("工单结算来源无效。")
            number = int(part)
            if number not in ids:
                ids.append(number)
    if ids:
        return ids
    single = (request.form.get("customer_reimbursement_id") or "").strip()
    if single:
        if not single.isdigit():
            raise ValueError("工单结算来源无效。")
        return [int(single)]
    return []


def create_invoice_from_form(save_token=None, *, IntegrityError, abort, can_access_client, copy_customer_reimbursement_attachments_to_invoice, copy_mileage_proofs_to_invoice, db, finish_invoice_save_token, g, invoice_items_from_form, invoice_number_exists, invoice_totals, log_action, money, next_invoice_number, now, posted_client_id, posted_service_order_id, recognize_invoice_if_accounting_enabled, record_invoice_order, request, save_uploaded_attachment, service_order_active_invoice, to_float, uploaded_attachments_from_request):
    client_id = posted_client_id()
    if not can_access_client(client_id):
        abort(403)
    reimbursement_ids = _requested_reimbursement_ids(request)
    source_reimbursements = []
    for reimbursement_id in reimbursement_ids:
        reimbursement = db().execute(
            "select * from customer_reimbursements where id = ?",
            (reimbursement_id,),
        ).fetchone()
        if not reimbursement:
            raise ValueError("工单结算来源无效。")
        if reimbursement["status"] != "approved":
            raise ValueError("工单结算审核通过后才能生成发票。")
        if reimbursement["invoice_id"]:
            raise ValueError("这份工单结算已经生成过发票。")
        if service_order_active_invoice(reimbursement["service_order_id"]):
            raise ValueError("工单 %s 已经有关联发票，不能重复生成。" % reimbursement["service_order_id"])
        source_order = db().execute(
            "select client_id from service_orders where id = ?",
            (reimbursement["service_order_id"],),
        ).fetchone()
        if source_order and source_order["client_id"] and client_id != source_order["client_id"]:
            raise ValueError("发票客户必须与工单关联客户一致。")
        source_reimbursements.append(reimbursement)
    # 合并开票：主工单仍是第一张结算的工单（旧列保持单值，兼容历史读取）；
    # 其余工单只进关系表。
    service_order_id = posted_service_order_id(require_start_date=True, required=True)
    if source_reimbursements and service_order_id != source_reimbursements[0]["service_order_id"]:
        raise ValueError("主工单必须与第一份工单结算一致。")
    source_reimbursement = source_reimbursements[0] if source_reimbursements else None
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
        for reimbursement in source_reimbursements:
            copy_customer_reimbursement_attachments_to_invoice(invoice_id, reimbursement["id"])
        copy_mileage_proofs_to_invoice(invoice_id, service_order_id)
        for reimbursement in source_reimbursements:
            db().execute(
                "update customer_reimbursements set invoice_id = ? where id = ? and invoice_id is null",
                (invoice_id, reimbursement["id"]),
            )
    # Phase 1 双写：单工单开票仍写 invoices.service_order_id，同时记录关系表；
    # 失败只回滚保存点，不阻断开票（由回填脚本兜底）。合并开票时每个工单一行关联。
    for reimbursement in source_reimbursements or [None]:
        # 每份结算记自己那一份金额（整数分）；合并时若都记整张发票总额，工单口径的金额会重复计算。
        order_amount_cents = (
            int(round(to_float(reimbursement["total_amount"] or 0) * 100))
            if reimbursement is not None else None
        )
        record_invoice_order(
            invoice_id,
            reimbursement["service_order_id"] if reimbursement else service_order_id,
            db=db,
            customer_reimbursement_id=reimbursement["id"] if reimbursement else None,
            amount_cents=order_amount_cents,
            now=now,
        )
    invoice_summary = f"金额：{money(invoice_totals(invoice_id)['total'], request.form.get('currency', 'USD'))}"
    log_action("create", "invoice", invoice_id, invoice_number, invoice_summary)
    db().commit()
    return invoice_id
