"""Legacy-compatible access helpers; explicit runtime dependencies."""

def can_manage_customer_reimbursement(*, g, has_action_permission):
    return g.user and (
        has_action_permission("customer_reimbursements", "create")
        or has_action_permission("customer_reimbursements", "edit")
        or has_action_permission("customer_reimbursements", "delete")
        or has_action_permission("customer_reimbursements", "approve")
    )
