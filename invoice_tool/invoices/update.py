"""Legacy-compatible invoice_update helpers; explicit runtime dependencies."""

def update_invoice_from_form(invoice_id, *, abort, accounting_base_enabled, can_access_client, db, invoice_items_from_form, posted_client_id, posted_service_order_id, recognize_invoice_if_accounting_enabled, request, save_uploaded_attachment, uploaded_attachments_from_request):
    client_id = posted_client_id()
    if not can_access_client(client_id):
        abort(403)
    service_order_id = posted_service_order_id()
    item_rows = invoice_items_from_form()
    if accounting_base_enabled() and db().execute(
        "select 1 from posting_events where source_type='invoice' and source_id=? "
        "and event_type='invoice.confirmed' limit 1", (invoice_id,),
    ).fetchone():
        raise ValueError("该发票已完成会计确认，不能直接覆盖修改；请走更正流程。")
    db().execute(
        """
        update invoices
        set client_id = ?, service_order_id = ?, issue_date = ?, due_date = ?, currency = ?,
            notes = ?
        where id = ?
        """,
        (
            client_id,
            service_order_id,
            request.form.get("issue_date"),
            request.form.get("due_date"),
            request.form.get("currency", "USD"),
            request.form.get("notes", "").strip(),
            invoice_id,
        ),
    )
    db().execute("delete from invoice_items where invoice_id = ?", (invoice_id,))
    for project, amount in item_rows:
        db().execute(
            """
            insert into invoice_items (invoice_id, project_id, description, amount, tax_rate)
            values (?, ?, ?, ?, ?)
            """,
            (invoice_id, project["id"], project["name"], amount, project["tax_rate"]),
        )
    db().execute("update invoices set status = 'completed', return_reason = null where id = ?", (invoice_id,))
    recognize_invoice_if_accounting_enabled(invoice_id)
    for uploaded in uploaded_attachments_from_request():
        if uploaded and uploaded.filename:
            save_uploaded_attachment(invoice_id, uploaded)
