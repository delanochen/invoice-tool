"""Legacy-compatible row_field helpers; explicit runtime dependencies."""

def _row_field(row, key, default=0):
    """读取结算明细行的字段，兼容 dict 与数据库 Row（后者没有 .get）。"""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def customer_reimbursement_item_expense_amount(row, field_name, *, _row_field, money_decimal):
    """单元格实际生效金额。

    结算单同时保存两列：
      * ``field_name``  —— 人工调整值（0 表示未调整，沿用来源金额）
      * ``auto_field_name`` —— 从员工报销自动转入的来源合计
    用户看到、以及计入合计的就是二者中「手改优先」的那一个，不能相加，
    否则会把来源金额重复计入。
    """
    manual = money_decimal(_row_field(row, field_name))
    return manual if manual else money_decimal(_row_field(row, f"auto_{field_name}"))


def calculate_customer_reimbursement_item(row, sort_order=0, *, Decimal, _row_field, customer_reimbursement_item_expense_amount, decimal_value, money_decimal, money_float):
    labor_total = (
        decimal_value(row.get("standard_hours")) * money_decimal(row.get("standard_rate"))
        + decimal_value(row.get("transport_hours")) * money_decimal(row.get("transport_rate"))
        + decimal_value(row.get("public_transport_hours")) * money_decimal(row.get("public_transport_rate"))
        + decimal_value(row.get("overtime_hours")) * money_decimal(row.get("overtime_rate"))
        + decimal_value(row.get("holiday_hours")) * money_decimal(row.get("holiday_rate"))
    )
    # 自驾交通计费：报价单/合同按自驾计费方式生成的行带 driving_amount，
    # 直接采用（仅里程=里程×单价 / 仅时长=时长×单价 / 里程+时长=两项相加）。
    # 未带 driving_amount 的行（既有合同路径）仍按 里程 × 里程单价 计费，口径不变。
    driving_amount = _row_field(row, "driving_amount", None)
    if driving_amount is not None:
        mileage_total = money_decimal(driving_amount)
    else:
        mileage_total = decimal_value(row.get("miles")) * money_decimal(row.get("mileage_rate"))
    travel_fields = ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")
    travel_total = sum(
        (
            customer_reimbursement_item_expense_amount(row, key)
            for key in travel_fields
        ),
        Decimal("0"),
    )
    row["labor_total"] = money_float(labor_total)
    row["mileage_total"] = money_float(mileage_total)
    row["total"] = money_float(money_decimal(labor_total) + money_decimal(mileage_total) + travel_total)
    row["sort_order"] = sort_order
    return row
