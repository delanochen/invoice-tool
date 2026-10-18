"""Legacy-compatible invoice_items helpers; explicit runtime dependencies."""

def latest_customer_reimbursement(order_id, *, db):
    return db().execute(
        """
        select customer_reimbursements.*, users.name as creator_name
        from customer_reimbursements
        left join users on users.id = customer_reimbursements.created_by
        where service_order_id = ?
        order by created_at desc, id desc
        limit 1
        """,
        (order_id,),
    ).fetchone()


def customer_reimbursement_linked_invoice(reimbursement, order_id, *, db, service_order_active_invoice):
    if reimbursement and reimbursement["invoice_id"]:
        invoice = db().execute(
            "select * from invoices where id = ?",
            (reimbursement["invoice_id"],),
        ).fetchone()
        if invoice:
            return invoice
    return service_order_active_invoice(order_id)


def customer_reimbursement_other_invoice_breakdown(reimbursement_id, *, customer_reimbursement_items, expense_settlement_invoice_field, json, money_decimal, money_float, normalized_project_name):
    """“其他”桶按来源项目聚合：遍历结算明细行 auto_expense_sources（field=other）。

    口径：工单结算“其他”不接受手工输入，auto_expense_sources 就是最终勾选结果的
    记录，发票拆分直接按来源金额聚合，无任何分摊规则。
    返回 {发票项目名: 金额}；出现没有映射的来源项目时抛错（fail-fast，不静默吞金额）。
    """
    breakdown = {}
    unmapped = []
    for item in customer_reimbursement_items(reimbursement_id):
        sources = item["auto_expense_sources"]
        if isinstance(sources, str):
            try:
                sources = json.loads(sources)
            except (TypeError, ValueError):
                sources = {}
        for source in (sources or {}).get("other", []):
            _field, invoice_project = expense_settlement_invoice_field(source.get("project_name"))
            if not invoice_project:
                unmapped.append(normalized_project_name(source.get("project_name")) or "(空项目名)")
                continue
            amount = money_decimal(source.get("amount"))
            breakdown[invoice_project] = money_float(money_decimal(breakdown.get(invoice_project)) + amount)
    if unmapped:
        raise ValueError(
            "以下报销来源项目没有配置「员工报销项目 → 结算/发票项目」映射，无法拆分发票行："
            + "、".join(sorted(set(unmapped)))
            + "。请先在映射表中配置。"
        )
    return breakdown


def customer_reimbursement_invoice_items(reimbursement, *, CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS, MRO_CANONICAL_PROJECT_NAME, _parse_quotation_revision_snapshot, canonical_project_mapping_key, customer_reimbursement_other_invoice_breakdown, db, is_mro_project_name, money_decimal, money_float, normalize_settlement_mode, project_name_key, quote_amounts):
    # 客户固定总价是一项完整的 Technical Services 服务。内部工时、里程、
    # 报销和材料仍用于成本/利润核算，但绝不能拆成额外的客户发票行。
    if reimbursement["settlement_basis"] == "quotation" and reimbursement["quotation_revision_id"]:
        quotation_revision = db().execute(
            "select * from quotation_revisions where id = ?",
            (reimbursement["quotation_revision_id"],),
        ).fetchone()
        snapshot = _parse_quotation_revision_snapshot(quotation_revision) if quotation_revision else {}
        if normalize_settlement_mode(snapshot.get("settlement_mode")) == "fixed":
            project = db().execute(
                """
                select * from projects
                where project_type = 'invoice' and lower(trim(name)) = lower('Technical Services')
                order by is_active desc, id asc limit 1
                """
            ).fetchone()
            if not project:
                raise ValueError("请先在项目维护中创建发票项目：Technical Services。")
            final_amount = quote_amounts(
                snapshot.get("gross_total") or quotation_revision["total"] or 0,
                snapshot.get("discount_amount") or 0,
            )["final_amount"]
            if final_amount <= 0:
                raise ValueError("报价单最终金额为 0，无法生成发票。")
            return [{
                "project_id": project["id"],
                "amount": money_float(final_amount),
                "tax_rate": project["tax_rate"],
            }]

    breakdown = customer_reimbursement_other_invoice_breakdown(reimbursement["id"])
    if not breakdown and money_float(money_decimal(reimbursement["mro_supplies_total"])) > 0:
        # 旧结算单没有 auto_expense_sources 记录时的兜底：按 MRO 快照金额开一行，
        # 避免“其他”金额在发票上消失；新结算单一律走来源项目拆分。
        breakdown = {"MRO Supplies配件及耗材费": money_float(money_decimal(reimbursement["mro_supplies_total"]))}

    required_names = tuple(CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS) + tuple(
        name for name in breakdown if name not in CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS
    )
    projects = db().execute(
        """
        select *
        from projects
        where project_type = 'invoice'
        """
    ).fetchall()
    canonical_invoice_keys = tuple(project_name_key(name) for name in required_names)
    projects_by_name = {}
    for project in projects:
        canonical_key = canonical_project_mapping_key(project["name"], canonical_invoice_keys)
        if canonical_key not in canonical_invoice_keys:
            continue
        expected_name = required_names[canonical_invoice_keys.index(canonical_key)]
        current = projects_by_name.get(expected_name)
        if current is None or project_name_key(project["name"]) == canonical_key:
            projects_by_name[expected_name] = project
    missing = [name for name in required_names if name not in projects_by_name]
    if missing and all(is_mro_project_name(name) for name in missing):
        # 发票项目可能仍是裸名 'MRO Supplies'：PostgreSQL 不跑 init_db 的别名合并，
        # 老库里只有裸名；两个写法都认，优先规范全名，避免开票时500。
        canonical_key = project_name_key(MRO_CANONICAL_PROJECT_NAME)
        candidates = [project for project in projects if is_mro_project_name(project["name"])]
        if candidates:
            chosen = next(
                (
                    project
                    for project in candidates
                    if project_name_key(project["name"]) == canonical_key
                ),
                candidates[0],
            )
            for name in missing:
                projects_by_name[name] = chosen
    missing = [name for name in required_names if name not in projects_by_name]
    if missing:
        raise ValueError(f"请先在项目维护中创建发票项目：{', '.join(missing)}。")
    items = []
    for project_name, amount_field in CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS.items():
        amount = float(reimbursement[amount_field] or 0)
        if amount <= 0:
            continue
        project = projects_by_name[project_name]
        items.append(
            {
                "project_id": project["id"],
                "amount": round(amount, 2),
                "tax_rate": project["tax_rate"],
            }
        )
    for project_name, amount in breakdown.items():
        # 与固定三类同名（配置错误）时跳过，避免同一发票项目出现两行
        if amount <= 0 or project_name in CUSTOMER_REIMBURSEMENT_INVOICE_PROJECTS:
            continue
        project = projects_by_name[project_name]
        items.append(
            {
                "project_id": project["id"],
                "amount": round(amount, 2),
                "tax_rate": project["tax_rate"],
            }
        )
    if not items:
        raise ValueError("工单结算金额为 0，无法生成发票。")
    return items
