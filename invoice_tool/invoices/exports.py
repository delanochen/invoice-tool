"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def build_invoice_zip(invoice, client, items, *, BytesIO, attachment_file_path, get_invoice_attachments, render_invoice_pdf, secure_filename, zipfile):
    archive_name = secure_filename(invoice["invoice_number"])
    if not archive_name:
        archive_name = f"invoice-{invoice['id']}"
    pdf_bytes = render_invoice_pdf(invoice, client, items)
    attachments = get_invoice_attachments(invoice["id"])
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{archive_name}/{archive_name}.pdf", pdf_bytes)
        for attachment in attachments:
            archive.write(attachment_file_path(attachment), arcname=f"{archive_name}/{attachment['original_filename']}")
    return archive_name, buffer.getvalue()


def render_invoice_export_html(invoice, client, items, *, STATUS_LABELS, get_company_profile, get_invoice_terms, get_payment_instructions, invoice_service_order, invoice_totals, render_template):
    return render_template(
        "invoice_export.html",
        invoice=invoice,
        client=client,
        service_order=invoice_service_order(invoice),
        items=items,
        totals=invoice_totals(invoice["id"]),
        company=get_company_profile(),
        terms=get_invoice_terms(),
        payment=get_payment_instructions(),
        labels=STATUS_LABELS,
    )
