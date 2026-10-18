"""Legacy-compatible delivery_record helpers; explicit runtime dependencies."""

def record_email_delivery(entity_type, entity_id, recipient, subject, *,
                          status=None, error_message=None, employee_id=None, db, g, now):
    """记录一次邮件投递（Phase 5C 起支持 failed + error_message + employee_id）。

    新列（status / error_message / employee_id）由 0294 迁移提供，且只有调用方
    显式传参时才进 INSERT —— 迁移生效前后既有调用方（invoice /
    customer_reimbursement）的行为与列集完全不变。
    """
    columns = ["entity_type", "entity_id", "recipient", "subject", "sent_by", "sent_by_name", "sent_at"]
    values = [
        entity_type,
        entity_id,
        str(recipient or ""),
        str(subject or ""),
        g.user["id"] if g.user else None,
        g.user["name"] if g.user else "",
        now(),
    ]
    if status is not None:
        columns.extend(("status",))
        values.append(status)
    if error_message is not None:
        columns.extend(("error_message",))
        values.append(error_message)
    if employee_id is not None:
        columns.extend(("employee_id",))
        values.append(employee_id)
    placeholders = ", ".join("?" for _ in columns)
    db().execute(
        f"insert into email_delivery_logs ({', '.join(columns)}) values ({placeholders})",
        tuple(values),
    )
