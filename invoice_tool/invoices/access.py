"""Legacy-compatible invoice_access helpers; explicit runtime dependencies."""

def require_invoice_access(invoice_id, *, abort, can_access_client, can_view_invoices, db):
    if not can_view_invoices():
        abort(403)
    invoice = db().execute("select * from invoices where id = ?", (invoice_id,)).fetchone()
    if not invoice:
        abort(404)
    if not can_access_client(invoice["client_id"]):
        abort(403)
    return invoice
