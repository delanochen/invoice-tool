"""Invoice 附件下载与预览; legacy business behavior preserved."""
import re
from pathlib import Path
from urllib.parse import quote

from flask import Response, g, request, abort, flash, redirect, url_for, jsonify, send_file
from werkzeug.datastructures import Headers

from invoice_tool.attachments.download_capacity import limited_archive_download
from invoice_tool.attachments.streaming_zip import stream_attachment_zip


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
    @limited_archive_download
    def download_invoice_attachments(invoice_id):
        """Serve every attachment of one invoice as a single streamed ZIP.

        One request instead of one request per attachment: an invoice can carry
        hundreds of attachments, and firing them all from the browser saturates
        the client's link and the shared workers.
        """
        require_invoice_access(invoice_id)
        rows = db().execute(
            "select * from invoice_attachments where invoice_id = ? order by id",
            (invoice_id,),
        ).fetchall()
        files, seen = [], set()
        for row in rows:
            try:
                path = Path(attachment_file_path(row)).resolve()
            except OSError:
                continue
            if path in seen:
                continue
            seen.add(path)
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(row["original_filename"])).strip('. ') or '附件'
            files.append(('发票附件', path, name))
        if not files:
            abort(404)
        filename = 'invoice-%s-attachments.zip' % invoice_id
        headers = Headers({'Cache-Control': 'private, no-store', 'X-Accel-Buffering': 'no'})
        headers['Content-Disposition'] = "attachment; filename=attachments.zip; filename*=UTF-8''" + quote(filename, safe='')
        return Response(stream_attachment_zip(['发票附件'], files), mimetype='application/zip',
                        headers=headers, direct_passthrough=True)


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
    app.add_url_rule('/invoices/<int:invoice_id>/attachments.zip', endpoint='download_invoice_attachments', view_func=download_invoice_attachments, methods=['GET'])
    return {'download_attachment': download_attachment, 'preview_attachment': preview_attachment,
            'download_invoice_attachments': download_invoice_attachments}
