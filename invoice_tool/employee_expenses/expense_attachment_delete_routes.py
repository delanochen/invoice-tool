"""Expense attachment deletion endpoint."""
import secrets
import os
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_attachment_delete_routes(app, *,
    db,
    expense_attachment_path,
    is_manager,
    login_required,
    require_expense,
):
    @login_required
    def delete_expense_attachment(attachment_id):
        attachment = db().execute("select * from expense_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        expense, order = require_expense(attachment["expense_id"])
        if expense["created_by"] != g.user["id"] and not is_manager():
            abort(403)
        if expense["status"] not in {"draft", "returned"}:
            abort(403)
        try:
            os.remove(expense_attachment_path(attachment))
        except FileNotFoundError:
            pass
        try:
            # 「重复报销检查」按附件存记录，先清引用再删附件（PostgreSQL 外键不一定级联）
            db().execute(
                "delete from expense_duplicate_checks where attachment_id = ? or matched_attachment_id = ?",
                (attachment_id, attachment_id),
            )
            db().execute("delete from expense_attachments where id = ?", (attachment_id,))
            db().commit()
        except Exception:
            db().rollback()
            app.logger.exception("删除报销附件 %s 失败", attachment_id)
            raise RuntimeError("删除附件失败，数据没有改动：这张附件还被重复报销检查记录引用。请稍后重试或联系管理员。")
        flash("附件已删除。", "success")
        return redirect(url_for("edit_expense", expense_id=expense["id"]))


    app.add_url_rule('/expense-attachments/<int:attachment_id>/delete', endpoint='delete_expense_attachment', view_func=delete_expense_attachment, methods=['POST'])
    return {'delete_expense_attachment': delete_expense_attachment}
