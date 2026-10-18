"""账期日期与描述的纯计算，供 app.py 兼容入口调用。"""

import calendar
from datetime import date, timedelta


def shifted_month(year, month, offset):
    month_index = year * 12 + (month - 1) + int(offset)
    return month_index // 12, month_index % 12 + 1


def calculate_payment_due_date(issue_date_value, payment_term, *, month_shifter=shifted_month):
    try:
        issue = date.fromisoformat(str(issue_date_value or ""))
    except ValueError as error:
        raise ValueError("请选择有效的开票日期。") from error
    if not payment_term or payment_term["rule_type"] == "fixed_days":
        days = int(payment_term["fixed_days"] if payment_term else 30)
        return issue + timedelta(days=max(days, 0))
    cutoff_day = int(payment_term["cutoff_day"] or 1)
    due_day = int(payment_term["due_day"] or 1)
    month_offset = (
        int(payment_term["before_due_months"] or 0)
        if issue.day <= cutoff_day
        else int(payment_term["after_due_months"] or 0)
    )
    target_year, target_month = month_shifter(issue.year, issue.month, month_offset)
    target_day = min(due_day, calendar.monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def payment_term_description(payment_term):
    if payment_term["rule_type"] == "fixed_days":
        return f"开票日后 {payment_term['fixed_days']} 天到期"
    return (
        f"每月 {payment_term['cutoff_day']} 日截止（含当天）；截止日前到期月 +"
        f"{payment_term['before_due_months']}，截止日后到期月 +{payment_term['after_due_months']}；"
        f"到期日 {payment_term['due_day']} 日"
    )
