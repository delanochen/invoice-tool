"""Optional work-order settlement dates and shared inclusive range filters."""
from datetime import date
from flask import abort


def optional_settlement_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        abort(400, description="预计结算日必须是有效的 YYYY-MM-DD 日期。")
    if parsed.isoformat() != value:
        abort(400, description="预计结算日必须是有效的 YYYY-MM-DD 日期。")
    return value


def settlement_date_filters(args, clauses, params):
    start = optional_settlement_date(args.get("expected_settlement_from"))
    end = optional_settlement_date(args.get("expected_settlement_to"))
    if start and end and start > end:
        abort(400, description="预计结算日起始日期不能晚于截止日期。")
    if start:
        clauses.append("service_orders.expected_settlement_date >= ?")
        params.append(start)
    if end:
        clauses.append("service_orders.expected_settlement_date <= ?")
        params.append(end)
    return {"expected_settlement_from": start or "", "expected_settlement_to": end or ""}
