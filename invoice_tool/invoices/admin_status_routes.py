"""Invoice 管理员状态调整; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_admin_status_routes(app, *,
    create_message,
    db,
    log_action,
    login_required,
    require_invoice_access,
):
    @login_required
    def admin_update_invoice_status(invoice_id):
        invoice = require_invoice_access(invoice_id)
        if g.user["role"] != "admin":
            abort(403)
        if invoice["paid_at"]:
            flash("这张发票已经核销，不能再修改为保存未提交状态。", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        target_status = request.form.get("status", "")
        if target_status != "draft":
            flash("当前只允许管理员将未核销发票改为保存未提交状态。", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        db().execute(
            "update invoices set status = 'draft', return_reason = null where id = ?",
            (invoice_id,),
        )
        create_message(
            invoice["created_by"],
            "发票状态已调整",
            f"管理员已将发票 {invoice['invoice_number']} 调整为保存未提交状态。",
            url_for("edit_invoice", invoice_id=invoice_id),
        )
        log_action("status_change", "invoice", invoice_id, invoice["invoice_number"], "调整为保存未提交")
        db().commit()
        flash("发票状态已修改为保存未提交。", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))


    app.add_url_rule('/invoices/<int:invoice_id>/admin-status', endpoint='admin_update_invoice_status', view_func=admin_update_invoice_status, methods=['POST'])
    return {'admin_update_invoice_status': admin_update_invoice_status}
