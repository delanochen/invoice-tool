"""Expense workflow reset endpoint."""
import secrets
import shutil
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_reset_routes(app, *,
    cancel_expense_payment_order,
    db,
    is_internal_user,
    log_action,
    login_required,
    normalized_role,
    now,
):
    @login_required
    def process_expense_action():
        if not is_internal_user():
            abort(403)
        try:
            expense_id = int(request.form.get("expense_id", ""))
        except (TypeError, ValueError):
            flash("请选择一张报销单据。", "error")
            return redirect(url_for("expense_processing"))
        expense = db().execute("select * from expenses where id = ?", (expense_id,)).fetchone()
        if not expense:
            abort(404)
        action = request.form.get("action", "")
        if action != "reset_workflow" or normalized_role() != "admin":
            abort(403)
        try:
            cancel_expense_payment_order(expense_id)
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))
        db().execute(
            """
            update expenses
            set status = 'submitted', return_reason = null,
                reviewed_by = null, reviewed_at = null,
                payout_status = 'pending', reimbursed_by = null, reimbursed_at = null,
                updated_at = ?
            where id = ?
            """,
            (now(), expense_id),
        )
        log_action("reset", "expense", expense_id, expense["expense_number"], "流程状态重置为待经理审核；未付款的付款单已取消")
        db().commit()
        flash("报销已重置为待经理审核；未付款的付款单已取消。", "success")
        return redirect(url_for("expense_detail", expense_id=expense_id))


    app.add_url_rule('/expense-processing/action', endpoint='process_expense_action', view_func=process_expense_action, methods=['POST'])
    return {'process_expense_action': process_expense_action}
