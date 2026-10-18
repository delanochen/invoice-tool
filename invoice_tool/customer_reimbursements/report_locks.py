"""Legacy-compatible report_locks helpers; explicit runtime dependencies."""

def order_has_approved_reimbursement(order_id, *, db):
    """Return True if the service order already has an approved customer reimbursement.

    Once the customer reimbursement is approved, the order's daily reports are
    locked: no create / edit / delete. The reimbursement must be returned to
    draft first. This prevents phantom report hours that affect payroll without
    changing what the customer pays.
    """
    if not order_id:
        return False
    row = db().execute(
        "select 1 from customer_reimbursements where service_order_id = ? and status = 'approved' limit 1",
        (order_id,),
    ).fetchone()
    return row is not None


def report_locked_by_approved_reimbursement(order, *, flash, order_has_approved_reimbursement):
    """Flash a friendly lock message and return True when the order is locked."""
    if order and order_has_approved_reimbursement(order["id"]):
        flash(
            "该工单的客户结算已通过审核，日报已锁定。如需新建、修改或删除日报，"
            "请先在工单结算页面将结算退回为草稿。",
            "error",
        )
        return True
    return False
