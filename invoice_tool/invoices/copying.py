"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def copy_file_to_invoice_attachment(invoice_id, source_path, original_filename, content_type=None, uploaded_by=None, *, db, g, invoice_attachment_dir, now, os, secrets, shutil, unique_invoice_attachment_filename):
    if not os.path.isfile(source_path):
        return False
    original_filename = unique_invoice_attachment_filename(invoice_id, original_filename)
    extension = os.path.splitext(original_filename)[1].lstrip(".").lower() or "dat"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    shutil.copyfile(source_path, os.path.join(invoice_attachment_dir(invoice_id), stored_filename))
    db().execute(
        """
        insert into invoice_attachments (
            invoice_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (invoice_id, original_filename, stored_filename, content_type, uploaded_by or g.user["id"], now()),
    )
    return True


def copy_customer_reimbursement_attachments_to_invoice(invoice_id, reimbursement_id, *, copy_file_to_invoice_attachment, customer_reimbursement_attachment_path, get_customer_reimbursement_attachments):
    rows = get_customer_reimbursement_attachments(reimbursement_id)
    copied = 0
    for row in rows:
        if copy_file_to_invoice_attachment(
            invoice_id,
            customer_reimbursement_attachment_path(row),
            row["original_filename"],
            row["content_type"],
            row["uploaded_by"],
        ):
            copied += 1
    return copied


def copy_mileage_proofs_to_invoice(invoice_id, service_order_id, *, copy_file_to_invoice_attachment, db, existing_attachment_names, normalized_attachment_filename, report_attachment_path):
    existing_names = existing_attachment_names(invoice_id)
    rows = db().execute(
        """
        select service_report_attachments.*, service_reports.report_date
        from service_report_attachments
        join service_reports on service_reports.id = service_report_attachments.report_id
        where service_reports.service_order_id = ?
          and service_report_attachments.category = 'mileage_proof'
        order by service_reports.report_date asc, service_report_attachments.uploaded_at asc,
                 service_report_attachments.id asc
        """,
        (service_order_id,),
    ).fetchall()
    copied = 0
    for row in rows:
        original_name = row["original_filename"] or "mileage-proof"
        report_date = (row["report_date"] or "").strip()
        if report_date:
            original_name = f"里程佐证-{report_date}-{original_name}"
        normalized_name = normalized_attachment_filename(original_name)
        if normalized_name in existing_names:
            continue
        if copy_file_to_invoice_attachment(
            invoice_id,
            report_attachment_path(row),
            original_name,
            row["content_type"],
            row["uploaded_by"],
        ):
            copied += 1
            existing_names.add(normalized_name)
    return copied
