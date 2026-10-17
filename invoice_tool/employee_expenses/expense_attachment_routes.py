"""Expense attachment preview, download and interpretation endpoints."""
import secrets
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_attachment_routes(app, *,
    resolve_EXPENSE_ATTACHMENTS_DIR,
    db,
    expense_attachment_path,
    login_required,
    require_expense,
    run_expense_attachment_interpretation,
    safe_attachment_response,
):
    @login_required
    def preview_expense_attachment(attachment_id):
        attachment = db().execute("select * from expense_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        require_expense(attachment["expense_id"])
        return safe_attachment_response(
            expense_attachment_path(attachment),
            attachment["original_filename"],
        )


    @login_required
    def download_expense_attachment(attachment_id):
        attachment = db().execute("select * from expense_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        require_expense(attachment["expense_id"])
        return send_file(expense_attachment_path(attachment), as_attachment=True, download_name=attachment["original_filename"])


    @login_required
    def get_expense_attachment_interpretation(attachment_id):
        """弹窗展示附件的智能解读结果。"""
        attachment = db().execute("select * from expense_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        require_expense(attachment["expense_id"])
        row = db().execute(
            "select * from expense_attachment_interpretations where attachment_id = ?",
            (attachment_id,),
        ).fetchone()
        if not row:
            return jsonify({"ok": True, "status": "none", "content": "", "error": "", "model": "", "updated_at": ""})
        return jsonify({
            "ok": True,
            "status": row["status"],
            "content": row["content"],
            "error": row["error"],
            "model": row["model"],
            "updated_at": row["updated_at"],
        })


    @login_required
    def interpret_expense_attachment(attachment_id):
        """立即调用配置的本地大模型解读该附件（已有结果则覆盖重做）。"""
        attachment = db().execute("select * from expense_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        require_expense(attachment["expense_id"])
        result = run_expense_attachment_interpretation(
            db(), attachment_id, resolve_EXPENSE_ATTACHMENTS_DIR(), force=True
        )
        db().commit()
        status_code = 200 if result["ok"] else 502
        return jsonify({"ok": result["ok"], "status": result["status"], "error": result["error"]}), status_code


    app.add_url_rule('/expense-attachments/<int:attachment_id>', endpoint='preview_expense_attachment', view_func=preview_expense_attachment, methods=['GET'])
    app.add_url_rule('/expense-attachments/<int:attachment_id>/download', endpoint='download_expense_attachment', view_func=download_expense_attachment, methods=['GET'])
    app.add_url_rule('/expense-attachments/<int:attachment_id>/interpretation', endpoint='get_expense_attachment_interpretation', view_func=get_expense_attachment_interpretation, methods=['GET'])
    app.add_url_rule('/expense-attachments/<int:attachment_id>/interpret', endpoint='interpret_expense_attachment', view_func=interpret_expense_attachment, methods=['POST'])
    return {'preview_expense_attachment': preview_expense_attachment, 'download_expense_attachment': download_expense_attachment, 'get_expense_attachment_interpretation': get_expense_attachment_interpretation, 'interpret_expense_attachment': interpret_expense_attachment}
