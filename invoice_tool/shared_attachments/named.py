"""Legacy-compatible named_attachment helpers; explicit runtime dependencies."""

def save_named_attachment(uploaded, directory, table, owner_column=None, owner_id=None, *, ALLOWED_ATTACHMENT_LABEL, allowed_attachment, db, g, now, os, secrets):
    if not uploaded or not uploaded.filename:
        return
    if not allowed_attachment(uploaded.filename):
        raise ValueError(f"附件只支持 {ALLOWED_ATTACHMENT_LABEL}。")
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    os.makedirs(directory, exist_ok=True)
    uploaded.save(os.path.join(directory, stored_filename))
    if owner_column:
        db().execute(
            f"""
            insert into {table} (
                {owner_column}, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (owner_id, original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
        )
    else:
        db().execute(
            f"""
            insert into {table} (
                original_filename, stored_filename, content_type, uploaded_by, uploaded_at
            ) values (?, ?, ?, ?, ?)
            """,
            (original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
        )
