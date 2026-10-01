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

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

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
    return _finish_component_rows(prepared, gross)


def _finish_component_rows(prepared, gross):
    """共享收口：分位分配 + 三项税务合计 + 闭合防御（SL / ER 两侧共用）。"""
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


def _allocation_tie_key(row):
    """最大余额法 tie 时的稳定排序键：service_date → work_order_id →
    component_code → source_id，升序。缺失值排最后；数值列按数值比较。"""
    day = row.get("service_date")
    day_key = day.isoformat() if hasattr(day, "isoformat") else (day or "")
    order_id = row.get("work_order_id")
    if isinstance(order_id, int):
        order_key = (0, order_id, "")
    else:
        order_key = (1, 0, str(order_id or ""))
    code = row.get("component_code") or ""
    source_id = row.get("source_id")
    if isinstance(source_id, int):
        source_key = (0, source_id, "")
    else:
        source_key = (1, 0, str(source_id or ""))
    return (day_key, order_key, code, source_key)


def _raw_decimal(value):
    """上游工资行的原始金额可能是 float / str / Decimal，统一成 Decimal。"""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _allocate_cents(rows, gross):
    """把原始金额用真正的最大余额法（largest remainder）确定性分配到分。

    背景：工资组件来自「逐条工时 × 当日费率」的原始浮点累加，逐条量化到
    numeric(14,2) 后其和可能与量化后的总额差 1-2 分（纯分位分配问题，
    不是金额错误——真正的算法/数据漂移已在上方 raw_total != gross 处拦下，
    本函数绝不掩盖 drift）。

    算法：
    1. 保留每个组件量化前的 Decimal raw amount；
    2. 每行先向下取整到分（base），fractional remainder = raw - base
       （单位：分，落在 [0, 1)）；
    3. 需补的整分差额 D = gross - sum(base)。raw 合计已先通过 gross 一致性
       校验，可证 0 <= D <= 行数，因此每行最多只承受 1 分 rounding allocation；
    4. 按 remainder 从大到小排序，前 D 行各 +1 分；remainder 相同（tie）时
       按 _allocation_tie_key 稳定排序，结果确定、可重复。

    任何一行偏离其原始值不超过 1 分；调整只由分位残差驱动，与组件金额
    大小、名称、角色均无关。
    """
    bases = [_raw_decimal(row["amount_raw"]).quantize(CENT, rounding=ROUND_DOWN)
             for row in rows]
    deficit = (gross - sum(bases)) / CENT
    deficit_cents = int(deficit.to_integral_value(rounding=ROUND_HALF_UP))
    if deficit_cents == 0 or not rows:
        return bases
    remainders = [(_raw_decimal(row["amount_raw"]) - base) / CENT
                  for row, base in zip(rows, bases)]
    order = sorted(range(len(rows)),
                   key=lambda i: (-remainders[i], _allocation_tie_key(rows[i])))
    amounts = list(bases)
    for k in range(deficit_cents):
        amounts[order[k]] += CENT
    return amounts


# ---------------------------------------------------------------------------
# Phase 3A：Expense / ER 侧组件分类（与工资侧同一套结构化原则，禁止名称猜）。
# ---------------------------------------------------------------------------

EXPENSE_COMPONENT_CODE = "expense_item"

# Phase 3D：业务用途判定必须显式选择口径，绝不按 created_at 等日期隐式分支。
EXPENSE_CLASSIFICATION_CURRENT = "current"          # 新数据：只认 business_purpose
EXPENSE_CLASSIFICATION_LEGACY_BACKFILL = "legacy_backfill"  # 历史回填：兼容旧凭证


def expense_purpose_ok(expense, classification_mode=EXPENSE_CLASSIFICATION_CURRENT):
    """业务用途是否成立（Phase 3D：历史口径与新数据口径彻底分开）。

    - current（3D 上线后的新数据 / 重新提交的历史单）：只认结构化字段
      business_purpose。description 是备注，reviewed_by 只是「审核动作」，
      两者都不再能替代业务用途。
    - legacy_backfill（历史 ER 回填专用）：business_purpose OR description OR
      reviewed_by——仅用于解释已冻结的历史 snapshot，且必须显式传入，
      绝不因为上线时间隐式切换。
    非法 mode 直接抛错，避免调用方漏传导致口径漂移。
    """

    def field(name):
        # database.Row 没有 .get()；缺列时按「未填写」处理，不让历史行炸掉。
        try:
            return expense[name]
        except (KeyError, IndexError, TypeError):
            return None

    if classification_mode == EXPENSE_CLASSIFICATION_CURRENT:
        return bool(field("business_purpose"))
    if classification_mode == EXPENSE_CLASSIFICATION_LEGACY_BACKFILL:
        return bool(field("business_purpose") or field("description")
                    or field("reviewed_by"))
    raise ValueError("未知的报销分类口径：%r" % (classification_mode,))


def classify_expense_item(*, amount_ok, receipt_ok, purpose_ok, work_order_ok,
                          tax_status):
    """单个 expense item 的结构化税务判定（Phase 3A 口径）。

    - Expense 侧不自动产生 taxable_compensation：证据不足一律
      tax_review_required，绝不因为缺凭证就改判 taxable；
    - substantiated 语义：True=证据充分；False=已判定证据不足；
      NULL（None）保留给「尚未判定/历史未迁移」，本判定不产生 NULL；
    - 证据充分但缺税务身份 → review 且 snapshot 保持 NULL（禁止猜身份）。
    返回 (tax_category, substantiated)。
    """
    substantiated = bool(amount_ok and receipt_ok and purpose_ok and work_order_ok)
    if not substantiated:
        return "tax_review_required", False
    if tax_status is None:
        return "tax_review_required", True
    return "accountable_reimbursement", True


def build_expense_payment_components(component_rows, *, employee_id, gross_amount,
                                     payment_order_id, tax_status_lookup,
                                     source_type=EXPENSE_COMPONENT_CODE):
    """把 expense_items 变成 ER 可写入的组件快照（无 db/flask 依赖）。

    component_rows 每行结构化字段：
        amount / component_name / service_date / work_order_id / source_id
        amount_ok / receipt_ok / purpose_ok / work_order_ok
    分类只看上述结构化布尔 + tax_status_lookup，禁止按项目名称猜。
    返回 (rows, totals)；构建期硬校验失败抛 ValueError。
    """
    gross = money(gross_amount)
    raw_total = money(sum(row["amount"] for row in component_rows) if component_rows else 0)
    if raw_total != gross:
        raise ValueError(
            "报销组件合计 %s 与付款单金额 %s 不一致，已中止生成（不静默补差）。"
            % (raw_total, gross)
        )
    prepared = []
    for row in component_rows:
        lookup_day = row.get("service_date")
        tax_status = tax_status_lookup(employee_id, lookup_day) if lookup_day else None
        category, substantiated = classify_expense_item(
            amount_ok=row.get("amount_ok"),
            receipt_ok=row.get("receipt_ok"),
            purpose_ok=row.get("purpose_ok"),
            work_order_ok=row.get("work_order_ok"),
            tax_status=tax_status)
        prepared.append({
            "payment_order_id": payment_order_id,
            "employee_id": employee_id,
            "component_code": source_type,
            "component_name": row.get("component_name") or source_type,
            "amount_raw": row["amount"],
            "quantity": None,
            "unit": None,
            "unit_rate": None,
            "service_date": lookup_day,
            "work_order_id": row.get("work_order_id"),
            "source_type": source_type,
            "source_id": row.get("source_id"),
            "daily_report_id": None,
            "tax_category": category,
            "tax_status_snapshot": tax_status,
            "substantiated": substantiated,
            "review_status": "review_required" if category == "tax_review_required" else "confirmed",
        })
    return _finish_component_rows(prepared, gross)
