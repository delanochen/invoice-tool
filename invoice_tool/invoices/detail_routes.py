"""Invoice 发票详情; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file

from invoice_tool.invoices import linked_attachments as _linked_attachments
from invoice_tool.invoices import order_links as _invoice_order_links


def register_invoice_detail_routes(app, *,
    resolve_STATUS_LABELS,
    accounting_base_enabled,
    resolve_date,
    db,
    email_delivery_summary,
    get_company_profile,
    get_customer_reimbursement_attachments,
    get_invoice_attachments,
    get_invoice_terms,
    get_payment_instructions,
    invoice_service_order,
    invoice_totals,
    load_invoice,
    login_required,
    normalized_attachment_filename,
    render_template,
):
    @login_required
    def invoice_detail(invoice_id):
        invoice, client, items = load_invoice(invoice_id)
        if invoice["status"] == "draft" and invoice["created_by"] == g.user["id"]:
            return redirect(url_for("edit_invoice", invoice_id=invoice_id))
        creator = db().execute("select name, email from users where id = ?", (invoice["created_by"],)).fetchone()
        service_order = invoice_service_order(invoice)
        linked_orders = _invoice_order_links.orders_for_invoices(
            [invoice_id], db=db, include_released=True
        ).get(invoice_id, [])
        has_accounting_voucher = bool(db().execute(
            "select 1 from posting_events where source_type='invoice' and source_id=? "
        "and event_type='invoice.confirmed' limit 1", (invoice_id,),
        ).fetchone())
        attachments = get_invoice_attachments(invoice_id)
        # 结算凭证只读引用：历史发票里这些文件已被复制成附件，按同名跳过避免重复展示。
        linked_attachment_rows = _linked_attachments.settlement_attachment_entries(
            _linked_attachments.invoice_settlements(invoice_id, db=db),
            get_customer_reimbursement_attachments=get_customer_reimbursement_attachments,
            normalized_attachment_filename=normalized_attachment_filename,
            skip_names=[normalized_attachment_filename(row["original_filename"]) for row in attachments],
        )
        return render_template(
            "invoice_detail.html",
            invoice=invoice,
            client=client,
            items=items,
            totals=invoice_totals(invoice_id),
            creator=creator,
            service_order=service_order,
            linked_orders=linked_orders,
            attachments=attachments,
            linked_attachments=linked_attachment_rows,
            company=get_company_profile(),
            accounting_enabled=accounting_base_enabled(),
            has_accounting_voucher=has_accounting_voucher,
            terms=get_invoice_terms(),
            payment=get_payment_instructions(),
            labels=resolve_STATUS_LABELS(),
            today=resolve_date().today().isoformat(),
            email_delivery=email_delivery_summary("invoice", invoice_id),
        )


    app.add_url_rule('/invoices/<int:invoice_id>', endpoint='invoice_detail', view_func=invoice_detail, methods=['GET'])
    return {'invoice_detail': invoice_detail}
