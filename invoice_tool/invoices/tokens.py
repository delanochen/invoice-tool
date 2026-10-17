"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def claim_invoice_save_token(token, invoice_id=None, *, db, now):
    token = str(token or "").strip()
    if not token:
        raise ValueError("保存令牌无效，请刷新页面后重试。")
    cursor = db().execute(
        """
        insert or ignore into invoice_save_tokens (token, invoice_id, created_at)
        values (?, ?, ?)
        """,
        (token, invoice_id, now()),
    )
    return cursor.rowcount == 1


def finish_invoice_save_token(token, invoice_id, *, db):
    db().execute(
        "update invoice_save_tokens set invoice_id = ? where token = ?",
        (invoice_id, token),
    )
