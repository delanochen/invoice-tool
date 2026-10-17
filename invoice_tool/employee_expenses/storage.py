"""Legacy-compatible employee-expense storage; explicit dependencies only."""

def expense_attachment_dir(expense_id, *, EXPENSE_ATTACHMENTS_DIR, os):
    path = os.path.join(EXPENSE_ATTACHMENTS_DIR, str(expense_id))
    os.makedirs(path, exist_ok=True)
    return path


def expense_attachment_path(attachment, *, EXPENSE_ATTACHMENTS_DIR, os):
    return os.path.join(EXPENSE_ATTACHMENTS_DIR, str(attachment["expense_id"]), attachment["stored_filename"])


def expense_attachment_fingerprints(path, *, Image, ImageOps, hashlib):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    image_hash = ""
    try:
        with Image.open(path) as image:
            pixels = list(ImageOps.grayscale(image).resize((9, 8)).getdata())
            bits = [pixels[row * 9 + column] > pixels[row * 9 + column + 1]
                    for row in range(8) for column in range(8)]
            image_hash = f"{sum(1 << index for index, bit in enumerate(bits) if bit):016x}"
    except Exception:
        # 图片指纹是尽力而为的辅助数据：Pillow 对超大图会抛
        # DecompressionBombError（不是 OSError/ValueError 的子类），
        # 不能让它把整条保存链路打成 500。
        pass
    return digest.hexdigest(), image_hash


def save_expense_attachment(expense_id, uploaded, expense_item_key=None, *, ALLOWED_ATTACHMENT_LABEL, allowed_attachment, db, expense_attachment_dir, expense_attachment_fingerprints, g, now, os, secrets):
    if not uploaded or not uploaded.filename:
        return
    if not allowed_attachment(uploaded.filename):
        raise ValueError(f"附件只支持 {ALLOWED_ATTACHMENT_LABEL}。")
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    stored_path = os.path.join(expense_attachment_dir(expense_id), stored_filename)
    try:
        uploaded.save(stored_path)
    except OSError as error:
        raise ValueError(f"附件保存失败，请重试或更换文件后重新上传。({error})")
    file_sha256, image_dhash = expense_attachment_fingerprints(stored_path)
    return db().execute(
        """
        insert into expense_attachments (
            expense_id, expense_item_key, original_filename, stored_filename,
            content_type, uploaded_by, uploaded_at, file_sha256, image_dhash
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            expense_id, expense_item_key, original_filename, stored_filename,
            uploaded.content_type, g.user["id"], now(), file_sha256, image_dhash,
        ),
    ).lastrowid


def save_expense_uploads(expense_id, item_rows=None, *, request, save_expense_attachment, uploaded_attachments_from_request):
    # 整单级“通用附件”已下线：所有附件必须挂在明细行（expense_item_key 必填）。
    # 请求里仍带 name="attachments" 的非空文件时明确报错，不允许静默丢弃。
    if uploaded_attachments_from_request():
        raise ValueError("附件请挂到对应明细行后再保存。")
    for item in item_rows or []:
        line_key = item["line_key"]
        for uploaded in request.files.getlist(f"item_attachments_{line_key}"):
            save_expense_attachment(expense_id, uploaded, line_key)


def get_expense_attachments(expense_id, *, db):
    return db().execute(
        """
        select expense_attachments.*, users.name as uploader_name,
               transferred.id as transferred_attachment_id
        from expense_attachments left join users on users.id = expense_attachments.uploaded_by
        left join customer_reimbursement_attachments as transferred
          on transferred.source_expense_attachment_id = expense_attachments.id
        where expense_attachments.expense_id = ?
        order by expense_attachments.uploaded_at desc, expense_attachments.id desc
        """,
        (expense_id,),
    ).fetchall()


def group_expense_attachments(attachments):
    """只按明细行分组；expense_item_key 为空的旧数据返回在 "" 组，由详情页提示。"""
    by_item = {}
    for attachment in attachments:
        line_key = (attachment["expense_item_key"] or "").strip()
        by_item.setdefault(line_key, []).append(attachment)
    return by_item
