"""Legacy-compatible settlement_access helpers; explicit runtime dependencies."""

def require_customer_reimbursement(reimbursement_id, *, abort, can_view_customer_reimbursement, db, require_service_order):
    if not can_view_customer_reimbursement():
        abort(403)
    reimbursement = db().execute("select * from customer_reimbursements where id = ?", (reimbursement_id,)).fetchone()
    if not reimbursement:
        abort(404)
    order = require_service_order(reimbursement["service_order_id"])
    return reimbursement, order
