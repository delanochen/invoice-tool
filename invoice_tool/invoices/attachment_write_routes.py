"""Invoice 附件上传与删除; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_attachment_write_routes(app, *,
    attachment_file_path,
    db,
    login_required,
    resolve_os,
    require_invoice_access,
    save_uploaded_attachment,
    uploaded_attachments_from_request,
    validate_attachment_uploads,
):
    @login_required
    def upload_attachment(invoice_id):
        require_invoice_access(invoice_id)
        uploads = uploaded_attachments_from_request()
        if not uploads:
            flash("请选择要上传的附件。", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        if not validate_attachment_uploads(uploads, invoice_id):
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        for uploaded in uploads:
            save_uploaded_attachment(invoice_id, uploaded)
        db().commit()
        flash(f"已上传 {len(uploads)} 个附件。", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))


    @login_required
    def delete_attachment(attachment_id):
        attachment = db().execute("select * from invoice_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        require_invoice_access(attachment["invoice_id"])
        try:
            resolve_os().remove(attachment_file_path(attachment))
        except FileNotFoundError:
            pass
        db().execute("delete from invoice_attachments where id = ?", (attachment_id,))
        db().commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "attachment_id": attachment_id})
        flash("附件已删除。", "success")
        return redirect(url_for("invoice_detail", invoice_id=attachment["invoice_id"]))


    app.add_url_rule('/invoices/<int:invoice_id>/attachments', endpoint='upload_attachment', view_func=upload_attachment, methods=['POST'])
    app.add_url_rule('/attachments/<int:attachment_id>/delete', endpoint='delete_attachment', view_func=delete_attachment, methods=['POST'])
    return {'upload_attachment': upload_attachment, 'delete_attachment': delete_attachment}
