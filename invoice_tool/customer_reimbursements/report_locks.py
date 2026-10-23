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


def report_locked_by_approved_reimbursement(order, *, flash, order_has_approved_reimbursement, silent=False):
    """Return True when an approved settlement freezes report writes.

    Locked reports remain viewable through the internal edit page, while create,
    save and delete operations stay blocked so approved settlement inputs cannot
    drift after approval.
    """
    if order and order_has_approved_reimbursement(order["id"]):
        if not silent:
            flash(
                "该工单的客户结算已通过审核：不能新增、保存修改或删除日报；已有日报仍可打开只读查看。"
                "如需调整数据，请先在工单结算页面将结算退回为草稿。",
                "error",
            )
        return True
    return False
