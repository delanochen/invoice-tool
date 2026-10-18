"""Legacy-compatible mro_total helpers; explicit runtime dependencies."""

def approved_mro_supplies_total(order_id, cutoff_at=None, exclude_ignored_for_reimbursement_id=None, *, Decimal, approved_customer_reimbursement_expense_rows, customer_reimbursement_expense_field, money_decimal, money_float, project_name_key):
    total = sum(
        (
            money_decimal(row["amount"])
            for row in approved_customer_reimbursement_expense_rows(
                order_id, cutoff_at, exclude_ignored_for_reimbursement_id=exclude_ignored_for_reimbursement_id
            )
            if customer_reimbursement_expense_field(row["project_name"]) == "other"
            and project_name_key(row["project_name"]).startswith(project_name_key("MRO Supplies"))
        ),
        Decimal("0"),
    )
    return money_float(total)
