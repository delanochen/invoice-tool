"""Customer-facing settlement attachment export rules."""


def exportable_settlement_attachments(conn, reimbursement_id):
    """Return settlement attachments that belong in customer-facing exports.

    Manual settlement uploads are always kept. Expense-source attachments are
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
