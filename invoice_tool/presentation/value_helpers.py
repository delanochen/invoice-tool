"""Legacy-compatible residual_formatting helpers; explicit runtime dependencies."""

def project_color(project_id, *, PROJECT_COLORS):
    return PROJECT_COLORS[int(project_id or 0) % len(PROJECT_COLORS)]


def pdf_text(value):
    return "" if value is None else str(value)


def to_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def decimal_value(value, default, *, Decimal, InvalidOperation):
    try:
        return Decimal(str(value if value is not None and value != "" else default))
    except (InvalidOperation, ValueError):
        return Decimal(str(default))


def money_decimal(value, *, CENT, Decimal, ROUND_HALF_UP, decimal_value):
    amount = decimal_value(value)
    if abs(amount) < CENT:
        amount = Decimal("0")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def money_float(value, *, money_decimal):
    return float(money_decimal(value))


def nl2br_filter(value, *, Markup, escape):
    """Escape untrusted text, then render newlines as <br>.

    Address fields are free-text form input, so they must be escaped *before*
    being marked safe. A bare ``|safe`` let a crafted address inject markup
    (stored XSS) into the invoice detail / export pages.
    """
    text = "" if value is None else str(value)
    # str() is required: Markup.replace() escapes its replacement argument,
    # which would turn the intended "<br>" into literal "&lt;br&gt;".
    return Markup(str(escape(text)).replace("\n", "<br>"))
