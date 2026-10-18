"""Legacy-compatible residual_settlement_values helpers; explicit runtime dependencies."""

def is_us_weekend_or_holiday(day, *, observed_us_holidays):
    return day.weekday() >= 5 or day in observed_us_holidays(day.year)


def reimbursement_number(value):
    number = float(value or 0)
    return f"{number:,.2f}" if number else ""


def reimbursement_paragraph(value, style, *, Paragraph):
    text = "" if value is None else str(value)
    return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"), style)
