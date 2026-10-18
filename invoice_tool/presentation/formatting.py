"""Legacy-compatible formatting helpers; explicit runtime dependencies."""

def role_label(role):
    labels = {
        "admin": "管理员",
        "manager": "经理",
        "finance": "财务",
        "employee": "员工",
        "user": "员工",
        "internal": "财务",
        "external": "外部管理员",
        "external_manager": "外部管理员",
        "external_employee": "外部员工",
    }
    return labels.get(role, role)


def state_label(state_code, *, current_language, localized_state_name):
    """站点州简写 + 当前语言的州名，如 "TX · 得克萨斯州"（en 下为 "TX · Texas"）。"""
    code = (state_code or "").strip().upper()
    if not code:
        return ""
    name = localized_state_name(code, current_language())
    return f"{code} · {name}" if name else code


def money(value, currency="USD"):
    symbols = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£", "JPY": "¥"}
    amount = float(value or 0)
    if currency == "JPY":
        return f"{symbols.get(currency, currency + ' ')}{amount:,.0f}"
    return f"{symbols.get(currency, currency + ' ')}{amount:,.2f}"


def hours(value):
    """时间数量（工时 / 时长 / 小时）保留两位小数；金额与单价不要用它。

    只用于**只读展示**：表单里的 input 值必须保留原始精度（如 0.25 小时），
    否则保存时会把用户填的值改掉。

    两位小数是必须的：工时按 15 分钟量化，最小档位就是 0.25 小时，
    一位小数会把 2.25 显示成 2.2（f"{2.25:.1f}" == "2.2"），与
    2.25×时薪 算出的金额对不上，用户会以为算错。去掉末尾无意义的 0，
    使 2.0 显示成 "2"、2.25 显示成 "2.25"。
    """
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    text = f"{number:.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def payment_label(invoice):
    if invoice["status"] == "void":
        return "不适用"
    if invoice["paid_at"]:
        return "已核销"
    if invoice["status"] == "completed":
        return "待核销"
    return "流程中"


def local_datetime(value, *, app_timezone, datetime, timezone):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)[:16].replace("T", " ")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(app_timezone()).strftime("%Y-%m-%d %H:%M")
