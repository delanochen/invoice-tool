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


def customer_reimbursement_totals(items, mro_supplies_total=0, rental_fuel_total=0, *, Decimal, _row_field, customer_reimbursement_item_expense_amount, money_decimal, money_float):
    """结算合计（口径铁律，勿改）。
    total_amount = Σ item["total"] + rental_fuel_total。
    MRO Supplies 经 expense_settlement_invoice_map 映射表进入 other 字段，
    已通过 auto_other → item["total"] 计入合计；mro_supplies_total 参数只用于快照列/
    PDF/发票展示，**绝不能加进 total_amount**（加进去才是真翻倍）。rental_fuel_total
    才是独立于明细行的增量（不进 auto_fuel）。自洽关系：
    total_amount - (labor+lodging+other+mileage) == rental_fuel_total。
    下方迁移 SQL 里 `+ mro_supplies_total` 是老数据一次性修补，非现行口径。"""
    labor_total = sum((money_decimal(item["labor_total"]) for item in items), Decimal("0"))
    lodging_total = sum(
        (customer_reimbursement_item_expense_amount(item, "lodging") for item in items),
        Decimal("0"),
    )
    manual_travel_total = sum(
        customer_reimbursement_item_expense_amount(item, key)
        for item in items
        for key in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi")
    ) or Decimal("0")
    other_total = sum(
        (customer_reimbursement_item_expense_amount(item, "other") for item in items),
        Decimal("0"),
    )
    mileage_total = sum((money_decimal(item["mileage_total"]) for item in items), Decimal("0"))
    mro_supplies_total = money_decimal(mro_supplies_total)
    rental_fuel_total = money_decimal(rental_fuel_total)
    # 结算金额中来自员工报销的部分（人工调整后仍按来源金额展示，仅作参考）。
    employee_expense_total = sum(
        (money_decimal(_row_field(item, f"auto_{key}")) for item in items
         for key in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")),
        Decimal("0"),
    )
    travel_total = manual_travel_total + rental_fuel_total
    total_amount = (
        sum((money_decimal(item["total"]) for item in items), Decimal("0"))
        + rental_fuel_total
    )
    return {
        "labor_total": money_float(labor_total),
        "lodging_total": money_float(lodging_total),
        "travel_total": money_float(travel_total),
        "other_total": money_float(other_total),
        "mileage_total": money_float(mileage_total),
        "mro_supplies_total": money_float(mro_supplies_total),
        "rental_fuel_total": money_float(rental_fuel_total),
        "employee_expense_total": money_float(employee_expense_total),
        "total_amount": money_float(total_amount),
    }


def customer_reimbursement_column_totals(items, *, Decimal, customer_reimbursement_item_expense_amount, money_decimal, money_float):
    totals = {}
    for field in ("standard_hours", "transport_hours", "public_transport_hours", "overtime_hours", "holiday_hours", "miles"):
        totals[field] = sum(float(item[field] or 0) for item in items)
    for field in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"):
        totals[field] = money_float(sum(
            (customer_reimbursement_item_expense_amount(item, field) for item in items),
            Decimal("0"),
        ))
    for field in ("labor_total", "mileage_total", "total"):
        totals[field] = money_float(sum((money_decimal(item[field]) for item in items), Decimal("0")))
    return totals
