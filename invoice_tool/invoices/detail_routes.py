"""Invoice 发票详情; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_detail_routes(app, *,
    resolve_STATUS_LABELS,
    accounting_base_enabled,
    resolve_date,
    db,
    email_delivery_summary,
    get_company_profile,
    get_invoice_attachments,
    get_invoice_terms,
    get_payment_instructions,
    invoice_service_order,
    invoice_totals,
    load_invoice,
    login_required,
    render_template,
):
    @login_required
    def invoice_detail(invoice_id):
        invoice, client, items = load_invoice(invoice_id)
        if invoice["status"] == "draft" and invoice["created_by"] == g.user["id"]:
            return redirect(url_for("edit_invoice", invoice_id=invoice_id))
        creator = db().execute("select name, email from users where id = ?", (invoice["created_by"],)).fetchone()
        service_order = invoice_service_order(invoice)
        has_accounting_voucher = bool(db().execute(
            "select 1 from posting_events where source_type='invoice' and source_id=? "
        "and event_type='invoice.confirmed' limit 1", (invoice_id,),
        ).fetchone())
        return render_template(
            "invoice_detail.html",
            invoice=invoice,
            client=client,
            items=items,
            totals=invoice_totals(invoice_id),
            creator=creator,
            service_order=service_order,
            attachments=get_invoice_attachments(invoice_id),
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
