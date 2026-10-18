"""Expense duplicate review and return endpoint registration."""
import secrets
from flask import abort, g, flash, redirect, render_template, request, url_for


def register_expense_review_routes(app, *,
    db,
    log_action,
    login_required,
    normalized_role,
    notify_expense_participants,
    now,
    require_expense,
):
    @login_required
    def review_expense_duplicate_check(check_id):
        if normalized_role() not in {"admin", "manager"}:
            abort(403)
        check = db().execute(
            "select * from expense_duplicate_checks where id = ?", (check_id,)
        ).fetchone()
        if not check:
            abort(404)
        status = request.form.get("review_status", "")
        labels = {
            "confirmed": "已确认重复",
            "not_duplicate": "已标记为非重复",
            "legal_split": "已标记为合法分摊",
        }
        if status not in labels:
            abort(400)
        db().execute(
            """
            update expense_duplicate_checks
            set review_status = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ?
            where id = ?
            """,
            (status, g.user["id"], now(), now(), check_id),
        )
        log_action("review", "expense_duplicate_check", check_id, labels[status], f"报销ID：{check['expense_id']}")
        db().commit()
        flash(labels[status], "success")
        return redirect(url_for("expense_detail", expense_id=check["expense_id"]))


    @login_required
    def return_expense(expense_id):
        expense, order = require_expense(expense_id)
        if expense["status"] != "submitted":
            flash("只有待经理审核的报销可以退回。", "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))
        reason = request.form.get("return_reason", "").strip()
        if not reason:
            flash("请填写退回原因。", "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))
        db().execute(
            "update expenses set status = 'returned', return_reason = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ? where id = ?",
            (reason, g.user["id"], now(), now(), expense_id),
        )
        notify_expense_participants(expense, "报销已被退回", f"报销 {expense['expense_number']} 已被退回。原因：{reason}", url_for("expense_detail", expense_id=expense_id))
        log_action("return", "expense", expense_id, expense["expense_number"], f"原因：{reason}")
        db().commit()
        flash("报销已退回。", "success")
        return redirect(url_for("expense_detail", expense_id=expense_id))


    app.add_url_rule('/expense-duplicate-checks/<int:check_id>/review', endpoint='review_expense_duplicate_check', view_func=review_expense_duplicate_check, methods=['POST'])
    app.add_url_rule('/expenses/<int:expense_id>/return', endpoint='return_expense', view_func=return_expense, methods=['POST'])
    return {'review_expense_duplicate_check': review_expense_duplicate_check, 'return_expense': return_expense}
