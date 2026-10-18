"""Legacy-compatible phone helpers; explicit runtime dependencies."""


def normalize_phone(value, country_code="US", *, re):
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if country_code == "US" and len(digits) == 10:
        digits = "1" + digits
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if country_code == "US" and len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if 8 <= len(digits) <= 15:
        return "+" + digits
    raise ValueError("请填写有效的手机号，并包含国家代码。")
