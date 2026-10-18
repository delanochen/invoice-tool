"""Legacy-compatible settlement_items helpers; explicit runtime dependencies."""

def customer_reimbursement_items(reimbursement_id, *, db):
    return db().execute(
        """
        select *
        from customer_reimbursement_items
        where customer_reimbursement_id = ?
        order by sort_order, id
        """,
        (reimbursement_id,),
    ).fetchall()
