"""Legacy-compatible settlement_date helpers; explicit runtime dependencies."""

def _reimbursement_date_key(value, *, date, datetime):
    """将 psycopg 日期/时间值统一为 'YYYY-MM-DD' 结算匹配键。"""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return ""
    # 兼容 '2026-09-18 00:00:00' / ISO '2026-09-18T00:00:00'
    return text.split("T")[0].split(" ")[0]
