"""Contract numbering, form validation and uploads with explicit runtime dependencies."""

def next_contract_number(*, date, db, lock_number_allocation, secrets):
    lock_number_allocation(db())
    prefix = f"CT{date.today():%y}"
    row = db().execute(
        """
        select contract_number from contracts
        where contract_number like ?
        order by contract_number desc limit 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    if not row:
        return f"{prefix}001"
    suffix = row["contract_number"][len(prefix):]
    try:
        return f"{prefix}{int(suffix) + 1:03d}"
    except ValueError:
        return f"{prefix}{secrets.randbelow(900) + 100}"



def contract_attachment_dir(contract_id, *, CONTRACT_ATTACHMENTS_DIR, os):
    path = os.path.join(CONTRACT_ATTACHMENTS_DIR, str(contract_id))
    os.makedirs(path, exist_ok=True)
    return path



def contract_attachment_path(attachment, *, CONTRACT_ATTACHMENTS_DIR, os):
    return os.path.join(
        CONTRACT_ATTACHMENTS_DIR,
        str(attachment["contract_id"]),
        attachment["stored_filename"],
    )



def save_contract_uploads(contract_id, *, ALLOWED_ATTACHMENT_LABEL, allowed_attachment, contract_attachment_dir, db, g, now, os, request, secrets):
    existing = {
        row["original_filename"].strip().casefold()
        for row in db().execute(
            "select original_filename from contract_attachments where contract_id = ?",
            (contract_id,),
        ).fetchall()
    }
    for uploaded in request.files.getlist("attachments"):
        if not uploaded or not uploaded.filename:
            continue
        if not allowed_attachment(uploaded.filename):
            raise ValueError(f"附件只支持 {ALLOWED_ATTACHMENT_LABEL}。")
        original_filename = os.path.basename(uploaded.filename).strip()
        if original_filename.casefold() in existing:
            raise ValueError(f"附件“{original_filename}”已经存在。")
        extension = original_filename.rsplit(".", 1)[1].lower()
        stored_filename = f"{secrets.token_hex(12)}.{extension}"
        uploaded.save(os.path.join(contract_attachment_dir(contract_id), stored_filename))
        db().execute(
            """
            insert into contract_attachments (
                contract_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                contract_id,
                original_filename,
                stored_filename,
                uploaded.content_type,
                g.user["id"],
                now(),
            ),
        )
        existing.add(original_filename.casefold())



def contract_form_values(contract=None, *, CONTRACT_STATUS_LABELS, CONTRACT_TYPE_LABELS, can_access_client, db, next_contract_number, request, to_float):
    contract_type = request.form.get("contract_type", "framework")
    status = request.form.get("status", "draft")
    if contract_type not in CONTRACT_TYPE_LABELS:
        raise ValueError("请选择有效的合同类型。")
    if status not in CONTRACT_STATUS_LABELS:
        raise ValueError("请选择有效的合同状态。")
    try:
        client_id = int(request.form.get("client_id", ""))
    except (TypeError, ValueError):
        raise ValueError("请选择客户。")
    client = db().execute("select * from clients where id = ?", (client_id,)).fetchone()
    if not client or not can_access_client(client_id):
        raise ValueError("选择的客户不存在或无权访问。")
    contract_number = request.form.get("contract_number", "").strip() or (
        contract["contract_number"] if contract else next_contract_number()
    )
    title = request.form.get("title", "").strip()
    if not title:
        raise ValueError("请填写合同名称。")
    start_date = request.form.get("start_date") or None
    end_date = request.form.get("end_date") or None
    if start_date and end_date and end_date < start_date:
        raise ValueError("合同结束日期不能早于开始日期。")
    amount_text = request.form.get("amount", "").strip()
    amount = to_float(amount_text) if amount_text else None
    if amount is not None and amount < 0:
        raise ValueError("合同金额不能小于零。")
    project_name = request.form.get("project_name", "").strip()
    rate_card = request.form.get("rate_card", "").strip()
    if contract_type == "project":
        if not project_name:
            raise ValueError("项目合同必须填写项目名称。")
        if amount is None:
            raise ValueError("项目合同必须填写合同金额。")
        rate_card = ""
    else:
        project_name = ""
    return {
        "contract_number": contract_number,
        "client_id": client_id,
        "contract_type": contract_type,
        "title": title,
        "status": status,
        "signed_date": request.form.get("signed_date") or None,
        "start_date": start_date,
        "end_date": end_date,
        "currency": request.form.get("currency", "USD"),
        "amount": amount,
        "payment_terms": request.form.get("payment_terms", "").strip(),
        "rate_card": rate_card,
        "project_name": project_name,
        "notes": request.form.get("notes", "").strip(),
    }
