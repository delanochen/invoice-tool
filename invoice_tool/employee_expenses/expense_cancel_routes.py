"""Expense cancellation endpoint."""
import secrets
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_cancel_routes(app, *,
    cancel_expense_payment_order,
    db,
    log_action,
    login_required,
    now,
    require_expense,
):
    @login_required
    def cancel_expense(expense_id):
        """作废报销单（从源头作废，保留审计痕迹）。

        规则：
        - 只有 draft / submitted / returned 状态能作废（approved 的不能直接作废）
        - 关联的 ER 付款单如果是 draft → 自动取消
        - 关联的 ER 付款单如果是 approved/pending_payment/paid → 提示不能直接作废
        """
        expense, order = require_expense(expense_id)
        if expense["status"] == "cancelled":
            flash("该报销单已经是作废状态。", "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))
        if expense["status"] not in {"draft", "submitted", "returned", "approved"}:
            flash("只有保存未提交、待审核、已退回或已通过的报销可以作废。", "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))

        reason = request.form.get("cancel_reason", "").strip()
        if not reason:
            flash("请填写作废原因。", "error")
            return redirect(url_for("expense_detail", expense_id=expense_id))

        # 检查关联的 ER 付款单
        payment = db().execute(
            "select id,payment_number,status from employee_payment_orders where source_key=?",
            (f"expense:{expense_id}",),
        ).fetchone()
        if payment and payment["status"] in {"approved", "pending_payment", "paid", "reconciled"}:
            flash(
                f"该报销已关联已审核/已发放的付款单 {payment['payment_number']}（状态：{payment['status']}），"
                "不能直接作废。请先在员工付款中心作废该付款单。",
                "error",
            )
            return redirect(url_for("expense_detail", expense_id=expense_id))

        db().execute(
            "update expenses set status='cancelled', cancel_reason=?, cancelled_by=?, cancelled_at=?, updated_at=? where id=?",
            (reason, g.user["id"], now(), now(), expense_id),
        )

        # 自动取消关联的 draft ER 单
        if payment and payment["status"] == "draft":
            try:
                cancel_expense_payment_order(expense_id)
                log_action("cancel", "employee_payment", payment["id"], payment["payment_number"],
                           f"关联报销单 {expense['expense_number']} 已作废，draft ER 单一并取消")
            except Exception:
                app.logger.exception("取消关联 draft ER 单 %s 时出错", payment["id"])

        log_action("cancel", "expense", expense_id, expense["expense_number"], f"工单：{order['order_number']}，原因：{reason}")
        db().commit()
        flash("报销单已作废。", "success")
        return redirect(url_for("expense_detail", expense_id=expense_id))


    app.add_url_rule('/expenses/<int:expense_id>/cancel', endpoint='cancel_expense', view_func=cancel_expense, methods=['POST'])
    return {'cancel_expense': cancel_expense}
