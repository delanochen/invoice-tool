"""Expense approval endpoint."""
import secrets
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_approval_routes(app, *,
    db,
    ensure_expense_payment_order,
    expense_beneficiary_id,
    latest_customer_reimbursement,
    log_action,
    login_required,
    money,
    notify_expense_participants,
    notify_role,
    now,
    require_expense,
    update_customer_reimbursement_totals,
):
    @login_required
    def approve_expense(expense_id):
        expense, order = require_expense(expense_id)
        if expense["status"] != "submitted":
            flash("只有待经理审核的报销可以通过。", "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))
        db().execute(
            """
            update expenses
            set status = 'approved', return_reason = null, reviewed_by = ?, reviewed_at = ?,
                payout_status = 'pending', reimbursed_by = null, reimbursed_at = null, updated_at = ?
            where id = ?
            """,
            (g.user["id"], now(), now(), expense_id),
        )
        reimbursement = latest_customer_reimbursement(order["id"])
        if reimbursement and reimbursement["status"] in {"draft", "returned"}:
            update_customer_reimbursement_totals(reimbursement["id"])
        message_link = url_for("expense_detail", expense_id=expense_id)
        message_body = (
            f"{g.user['name']}已审核通过报销 {expense['expense_number']}，"
            f"工单 {order['order_number']}，金额 {money(expense['amount'], expense['currency'])}。"
        )
        notify_expense_participants(expense, "报销已审核通过", message_body, message_link)
        notify_role(
            ["admin"],
            "报销已审核通过",
            message_body,
            message_link,
            exclude_user_ids={expense["created_by"], expense_beneficiary_id(expense), g.user["id"]},
        )
        log_action("approve", "expense", expense_id, expense["expense_number"], f"工单：{order['order_number']}")
        # Approval now creates the accounts-payable document while preserving the
        # expense's existing review and profitability semantics.
        # Phase 3A：组件快照硬校验失败（明细合计 != gross 等）时整体回滚，
        # 报销单保持 submitted，不让没有快照的 ER 落库。
        try:
            payment_id = ensure_expense_payment_order(expense_id)
        except ValueError as error:
            db().rollback()
            flash(f"报销已通过，但员工付款单生成失败，已整体回滚：{error}", "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))
        db().commit()
        flash("报销已审核通过，员工付款单已自动生成。" if payment_id else "报销已审核通过。", "success")
        return redirect(url_for("expense_detail", expense_id=expense_id))


    app.add_url_rule('/expenses/<int:expense_id>/approve', endpoint='approve_expense', view_func=approve_expense, methods=['POST'])
    return {'approve_expense': approve_expense}
