"""Invoice PDF 导出（发票与附件分开下载）；legacy 行为已调整。"""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_export_routes(app, *,
    BytesIO,
    build_invoice_zip,
    load_invoice,
    login_required,
    render_invoice_pdf,
    secure_filename,
):
    def _pdf_response(invoice, client, items):
        invoice_name = secure_filename(invoice["invoice_number"]) or f"invoice-{invoice['id']}"
        return send_file(
            BytesIO(render_invoice_pdf(invoice, client, items)),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{invoice_name}.pdf",
        )

    @login_required
    def export_invoice(invoice_id):
        # 旧入口 /export 原本输出「发票 PDF + 全部附件」的 ZIP：合并开票后一张发票可能带
        # 几百个凭证（253 MB），点一次就把整包塞进 worker 内存，且用户只是想下发票本身。
        # 现在 /export 只给发票 PDF，附件统一走 /invoices/<id>/attachments.zip。
        invoice, client, items = load_invoice(invoice_id)
        return _pdf_response(invoice, client, items)


    @login_required
    def export_invoice_pdf(invoice_id):
        invoice, client, items = load_invoice(invoice_id)
        return _pdf_response(invoice, client, items)


    app.add_url_rule('/invoices/<int:invoice_id>/export', endpoint='export_invoice', view_func=export_invoice, methods=['GET', 'POST'])
    app.add_url_rule('/invoices/<int:invoice_id>/export-pdf', endpoint='export_invoice_pdf', view_func=export_invoice_pdf, methods=['GET'])
    return {'export_invoice': export_invoice, 'export_invoice_pdf': export_invoice_pdf}
