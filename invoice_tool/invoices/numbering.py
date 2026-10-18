"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def next_invoice_number(*, date, db, lock_number_allocation):
    # 新单据统一规则：IN-YYMM-4位流水（与 SL/ER/EA/AS 同构）。
    # 历史发票号为 PP-YYMM-随机6位，存量不动；新号只统计 IN- 前缀同月份的最大流水。
    lock_number_allocation(db())
    stamp = date.today().strftime("%y%m")
    root = f"IN-{stamp}-"
    rows = db().execute(
        "select invoice_number from invoices where invoice_number like ?",
        (root + "%",),
    ).fetchall()
    sequence = 1
    for row in rows:
        try:
            value = int(str(row["invoice_number"]).rsplit("-", 1)[1])
        except (ValueError, IndexError):
            continue
        if value >= sequence:
            sequence = value + 1
    return f"{root}{sequence:04d}"


def invoice_number_exists(invoice_number, exclude_invoice_id=None, *, db):
    if exclude_invoice_id:
        return db().execute(
            "select id from invoices where invoice_number = ? and id != ?",
            (invoice_number, exclude_invoice_id),
        ).fetchone() is not None
    return db().execute("select id from invoices where invoice_number = ?", (invoice_number,)).fetchone() is not None
