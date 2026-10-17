"""Explicit bridge to existing expense accounts-payable behavior."""

def cancel_expense_payment_order(expense_id, *, cancel_expense_payment_order, db, now, log_action):
    return cancel_expense_payment_order({
        "db": db, "now": now, "log_action": log_action,
    }, expense_id)
