"""Expense deletion endpoint."""
import secrets
import shutil
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_delete_routes(app, *,
    can_delete_expense,
    cancel_expense_payment_order,
    db,
    expense_attachment_dir,
    log_action,
    login_required,
    require_expense,
):
    @login_required
    def delete_expense(expense_id):
        expense, order = require_expense(expense_id)
        if not can_delete_expense(expense):
            if expense["status"] not in {"draft", "returned"}:
                flash("只有保存未提交或被退回的报销可以删除。", "error")
                return redirect(url_for("expense_detail", expense_id=expense_id))
            abort(403)
        # 上游报销删除前，先看它是否已生成员工付款单。已发放/已对账的付款单是
        # 财务凭证，绝不随报销单删除（先在付款中心作废/冲销）；未付款的付款单
        # 则随报销单一并清理，避免在员工付款中心留下指向已删报销的孤儿单。
        payment = db().execute(
            "select id,payment_number,status from employee_payment_orders where source_key=?",
            (f"expense:{expense_id}",),
        ).fetchone()
        if payment and payment["status"] in {"paid", "reconciled"}:
            flash(
                f"该报销已关联已发放的付款单 {payment['payment_number']}，不能删除。"
                "请先在员工付款中心作废/冲销该付款单。",
                "error",
            )
            return redirect(url_for("expense_detail", expense_id=expense_id))
        shutil.rmtree(expense_attachment_dir(expense_id), ignore_errors=True)
        try:
            if payment:
                # 未付款单：复用取消逻辑（自动冲销借款抵扣），再连同子表彻底删除。
                cancel_expense_payment_order(expense_id)
                component_ids = [
                    row["id"] for row in db().execute(
                        "select id from employee_payment_components where payment_order_id=?",
                        (payment["id"],),
                    ).fetchall()
                ]
                if component_ids:
                    marks = ",".join("?" for _ in component_ids)
                    try:
                        db().execute(
                            f"delete from payroll_component_correction_allocations where component_id in ({marks})",
                            component_ids,
                        )
                    except Exception:
                        # 新建/旧库可能尚未建纠偏分配表；未付款的报销 ER 也不可能进入
                        # 工资纠偏循环，此处缺表不影响主流程。
                        app.logger.exception("清理付款单 %s 的纠偏分配记录时跳过（表可能不存在）", payment["id"])
                db().execute("delete from employee_advance_applications where payment_order_id=?", (payment["id"],))
                db().execute("delete from employee_payment_components where payment_order_id=?", (payment["id"],))
                db().execute("delete from payment_order_events where payment_order_id=?", (payment["id"],))
                db().execute("delete from payment_order_sources where payment_order_id=?", (payment["id"],))
                db().execute("delete from employee_payment_orders where id=?", (payment["id"],))
                log_action("delete", "employee_payment", payment["id"], payment["payment_number"],
                           f"关联报销单 {expense['expense_number']} 已删除，未付款付款单一并清理")
            # 依赖行逐个显式删除，不依赖外键级联；漏掉任何一张（尤其
            # 「重复报销检查」里被别人 matched_expense_id
            # 指到这条报销的记录）就会在 delete 时抛外键错误，用户只看到 500。
            db().execute(
                "delete from expense_duplicate_checks where expense_id = ? or matched_expense_id = ?",
                (expense_id, expense_id),
            )
            db().execute("delete from expense_save_tokens where expense_id = ?", (expense_id,))
            db().execute("delete from expense_attachments where expense_id = ?", (expense_id,))
            db().execute("delete from expense_items where expense_id = ?", (expense_id,))
            db().execute("delete from expenses where id = ?", (expense_id,))
            log_action("delete", "expense", expense_id, expense["expense_number"], f"工单：{order['order_number']}")
            db().commit()
        except Exception:
            db().rollback()
            app.logger.exception("删除报销 %s 失败", expense_id)
            # RuntimeError 由全局处理器转成页面上的友好提示，不再抛 500
            raise RuntimeError(
                "删除报销失败，数据没有改动：这条报销还被其它记录引用（例如重复报销检查记录、发票或工单结算）。"
                "请先解除这些引用，或联系管理员处理。"
            )
        flash("报销已删除。", "success")
        return redirect(url_for("service_order_detail", order_id=order["id"]))


    app.add_url_rule('/expenses/<int:expense_id>/delete', endpoint='delete_expense', view_func=delete_expense, methods=['POST'])
    return {'delete_expense': delete_expense}
