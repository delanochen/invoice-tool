"""Legacy-compatible invoice_recognition helpers; explicit runtime dependencies."""

def recognize_invoice_if_accounting_enabled(invoice_id, *, source_version=1, InvoiceRecognitionService, accounting_base_enabled, db, g):
    connection = db()
    if not accounting_base_enabled(connection):
        return None
    return InvoiceRecognitionService(connection).recognize(
        invoice_id, source_version=source_version, actor_id=g.user["id"]
    )
