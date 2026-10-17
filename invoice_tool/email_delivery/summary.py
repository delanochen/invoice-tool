"""Legacy-compatible delivery_summary helpers; explicit runtime dependencies."""

def email_delivery_summary(entity_type, entity_id, *, db):
    row = db().execute(
        """
        select count(*) as send_count,
               max(sent_at) as last_sent_at,
               max(is_legacy) as has_legacy
        from email_delivery_logs
        where entity_type = ? and entity_id = ?
        """,
        (entity_type, entity_id),
    ).fetchone()
    return {
        "count": int(row["send_count"] or 0),
        "last_sent_at": row["last_sent_at"] or "",
        "is_estimate": bool(row["has_legacy"]),
    }
