"""Legacy-compatible expense_numbering helpers; explicit runtime dependencies."""

def next_expense_number(*, date, db, lock_number_allocation, set_setting, setting_int):
    lock_number_allocation(db())
    prefix = f"EX{date.today():%y%m}"
    # 现存最大号；删除报销会让它下降，不能单独作为下一号依据，否则被删的号会被复用。
    row = db().execute(
        """
        select expense_number from expenses
        where expense_number like ?
        order by expense_number desc limit 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    current_max = 0
    if row:
        try:
            current_max = int(row["expense_number"][len(prefix):])
        except (TypeError, ValueError):
            current_max = 0
    # 持久化水位：只增不减。已发过的号（包括已被删除报销曾经占用的号）
    # 永远不再分配给新报销，避免历史号段撞单。
    hw_key = f"expense_high_water_{prefix}"
    high_water = setting_int(hw_key, 0)
    n = max(current_max, high_water) + 1
    # 防御：理论上不会撞号，历史数据/并发下跳过任何已占用号。
    while db().execute(
        "select id from expenses where expense_number = ?",
        (f"{prefix}{n:03d}",),
    ).fetchone():
        n += 1
    set_setting(hw_key, str(n))
    return f"{prefix}{n:03d}"
