"""Invoice 发票作废; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_void_routes(app, *,
    InvoiceRecognitionService,
    InvoiceVoidError,
    PostingError,
    resolve_date,
    db,
    log_action,
    login_required,
    require_invoice_access,
):
    @login_required
    def void_invoice(invoice_id):
        invoice = require_invoice_access(invoice_id)
        if invoice["status"] == "completed":
            recognition = db().execute(
                "select 1 from posting_events where source_type='invoice' and source_id=? "
            "and event_type='invoice.confirmed' limit 1", (invoice_id,),
            ).fetchone()
            if not recognition:
                flash("审核完成后的发票不能直接作废。", "error")
                return redirect(url_for("invoice_detail", invoice_id=invoice_id))
            reason_code = request.form.get("reason_code", "").strip()
            if not reason_code:
                flash("已过账发票作废必须填写原因。", "error")
                return redirect(url_for("invoice_detail", invoice_id=invoice_id))
            try:
                InvoiceRecognitionService(db()).void(
                    invoice_id, accounting_date=resolve_date().today(), reason_code=reason_code,
                    actor_id=g.user["id"],
                )
                log_action("void", "invoice", invoice_id, invoice["invoice_number"],
                           f"会计冲销：{reason_code}")
                db().commit()
            except (InvoiceVoidError, PostingError, ValueError) as error:
                db().rollback()
                flash(str(error), "error")
                return redirect(url_for("invoice_detail", invoice_id=invoice_id))
            flash("发票已作废，原凭证已保留并生成冲销凭证。", "success")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        if db().execute(
            "select 1 from posting_events where source_type='invoice' and source_id=? "
        "and event_type='invoice.confirmed' limit 1", (invoice_id,),
        ).fetchone():
            flash("该发票已有会计凭证，不能改回草稿；请走作废或更正流程。", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        db().execute("update invoices set status = 'void' where id = ?", (invoice_id,))
        log_action("void", "invoice", invoice_id, invoice["invoice_number"], "作废发票")
        db().commit()
        flash("发票已作废。", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))


    app.add_url_rule('/invoices/<int:invoice_id>/void', endpoint='void_invoice', view_func=void_invoice, methods=['POST'])
    return {'void_invoice': void_invoice}
