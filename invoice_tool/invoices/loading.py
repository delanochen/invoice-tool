"""Legacy-compatible invoice_loading helpers; explicit runtime dependencies."""
from invoice_tool.invoices import order_links as _invoice_order_links


def load_invoice(invoice_id, *, db, require_invoice_access):
    invoice = require_invoice_access(invoice_id)
    client = db().execute("select * from clients where id = ?", (invoice["client_id"],)).fetchone()
    items = db().execute("select * from invoice_items where invoice_id = ?", (invoice_id,)).fetchall()
    return invoice, client, items


def invoice_service_order(invoice, *, db):
    """发票关联的工单。多工单时返回主工单（关系表里最早一条），回填前的老发票仍走旧列。"""
    order_id = _invoice_order_links.primary_order_id(invoice, db=db)
    if not order_id:
        return None
    return db().execute(
        "select id, order_number, client_order_number from service_orders where id = ?",
        (order_id,),
    ).fetchone()
