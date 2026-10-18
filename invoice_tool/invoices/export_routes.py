"""Invoice ZIP 与 PDF 导出; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_export_routes(app, *,
    BytesIO,
    build_invoice_zip,
    load_invoice,
    login_required,
    render_invoice_pdf,
    secure_filename,
):
    @login_required
    def export_invoice(invoice_id):
        invoice, client, items = load_invoice(invoice_id)
        archive_name, archive_bytes = build_invoice_zip(invoice, client, items)
        return send_file(
            BytesIO(archive_bytes),
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{archive_name}.zip",
        )


    @login_required
    def export_invoice_pdf(invoice_id):
        invoice, client, items = load_invoice(invoice_id)
        invoice_name = secure_filename(invoice["invoice_number"]) or f"invoice-{invoice['id']}"
        return send_file(
            BytesIO(render_invoice_pdf(invoice, client, items)),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{invoice_name}.pdf",
        )


    app.add_url_rule('/invoices/<int:invoice_id>/export', endpoint='export_invoice', view_func=export_invoice, methods=['POST'])
    app.add_url_rule('/invoices/<int:invoice_id>/export-pdf', endpoint='export_invoice_pdf', view_func=export_invoice_pdf, methods=['GET'])
    return {'export_invoice': export_invoice, 'export_invoice_pdf': export_invoice_pdf}
