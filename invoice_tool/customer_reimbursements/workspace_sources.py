"""Save the workspace source selection in the enclosing settlement transaction."""
from settlement_review import (
    _contract_lodging_cap, _posted_settlement_adjustments,
    replace_expense_links, settlement_expense_candidates,
)


def save_workspace_sources(api, reimbursement, order, form):
    if form.get("workspace_sources_present") != "1":
        return False
    if reimbursement["status"] not in {"draft", "returned"}:
        raise ValueError("只有草稿或已退回的工单结算可以修改。")
    selected = set()
    for raw in form.getlist("expense_item_id"):
        try:
            selected.add(int(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("报销明细编号无效。") from exc
    candidates = settlement_expense_candidates(api, order["id"], reimbursement["id"])
    for item_id in selected:
        if not str(form.get(f"settled_amount_{item_id}", "")).strip():
            raise ValueError("已选报销明细必须填写客户结算金额。")
    amounts, reasons = _posted_settlement_adjustments(form, selected)
    try:
        nights = int(form.get("lodging_person_nights", reimbursement["lodging_person_nights"] or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("住宿人晚必须是整数。") from exc
    if nights < 0:
        raise ValueError("住宿人晚不能为负数。")
    rate = _contract_lodging_cap(api, order)
    replace_expense_links(api, reimbursement["id"],
                          {row["expense_item_id"]: row for row in candidates}, selected,
                          settled_amounts=amounts, adjustment_reasons=reasons)
    if selected:
        marks = ",".join("?" for _ in selected)
        api["db"]().execute(
            f"delete from customer_reimbursement_expense_ignores where customer_reimbursement_id=? and expense_item_id in ({marks})",
            (reimbursement["id"], *sorted(selected)),
        )
    api["db"]().execute(
        "update customer_reimbursements set lodging_person_nights=?, lodging_cap_rate_snapshot=?, "
        "contract_rate_version_id=?, expense_selection_mode='manual_review', expense_transfer_cutoff_at=? where id=?",
        (nights, rate["rate"], rate["version_id"], api["now"](), reimbursement["id"]),
    )
    return True


def restore_workspace_sources(workspace, form):
    """Keep the user's rejected source edits visible after transaction rollback."""
    selected = set(form.getlist("expense_item_id"))
    for key in ("lodging_candidates", "other_candidates"):
        for row in workspace.get(key, []):
            item_id = row["expense_item_id"]
            row["selected"] = str(item_id) in selected
            # Input values are escaped by Jinja. Keep arbitrary rejected input for correction.
            row["posted_settled_amount"] = form.get(f"settled_amount_{item_id}", str(row["settled_amount"]))
            row["adjustment_reason"] = form.get(f"adjustment_reason_{item_id}", row["adjustment_reason"])
