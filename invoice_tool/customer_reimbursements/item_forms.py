"""Legacy-compatible item_forms helpers; explicit runtime dependencies."""

def customer_reimbursement_items_from_form(order_id=None, *, assert_contract_rates_covered, calculate_customer_reimbursement_item, existing_items=None, money_float, request, resolve_contract_rates, settlement_basis=None, to_float, validate_fuel_reimbursement_allowed):
    if order_id is None:
        # 没有工单就无法从合同费率版本解析客户费率，禁止静默使用默认价
        raise ValueError("工单结算必须关联工单才能解析客户费率，请刷新页面后重试。")
    field_names = [
        "worker_name", "project_date", "standard_hours", "transport_hours", "public_transport_hours", "overtime_hours", "holiday_hours",
        "lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "miles", "other",
    ]
    # auto_* columns are the auto-transferred expense sources behind each cell.
    # The cell itself posts the effective amount the user sees, so the form must
    # echo auto_* back untouched for the merge step to recover the manual delta.
    auto_field_names = [f"auto_{name}" for name in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")]
    money_fields = {"lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"}
    posted = {name: request.form.getlist(name) for name in field_names}
    posted_auto = {name: request.form.getlist(name) for name in auto_field_names}
    source_report_ids = request.form.getlist("source_report_id")
    source_worker_user_ids = request.form.getlist("source_worker_user_id")
    count = max((len(values) for values in posted.values()), default=0)
    existing_items = [dict(item) for item in (existing_items or [])]
    existing_by_source = {
        (item.get("source_report_id"), item.get("source_worker_user_id")): item
        for item in existing_items
        if item.get("source_report_id") is not None and item.get("source_worker_user_id") is not None
    }
    existing_by_identity = {
        (str(item.get("project_date") or "")[:10], str(item.get("worker_name") or "")): item
        for item in existing_items
    }
    rows = []
    for index in range(count):
        worker_name = posted["worker_name"][index].strip() if index < len(posted["worker_name"]) else ""
        project_date = posted["project_date"][index].strip() if index < len(posted["project_date"]) else ""
        if not worker_name and not project_date:
            continue
        row = {
            "worker_name": worker_name,
            "project_date": project_date,
            "source_report_id": (
                int(source_report_ids[index])
                if index < len(source_report_ids) and source_report_ids[index].isdigit()
                else None
            ),
            "source_worker_user_id": (
                int(source_worker_user_ids[index])
                if index < len(source_worker_user_ids) and source_worker_user_ids[index].isdigit()
                else None
            ),
        }
        for name in field_names[2:]:
            value = posted[name][index] if index < len(posted[name]) else 0
            row[name] = money_float(value) if name in money_fields else to_float(value)
        for name in auto_field_names:
            values = posted_auto[name]
            row[name] = money_float(values[index]) if index < len(values) else 0
        if settlement_basis == "quotation":
            existing = existing_by_source.get((row.get("source_report_id"), row.get("source_worker_user_id")))
            if existing is None:
                existing = existing_by_identity.get((str(project_date)[:10], worker_name))
            if existing is None:
                raise ValueError("报价单结算明细无法匹配原结算快照，请刷新页面后重试。")
            for rate_field in ("standard_rate", "transport_rate", "public_transport_rate", "overtime_rate", "holiday_rate", "mileage_rate"):
                row[rate_field] = money_float(existing.get(rate_field) or 0)
        else:
            effective_rates, _missing = resolve_contract_rates(order_id, project_date)
            row.update(effective_rates)
        if not row["worker_name"] or not row["project_date"]:
            raise ValueError("工单结算每一行都必须有姓名和项目时间。")
        validate_fuel_reimbursement_allowed(row["fuel"], "工单结算油费")
        rows.append(calculate_customer_reimbursement_item(row, len(rows)))
    if not rows:
        raise ValueError("工单结算至少需要一行明细。")
    # 只有合同结算依赖合同费率版本。报价单结算沿用创建结算时固化的报价快照费率；
    # 固定总价报价尤其不能因为日报里存在工时/里程就反向要求合同费率。
    if settlement_basis != "quotation":
        assert_contract_rates_covered(order_id, rows)
    return rows


def save_customer_reimbursement_items(reimbursement_id, rows, *, calculate_customer_reimbursement_item, db, json, money_decimal):
    db().execute("delete from customer_reimbursement_items where customer_reimbursement_id = ?", (reimbursement_id,))
    for row in rows:
        # 入库前把「人工值 == 来源值」的单元格归一为 0，避免下一轮被误判成
        # 手改值而不再跟随来源（翻倍根因④的清理语义）。
        for name in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"):
            auto = row.get(f"auto_{name}")
            if auto is None:
                continue
            if money_decimal(row.get(name)) == money_decimal(auto):
                row[name] = 0
        calculate_customer_reimbursement_item(row, row.get("sort_order", 0))
        db().execute(
            """
            insert into customer_reimbursement_items (
                customer_reimbursement_id, source_report_id, source_worker_user_id,
                worker_name, project_date, standard_hours, transport_hours, public_transport_hours,
                overtime_hours, holiday_hours, standard_rate, transport_rate, public_transport_rate, overtime_rate, holiday_rate,
                labor_total, lodging, airfare, baggage, rental_car, fuel, parking, taxi, miles, mileage_rate,
                mileage_total, other, auto_lodging, auto_airfare, auto_baggage, auto_rental_car,
                auto_fuel, auto_parking, auto_taxi, auto_other, total, sort_order
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reimbursement_id, row.get("source_report_id"), row.get("source_worker_user_id"),
                row["worker_name"], row["project_date"], row["standard_hours"], row["transport_hours"], row.get("public_transport_hours", 0),
                row["overtime_hours"], row["holiday_hours"], row["standard_rate"], row["transport_rate"], row.get("public_transport_rate", 0),
                row["overtime_rate"], row["holiday_rate"], row["labor_total"], row["lodging"], row["airfare"],
                row["baggage"], row["rental_car"], row["fuel"], row["parking"], row["taxi"], row["miles"],
                row["mileage_rate"], row["mileage_total"], row["other"],
                row.get("auto_lodging", 0), row.get("auto_airfare", 0), row.get("auto_baggage", 0),
                row.get("auto_rental_car", 0), row.get("auto_fuel", 0), row.get("auto_parking", 0),
                row.get("auto_taxi", 0), row.get("auto_other", 0), row["total"], row["sort_order"],
            ),
        )

        sources = row.get("auto_expense_sources", {})
        db().execute("update customer_reimbursement_items set auto_expense_sources = ? where customer_reimbursement_id = ? and sort_order = ?",
                     (sources if isinstance(sources, str) else json.dumps(sources, ensure_ascii=False), reimbursement_id, row["sort_order"]))
