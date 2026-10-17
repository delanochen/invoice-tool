"""Expense attachment transfer endpoint."""
import secrets
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_transfer_routes(app, *,
    DatabaseError,
    IntegrityError,
    can_transfer_expense_attachment,
    copy_file_to_customer_reimbursement_attachment,
    db,
    expense_attachment_path,
    latest_customer_reimbursement,
    log_action,
    login_required,
    require_expense,
):
    @login_required
    def transfer_expense_attachment(attachment_id):
        attachment = db().execute("select * from expense_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        expense, order = require_expense(attachment["expense_id"])
        if not can_transfer_expense_attachment(expense):
            abort(403)
        return_url = url_for(
            "expense_detail" if request.form.get("return_to") == "detail" else "edit_expense",
            expense_id=expense["id"],
        )

        def finish_transfer(message, category="success"):
            flash(message, category)
            if request.headers.get("X-History-Replace") == "1":
                return jsonify(ok=category == "success", redirect=return_url)
            return redirect(return_url)

        existing = db().execute(
            "select id from customer_reimbursement_attachments where source_expense_attachment_id = ?",
            (attachment_id,),
        ).fetchone()
        if existing:
            return finish_transfer("该附件已经传递到工单结算。")

        reimbursement = latest_customer_reimbursement(order["id"])
        if not reimbursement:
            return finish_transfer("该工单尚未生成工单结算，暂时无法传递附件。", "error")
        if reimbursement["status"] not in {"draft", "returned"}:
            return finish_transfer("工单结算已经提交或审核完成，不能再传递附件。", "error")

        try:
            copied = copy_file_to_customer_reimbursement_attachment(
                reimbursement["id"],
                expense_attachment_path(attachment),
                attachment["original_filename"],
                attachment["content_type"],
                g.user["id"],
                source_expense_attachment_id=attachment_id,
            )
            if not copied:
                raise ValueError("附件文件不存在，无法传递。")
            log_action(
                "transfer",
                "expense_attachment",
                attachment_id,
                attachment["original_filename"],
                f"报销：{expense['expense_number']}；工单结算：{reimbursement['file_name']}",
            )
            db().commit()
        except IntegrityError:
            db().rollback()
            return finish_transfer("该附件已经传递到工单结算。")
        except (OSError, DatabaseError, ValueError) as error:
            db().rollback()
            app.logger.exception("Failed to transfer expense attachment %s", attachment_id)
            return finish_transfer(str(error) or "附件传递失败，请稍后重试。", "error")

        return finish_transfer("附件已复制到工单结算。")


    app.add_url_rule('/expense-attachments/<int:attachment_id>/transfer', endpoint='transfer_expense_attachment', view_func=transfer_expense_attachment, methods=['POST'])
    return {'transfer_expense_attachment': transfer_expense_attachment}
