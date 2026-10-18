"""Legacy-compatible messages helpers; explicit runtime dependencies."""

def notify_role(roles, title, body, link=None, exclude_user_ids=None, *, create_message, db):
    excluded = {int(user_id) for user_id in (exclude_user_ids or []) if user_id is not None}
    placeholders = ",".join("?" for _ in roles)
    rows = db().execute(f"select id from users where role in ({placeholders})", list(roles)).fetchall()
    for row in rows:
        if row["id"] in excluded:
            continue
        create_message(row["id"], title, body, link)


def unread_message_count(*, db, g):
    if not g.user:
        return 0
    return db().execute(
        "select count(*) as count from messages where user_id = ? and is_read = 0",
        (g.user["id"],),
    ).fetchone()["count"]


def _message_ids_for_user(raw_ids, *, db, g):
    """把表单里的 id 收敛成当前用户自己的消息 id。

    消息是「每人一份」的私有数据（`where user_id = ?` 就是权限边界），
    所以这里必须再过滤一次 —— 不能因为前端只传自己的 id 就信任它。
    非数字、越界的 id 一律丢弃，不报错。
    """
    ids = []
    for value in raw_ids:
        try:
            message_id = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if message_id > 0:
            ids.append(message_id)
    if not ids:
        return []
    # 去重后保持稳定顺序，便于日志与测试断言。
    unique = sorted(set(ids))
    placeholders = ",".join("?" for _ in unique)
    owned = db().execute(
        f"select id from messages where user_id = ? and id in ({placeholders})",
        (g.user["id"], *unique),
    ).fetchall()
    return [row["id"] for row in owned]
