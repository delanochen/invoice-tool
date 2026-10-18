"""Invoice 发票邮件发送入口; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_send_routes(app, *,
    is_external_user,
    login_required,
    require_invoice_access,
    send_invoice_email,
):
    @login_required
    def send_invoice(invoice_id):
        invoice = require_invoice_access(invoice_id)
        if is_external_user():
            abort(403)
        if invoice["status"] != "completed":
            flash("只有经理审核完成后的发票才能发送邮件。", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        try:
            send_invoice_email(invoice_id)
        except Exception as error:
            app.logger.exception("Unable to send invoice %s", invoice_id)
            flash(f"发票邮件发送失败：{error}", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        flash("发票邮件已发送。", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))


    app.add_url_rule('/invoices/<int:invoice_id>/send', endpoint='send_invoice', view_func=send_invoice, methods=['POST'])
    return {'send_invoice': send_invoice}
