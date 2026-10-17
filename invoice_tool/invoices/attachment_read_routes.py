"""Invoice 附件下载与预览; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_attachment_read_routes(app, *,
    attachment_file_path,
    db,
    login_required,
    require_invoice_access,
    safe_attachment_response,
):
    @login_required
    def download_attachment(attachment_id):
        attachment = db().execute("select * from invoice_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        require_invoice_access(attachment["invoice_id"])
        return send_file(attachment_file_path(attachment), as_attachment=True, download_name=attachment["original_filename"])


    @login_required
    def preview_attachment(attachment_id):
        attachment = db().execute("select * from invoice_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        require_invoice_access(attachment["invoice_id"])
        return safe_attachment_response(
            attachment_file_path(attachment),
            attachment["original_filename"],
        )


    app.add_url_rule('/attachments/<int:attachment_id>', endpoint='download_attachment', view_func=download_attachment, methods=['GET'])
    app.add_url_rule('/attachments/<int:attachment_id>/preview', endpoint='preview_attachment', view_func=preview_attachment, methods=['GET'])
    return {'download_attachment': download_attachment, 'preview_attachment': preview_attachment}
