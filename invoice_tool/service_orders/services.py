"""Work-order numbering and deletion dependencies; preserve legacy behavior."""

def next_service_order_number(*, date, db, lock_number_allocation):
    lock_number_allocation(db())
    prefix = f"SO{date.today():%y%m}"
    rows = db().execute(
        """
        select order_number from service_orders
        where order_number like ?
        order by order_number
        """,
        (f"{prefix}%",),
    ).fetchall()
    used = set()
    for row in rows:
        suffix = row["order_number"][len(prefix):]
        if suffix.isdigit() and int(suffix) > 0:
            used.add(int(suffix))
    sequence = 1
    while sequence in used:
        sequence += 1
    return f"{prefix}{sequence:03d}"


def service_order_dependency_counts(order_id, *, db):
    return {
        "reports": db().execute(
            "select count(*) as count from service_reports where service_order_id = ?",
            (order_id,),
        ).fetchone()["count"],
        "invoices": db().execute(
            "select count(*) as count from invoices where service_order_id = ? and status != 'void'",
            (order_id,),
        ).fetchone()["count"],
        "customer_reimbursements": db().execute(
            "select count(*) as count from customer_reimbursements where service_order_id = ?",
            (order_id,),
        ).fetchone()["count"],
        "expenses": db().execute(
            "select count(*) as count from expenses where service_order_id = ?",
            (order_id,),
        ).fetchone()["count"],
    }


def service_order_delete_blockers(order_id, *, service_order_dependency_counts):
    counts = service_order_dependency_counts(order_id)
    labels = {
        "reports": "工作日报",
        "invoices": "发票",
        "customer_reimbursements": "工单结算",
        "expenses": "员工报销",
    }
    return [labels[key] for key, count in counts.items() if count]
