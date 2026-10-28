"""Enrich displayed source snapshots from links in the same settlement version."""


def enrich_expense_source_details(items, reimbursement_id, connection):
    sources = [source for item in items
               for group in item.get("expense_sources", {}).values() for source in group]
    if not sources:
        return
    rows = connection.execute(
        """select ei.expense_id, ei.line_key, links.amount_snapshot, links.adjustment_reason
           from customer_reimbursement_expense_links links
           join expense_items ei on ei.id = links.expense_item_id
           where links.customer_reimbursement_id = ?""", (reimbursement_id,)
    ).fetchall()
    links = {(row["expense_id"], row["line_key"]): row for row in rows}
    for source in sources:
        link = links.get((source.get("expense_id"), source.get("line_key")))
        if not link:
            continue
        if source.get("source_amount") is None and link["amount_snapshot"] is not None:
            source["source_amount"] = float(link["amount_snapshot"])
        if not source.get("adjustment_reason"):
            source["adjustment_reason"] = link["adjustment_reason"] or ""
