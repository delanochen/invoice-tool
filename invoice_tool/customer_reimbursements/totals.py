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


def approved_rental_vehicle_fuel_total(order_id, *, db, money_float):
    row = db().execute(
        """
        select coalesce(sum(expense_items.amount), 0) as total
        from expenses
        join expense_items on expense_items.expense_id = expenses.id
        left join projects on projects.id = expense_items.project_id
        where expenses.service_order_id = ?
          and expenses.status = 'approved'
          and expense_items.fuel_vehicle_type = 'rental'
          and (
            lower(coalesce(projects.name, expense_items.project)) like '%fuel%'
            or lower(coalesce(projects.name, expense_items.project)) like '%gas%'
            or coalesce(projects.name, expense_items.project) like '%油费%'
            or coalesce(projects.name, expense_items.project) like '%加油%'
          )
        """,
        (order_id,),
    ).fetchone()
    return money_float(row["total"] if row else 0)
