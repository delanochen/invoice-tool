"""Legacy-compatible invoice_loading helpers; explicit runtime dependencies."""

def load_invoice(invoice_id, *, db, require_invoice_access):
    invoice = require_invoice_access(invoice_id)
    client = db().execute("select * from clients where id = ?", (invoice["client_id"],)).fetchone()
    items = db().execute("select * from invoice_items where invoice_id = ?", (invoice_id,)).fetchall()
    return invoice, client, items


def invoice_service_order(invoice, *, db):
    if not invoice["service_order_id"]:
        return None
    return db().execute(
        "select id, order_number, client_order_number from service_orders where id = ?",
        (invoice["service_order_id"],),
    ).fetchone()
