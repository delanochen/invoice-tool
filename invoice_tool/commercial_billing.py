"""统一商务单据（合同 / 报价单）的费率与结算纯逻辑。

本模块不 import app，只接收纯 Python / Decimal / 字符串输入，便于单元测试；
所有金额一律使用 Decimal，落库前统一四舍五入到分。

覆盖（对应已确认业务规则）：
  * 自驾交通计费方式：仅里程 / 仅时长 / 里程＋时长；
  * 报价单折扣：折扣前总额 / 折扣金额 / 最终报价金额，最终金额＝折扣前总额－折扣金额；
  * 固定总价 vs 按实际数量结算的取价与折扣应用（折扣只应用一次、不产生负数应收）。

费率口径铁律（勿改）：
  * 「未填写」（None / 空串）与「明确填写 0」必须区分 —— 启用某计费项目但对应
    费率未明确填写时返回 missing 提示，绝不静默用 0 兜底；
  * 自驾计费只控制客户收入；员工交通工资、里程补贴、报销仍按员工标准计算，
    与本模块无关。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")

# 自驾交通计费方式
DRIVING_MODE_MILEAGE_ONLY = "mileage_only"
DRIVING_MODE_TIME_ONLY = "time_only"
DRIVING_MODE_MILEAGE_AND_TIME = "mileage_and_time"
DRIVING_MODES = (
    DRIVING_MODE_MILEAGE_ONLY,
    DRIVING_MODE_TIME_ONLY,
    DRIVING_MODE_MILEAGE_AND_TIME,
)
DRIVING_MODE_LABELS = {
    DRIVING_MODE_MILEAGE_ONLY: "仅里程",
    DRIVING_MODE_TIME_ONLY: "仅时长",
    DRIVING_MODE_MILEAGE_AND_TIME: "里程＋时长",
}

# 结算方式
SETTLEMENT_MODE_ACTUAL = "actual"          # 按实际数量
SETTLEMENT_MODE_FIXED = "fixed"            # 固定总价
SETTLEMENT_MODES = (SETTLEMENT_MODE_ACTUAL, SETTLEMENT_MODE_FIXED)
SETTLEMENT_MODE_LABELS = {
    SETTLEMENT_MODE_ACTUAL: "按实际数量",
    SETTLEMENT_MODE_FIXED: "固定总价",
}

# 统一商务费率目录：合同与报价单共用同一套「费用项目 + 计价方式」结构，
# 价格各自独立。内部 key 沿用合同/结算已经使用的稳定 key；显示名称与报价单一致。
COMMERCIAL_RATE_TYPES = (
    ("regular_hours", "Regular Labor", "hour"),
    ("overtime_hours", "Overtime Labor", "hour"),
    ("holiday_hours", "Holiday Labor", "hour"),
    ("travel_hours", "Travel Time", "hour"),
    ("public_transport_hours", "Public Transportation Time", "hour"),
    ("waiting_standby_hours", "Waiting / Standby Time", "hour"),
    ("technical_support_hours", "Technical Support", "hour"),
    ("mileage", "Mileage", "mile"),
    ("lodging_cap", "Lodging", "person_night"),
    ("per_diem", "Per Diem", "day"),
)
COMMERCIAL_RATE_LABELS = {key: label for key, label, _unit in COMMERCIAL_RATE_TYPES}
COMMERCIAL_RATE_UNITS = {key: unit for key, _label, unit in COMMERCIAL_RATE_TYPES}

COMMERCIAL_RATE_DISPLAY_UNITS = {
    "hour": "Per Hour",
    "mile": "Per Mile",
    "person_night": "Person / Night",
    "day": "Person / Day",
}
COMMERCIAL_RATE_FORM_LINES = tuple(
    (key, label, COMMERCIAL_RATE_DISPLAY_UNITS[unit])
    for key, label, unit in COMMERCIAL_RATE_TYPES
)

# 0.1.383 及以前的报价单 JSON 使用报价专属 key。读取历史报价、复制报价和
# 生成历史修订 PDF 时统一归一化；历史快照本身不改写。
LEGACY_QUOTATION_RATE_KEYS = {
    "regular_labor": "regular_hours",
    "overtime_labor": "overtime_hours",
    "holiday_labor": "holiday_hours",
    "travel_time": "travel_hours",
    "public_transport": "public_transport_hours",
    "waiting_standby": "waiting_standby_hours",
    "technical_support": "technical_support_hours",
    "lodging": "lodging_cap",
}
CANONICAL_TO_LEGACY_QUOTATION_RATE_KEYS = {
    canonical: legacy for legacy, canonical in LEGACY_QUOTATION_RATE_KEYS.items()
}


def canonical_commercial_rate_key(key):
    key = str(key or "")
    return LEGACY_QUOTATION_RATE_KEYS.get(key, key)

# 自驾计费依赖的费率 key
DRIVING_MILEAGE_RATE_KEY = "mileage"
DRIVING_TIME_RATE_KEY = "travel_hours"


def _dec(value, default=Decimal("0")):
    """转 Decimal，容忍 None / 空串 / 非法输入；不做任何舍入（乘法精度保留）。"""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _is_blank(value):
    return value is None or str(value).strip() == ""


def round2(value):
    """金额统一保留 2 位小数，四舍五入到分。"""
    return _dec(value).quantize(CENT, ROUND_HALF_UP)


def normalize_driving_mode(mode):
    mode = (mode or DRIVING_MODE_MILEAGE_ONLY)
    if mode not in DRIVING_MODES:
        raise ValueError(f"不支持的自驾交通计费方式：{mode}")
    return mode


def driving_rate_missing(mode, mileage_rate, driving_time_rate):
    """启用项目但对应费率「未明确填写」时返回缺失项目展示名列表。

    区分「未填写」（None / 空串 → missing）与「明确填写 0」（已配置）。
    """
    mode = normalize_driving_mode(mode)
    missing = []
    if mode in (DRIVING_MODE_MILEAGE_ONLY, DRIVING_MODE_MILEAGE_AND_TIME) and _is_blank(mileage_rate):
        missing.append("自驾里程单价")
    if mode in (DRIVING_MODE_TIME_ONLY, DRIVING_MODE_MILEAGE_AND_TIME) and _is_blank(driving_time_rate):
        missing.append("自驾交通工时单价")
    return missing


def driving_transport_amount(mode, miles, hours, mileage_rate, driving_time_rate):
    """自驾交通计费金额。

    * 仅里程        ：里程 × 自驾里程单价
    * 仅时长        ：时长 × 自驾交通工时单价
    * 里程＋时长     ：两项相加
    未启用的项目即使有数量也不参与计算；返回金额四舍五入到分。
    """
    mode = normalize_driving_mode(mode)
    miles = _dec(miles)
    hours = _dec(hours)
    mileage_rate = _dec(mileage_rate)
    driving_time_rate = _dec(driving_time_rate)
    if mode == DRIVING_MODE_MILEAGE_ONLY:
        return round2(miles * mileage_rate)
    if mode == DRIVING_MODE_TIME_ONLY:
        return round2(hours * driving_time_rate)
    return round2(miles * mileage_rate + hours * driving_time_rate)


def quote_amounts(gross_total, discount_amount=None):
    """报价单三个金额：折扣前总额 / 折扣金额 / 最终报价金额。

    最终报价金额＝折扣前总额－折扣金额。
    折扣默认 0；不得小于 0，不得超过折扣前总额。纯函数，重复调用结果一致，
    折扣只在这里定义一次，供固定/实际结算统一取用（不按日报、报销分别扣除）。
    """
    gross = round2(_dec(gross_total))
    discount = round2(_dec(discount_amount, Decimal("0")))
    if discount < 0:
        raise ValueError("折扣金额不能小于 0。")
    if discount > gross:
        raise ValueError("折扣金额不能超过折扣前总额。")
    final = round2(gross - discount)
    return {
        "gross_total": gross,
        "discount_amount": discount,
        "final_amount": final,
    }


def normalize_settlement_mode(mode):
    mode = (mode or SETTLEMENT_MODE_ACTUAL)
    if mode not in SETTLEMENT_MODES:
        raise ValueError(f"不支持的结算方式：{mode}")
    return mode


def resolve_quote_settlement(settlement_mode, gross_total, discount_amount, actual_gross_total=None):
    """把报价单取价成工单结算的客户应收基准金额。

    * 固定总价：基础客户结算金额＝最终报价金额；日报记录实际工作、报销核算成本，
      不因日报增多重复累加基础报价；额外收费通过明确确认的增项处理。
    * 按实际数量：按日报实际数量×费率合计（实际折扣前金额）减一次报价单折扣；
      报价金额属于预计金额。
    折扣只应用一次；重复生成/重算结算不得重复扣折扣（本函数只消费一次折扣，
    结果里返回已应用的 discount_amount 供快照落库）。
    按实际数量结算时若折扣超过实际折扣前金额，不产生负数应收：折扣按实际封顶，
    并标记 excess_discount=True 供界面提示处理。
    """
    q = quote_amounts(gross_total, discount_amount)
    mode = normalize_settlement_mode(settlement_mode)
    if mode == SETTLEMENT_MODE_FIXED:
        return {
            "basis": SETTLEMENT_MODE_FIXED,
            "base_amount": q["final_amount"],
            "discount_amount": q["discount_amount"],
            "excess_discount": False,
        }
    actual = round2(_dec(actual_gross_total))
    # 折扣超过实际折扣前金额：不产生负数应收，按实际封顶并提示。
    applied = min(q["discount_amount"], actual)
    excess = q["discount_amount"] > actual
    return {
        "basis": SETTLEMENT_MODE_ACTUAL,
        "base_amount": round2(actual - applied),
        "discount_amount": applied,
        "excess_discount": bool(excess),
    }
