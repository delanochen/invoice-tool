"""Expense AI review endpoint."""
import secrets
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_ai_review_routes(app, *,
    resolve_EXPENSE_ATTACHMENTS_DIR,
    db,
    has_action_permission,
    login_required,
    run_expense_ai_review_task,
):
    @login_required
    def expense_ai_review(expense_id):
        """AI 智能审核意见。

        GET：只返回已有记录（夜间批量 worker 生成；无记录返回 status=none，不触发模型）。
        POST：强制重新审核（同步执行，详情页「立即审核/重新审核」按钮用）。需 expenses/ai_review 权限。
        """
        if not has_action_permission("expenses", "ai_review"):
            abort(403)
        expense = db().execute("select * from expenses where id = ?", (expense_id,)).fetchone()
        if not expense:
            abort(404)
        if request.method == "GET":
            row = db().execute(
                "select * from expense_ai_reviews where expense_id = ?", (expense_id,)
            ).fetchone()
            if not row:
                return jsonify({"ok": True, "status": "none", "conclusion": "", "content": "", "model": "", "error": "", "updated_at": ""})
            return jsonify({
                "ok": row["status"] != "failed",
                "status": row["status"], "conclusion": row["conclusion"],
                "content": row["content"], "model": row["model"],
                "error": row["error"], "updated_at": row["updated_at"],
            })
        result = run_expense_ai_review_task(db(), expense_id, resolve_EXPENSE_ATTACHMENTS_DIR(), force=True)
        db().commit()
        status_code = 200 if result["ok"] else 502
        return jsonify(result), status_code


    app.add_url_rule('/expenses/<int:expense_id>/ai_review', endpoint='expense_ai_review', view_func=expense_ai_review, methods=['GET', 'POST'])
    return {'expense_ai_review': expense_ai_review}
