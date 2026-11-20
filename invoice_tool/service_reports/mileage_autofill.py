"""保存工作日报时，为留空的里程 / 交通时长自动计算并回填。

口径完全复用 ``service_report_assist.ServiceReportAssistService.plan_worker``：
出发地取值链（表单 > 员工主数据 > 日报出发地址）、省油路线、往返倍数、
1.15 冗余 + 0.25h 向上取整都与「辅助填写」和里程佐证一致，避免三套口径分叉。

只填空值：用户手填的数字（含显式的 0）一律不覆盖。
随行（following）不调用 Google（没有驾车路线），留空按 0 / 0 落库。
飞机出行的公共交通时长无法由驾车路线推导，仍维持必填。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 只有自己驾车才值得请求 Google 路线。
DRIVING_MODES = frozenset({"self_drive", "rental_drive"})
# 随行没有独立驾车路线，留空按 0 处理（0 英里是合法值，见 zero-mileage 口径）。
PASSENGER_MODES = frozenset({"following"})


def auto_fill_missing_worker_travel(
    worker_rows: List[Dict[str, Any]],
    *,
    destination: str,
    fallback_origin: str,
    assist: Any,
) -> Dict[str, Any]:
    """就地回填 worker_rows 中自驾 / 租车员工留空的里程与交通时长。

    ``assist`` 是 ``ServiceReportAssistService`` 实例（或同接口替身），其
    ``plan_worker`` 负责地址解析、路线请求与统一口径的里程 / 时长计算。

    返回 {"filled": [...], "failed": [...]}，供调用方提示用户：
    - filled：成功回填的员工（含计算值）
    - failed：路线失败 / 缺少出发地 / 缺少目的地的员工（仍按 0 落库）
    """
    result: Dict[str, Any] = {"filled": [], "failed": []}
    if not worker_rows:
        return result

    for row in worker_rows:
        mode = (row.get("worker_travel_mode") or "self_drive").strip()
        miles_blank = not str(row.get("worker_driving_miles") or "").strip()
        hours_blank = not str(row.get("worker_travel_hours") or "").strip()
        if not (miles_blank or hours_blank):
            continue

        user_id = str(row.get("worker_user_id") or "")
        name = row.get("worker_name") or f"#{user_id}"

        if mode in PASSENGER_MODES:
            if miles_blank:
                row["worker_driving_miles"] = "0"
            if hours_blank:
                row["worker_travel_hours"] = "0"
            continue

        if mode not in DRIVING_MODES:
            continue

        try:
            plan = assist.plan_worker(
                {
                    "user_id": user_id,
                    "name": name,
                    "travel_mode": mode,
                    "trip_type": row.get("worker_trip_type"),
                    "current_origin": row.get("worker_origin") or "",
                },
                destination,
                fallback_origin,
            )
        except Exception:
            logger.exception("auto-fill mileage failed for worker %s", user_id)
            plan = None

        if not plan or plan.get("route_status") != "success":
            if miles_blank:
                row["worker_driving_miles"] = "0"
            if hours_blank:
                row["worker_travel_hours"] = "0"
            result["failed"].append(
                {
                    "user_id": user_id,
                    "name": name,
                    "reason": (plan or {}).get("reason") or "route_failed",
                }
            )
            continue

        written: Dict[str, Any] = {"user_id": user_id, "name": name}
        if miles_blank:
            value = plan.get("reported_miles")
            row["worker_driving_miles"] = str(value) if value is not None else "0"
            written["miles"] = value
        if hours_blank:
            value = plan.get("travel_hours")
            row["worker_travel_hours"] = str(value) if value is not None else "0"
            written["hours"] = value
        result["filled"].append(written)

    return result
