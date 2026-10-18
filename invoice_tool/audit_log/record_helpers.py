"""Legacy-compatible audit helpers; explicit runtime dependencies."""

def log_action(action, entity_type, entity_id, entity_label, summary="", *, db, g, now):
    if not g.user:
        return
    db().execute(
        """
        insert into audit_logs (
            user_id, user_name, action, entity_type, entity_id, entity_label, summary, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            g.user["name"],
            action,
            entity_type,
            entity_id,
            str(entity_label or ""),
            str(summary or ""),
            now(),
        ),
    )


def log_login_action(user, *, db, now, request_ip_address, role_label):
    db().execute(
        """
        insert into audit_logs (
            user_id, user_name, action, entity_type, entity_id, entity_label, summary, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"],
            user["name"],
            "login",
            "user",
            user["id"],
            user["email"] or user["name"],
            f"角色：{role_label(user['role'])}；IP：{request_ip_address() or '-'}",
            now(),
        ),
    )
