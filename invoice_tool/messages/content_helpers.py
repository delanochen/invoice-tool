"""Legacy-compatible residual_messages helpers; explicit runtime dependencies."""

def create_message(user_id, title, body, link, *, db, now):
    db().execute(
        "insert into messages (user_id, title, body, link, created_at) values (?, ?, ?, ?, ?)",
        (user_id, title, body, link, now()),
    )


def review_message_body(user_name, invoice, client, total, is_resubmission, *, money):
    action = "重新提交了发票" if is_resubmission else "提交了发票"
    client_label = client["short_name"] or client["name"]
    return f"{user_name}{action} {invoice['invoice_number']} 客户:{client_label} 金额:{money(total, invoice['currency'])} 请审核。"


def return_message_body(invoice, client, total, reason, *, money):
    client_label = client["short_name"] or client["name"]
    return f"发票 {invoice['invoice_number']} 已被经理退回。客户:{client_label} 金额:{money(total, invoice['currency'])} 原因：{reason}"
