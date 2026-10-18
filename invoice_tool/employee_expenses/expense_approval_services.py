"""Explicit bridge to existing expense accounts-payable behavior."""

def ensure_expense_payment_order(expense_id, *, ensure_expense_payment_order, db, now, log_action, lock_number_allocation, worker_tax_status_lookup):
    return ensure_expense_payment_order({
        "db": db, "now": now, "log_action": log_action,
        "lock_number_allocation": lock_number_allocation,
        "worker_tax_status_lookup": worker_tax_status_lookup,
    }, expense_id)
