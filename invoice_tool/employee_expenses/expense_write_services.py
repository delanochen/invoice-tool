"""Expense save-token persistence helpers."""

def claim_expense_save_token(token, expense_id=None, *, db, now):
    token = str(token or "").strip()
    if not token:
        raise ValueError("保存令牌无效，请刷新页面后重试。")
    cursor = db().execute(
        """
        insert or ignore into expense_save_tokens (token, expense_id, created_at)
        values (?, ?, ?)
        """,
        (token, expense_id, now()),
    )
    return cursor.rowcount == 1


def finish_expense_save_token(token, expense_id, *, db):
    db().execute(
        "update expense_save_tokens set expense_id = ? where token = ?",
        (expense_id, token),
    )
