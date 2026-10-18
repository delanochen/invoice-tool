"""Legacy-compatible source_queries helpers; explicit runtime dependencies."""

def approved_customer_reimbursement_expense_rows(order_id, cutoff_at=None, exclude_ignored_for_reimbursement_id=None, *, db):
    return db().execute(
        """
        select expense_items.id as expense_item_id, expense_items.amount, expense_items.fuel_vehicle_type,
               expenses.id as expense_id, expenses.expense_number, expense_items.line_key,
               expense_items.description as item_description,
               (select count(*) from expense_items ordinal where ordinal.expense_id = expenses.id
                and (ordinal.sort_order < expense_items.sort_order or (ordinal.sort_order = expense_items.sort_order and ordinal.id <= expense_items.id))) as line_number,
               coalesce(projects.name, expense_items.project) as project_name,
               expenses.expense_date, users.name as worker_name
        from expenses
        join expense_items on expense_items.expense_id = expenses.id
        join users on users.id = coalesce(expenses.beneficiary_id, expenses.created_by)
        left join projects on projects.id = expense_items.project_id
        where expenses.service_order_id = ? and expenses.status = 'approved'
          and (? is null or coalesce(expenses.reviewed_at, expenses.updated_at, expenses.created_at) <= ?)
          and (? is null or not exists (
                select 1 from customer_reimbursement_expense_ignores ig
                where ig.customer_reimbursement_id = ? and ig.expense_item_id = expense_items.id
          ))
        order by expenses.expense_date, expenses.id, expense_items.sort_order, expense_items.id
        """,
        (
            order_id,
            cutoff_at, cutoff_at,
            exclude_ignored_for_reimbursement_id, exclude_ignored_for_reimbursement_id,
        ),
    ).fetchall()


def customer_reimbursement_pending_sources(reimbursement, *, Decimal, _reimbursement_date_key, customer_reimbursement_expense_field, customer_reimbursement_items, db, json, money_decimal, money_float):
    """只读比对：该工单下尚未计入当前结算的报销明细，按报销状态分四档。

    - includable（已审核未计入且未标记忽略）：硬拦提交
    - ignored（已审核未计入但已被显式标记忽略）：不拦，横幅展示可取消
    - pending（待审核）：默认拦（confirm_pending=1 可放行）
    - returned（已退回）：不拦（退回提示由横幅呈现）
    零写库，仅用于结算页横幅与 gate 校验。"""
    order_id = reimbursement["service_order_id"]
    reimb_id = reimbursement["id"]
    ignored_rows = db().execute(
        "select expense_item_id, reason from customer_reimbursement_expense_ignores where customer_reimbursement_id=?",
        (reimb_id,),
    ).fetchall()
    ignored_set = {row["expense_item_id"] for row in ignored_rows}
    ignored_reasons = {row["expense_item_id"]: (row["reason"] or "") for row in ignored_rows}
    included_keys = set()
    for item in customer_reimbursement_items(reimb_id):
        sources = item["auto_expense_sources"]
        if isinstance(sources, str):
            try:
                sources = json.loads(sources or "{}")
            except (ValueError, TypeError):
                sources = {}
        for field_sources in (sources or {}).values():
            for source in field_sources:
                if source.get("expense_id") and source.get("line_key"):
                    included_keys.add((source["expense_id"], source["line_key"]))
    rows = db().execute(
        """
        select expenses.id as expense_id, expenses.expense_number, expenses.status,
               expense_items.id as expense_item_id,
               expense_items.line_key, expense_items.amount, expense_items.description as item_description,
               (select count(*) from expense_items ordinal where ordinal.expense_id = expenses.id
                and (ordinal.sort_order < expense_items.sort_order or (ordinal.sort_order = expense_items.sort_order and ordinal.id <= expense_items.id))) as line_number,
               coalesce(projects.name, expense_items.project) as project_name,
               expense_items.fuel_vehicle_type as fuel_vehicle_type,
               expenses.expense_date, users.name as worker_name
        from expenses
        join expense_items on expense_items.expense_id = expenses.id
        join users on users.id = coalesce(expenses.beneficiary_id, expenses.created_by)
        left join projects on projects.id = expense_items.project_id
        where expenses.service_order_id = ?
        order by expenses.expense_date, expenses.id, expense_items.sort_order, expense_items.id
        """,
        (order_id,),
    ).fetchall()
    includable, ignored, pending, returned = [], [], [], []
    for row in rows:
        field_name = customer_reimbursement_expense_field(row["project_name"], row["fuel_vehicle_type"])
        if not field_name:
            continue
        key = (row["expense_id"], row["line_key"])
        if key in included_keys:
            continue
        entry = {
            "expense_id": row["expense_id"],
            "expense_item_id": row["expense_item_id"],
            "expense_number": row["expense_number"],
            "line_key": row["line_key"],
            "line_number": row["line_number"],
            "worker_name": row["worker_name"],
            "project_name": row["project_name"],
            "description": row["item_description"],
            "amount": money_float(row["amount"]),
            "date": _reimbursement_date_key(row["expense_date"]),
            "status": row["status"],
        }
        status = row["status"]
        if row["expense_item_id"] in ignored_set:
            entry["ignore_reason"] = ignored_reasons[row["expense_item_id"]]
            ignored.append(entry)
        elif status == "approved":
            includable.append(entry)
        elif status in ("pending", "submitted"):
            pending.append(entry)
        elif status == "returned":
            returned.append(entry)
    def bucket_total(entries):
        return money_float(sum((money_decimal(e["amount"]) for e in entries), Decimal("0")))
    return {
        "includable": includable,
        "ignored": ignored,
        "pending": pending,
        "returned": returned,
        "includable_total": bucket_total(includable),
        "ignored_total": bucket_total(ignored),
        "pending_total": bucket_total(pending),
        "returned_total": bucket_total(returned),
    }
