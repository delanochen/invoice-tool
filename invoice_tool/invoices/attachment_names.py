"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def normalized_attachment_filename(filename, *, os):
    return os.path.basename(filename or "").strip().casefold()


def existing_attachment_names(invoice_id, *, db, normalized_attachment_filename):
    rows = db().execute(
        "select original_filename from invoice_attachments where invoice_id = ?",
        (invoice_id,),
    ).fetchall()
    return {normalized_attachment_filename(row["original_filename"]) for row in rows}


def duplicate_attachment_names(uploads, invoice_id=None, *, existing_attachment_names, normalized_attachment_filename, os):
    seen = existing_attachment_names(invoice_id) if invoice_id else set()
    duplicates = []
    for uploaded in uploads:
        original_name = os.path.basename(uploaded.filename or "").strip()
        normalized_name = normalized_attachment_filename(original_name)
        if not normalized_name:
            continue
        if normalized_name in seen:
            duplicates.append(original_name)
            continue
        seen.add(normalized_name)
    return duplicates


def validate_attachment_uploads(uploads, invoice_id=None, *, ALLOWED_ATTACHMENT_LABEL, allowed_attachment, duplicate_attachment_names, flash):
    for uploaded in uploads:
        if uploaded and uploaded.filename and not allowed_attachment(uploaded.filename):
            flash(f"附件只支持 {ALLOWED_ATTACHMENT_LABEL}。", "error")
            return False
    duplicates = duplicate_attachment_names(uploads, invoice_id)
    if duplicates:
        flash(f"附件重复：{', '.join(duplicates)}。请删除重复附件后再上传。", "error")
        return False
    return True


def unique_invoice_attachment_filename(invoice_id, original_filename, *, existing_attachment_names, normalized_attachment_filename, os):
    original_filename = os.path.basename(original_filename or "").strip() or "attachment"
    stem, extension = os.path.splitext(original_filename)
    stem = stem or "attachment"
    existing = existing_attachment_names(invoice_id)
    candidate = original_filename
    counter = 2
    while normalized_attachment_filename(candidate) in existing:
        candidate = f"{stem}-{counter}{extension}"
        counter += 1
    return candidate
