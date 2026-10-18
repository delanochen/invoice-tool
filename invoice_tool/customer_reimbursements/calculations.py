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
