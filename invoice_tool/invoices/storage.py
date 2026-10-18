"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def invoice_attachment_dir(invoice_id, *, ATTACHMENTS_DIR, os):
    path = os.path.join(ATTACHMENTS_DIR, str(invoice_id))
    os.makedirs(path, exist_ok=True)
    return path


def attachment_file_path(attachment, *, ATTACHMENTS_DIR, os):
    return os.path.join(ATTACHMENTS_DIR, str(attachment["invoice_id"]), attachment["stored_filename"])


def invoice_attachment_path(invoice_id, *, ATTACHMENTS_DIR, os):
    return os.path.join(ATTACHMENTS_DIR, str(invoice_id))


def get_invoice_attachments(invoice_id, *, db):
    return db().execute(
        """
        select invoice_attachments.*, users.name as uploader_name
        from invoice_attachments left join users on users.id = invoice_attachments.uploaded_by
        where invoice_id = ?
        order by uploaded_at desc
        """,
        (invoice_id,),
    ).fetchall()


def save_uploaded_attachment(invoice_id, uploaded, *, db, existing_attachment_names, g, invoice_attachment_dir, normalized_attachment_filename, now, os, secrets):
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    if "." not in original_filename:
        original_filename = f"{original_filename}.{extension}"
    if normalized_attachment_filename(original_filename) in existing_attachment_names(invoice_id):
        raise RuntimeError(f"附件已存在：{original_filename}")
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    uploaded.save(os.path.join(invoice_attachment_dir(invoice_id), stored_filename))
    db().execute(
        """
        insert into invoice_attachments (
            invoice_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (invoice_id, original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
    )
