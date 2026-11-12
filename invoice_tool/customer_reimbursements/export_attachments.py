"""Customer-facing settlement attachment export rules."""


def exportable_settlement_attachments(conn, reimbursement_id):
    """Return settlement attachments that belong in customer-facing exports.

    Legacy mileage-proof copies are omitted. Other manual uploads are kept.
    Expense-source attachments are
    exported only when at least one expense line represented by that attachment
    has a positive customer settlement amount in this settlement. A stored
    settled_amount of NULL means the historical/full amount, so amount_snapshot
    (and finally the live expense amount) is used as the fallback.
    """
    return conn.execute(
        """
        select ca.*
        from customer_reimbursement_attachments ca
        where ca.customer_reimbursement_id = ?
          and (
            ca.source_expense_attachment_id is not null
            or (
                coalesce(ca.original_filename, '') not like '里程佐证-%'
                and coalesce(ca.original_filename, '') <> 'mileage-proof'
            )
          )
          and (
            ca.source_expense_attachment_id is null
            or exists (
                select 1
                from expense_attachments ea
                join expense_items ei
                  on ei.expense_id = ea.expense_id
                 and (
                    ea.expense_item_key is null
                    or ea.expense_item_key = ''
                    or ei.line_key = ea.expense_item_key
                 )
                join customer_reimbursement_expense_links links
                  on links.customer_reimbursement_id = ca.customer_reimbursement_id
                 and links.expense_item_id = ei.id
                where ea.id = ca.source_expense_attachment_id
                  and coalesce(links.settled_amount, links.amount_snapshot, ei.amount, 0) > 0
            )
          )
        order by ca.id
        """,
        (reimbursement_id,),
    ).fetchall()


def settlement_attachment_expenses(conn, reimbursement_id):
    """Map source attachments to the expense lines included in the export."""
    rows = conn.execute(
        """
        select ea.id as attachment_id, expenses.expense_date,
               coalesce(beneficiary.name, creator.name) as worker_name,
               coalesce(projects.name, ei.project) as project_name,
               coalesce(links.amount_snapshot, ei.amount, 0) as original_amount,
               coalesce(links.settled_amount, links.amount_snapshot, ei.amount, 0) as settled_amount,
               links.adjustment_reason
        from customer_reimbursement_expense_links links
        join expense_items ei on ei.id = links.expense_item_id
        join expenses on expenses.id = ei.expense_id
        join expense_attachments ea on ea.expense_id = ei.expense_id
            and (coalesce(ea.expense_item_key, '') = '' or ea.expense_item_key = ei.line_key)
        left join projects on projects.id = ei.project_id
        left join users creator on creator.id = expenses.created_by
        left join users beneficiary on beneficiary.id = expenses.beneficiary_id
        where links.customer_reimbursement_id = ?
          and coalesce(links.settled_amount, links.amount_snapshot, ei.amount, 0) > 0
        order by ei.id, ea.id
        """,
        (reimbursement_id,),
    ).fetchall()
    by_attachment = {}
    for row in rows:
        by_attachment.setdefault(row["attachment_id"], []).append(row)
    return by_attachment
