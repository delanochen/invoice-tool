"""Legacy-compatible attachment_files helpers; explicit runtime dependencies."""

def get_customer_reimbursement_attachments(reimbursement_id, *, db):
    return db().execute(
        """
        select customer_reimbursement_attachments.*, users.name as uploader_name
        from customer_reimbursement_attachments
        left join users on users.id = customer_reimbursement_attachments.uploaded_by
        where customer_reimbursement_id = ?
        order by uploaded_at desc, id desc
        """,
        (reimbursement_id,),
    ).fetchall()


def save_customer_reimbursement_attachment(reimbursement_id, uploaded, *, ALLOWED_ATTACHMENT_LABEL, allowed_attachment, customer_reimbursement_attachment_dir, db, g, now, os, secrets):
    if not uploaded or not uploaded.filename:
        return
    if not allowed_attachment(uploaded.filename):
        raise ValueError(f"附件只支持 {ALLOWED_ATTACHMENT_LABEL}。")
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    uploaded.save(os.path.join(customer_reimbursement_attachment_dir(reimbursement_id), stored_filename))
    db().execute(
        """
        insert into customer_reimbursement_attachments (
            customer_reimbursement_id, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (reimbursement_id, original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
    )


def unique_customer_reimbursement_attachment_filename(reimbursement_id, original_filename, *, db, normalized_attachment_filename, os):
    original_filename = os.path.basename(original_filename or "attachment").strip() or "attachment"
    stem, extension = os.path.splitext(original_filename)
    stem = stem or "attachment"
    existing = {
        normalized_attachment_filename(row["original_filename"])
        for row in db().execute(
            "select original_filename from customer_reimbursement_attachments where customer_reimbursement_id = ?",
            (reimbursement_id,),
        ).fetchall()
    }
    candidate = original_filename
    counter = 2
    while normalized_attachment_filename(candidate) in existing:
        candidate = f"{stem}-{counter}{extension}"
        counter += 1
    return candidate


def copy_file_to_customer_reimbursement_attachment(
    reimbursement_id,
    source_path,
    original_filename,
    content_type=None,
    uploaded_by=None,
    source_expense_attachment_id=None,
 *, DatabaseError, IntegrityError, app, customer_reimbursement_attachment_dir, db, g, now, os, secrets, shutil, unique_customer_reimbursement_attachment_filename):
    if not os.path.isfile(source_path):
        return False
    original_filename = unique_customer_reimbursement_attachment_filename(reimbursement_id, original_filename)
    extension = os.path.splitext(original_filename)[1].lstrip(".").lower() or "dat"
    stored_filename = f"{secrets.token_hex(12)}.{extension}"
    destination_path = os.path.join(customer_reimbursement_attachment_dir(reimbursement_id), stored_filename)
    try:
        shutil.copyfile(source_path, destination_path)
    except OSError as error:
        # 拷贝失败（文件被占用、磁盘异常等）不应打断报销保存主流程。
        app.logger.warning("Unable to copy expense attachment %s to settlement %s: %s", source_path, reimbursement_id, error)
        return False
    try:
        db().execute(
            """
            insert into customer_reimbursement_attachments (
                customer_reimbursement_id, original_filename, stored_filename, content_type,
                source_expense_attachment_id, uploaded_by, uploaded_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reimbursement_id,
                original_filename,
                stored_filename,
                content_type,
                source_expense_attachment_id,
                uploaded_by or g.user["id"],
                now(),
            ),
        )
    except IntegrityError:
        # 唯一索引 idx_customer_reimbursement_attachment_expense_source：
        # 并发同步已复制过同一来源附件，视为已处理而非错误。
        try:
            os.remove(destination_path)
        except FileNotFoundError:
            pass
        return False
    except DatabaseError:
        try:
            os.remove(destination_path)
        except FileNotFoundError:
            pass
        raise
    return True


def copy_mileage_proofs_to_customer_reimbursement(reimbursement_id, service_order_id, *, copy_file_to_customer_reimbursement_attachment, db, report_attachment_path):
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
        if copy_file_to_customer_reimbursement_attachment(
            reimbursement_id,
            report_attachment_path(row),
            original_name,
            row["content_type"],
            row["uploaded_by"],
        ):
            copied += 1
    return copied
