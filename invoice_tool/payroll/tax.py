"""税务分类：把「钱是什么组件」翻译成「税务上是什么」。

本模块刻意保持无 db / flask 依赖：所有外部查询通过注入的 lookup 完成。

    config_lookup(component_code, day_iso) -> config row | None
        payroll_component_tax_config 按 code + 日期取生效版本
    tax_status_lookup(employee_id, day_iso) -> 'W2' | '1099' | None
        worker_tax_status_history 按 service_date 查当时的税务身份；
        查不到一律返回 None（＝缺税务身份，禁止猜测）
    mileage_evidence_lookup(worker_id, report_ids) -> bool
        该员工这些日报是否全部有 status='success' 的里程佐证

金额口径与 employee_finance._money 完全一致：
    Decimal(str(v)).quantize(Decimal('0.01'), ROUND_HALF_UP)
禁止用 Python round() 做金额一致性判断（banker's rounding 会误判）。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")

TAX_CATEGORIES = ("taxable_compensation", "accountable_reimbursement", "tax_review_required")

WORKER_TAX_STATUSES = ("W2", "1099")


def validate_tax_status_segment(existing_rows, tax_status, effective_from, effective_to):
    """校验一条 W-2 / 1099 身份有效期（纯函数，供服务层在写入前调用）。

    - tax_status 只允许 W2 / 1099；
    - effective_from 必填、effective_to 可空，结束不得早于开始；
    - 与任何已有有效期重叠 → 拒绝（不允许两个有效身份区间相交）。
    返回 None 表示通过，否则抛 ValueError。
    """
    if tax_status not in WORKER_TAX_STATUSES:
        raise ValueError("税务身份只能是 W2 或 1099。")
    if not effective_from:
        raise ValueError("必须填写生效日期（effective_from）。")
    if effective_to and effective_to < effective_from:
        raise ValueError("结束日期不能早于开始日期。")
    for row in existing_rows:
        row_from = _iso(row["effective_from"])
        row_to = _iso(row["effective_to"]) if row["effective_to"] else None
        # 开放段（effective_to 为空）视为延伸到无穷远。
        overlaps = (effective_to is None or row_from <= effective_to) and \
                   (row_to is None or row_to >= effective_from)
        if overlaps:
            raise ValueError(
                "生效区间 %s ~ %s 与已有记录 %s ~ %s 重叠，员工同一时段只能有一个税务身份。"
                % (effective_from, effective_to or "（至今）", row_from, row_to or "（至今）")
            )

# component_code -> 兜底展示名（config 缺失时使用，仅展示用，绝不参与判定）
DEFAULT_COMPONENT_NAMES = {
    "standard_pay": "标准工资",
    "overtime_pay": "加班工资",
    "holiday_pay": "假期工资",
    "following_allowance": "随行补贴",
    "rental_driving_allowance": "租车驾驶补贴",
    "self_drive_allowance": "自驾车补",
    "report_writing_fee": "报告撰写费",
    "base_salary": "基本工资",
    "meal_allowance": "餐补",
    "offline_salary": "线下约定工资",
}


def money(value):
    """与 employee_finance._money 同口径（改动任一侧必须同步另一侧）。"""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def classify_component(row, *, config, tax_status, substantiated):
    """单个组件的税务分类。规则按 Phase 2 拍板口径，禁止按名称猜。

    - config 缺失 → tax_review_required（缺规则）
    - self_drive_allowance：佐证不足 → tax_review_required（哪怕 config 说可报销）
    - config 要求佐证但未证实 → tax_review_required
    - 缺税务身份（tax_status None）→ tax_review_required（snapshot 保持 NULL）
    - 其余 → config 的 default_tax_category
    """
    code = row["component_code"]
    category = None
    requires_substantiation = False
    if config is not None:
        category = config["default_tax_category"]
        requires_substantiation = bool(config["requires_substantiation"])
        if category not in TAX_CATEGORIES:
            category = None
    if code == "self_drive_allowance" and not substantiated:
        return "tax_review_required"
    if requires_substantiation and not substantiated:
        return "tax_review_required"
    if category is None or tax_status is None:
        return "tax_review_required"
    return category


def _iso(day):
    return day.isoformat() if hasattr(day, "isoformat") else day


def build_payment_components(component_rows, *, employee_id, gross_amount, payment_order_id,
                             config_lookup, tax_status_lookup, mileage_evidence_lookup,
                             period_end, source_type="payroll_generation"):
    """把 payroll 组件明细变成可写入 employee_payment_components 的行。

    返回 (rows, totals)；rows 为 insert 参数 dict，totals 为三项税务合计
    （Decimal，分位）。硬校验失败抛 ValueError，由调用方回滚整个事务。
    """
    gross = money(gross_amount)
    raw_total = money(sum(row["amount"] for row in component_rows) if component_rows else 0)
    if raw_total != gross:
        raise ValueError(
            "工资组件合计 %s 与付款单金额 %s 不一致，已中止生成（不静默补差）。"
            % (raw_total, gross)
        )
    period_end_iso = _iso(period_end)
    prepared = []
    for row in component_rows:
        code = row["component_code"]
        service_date = row.get("service_date")
        # 无 service_date 的周期性组件按周期末日取规则/身份口径。
        lookup_day = service_date or period_end_iso
        config = config_lookup(code, lookup_day)
        if code == "self_drive_allowance":
            substantiated = bool(mileage_evidence_lookup(employee_id, row.get("report_ids") or set()))
        elif config is not None and config["requires_substantiation"]:
            substantiated = None  # 需佐证但不是里程：留给人工判定
        else:
            substantiated = None
        tax_status = tax_status_lookup(employee_id, lookup_day)
        category = classify_component(row, config=config, tax_status=tax_status,
                                      substantiated=substantiated)
        prepared.append({
            "payment_order_id": payment_order_id,
            "employee_id": employee_id,
            "component_code": code,
            "component_name": (config["display_name"] if config is not None and config["display_name"]
                               else DEFAULT_COMPONENT_NAMES.get(code, code)),
            "amount_raw": row["amount"],
            "quantity": row.get("quantity"),
            "unit": row.get("unit"),
            "unit_rate": row.get("unit_rate"),
            "service_date": service_date,
            "work_order_id": row.get("work_order_id"),
            "source_type": source_type,
            "source_id": None,
            "daily_report_id": row.get("daily_report_id"),
            "tax_category": category,
            "tax_status_snapshot": tax_status,
            "substantiated": substantiated,
            "review_status": "review_required" if category == "tax_review_required" else "confirmed",
        })
    amounts = _allocate_cents(prepared, gross)
    totals = {
        "taxable_compensation": money(0),
        "accountable_reimbursement": money(0),
        "tax_review_required": money(0),
    }
    for row, amount in zip(prepared, amounts):
        row["amount"] = amount
        totals[row["tax_category"]] = totals[row["tax_category"]] + amount
    if sum(totals.values()) != gross:  # 防御：分配算法自身必须闭合
        raise ValueError("税务合计与付款单金额不一致：%s vs %s" % (sum(totals.values()), gross))
    return prepared, totals


def _allocate_cents(rows, gross):
    """把原始金额按 ROUND_HALF_UP 量化到分，并保证合计恰好等于 gross。

    背景工资组件来自「逐条工时 × 当日费率」的原始浮点累加，逐条量化到
    numeric(14,2) 后其和可能与量化后的总额差 1-2 分（纯分位分配问题，
    不是金额错误——真正的算法/数据漂移已在上方 raw_total != gross 处拦下）。
    处理方式是确定性最大余额法：只把分位残差摊到原始金额最大的组件上，
    任何一行偏离其原始值不超过 1 分，且每次触发都会写 log_action 审计。
    """
    amounts = [money(row["amount_raw"]) for row in rows]
    residual = gross - sum(amounts)
    if residual == 0 or not rows:
        return amounts
    # residual 以分为单位（|residual| < 行数 × 0.01）；逐分摊给原始金额最大者。
    residual_cents = int((residual / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    step = CENT if residual_cents > 0 else -CENT
    order = sorted(range(len(rows)), key=lambda i: (-abs(rows[i]["amount_raw"]), i))
    for k in range(abs(residual_cents)):
        amounts[order[k % len(order)]] += step
    return amounts
