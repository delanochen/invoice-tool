"""Settlement amounts remain snapshots; expense ownership follows its source."""

import json
from copy import deepcopy


def _sources(row):
    value = row.get("auto_expense_sources") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if not isinstance(value, dict):
        return []
    return [source for group in value.values() if isinstance(group, list)
            for source in group if isinstance(source, dict)]


def resolve_expense_owner_names(rows, connection):
    """Resolve only unambiguous expense-only rows, without writing snapshots."""
    rows = [deepcopy(dict(row)) for row in rows]
    candidates = []
    numeric_fields = ("standard_hours", "transport_hours", "public_transport_hours",
                      "overtime_hours", "holiday_hours", "labor_total", "miles", "mileage_total")
    for row in rows:
        if row.get("source_report_id") or row.get("source_worker_user_id"):
            continue
        if any(row.get(field) for field in numeric_fields):
            continue
        sources = _sources(row)
        if sources and all(isinstance(s.get("expense_id"), int) for s in sources):
            candidates.append((row, sources))
    ids = sorted({s["expense_id"] for _, sources in candidates for s in sources})
    if not ids:
        return rows
    owners = {r["expense_id"]: (r["employee_id"], r["name"]) for r in connection.execute(
        "select e.id as expense_id, u.id as employee_id, u.name from expenses e "
        "join users u on u.id = coalesce(e.beneficiary_id, e.created_by) "
        "where e.id in (" + ",".join("?" for _ in ids) + ")", ids).fetchall()}
    for row, sources in candidates:
        if any(s["expense_id"] not in owners for s in sources):
            continue
        identities = {owners[s["expense_id"]] for s in sources}
        if len(identities) != 1:
            continue
        _, name = identities.pop()
        if not name:
            continue
        row["worker_name"] = name
        for source in sources:
            source["worker_name"] = name
        # Preserve the stored JSON/dict representation for existing consumers.
        if isinstance(row.get("auto_expense_sources"), str):
            value = json.loads(row["auto_expense_sources"])
            for group in value.values():
                if isinstance(group, list):
                    for source in group:
                        if isinstance(source, dict):
                            source["worker_name"] = name
            row["auto_expense_sources"] = json.dumps(value, ensure_ascii=False)
    return rows

def customer_reimbursement_items(reimbursement_id, *, db):
    connection = db()
    rows = connection.execute(
        """
        select *
        from customer_reimbursement_items
        where customer_reimbursement_id = ?
        order by sort_order, id
        """,
        (reimbursement_id,),
    ).fetchall()
    return resolve_expense_owner_names(rows, connection)
