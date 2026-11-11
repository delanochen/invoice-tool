"""Invoice 发票删除; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_delete_routes(app, *,
    IntegrityError,
    db,
    has_action_permission,
    invoice_attachment_path,
    log_action,
    login_required,
    release_invoice_orders,
    require_invoice_access,
    resolve_shutil,
):
    @login_required
    def delete_invoice(invoice_id):
        invoice = require_invoice_access(invoice_id)
        # v0.1.239: 删除权限完全由菜单配置「发票-删除」决定（默认财务/经理/管理员），
        # 不再按角色写死；无权限时集中式路由闸门会先行拦截（403）。
        if not has_action_permission("invoices", "delete"):
            abort(403)
        recognition = db().execute(
            "select 1 from posting_events where source_type='invoice' and source_id=? "
        "and event_type='invoice.confirmed' limit 1", (invoice_id,),
        ).fetchone()
        if recognition:
            flash("该发票已有会计凭证，不能物理删除；请使用作废并生成冲销凭证。", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        try:
            db().execute(
                "update messages set link = null where link = ? or link = ?",
                (url_for("invoice_detail", invoice_id=invoice_id),
                 url_for("edit_invoice", invoice_id=invoice_id)),
            )
            db().execute("update customer_reimbursements set invoice_id = null where invoice_id = ?", (invoice_id,))
            # v0.1.475: 物理删除也必须释放工单关联，否则 invoice_orders 里会留下指向不存在发票的
            # 有效关联，把工单的开票资格一直占着（工单列表显示未开票、合并开票页却列不出来）。
            released = release_invoice_orders(invoice_id, db=db)
            db().execute("delete from invoices where id = ?", (invoice_id,))
            log_action(
                "delete", "invoice", invoice_id, invoice["invoice_number"],
                f"删除状态为 {invoice['status']} 的发票" + (f"，释放 {released} 条工单关联" if released else ""),
            )
            db().commit()
        except IntegrityError:
            db().rollback()
            flash("该发票仍有关联业务记录，不能删除；请先解除关联或走作废流程。", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        resolve_shutil().rmtree(invoice_attachment_path(invoice_id), ignore_errors=True)
        flash("发票已删除。", "success")
        return redirect(url_for("invoices"))


    app.add_url_rule('/invoices/<int:invoice_id>/delete', endpoint='delete_invoice', view_func=delete_invoice, methods=['POST'])
    return {'delete_invoice': delete_invoice}
