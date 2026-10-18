"""Legacy-compatible order_validation helpers; explicit runtime dependencies."""

def client_order_number_warning(customer_name, client_order_number):
    """Return a warning for the current Sanhe Tongfei work-order number convention."""
    normalized_customer = (customer_name or "").strip().casefold()
    if "三河同飞" not in normalized_customer and "sanhe tongfei" not in normalized_customer:
        return ""
    number = (client_order_number or "").strip()
    if not number:
        return "未填写服务订单号码，无法按三河同飞规则识别。"
    if not number.startswith("SHPG") and len(number) < 16:
        return "服务订单号码应以 SHPG 开头且总长度不少于16位，当前格式不符合三河同飞规则。"
    if not number.startswith("SHPG"):
        return "服务订单号码应以 SHPG 开头，当前格式不符合三河同飞规则。"
    if len(number) < 16:
        return f"服务订单号码总长度应不少于16位，当前为{len(number)}位。"
    return ""
