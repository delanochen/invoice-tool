"""Invoice 收款标记与撤销; legacy business behavior preserved."""
from flask import g, request, abort, flash, redirect, url_for, jsonify, send_file


def register_invoice_payment_routes(app, *,
    resolve_date,
    db,
    invoice_totals,
    is_internal_user,
    log_action,
    login_required,
    require_invoice_access,
    to_float,
):
    @login_required
    def mark_invoice_paid(invoice_id):
        invoice = require_invoice_access(invoice_id)
        if not is_internal_user():
            abort(403)
        if invoice["status"] != "completed":
            flash("只有已完成的发票才能核销。", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        totals = invoice_totals(invoice_id)
        db().execute(
            """
        update invoices set paid_at = ?, payment_amount = ?, payment_note = ?
        where id = ?
        """,
            (
                request.form.get("paid_at") or resolve_date().today().isoformat(),
                to_float(request.form.get("payment_amount"), totals["total"]),
                request.form.get("payment_note", "").strip(),
                invoice_id,
            ),
        )
        log_action("mark_paid", "invoice", invoice_id, invoice["invoice_number"], "记录发票核销")
        db().commit()
        flash("发票已核销。", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))


    @login_required
    def unmark_invoice_paid(invoice_id):
        invoice = require_invoice_access(invoice_id)
        db().execute("update invoices set paid_at = null, payment_amount = null, payment_note = null where id = ?", (invoice_id,))
        log_action("unmark_paid", "invoice", invoice_id, invoice["invoice_number"], "取消发票核销")
        db().commit()
        flash("发票核销记录已取消。", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))


    app.add_url_rule('/invoices/<int:invoice_id>/mark-paid', endpoint='mark_invoice_paid', view_func=mark_invoice_paid, methods=['POST'])
    app.add_url_rule('/invoices/<int:invoice_id>/unmark-paid', endpoint='unmark_invoice_paid', view_func=unmark_invoice_paid, methods=['POST'])
    return {'mark_invoice_paid': mark_invoice_paid, 'unmark_invoice_paid': unmark_invoice_paid}
