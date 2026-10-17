"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def customer_reimbursement_email_attachments(invoice, *, customer_reimbursement_outgoing_attachments, latest_customer_reimbursement, require_service_order):
    if not invoice["service_order_id"]:
        return []
    reimbursement = latest_customer_reimbursement(invoice["service_order_id"])
    if not reimbursement:
        return []
    order = require_service_order(invoice["service_order_id"])
    _, attachments = customer_reimbursement_outgoing_attachments(reimbursement, order)
    return attachments


def unique_email_attachment_filename(filename, used_names, *, os):
    filename = os.path.basename(filename or "").strip() or "attachment"
    stem, extension = os.path.splitext(filename)
    candidate = filename
    counter = 2
    while candidate.casefold() in used_names:
        candidate = f"{stem or 'attachment'}-{counter}{extension}"
        counter += 1
    used_names.add(candidate.casefold())
    return candidate


def invoice_email_attachments(invoice, client, items, *, attachment_file_path, customer_reimbursement_email_attachments, get_invoice_attachments, os, render_invoice_pdf, secure_filename, unique_email_attachment_filename):
    invoice_name = secure_filename(invoice["invoice_number"]) or f"invoice-{invoice['id']}"
    attachments = [
        {
            "filename": f"{invoice_name}.pdf",
            "content": render_invoice_pdf(invoice, client, items),
            "maintype": "application",
            "subtype": "pdf",
        }
    ]
    for attachment in get_invoice_attachments(invoice["id"]):
        path = attachment_file_path(attachment)
        if not os.path.exists(path):
            continue
        maintype, _, subtype = (attachment["content_type"] or "application/octet-stream").partition("/")
        with open(path, "rb") as file:
            attachments.append(
                {
                    "filename": attachment["original_filename"],
                    "content": file.read(),
                    "maintype": maintype or "application",
                    "subtype": subtype or "octet-stream",
                }
            )
    attachments.extend(customer_reimbursement_email_attachments(invoice))

    used_names = set()
    for attachment in attachments:
        attachment["filename"] = unique_email_attachment_filename(attachment["filename"], used_names)
    return attachments
