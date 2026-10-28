"""Business routes with explicit current-app resolvers; no reverse import."""

from urllib.parse import quote

from flask import Response
from werkzeug.datastructures import Headers

from invoice_tool.attachments.download_capacity import limited_archive_download
from invoice_tool.attachments.streaming_zip import stream_attachment_zip


def register_order_attachments_routes(app, *,
    _cb_resolve_ALLOWED_IMAGE_EXTENSIONS,
    _cb_resolve_CUSTOMER_REIMBURSEMENT_DIR,
    _cb_resolve_Path,
    _cb_resolve_REPORT_ATTACHMENTS_DIR,
    _cb_resolve_abort,
    _cb_resolve_app,
    _cb_resolve_can_create_service_report,
    _cb_resolve_can_view_customer_reimbursement,
    _cb_resolve_customer_reimbursement_attachment_path,
    _cb_resolve_customer_reimbursement_file_path,
    _cb_resolve_db,
    _cb_resolve_ensure_customer_reimbursement_pdf_file,
    _cb_resolve_ensure_service_order_picture_folder,
    _cb_resolve_flash,
    _cb_resolve_g,
    _cb_resolve_has_action_permission,
    _cb_resolve_has_menu_permission,
    _cb_resolve_is_google_photos_share_url,
    _cb_resolve_login_required,
    _cb_resolve_os,
    _cb_resolve_re,
    _cb_resolve_redirect,
    _cb_resolve_report_attachment_path,
    _cb_resolve_request,
    _cb_resolve_require_service_order,
    _cb_resolve_secure_filename,
    _cb_resolve_send_file,
    _cb_resolve_service_order_incoming_folder,
    _cb_resolve_shared_photos_root,
    _cb_resolve_start_google_photos_import_job,
    _cb_resolve_tempfile,
    _cb_resolve_url_for,
    _cb_resolve_zipfile,
):
    @_cb_resolve_login_required()
    def import_google_photos_for_service_order(order_id):
        order = _cb_resolve_require_service_order()(order_id)
        if not _cb_resolve_can_create_service_report()(order):
            _cb_resolve_abort()(403)
        share_url = _cb_resolve_request().form.get("google_photos_url", "").strip()
        if not _cb_resolve_is_google_photos_share_url()(share_url):
            _cb_resolve_flash()("请输入有效的 Google Photos 分享链接。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("service_order_detail", order_id=order_id))
        try:
            _cb_resolve_ensure_service_order_picture_folder()(order["order_number"])
            _cb_resolve_service_order_incoming_folder()(order["order_number"], "google-photos")
        except OSError:
            _cb_resolve_app().logger.exception("Failed to prepare Google Photos import folder for service order %s", order["order_number"])
            _cb_resolve_flash()("无法写入服务器照片目录，请检查共享照片目录或权限。", "error")
            return _cb_resolve_redirect()(_cb_resolve_url_for()("service_order_detail", order_id=order_id))
        _cb_resolve_start_google_photos_import_job()(order_id, _cb_resolve_g().user["id"], share_url)
        _cb_resolve_flash()("Google Photos 分享链接导入任务已开始。完成后会通过邮件和系统消息通知你。", "success")
        return _cb_resolve_redirect()(_cb_resolve_url_for()("service_order_detail", order_id=order_id))


    @_cb_resolve_login_required()
    @limited_archive_download
    def download_service_order_attachments(order_id):
        order = _cb_resolve_require_service_order()(order_id)
        categories = {"self_check": "自检照片", "arrival": "进场照片", "departure": "离场照片",
                      "site": "现场工作照片", "mileage_proof": "里程佐证"}
        folders = [*categories.values(), "工单结算"]
        files = []
        def collect(folder, path, name, root):
            path = _cb_resolve_Path()(path).resolve()
            if not path.is_relative_to(_cb_resolve_Path()(root).resolve()):
                _cb_resolve_abort()(400, description="附件路径无效。")
            name = _cb_resolve_re().sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(name)).strip('. ') or '附件'
            files.append((folder, path, name))
        if _cb_resolve_has_action_permission()('service_reports', 'view'):
            for row in _cb_resolve_db()().execute("""select a.*, r.report_date from service_report_attachments a
                join service_reports r on r.id=a.report_id where r.service_order_id=? order by r.report_date,a.id""", (order_id,)):
                if row['category'] in categories:
                    collect(categories[row['category']], _cb_resolve_report_attachment_path()(row),
                            f"{row['report_date']}-日报{row['report_id']}-{row['original_filename']}", _cb_resolve_REPORT_ATTACHMENTS_DIR())
        if _cb_resolve_has_menu_permission()('field_work'):
            root = _cb_resolve_shared_photos_root()()
            # Include server-imported photos as well as phone uploads, without thumbnails.
            picture_dir = (root / str(order['order_number']) / 'pictures').resolve()
            if not picture_dir.is_relative_to(root.resolve()):
                _cb_resolve_abort()(400)
            if picture_dir.is_dir():
                for path in sorted(picture_dir.rglob('*')):
                    if path.is_file() and path.suffix.lower().lstrip('.') in _cb_resolve_ALLOWED_IMAGE_EXTENSIONS():
                        collect('现场工作照片', path, '-'.join(path.relative_to(picture_dir).parts), picture_dir)
        if _cb_resolve_can_view_customer_reimbursement()():
            reimbursements = _cb_resolve_db()().execute(
                """select * from customer_reimbursements
                   where service_order_id=?
                     and (is_current or not exists (
                         select 1 from customer_reimbursements current_row
                         where current_row.service_order_id=customer_reimbursements.service_order_id
                           and current_row.is_current
                     ))
                   order by id""",
                (order_id,),
            ).fetchall()
            for reimbursement in reimbursements:
                try:
                    current_reimbursement, pdf_path = _cb_resolve_ensure_customer_reimbursement_pdf_file()(reimbursement, order)
                    _cb_resolve_db()().commit()
                except Exception:
                    _cb_resolve_db()().rollback()
                    _cb_resolve_app().logger.exception(
                        "Failed to generate settlement PDF while building attachment archive: reimbursement_id=%s",
                        reimbursement["id"],
                    )
                    current_reimbursement = reimbursement
                    pdf_path = _cb_resolve_customer_reimbursement_file_path()(reimbursement)
                collect(
                    '工单结算',
                    pdf_path,
                    current_reimbursement['file_name'],
                    _cb_resolve_CUSTOMER_REIMBURSEMENT_DIR(),
                )
                for row in _cb_resolve_db()().execute('select * from customer_reimbursement_attachments where customer_reimbursement_id=? order by id', (reimbursement['id'],)):
                    collect('工单结算', _cb_resolve_customer_reimbursement_attachment_path()(row), row['original_filename'], _cb_resolve_CUSTOMER_REIMBURSEMENT_DIR())
        filename = f"{_cb_resolve_secure_filename()(order['order_number']) or order_id}-附件.zip"
        headers = Headers({'Cache-Control': 'private, no-store', 'X-Accel-Buffering': 'no'})
        # RFC 5987 preserves the Chinese filename without unsafe header bytes.
        headers['Content-Disposition'] = "attachment; filename=attachments.zip; filename*=UTF-8''" + quote(filename, safe='')
        # Do not buffer to compute Content-Length: an archive may be many GiB.
        # All permissions and DB queries finish before iterating the response.
        return Response(stream_attachment_zip(folders, files), mimetype='application/zip',
                        headers=headers, direct_passthrough=True)


    app.add_url_rule('/service-orders/<int:order_id>/import-google-photos', endpoint='import_google_photos_for_service_order', view_func=import_google_photos_for_service_order, methods=['POST'])
    app.add_url_rule('/service-orders/<int:order_id>/attachments.zip', endpoint='download_service_order_attachments', view_func=download_service_order_attachments, methods=['GET'])
    return {'import_google_photos_for_service_order': import_google_photos_for_service_order, 'download_service_order_attachments': download_service_order_attachments}
