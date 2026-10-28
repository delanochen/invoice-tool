"""Legacy-compatible source_merge helpers; explicit runtime dependencies."""

def merge_service_reports_into_customer_reimbursement(rows, order_id, *, calculate_customer_reimbursement_item, customer_reimbursement_seed_rows, normalized_project_name):
    seeded_rows = customer_reimbursement_seed_rows(order_id)
    existing_rows = [dict(row) for row in rows]
    used_indexes = set()
    source_lookup = {}
    for index, row in enumerate(existing_rows):
        if row.get("source_report_id") and row.get("source_worker_user_id"):
            source_lookup[(row["source_report_id"], row["source_worker_user_id"])] = index

    merged = []
    expense_fields = ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")
    for seeded in seeded_rows:
        match_index = source_lookup.get((seeded["source_report_id"], seeded["source_worker_user_id"]))
        if match_index is None:
            seeded_name = normalized_project_name(seeded["worker_name"]).casefold()
            for index, existing in enumerate(existing_rows):
                if index in used_indexes or existing.get("source_report_id"):
                    continue
                if (
                    normalized_project_name(existing.get("worker_name")).casefold() == seeded_name
                    and str(existing.get("project_date") or "") == str(seeded["project_date"])
                ):
                    match_index = index
                    break
        if match_index is not None:
            used_indexes.add(match_index)
            existing = existing_rows[match_index]
            for field_name in expense_fields:
                seeded[field_name] = existing.get(field_name, 0)
        merged.append(calculate_customer_reimbursement_item(seeded, len(merged)))

    for index, existing in enumerate(existing_rows):
        if index in used_indexes or existing.get("source_report_id"):
            continue
        merged.append(calculate_customer_reimbursement_item(existing, len(merged)))
    return merged


def merge_approved_expenses_into_customer_reimbursement(
    rows, order_id, cutoff_at=None, reimbursement_id=None, selection_mode="legacy"
, *, _runtime_api, _fallback_row_for_worker, _reimbursement_date_key, _reimbursement_date_value, approved_customer_reimbursement_expense_rows, calculate_customer_reimbursement_item, customer_reimbursement_expense_field, linked_approved_expense_rows, money_decimal, money_float, normalized_project_name, resolve_contract_rates, _row_field):
    auto_fields = tuple(f"auto_{name}" for name in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"))
    merged = [dict(row) for row in rows]
    # The settlement form posts each expense cell as the value the user sees.
    # Capture whether the user overrode the auto-transferred sources *before*
    # the auto_* columns are rebuilt below, otherwise a figure the user
    # deliberately edited would be silently reverted on every save.
    manual_overrides = []
    for row in merged:
        override = {}
        for name in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"):
            if f"auto_{name}" not in row:
                continue
            manual = money_decimal(row.get(name))
            auto = money_decimal(row.get(f"auto_{name}"))
            # A cell equal to the source total means "untouched"; keep 0 so the
            # figure keeps tracking the sources.  Anything else is a real edit.
            override[name] = 0 if manual == auto else manual
        manual_overrides.append(override)
    # Historical drafts keep the old auto-transfer behavior until someone opens
    # the new review pool and explicitly saves a selection.  New/manual-review
    # settlements are rebuilt only from the persisted selected expense lines.
    if selection_mode == "manual_review":
        expense_rows = linked_approved_expense_rows(_runtime_api(), reimbursement_id, order_id, cutoff_at)
    else:
        expense_rows = approved_customer_reimbursement_expense_rows(
            order_id, cutoff_at, exclude_ignored_for_reimbursement_id=reimbursement_id
        )

    for row in merged:
        row["auto_expense_sources"] = {}
        for field_name in auto_fields:
            row[field_name] = 0

    row_lookup = {
        (normalized_project_name(row.get("worker_name")).casefold(), _reimbursement_date_key(row.get("project_date"))): row
        for row in merged
    }
    candidates_by_worker = {}
    for row in merged:
        candidates_by_worker.setdefault(
            normalized_project_name(row.get("worker_name")).casefold(), []
        ).append(row)
    for expense_item in expense_rows:
        field_name = customer_reimbursement_expense_field(
            expense_item["project_name"], expense_item["fuel_vehicle_type"]
        )
        if not field_name:
            continue
        worker_key = normalized_project_name(expense_item["worker_name"]).casefold()
        key = (worker_key, _reimbursement_date_key(expense_item["expense_date"]))
        row = row_lookup.get(key)
        if row is None:
            # 精确匹配落空：回落到同一员工的已有明细行，避免同人异日拆行导致重复计入。
            row = _fallback_row_for_worker(candidates_by_worker, worker_key)
        if row is None:
            fallback_rates, _missing = resolve_contract_rates(
                order_id, _reimbursement_date_value(expense_item["expense_date"])
            )
            row = {
                "worker_name": expense_item["worker_name"],
                "project_date": _reimbursement_date_value(expense_item["expense_date"]),
                "standard_hours": 0, "transport_hours": 0, "overtime_hours": 0, "holiday_hours": 0,
                "standard_rate": fallback_rates["standard_rate"],
                "transport_rate": fallback_rates["transport_rate"],
                "public_transport_rate": fallback_rates["public_transport_rate"],
                "overtime_rate": fallback_rates["overtime_rate"],
                "holiday_rate": fallback_rates["holiday_rate"],
                "lodging": 0, "airfare": 0, "baggage": 0, "rental_car": 0,
                "fuel": 0, "parking": 0, "taxi": 0, "other": 0,
                "miles": 0, "mileage_rate": fallback_rates["mileage_rate"],
            }
            for auto_field in auto_fields:
                row[auto_field] = 0
            merged.append(row)
            row_lookup[key] = row
            candidates_by_worker.setdefault(worker_key, []).append(row)
        row.setdefault("auto_expense_sources", {}).setdefault(field_name, []).append({
            "expense_id": expense_item["expense_id"], "expense_number": expense_item["expense_number"],
            "line_key": expense_item["line_key"], "line_number": expense_item["line_number"],
            "worker_name": expense_item["worker_name"], "project_name": expense_item["project_name"],
            "description": expense_item["item_description"], "amount": expense_item["amount"],
            # 报销原额：amount 是「客户认可金额」（部分计入时小于原额）。
            # 来源明细面板用它显示员工实际报销了多少，避免看起来像报销被改小。
            "source_amount": _row_field(expense_item, "full_amount", expense_item["amount"]),
            "adjustment_reason": _row_field(expense_item, "adjustment_reason", "") or "",
            "date": expense_item["expense_date"],
        })
        auto_field = f"auto_{field_name}"
        row[auto_field] = money_float(money_decimal(row.get(auto_field)) + money_decimal(expense_item["amount"]))

    # Re-apply each row's manual override so an edited cell keeps the figure the
    # user typed instead of snapping back to the raw expense sources.
    for index, row in enumerate(merged):
        if index >= len(manual_overrides):
            continue
        for name, manual in manual_overrides[index].items():
            if manual:
                row[name] = money_float(manual)

    return [calculate_customer_reimbursement_item(row, index) for index, row in enumerate(merged)]
