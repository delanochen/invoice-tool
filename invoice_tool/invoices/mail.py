"""Legacy-compatible invoice_mail helpers; explicit runtime dependencies."""

def send_invoice_email(invoice_id, *, db, get_company_profile, get_invoice_terms, get_payment_instructions, invoice_email_attachments, invoice_service_order, invoice_totals, load_invoice, log_action, now, record_email_delivery, render_template, send_email):
    invoice, client, items = load_invoice(invoice_id)
    if not client["email"]:
        raise RuntimeError("客户没有邮箱，无法发送。")
    mail_attachments = invoice_email_attachments(invoice, client, items)
    html = render_template(
        "email_invoice.html",
        invoice=invoice,
        client=client,
        service_order=invoice_service_order(invoice),
        items=items,
        totals=invoice_totals(invoice_id),
        company=get_company_profile(),
        terms=get_invoice_terms(),
        payment=get_payment_instructions(),
    )
    subject = f"Invoice {invoice['invoice_number']} from {get_company_profile()['name']}"
    send_email(
        to=client["email"],
        subject=subject,
        html=html,
        attachments=mail_attachments,
    )
    record_email_delivery("invoice", invoice_id, client["email"], subject)
    log_action(
        "send",
        "invoice",
        invoice_id,
        invoice["invoice_number"],
        f"发送至：{client['email']}；附件：{len(mail_attachments)} 个",
    )
    db().execute("update invoices set sent_at = ? where id = ?", (now(), invoice_id))
    db().commit()
