"""Legacy-compatible employee-expense access; explicit dependencies only."""

def expense_beneficiary_id(expense):
    return expense["beneficiary_id"] or expense["created_by"]


def expense_beneficiary_options(expense=None, *, db, expense_beneficiary_id, g):
    # Keep a historical recipient selectable, even after their account is disabled.
    current_id = expense_beneficiary_id(expense) if expense is not None else g.user["id"]
    return db().execute(
        """select id, name, is_active from users
           where (is_active = 1 and role in ('admin', 'manager', 'finance', 'employee', 'internal', 'user'))
              or id = ?
           order by name, id""", (current_id,),
    ).fetchall()


def posted_expense_beneficiary(expense=None, *, expense_beneficiary_id, expense_beneficiary_options, g, request):
    default_id = expense_beneficiary_id(expense) if expense is not None else g.user["id"]
    raw = request.form.get("beneficiary_id", str(default_id)).strip()
    if not raw.isdigit():
        raise ValueError("请选择有效的报销归属员工。")
    beneficiary = next((row for row in expense_beneficiary_options(expense) if row["id"] == int(raw)), None)
    if beneficiary is None:
        raise ValueError("只能选择在职的内部员工，或保留本单原有的报销归属员工。")
    return beneficiary


def expense_access_filter(*, g, normalized_role):
    if normalized_role() in {"admin", "manager", "finance"}:
        return "1 = 1", []
    return "(expenses.created_by = ? or coalesce(expenses.beneficiary_id, expenses.created_by) = ?)", [g.user["id"], g.user["id"]]


def notify_expense_participants(expense, title, body, link, *, create_message, expense_beneficiary_id):
    for user_id in {expense["created_by"], expense_beneficiary_id(expense)}:
        create_message(user_id, title, body, link)


def can_access_expense(expense, *, expense_beneficiary_id, g, normalized_role):
    if not g.user:
        return False
    if normalized_role() in {"admin", "manager", "finance"}:
        return True
    return g.user["id"] in {expense["created_by"], expense_beneficiary_id(expense)}


def can_delete_expense(expense, *, expense_beneficiary_id, g, is_manager, normalized_role):
    if not g.user:
        return False
    if normalized_role() == "admin":
        return True
    if expense["status"] not in {"draft", "returned"}:
        return False
    if is_manager():
        return True
    return g.user["id"] in {expense["created_by"], expense_beneficiary_id(expense)}


def require_expense(expense_id, *, abort, can_access_expense, db, require_service_order):
    expense = db().execute("select * from expenses where id = ?", (expense_id,)).fetchone()
    if not expense:
        abort(404)
    if not can_access_expense(expense):
        abort(403)
    order = require_service_order(expense["service_order_id"])
    return expense, order
