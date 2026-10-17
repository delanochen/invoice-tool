"""Legacy-compatible invoice_totals helpers; explicit runtime dependencies."""

def invoice_totals(invoice_id, *, db):
    rows = db().execute("select amount, tax_rate from invoice_items where invoice_id = ?", (invoice_id,)).fetchall()
    subtotal = sum(float(row["amount"] or 0) for row in rows)
    tax = sum(float(row["amount"] or 0) * float(row["tax_rate"] or 0) / 100 for row in rows)
    return {"subtotal": subtotal, "tax": tax, "total": max(subtotal + tax, 0)}
