"""Legacy-compatible updates helpers; explicit runtime dependencies."""

def create_customer_reimbursement(order, expense_selection_mode="legacy", *, assert_contract_rates_covered, copy_mileage_proofs_to_customer_reimbursement, customer_reimbursement_seed_rows, db, g, now, save_customer_reimbursement_items, secrets, update_customer_reimbursement_totals):
    file_name = f"费用报销单{order['client_order_number']}.pdf"
    quotation_revision = None
    if order["settlement_basis"] == "quotation":
        if not order["quotation_id"]:
            raise ValueError("该工单设置为按报价结算，但没有关联报价单。")
        quotation_revision = db().execute(
            """
            select quotation_revisions.*
            from quotation_revisions
            join quotations on quotations.id = quotation_revisions.quotation_id
            where quotation_revisions.quotation_id = ?
              and quotation_revisions.revision_no = quotations.current_revision_no
            """,
            (order["quotation_id"],),
        ).fetchone()
        if not quotation_revision:
            raise ValueError("关联报价单还没有可用于结算的修订版本，请先保存报价单。")
    # 0313：每张新结算自成一个结算系列（V1，且为当前有效版本）。
    # series_id 是 NOT NULL，必须先建系列行；同工单唯一 current 由部分唯一索引保证。
    series_id = db().execute(
        "insert into customer_reimbursement_series (service_order_id, created_at) "
        "values (?, ?) returning id",
        (order["id"], now()),
    ).fetchone()["id"]
    cursor = db().execute(
        """
        insert into customer_reimbursements (
            series_id, service_order_id, file_name, stored_filename, expense_selection_mode,
            settlement_basis, quotation_revision_id, is_current, version_no,
            created_by, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, true, 1, ?, ?)
        """,
        (
            series_id,
            order["id"], file_name, f"{secrets.token_hex(12)}.pdf", expense_selection_mode,
            order["settlement_basis"], quotation_revision["id"] if quotation_revision else None,
            g.user["id"], now(),
        ),
    )
    reimbursement_id = cursor.lastrowid
    # 按工单结算依据分流：报价单 → 报价单修订快照费率/自驾/折扣；合同 → 合同费率版本。
    rows = customer_reimbursement_seed_rows(order["id"])
    if not rows:
        raise ValueError("这个工单还没有可用于生成工单结算的工作日报。")
    if order["settlement_basis"] != "quotation":
        # 守门：合同路径用到的客户费率必须来自合同费率版本，缺失则阻止生成
        assert_contract_rates_covered(order["id"], rows)
    save_customer_reimbursement_items(reimbursement_id, rows)
    copy_mileage_proofs_to_customer_reimbursement(reimbursement_id, order["id"])
    update_customer_reimbursement_totals(reimbursement_id, rows)
    return db().execute("select * from customer_reimbursements where id = ?", (reimbursement_id,)).fetchone()


def sync_expense_attachments_to_settlement(order_id, *, copy_file_to_customer_reimbursement_attachment, customer_reimbursement_attachment_dir, db, expense_attachment_path, expense_settlement_invoice_field, latest_customer_reimbursement, normalized_project_name, os, selected_expense_item_ids):
    reimbursement = latest_customer_reimbursement(order_id)
    if not reimbursement or reimbursement["status"] not in {"draft", "returned"}:
        return 0
    manual_review = reimbursement["expense_selection_mode"] == "manual_review"
    selected_ids = selected_expense_item_ids(db(), reimbursement["id"]) if manual_review else set()

    # Remove previously copied source attachments when their expense line is no
    # longer selected. Uploaded/manual settlement attachments are never touched.
    if manual_review:
        copied_sources = db().execute("""
            select ca.id as settlement_attachment_id, ca.stored_filename, ea.expense_id, ea.expense_item_key,
                   ei.id as expense_item_id
            from customer_reimbursement_attachments ca
            join expense_attachments ea on ea.id = ca.source_expense_attachment_id
            left join expense_items ei on ei.expense_id = ea.expense_id and ei.line_key = ea.expense_item_key
            where ca.customer_reimbursement_id = ? and ca.source_expense_attachment_id is not null
        """, (reimbursement["id"],)).fetchall()
        for copied in copied_sources:
            keep = copied["expense_item_id"] in selected_ids if copied["expense_item_id"] else db().execute(
                "select 1 from expense_items where expense_id = ? and id in ({}) limit 1".format(
                    ",".join("?" for _ in selected_ids) or "null"
                ),
                [copied["expense_id"], *selected_ids],
            ).fetchone() is not None
            if keep:
                continue
            path = os.path.join(customer_reimbursement_attachment_dir(reimbursement["id"]), copied["stored_filename"])
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            db().execute("delete from customer_reimbursement_attachments where id = ?", (copied["settlement_attachment_id"],))

    attachments = db().execute("""
        select ea.*, e.id as source_expense_id, ei.id as expense_item_id,
               coalesce(p.name, ei.project, e.project) as project_name, ei.fuel_vehicle_type
        from expense_attachments ea join expenses e on e.id = ea.expense_id
        left join expense_items ei on ei.expense_id = e.id and ei.line_key = ea.expense_item_key
        left join projects p on p.id = ei.project_id
        where e.service_order_id = ? and not exists
          (select 1 from customer_reimbursement_attachments ca where ca.source_expense_attachment_id = ea.id)
        order by ea.id""", (order_id,)).fetchall()
    copied = 0
    for attachment in attachments:
        if manual_review:
            if attachment["expense_item_id"]:
                if attachment["expense_item_id"] not in selected_ids:
                    continue
            else:
                if not selected_ids:
                    continue
                placeholders = ",".join("?" for _ in selected_ids)
                if not db().execute(
                    f"select 1 from expense_items where expense_id = ? and id in ({placeholders}) limit 1",
                    [attachment["source_expense_id"], *selected_ids],
                ).fetchone():
                    continue
        settlement_field, _invoice_project = expense_settlement_invoice_field(attachment["project_name"])
        if ((settlement_field == "fuel" and attachment["fuel_vehicle_type"] != "rental")
                or normalized_project_name(attachment["project_name"]) == "个人自驾油费"):
            continue
        if copy_file_to_customer_reimbursement_attachment(reimbursement["id"], expense_attachment_path(attachment),
                attachment["original_filename"], attachment["content_type"], attachment["uploaded_by"],
                source_expense_attachment_id=attachment["id"]):
            copied += 1
    return copied


def _quotation_totals_with_discount(reimbursement, totals, *, _parse_quotation_revision_snapshot, db, money_decimal, money_float, normalize_settlement_mode, quote_amounts, resolve_quote_settlement):
    """按报价单结算方式与折扣计算最终应收（折扣只应用一次）。

    * 固定总价：基础客户应收＝最终报价金额（折扣前总额-折扣）；日报只记录实际
      工作、报销只核算成本，不因日报增多重复累加；明确确认的增项另行处理。
    * 按实际数量：实际数量×费率合计（totals["total_amount"]）减一次折扣，
      折扣超过实际折扣前金额时按实际封顶，不产生负数应收。
    """
    if reimbursement["settlement_basis"] != "quotation":
        return {
            "gross_amount": totals["total_amount"],
            "discount_amount": 0.0,
            "total_amount": totals["total_amount"],
        }
    if not reimbursement["quotation_revision_id"]:
        return {
            "gross_amount": totals["total_amount"],
            "discount_amount": 0.0,
            "total_amount": totals["total_amount"],
        }
    quotation_revision = db().execute(
        "select * from quotation_revisions where id = ?", (reimbursement["quotation_revision_id"],)
    ).fetchone()
    if not quotation_revision:
        return {
            "gross_amount": totals["total_amount"],
            "discount_amount": 0.0,
            "total_amount": totals["total_amount"],
        }
    snapshot = _parse_quotation_revision_snapshot(quotation_revision)
    mode = normalize_settlement_mode(snapshot.get("settlement_mode"))
    gross = money_decimal(snapshot.get("gross_total") or quotation_revision["total"] or 0)
    discount = money_decimal(snapshot.get("discount_amount") or 0)
    if mode == "fixed":
        final = quote_amounts(gross, discount)["final_amount"]
        return {
            "gross_amount": money_float(final),
            "discount_amount": money_float(discount),
            "total_amount": money_float(final),
        }
    resolved = resolve_quote_settlement(
        "actual", gross, discount, actual_gross_total=totals["total_amount"]
    )
    return {
        "gross_amount": totals["total_amount"],
        "discount_amount": money_float(resolved["discount_amount"]),
        "total_amount": money_float(resolved["base_amount"]),
    }


def update_customer_reimbursement_totals(reimbursement_id, rows=None, *, _runtime_api, _quotation_totals_with_discount, approved_mro_supplies_total, customer_reimbursement_items, customer_reimbursement_totals, db, merge_approved_expenses_into_customer_reimbursement, merge_service_reports_into_customer_reimbursement, save_customer_reimbursement_items, selected_expense_total, sync_expense_attachments_to_settlement):
    rows = rows if rows is not None else customer_reimbursement_items(reimbursement_id)
    reimbursement = db().execute(
        "select service_order_id, expense_transfer_cutoff_at, expense_selection_mode, "
        "settlement_basis, quotation_revision_id, mro_supplies_total, sources_frozen "
        "from customer_reimbursements where id = ?",
        (reimbursement_id,),
    ).fetchone()
    if not reimbursement:
        return customer_reimbursement_totals(rows)
    if reimbursement["sources_frozen"]:
        # 修订版快照：不再自动合并日报 / 报销来源，只按已复制的明细重算汇总。
        # 否则「从原版本复制完整快照、不重读最新来源」的语义会被 merge 打破。
        if reimbursement["expense_selection_mode"] == "manual_review":
            # 勾选模式下 MRO 合计跟随本版本的报销关联（含客户结算金额调整）。
            mro_total = selected_expense_total(_runtime_api(), reimbursement_id, "other")
        else:
            # 其余模式没有 links 可读，绝不能回头读 expenses（那就成了重读来源），
            # 照抄原版本复制过来的快照合计列。
            mro_total = reimbursement["mro_supplies_total"]
        totals = customer_reimbursement_totals(rows, mro_total)
        with_discount = _quotation_totals_with_discount(reimbursement, totals)
        db().execute(
            """
            update customer_reimbursements
            set labor_total = ?, lodging_total = ?, travel_total = ?, mileage_total = ?,
                mro_supplies_total = ?, rental_fuel_total = ?, total_amount = ?, other_total = ?,
                gross_amount = ?, discount_amount = ?
            where id = ?
            """,
            (
                totals["labor_total"], totals["lodging_total"], totals["travel_total"],
                totals["mileage_total"], totals["mro_supplies_total"], totals["rental_fuel_total"],
                with_discount["total_amount"], totals["other_total"],
                with_discount["gross_amount"], with_discount["discount_amount"], reimbursement_id,
            ),
        )
        return totals
    rows = merge_service_reports_into_customer_reimbursement(rows, reimbursement["service_order_id"])
    rows = merge_approved_expenses_into_customer_reimbursement(
        rows,
        reimbursement["service_order_id"],
        reimbursement["expense_transfer_cutoff_at"],
        reimbursement_id,
        reimbursement["expense_selection_mode"],
    )
    save_customer_reimbursement_items(reimbursement_id, rows)
    sync_expense_attachments_to_settlement(reimbursement["service_order_id"])
    mro_total = (
        selected_expense_total(_runtime_api(), reimbursement_id, "other")
        if reimbursement["expense_selection_mode"] == "manual_review"
        else approved_mro_supplies_total(
            reimbursement["service_order_id"],
            reimbursement["expense_transfer_cutoff_at"],
            exclude_ignored_for_reimbursement_id=reimbursement_id,
        )
    )
    totals = customer_reimbursement_totals(rows, mro_total)
    # 报价单折扣 / 结算方式：折扣只应用一次，写入结算快照列供对账与追溯。
    with_discount = _quotation_totals_with_discount(reimbursement, totals)
    db().execute(
        """
        update customer_reimbursements
        set labor_total = ?, lodging_total = ?, travel_total = ?, mileage_total = ?,
            mro_supplies_total = ?, rental_fuel_total = ?, total_amount = ?, other_total = ?,
            gross_amount = ?, discount_amount = ?
        where id = ?
        """,
        (
            totals["labor_total"], totals["lodging_total"], totals["travel_total"],
            totals["mileage_total"], totals["mro_supplies_total"], totals["rental_fuel_total"],
            with_discount["total_amount"], totals["other_total"],
            with_discount["gross_amount"], with_discount["discount_amount"], reimbursement_id,
        ),
    )
    return totals
