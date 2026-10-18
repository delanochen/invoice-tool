"""Legacy-compatible dashboard_metrics helpers; explicit runtime dependencies."""

def get_metrics(*, client_filter_clause, db, has_action_permission, invoice_totals, is_external_user):
    access_clause, access_params = client_filter_clause("invoices")
    rows = db().execute(
        f"select id, currency, paid_at, payment_amount from invoices where status = 'completed' and {access_clause}",
        access_params,
    ).fetchall()
    completed = paid = unpaid = invoice_count = 0
    for row in rows:
        total = invoice_totals(row["id"])["total"]
        invoice_count += 1
        completed += total
        if row["paid_at"]:
            paid += float(row["payment_amount"] or total)
        else:
            unpaid += total
    expense_rows = db().execute(
        """
        select status, amount
        from expenses
        where status in ('approved', 'submitted', 'returned')
        """
    ).fetchall()
    pending_reimbursement = db().execute(
        """
        select coalesce(sum(amount), 0) as total
        from expenses
        where status = 'approved' and payout_status != 'paid'
        """
    ).fetchone()["total"]
    reimbursed_expenses = db().execute(
        """
        select coalesce(sum(amount), 0) as total
        from expenses
        where status = 'approved' and payout_status = 'paid'
        """
    ).fetchone()["total"]
    pending_expenses = sum(float(row["amount"] or 0) for row in expense_rows if row["status"] != "approved")
    unpaid_payroll = 0
    if has_action_permission("payroll_report", "view"):
        unpaid_payroll = db().execute(
            """
            select coalesce(sum(net_amount), 0) as total
            from employee_payment_orders
            where payment_type = 'salary'
              and status in ('pending_review', 'approved', 'pending_payment', 'payment_failed')
            """
        ).fetchone()["total"]
    if is_external_user():
        pending_reimbursement = 0
        reimbursed_expenses = 0
        pending_expenses = 0
    return {
        "invoice_count": invoice_count,
        "completed": completed,
        "paid": paid,
        "unpaid": unpaid,
        "total": completed,
        "pending_reimbursement": float(pending_reimbursement or 0),
        "reimbursed_expenses": float(reimbursed_expenses or 0),
        "pending_expenses": pending_expenses,
        "unpaid_payroll": float(unpaid_payroll or 0),
    }
