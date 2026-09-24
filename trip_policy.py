"""行程类型（往返 / 单程）统一口径。

背景：
    历史实现里「是否往返」是由 AI 智能日报的 overnight_stay（是否住宿）
    反推的：不住宿 → 当天往返（里程 ×2），住宿 → 只算单程。这个派生关系
    让人困惑，也无法表达「住店但当天往返」之类的情况，而且用户其实只想
    知道要不要按往返算。

现在两边（AI 智能日报 draft + 工作日报 service_report）统一为显式的
行程类型 trip_type：

    round_trip 往返（默认）→ 报告里程 = 单程 × 2，交通时长 = 单程 × 2
    one_way    单程        → 报告里程 = 单程，交通时长 = 单程

overnight_stay 不再参与任何里程判定，也不再向用户提问。老草稿里已存的
overnight_stay 通过 normalize_trip_type() 迁移：
    True（住宿）→ one_way，False（不住宿）→ round_trip。

本模块是全系统唯一判定入口，任何地方都不要再自行用 bool 推往返。
"""
from __future__ import annotations

from typing import Any

ROUND_TRIP = "round_trip"
ONE_WAY = "one_way"

DEFAULT_TRIP_TYPE = ROUND_TRIP
TRIP_TYPES = (ROUND_TRIP, ONE_WAY)

TRIP_TYPE_LABELS = {ROUND_TRIP: "往返", ONE_WAY: "单程"}

# 兼容历史上各种写法（含前端 select 的 "true"/"false"、LLM 可能输出的中文）
_ALIASES = {
    "round_trip": ROUND_TRIP,
    "roundtrip": ROUND_TRIP,
    "round-trip": ROUND_TRIP,
    "往返": ROUND_TRIP,
    "round trip": ROUND_TRIP,
    "rt": ROUND_TRIP,
    "false": ROUND_TRIP,  # 旧 overnight_stay=False（不住宿）→ 往返
    "no": ROUND_TRIP,
    "one_way": ONE_WAY,
    "oneway": ONE_WAY,
    "one-way": ONE_WAY,
    "单程": ONE_WAY,
    "one way": ONE_WAY,
    "ow": ONE_WAY,
    "true": ONE_WAY,  # 旧 overnight_stay=True（住宿）→ 单程
    "yes": ONE_WAY,
}


def normalize_trip_type(value: Any, *, default: str = DEFAULT_TRIP_TYPE) -> str:
    """把任意历史/外部输入归一化为 ROUND_TRIP / ONE_WAY。

    - bool True（住宿）→ ONE_WAY；False（不住宿）→ ROUND_TRIP
    - None / 空串 / 未知值 → default（默认往返）
    """
    if isinstance(value, bool):
        return ONE_WAY if value else ROUND_TRIP
    if isinstance(value, str):
        key = value.strip().casefold()
        if not key:
            return default
        return _ALIASES.get(key, default)
    return default


def trip_multiplier(trip_type: Any) -> int:
    """往返 → 2，单程 → 1。里程与交通时长都按这个倍数放大。"""
    return 2 if normalize_trip_type(trip_type) == ROUND_TRIP else 1


def trip_label(trip_type: Any) -> str:
    return TRIP_TYPE_LABELS[normalize_trip_type(trip_type)]
