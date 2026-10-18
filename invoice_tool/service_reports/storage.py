"""Report attachment storage, deduplication and upload orchestration."""

def report_storage_context_values(order_number, report_date, report_id=None, *, datetime, secure_filename):
    """Build the attachment folder context without requiring a persisted report."""
    safe_order_number = secure_filename(order_number) or f"SO-{report_id or 'new'}"
    try:
        safe_report_date = datetime.strptime(report_date, "%Y-%m-%d").strftime("%Y%m%d")
    except (TypeError, ValueError):
        safe_report_date = str(report_date or "").replace("-", "") or "unknown-date"
    return safe_order_number, safe_report_date


def report_storage_context(report_id, *, abort, app, db, report_storage_context_values, request):
    row = db().execute(
        """
        select service_reports.report_date, service_orders.order_number
        from service_reports
        join service_orders on service_orders.id = service_reports.service_order_id
        where service_reports.id = ?
        """,
        (report_id,),
    ).fetchone()
    if not row:
        app.logger.warning(
            "Report attachment context missing: report_id=%s endpoint=%s path=%s",
            report_id,
            request.endpoint,
            request.path,
        )
        abort(404)
    return report_storage_context_values(row["order_number"], row["report_date"], report_id)


def report_attachment_relative_path(report_id, category, stored_filename, storage_context=None, *, Path, REPORT_PHOTO_FOLDERS, os, report_storage_context):
    order_number, report_date = storage_context or report_storage_context(report_id)
    category_folder = REPORT_PHOTO_FOLDERS.get(category)
    if not category_folder:
        raise ValueError("未知的日报附件类别。")
    return Path(order_number, report_date, category_folder, os.path.basename(stored_filename)).as_posix()


def report_attachment_dir(report_id, category, storage_context=None, *, REPORT_ATTACHMENTS_DIR, os, report_attachment_relative_path):
    relative = report_attachment_relative_path(
        report_id, category, "placeholder.jpg", storage_context=storage_context
    )
    path = os.path.join(REPORT_ATTACHMENTS_DIR, os.path.dirname(relative))
    os.makedirs(path, exist_ok=True)
    return path


def report_attachment_path(attachment, *, Path, REPORT_ATTACHMENTS_DIR, os):
    stored_filename = str(attachment["stored_filename"])
    stored_path = Path(stored_filename)
    if len(stored_path.parts) > 1:
        return os.path.join(REPORT_ATTACHMENTS_DIR, *stored_path.parts)
    return os.path.join(REPORT_ATTACHMENTS_DIR, str(attachment["report_id"]), stored_filename)


def prune_empty_report_folders(path, *, Path, REPORT_ATTACHMENTS_DIR):
    root = Path(REPORT_ATTACHMENTS_DIR).resolve()
    current = Path(path).resolve().parent
    while current != root:
        try:
            current.relative_to(root)
            current.rmdir()
        except (OSError, ValueError):
            break
        current = current.parent


def relocate_report_attachments(report_id, *, Path, REPORT_ATTACHMENTS_DIR, db, os, prune_empty_report_folders, report_attachment_path, report_attachment_relative_path, shutil):
    attachments = db().execute(
        "select * from service_report_attachments where report_id = ?",
        (report_id,),
    ).fetchall()
    for attachment in attachments:
        old_path = report_attachment_path(attachment)
        stored_filename = os.path.basename(attachment["stored_filename"])
        relative_path = report_attachment_relative_path(report_id, attachment["category"], stored_filename)
        new_path = os.path.join(REPORT_ATTACHMENTS_DIR, *Path(relative_path).parts)
        if os.path.normcase(os.path.abspath(old_path)) != os.path.normcase(os.path.abspath(new_path)):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            if os.path.isfile(old_path):
                shutil.move(old_path, new_path)
                prune_empty_report_folders(old_path)
            db().execute(
                "update service_report_attachments set stored_filename = ? where id = ?",
                (relative_path, attachment["id"]),
            )


def uploaded_report_files(field_name, *, request):
    return [file for file in request.files.getlist(field_name) if file and file.filename]


def compress_report_image(source, target_path, *, compress_image):
    compress_image(source, target_path)


def normalized_report_photo_name(filename, *, os, re):
    name = os.path.basename(filename or "").strip()
    stem, _ = os.path.splitext(name)
    stem = re.sub(r"\s*\(\d+\)$", "", stem).strip()
    return stem.casefold()


def is_duplicate_report_photo(report_id, category, original_filename, candidate_hash, *, db, file_sha256, normalized_report_photo_name, os, report_attachment_path):
    candidate_name = normalized_report_photo_name(original_filename)
    rows = db().execute(
        """
        select * from service_report_attachments
        where report_id = ? and category = ?
        """,
        (report_id, category),
    ).fetchall()
    for row in rows:
        existing_path = report_attachment_path(row)
        if os.path.isfile(existing_path):
            try:
                if file_sha256(existing_path) == candidate_hash:
                    return True
            except OSError:
                pass
        elif candidate_name and normalized_report_photo_name(row["original_filename"]) == candidate_name:
            return True
    return False


def save_report_attachment(report_id, uploaded, category, storage_context=None, *, allowed_image, compress_report_image, db, file_sha256, g, is_duplicate_report_photo, now, os, report_attachment_dir, report_attachment_relative_path, secrets):
    if not uploaded or not uploaded.filename:
        return False
    if not allowed_image(uploaded.filename):
        raise ValueError("日报照片仅支持 PNG、JPG、JPEG、WEBP、GIF。")
    source_filename = uploaded.filename or "photo"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"photo.{extension}"
    target_dir = report_attachment_dir(report_id, category, storage_context=storage_context)
    temporary_filename = f".{secrets.token_hex(12)}.jpg.tmp"
    temporary_path = os.path.join(target_dir, temporary_filename)
    compress_report_image(uploaded.stream, temporary_path)
    candidate_hash = file_sha256(temporary_path)
    if is_duplicate_report_photo(report_id, category, original_filename, candidate_hash):
        os.remove(temporary_path)
        return False
    image_filename = f"{secrets.token_hex(12)}.jpg"
    stored_filename = report_attachment_relative_path(
        report_id, category, image_filename, storage_context=storage_context
    )
    os.replace(temporary_path, os.path.join(target_dir, image_filename))
    content_type = "image/jpeg"
    db().execute(
        """
        insert into service_report_attachments (
            report_id, category, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (report_id, category, original_filename, stored_filename, content_type, g.user["id"], now()),
    )
    return True


def save_report_file_attachment(report_id, uploaded, category, storage_context=None, *, ALLOWED_ATTACHMENT_LABEL, allowed_attachment, db, file_sha256, g, is_duplicate_report_photo, now, os, report_attachment_dir, report_attachment_relative_path, secrets):
    if not uploaded or not uploaded.filename:
        return False
    if not allowed_attachment(uploaded.filename):
        raise ValueError(f"里程佐证只支持 {ALLOWED_ATTACHMENT_LABEL}。")
    source_filename = uploaded.filename or "attachment"
    extension = source_filename.rsplit(".", 1)[1].lower()
    original_filename = os.path.basename(source_filename).strip() or f"attachment.{extension}"
    if "." not in original_filename:
        original_filename = f"{original_filename}.{extension}"
    target_dir = report_attachment_dir(report_id, category, storage_context=storage_context)
    temporary_filename = f".{secrets.token_hex(12)}.{extension}.tmp"
    temporary_path = os.path.join(target_dir, temporary_filename)
    uploaded.save(temporary_path)
    candidate_hash = file_sha256(temporary_path)
    if is_duplicate_report_photo(report_id, category, original_filename, candidate_hash):
        os.remove(temporary_path)
        return False
    stored_basename = f"{secrets.token_hex(12)}.{extension}"
    stored_filename = report_attachment_relative_path(
        report_id, category, stored_basename, storage_context=storage_context
    )
    os.replace(temporary_path, os.path.join(target_dir, stored_basename))
    db().execute(
        """
        insert into service_report_attachments (
            report_id, category, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (report_id, category, original_filename, stored_filename, uploaded.content_type, g.user["id"], now()),
    )
    return True


def save_shared_report_photo(report_id, relative_path, category, storage_context=None, *, ALLOWED_IMAGE_EXTENSIONS, db, file_sha256, g, is_duplicate_report_photo, now, os, report_attachment_dir, report_attachment_relative_path, report_storage_context, resolve_shared_photo, secrets, shared_photos_root, shutil):
    # The Debian photo worker can move a selected server photo between page load and
    # form submission (for example into a date folder).  Treat that as a
    # recoverable form-validation error instead of aborting the whole request
    # with the application's generic page/record 404.
    source_path = resolve_shared_photo(relative_path, require_file=True, allow_missing=True)
    if (
        not source_path.is_file()
        or source_path.suffix.lower().lstrip(".") not in ALLOWED_IMAGE_EXTENSIONS
    ):
        raise ValueError("所选服务器照片已移动或不存在，请重新打开辅助填写选择照片后再保存。")
    order_number, _ = storage_context or report_storage_context(report_id)
    processed_root = (shared_photos_root() / order_number / "pictures").resolve()
    try:
        source_path.relative_to(processed_root)
    except ValueError:
        raise ValueError("只能选择已完成处理的服务器照片。")
    if source_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("服务器照片尚未完成处理。")
    if is_duplicate_report_photo(report_id, category, source_path.name, file_sha256(source_path)):
        return False
    image_filename = f"{secrets.token_hex(12)}.jpg"
    stored_filename = report_attachment_relative_path(
        report_id, category, image_filename, storage_context=storage_context
    )
    shutil.copyfile(
        source_path,
        os.path.join(
            report_attachment_dir(report_id, category, storage_context=storage_context),
            image_filename,
        ),
    )
    db().execute(
        """
        insert into service_report_attachments (
            report_id, category, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, ?, ?, ?, 'image/jpeg', ?, ?)
        """,
        (report_id, category, source_path.name, stored_filename, g.user["id"], now()),
    )
    return True


def save_generated_report_attachment(report_id, source_path, original_filename, replace_attachment_id=None, *, db, g, now, os, report_attachment_dir, report_attachment_path, report_attachment_relative_path, secrets, shutil):
    """把系统生成的图片（里程佐证）挂到日报的里程佐证附件上。

    同一员工重新生成时传入 replace_attachment_id：让原记录指向新文件并清掉旧
    文件，避免每点一次「自动生成」附件列表就多一行。
    """
    if not os.path.isfile(source_path):
        raise ValueError("佐证图片不存在，请重新生成。")
    extension = os.path.splitext(source_path)[1].lower().lstrip(".") or "png"
    target_dir = report_attachment_dir(report_id, "mileage_proof")
    image_filename = f"{secrets.token_hex(12)}.{extension}"
    shutil.copyfile(source_path, os.path.join(target_dir, image_filename))
    stored_filename = report_attachment_relative_path(report_id, "mileage_proof", image_filename)

    if replace_attachment_id:
        previous = db().execute(
            "select id, stored_filename from service_report_attachments "
            "where id = ? and report_id = ? and category = 'mileage_proof'",
            (replace_attachment_id, report_id),
        ).fetchone()
        if previous:
            old_path = report_attachment_path(previous)
            db().execute(
                "update service_report_attachments "
                "set original_filename = ?, stored_filename = ?, uploaded_by = ?, uploaded_at = ? "
                "where id = ?",
                (original_filename, stored_filename, g.user["id"], now(), previous["id"]),
            )
            try:
                os.remove(old_path)
            except OSError:
                pass
            return previous["id"]

    cursor = db().execute(
        """
        insert into service_report_attachments (
            report_id, category, original_filename, stored_filename, content_type, uploaded_by, uploaded_at
        ) values (?, 'mileage_proof', ?, ?, 'image/png', ?, ?)
        """,
        (report_id, original_filename, stored_filename, g.user["id"], now()),
    )
    return cursor.lastrowid


def save_report_uploads(report_id, storage_context=None, *, app, request, save_report_attachment, save_report_file_attachment, save_shared_report_photo, uploaded_report_files):
    try:
        for field_name, category in (
            ("self_check_photos", "self_check"),
            ("site_photos", "site"),
            ("arrival_photos", "arrival"),
            ("departure_photos", "departure"),
        ):
            for uploaded in uploaded_report_files(field_name):
                save_report_attachment(
                    report_id, uploaded, category, storage_context=storage_context
                )
            for relative_path in request.form.getlist(f"shared_photo_{category}"):
                save_shared_report_photo(
                    report_id, relative_path, category, storage_context=storage_context
                )
        for uploaded in uploaded_report_files("mileage_proof_attachments"):
            save_report_file_attachment(
                report_id, uploaded, "mileage_proof", storage_context=storage_context
            )
    except ValueError:
        raise
    except OSError as error:
        app.logger.warning(
            "Unable to save report attachment: report_id=%s endpoint=%s error=%s",
            report_id,
            request.endpoint,
            type(error).__name__,
        )
        raise ValueError(
            "日报照片保存失败，所选照片可能已移动或暂时不可用。请重新选择照片后再保存。"
        ) from error
