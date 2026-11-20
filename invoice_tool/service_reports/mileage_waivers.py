"""人工确认「合理 0 英里」豁免的读写与判定。

背景：日报保存时留空的里程会按 Google 路线自动回填；少数场景（例如当天
只在园区内步行巡检、车辆故障未动车）确实合理为 0 英里，无法也不必生成
路线佐证。该模块让填报人在日报行上显式确认，工资生成前的佐证拦截据此
跳过这些行；里程仍按 0 计发，不改变任何计薪口径。

豁免记录独立成表，不写入 service_report_mileage_evidence，避免污染
「有成功路线佐证」的判定与工资税务组件快照。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

TABLE = "service_report_mileage_zero_waivers"
# 与 mileage_autofill / 工资拦截保持同一口径：只有自驾和租车驾驶需要路线佐证。
CONFIRMABLE_MODES = frozenset({"self_drive", "rental_drive"})


def normalize_confirmation(value: Any) -> bool:
    """表单里的确认值 -> bool。未提交 / 0 / 空都视为未确认。"""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_applicable(worker_row: Dict[str, Any], miles: float) -> bool:
    """确认只对自驾/租车且里程确实为 0 的行生效。

    自动回填把里程算成非 0 时，即使表单带着确认标记也自动失效，
    防止「勾了豁免」却带着真实里程进入工资批次。
    """
    mode = str(worker_row.get("worker_travel_mode") or "").strip()
    if mode not in CONFIRMABLE_MODES:
        return False
    if not normalize_confirmation(worker_row.get("worker_zero_mileage_confirmed")):
        return False
    try:
        return abs(float(miles or 0)) < 1e-9
    except (TypeError, ValueError):
        return False


def available(db) -> bool:
    """新表是否已迁移。未迁移时所有豁免读写自动降级为「无豁免」。"""
    try:
        row = db().execute(
            "select to_regclass('public.service_report_mileage_zero_waivers') is not null as ok"
        ).fetchone()
        return bool(row and row["ok"])
    except Exception:
        return False


def replace_for_report(db, report_id: int, worker_rows: Iterable[Dict[str, Any]], *,
                       user_id: Any, now_text: str) -> int:
    """按日报整批替换豁免（与 service_report_workers 的 delete + insert 同事务）。

    返回写入条数。只对自驾/租车且 miles == 0 的行落库，并留下确认人/时间。
    """
    db().execute(f"delete from {TABLE} where report_id = ?", (report_id,))
    written = 0
    for row in worker_rows:
        if not is_applicable(row, row.get("worker_driving_miles")):
            continue
        db().execute(
            f"insert into {TABLE} (report_id, worker_user_id, confirmed_by, confirmed_at) "
            "values (?, ?, ?, ?)",
            (report_id, int(row["worker_user_id"]), user_id, now_text),
        )
        written += 1
    return written


def user_ids_for_report(db, report_id: int) -> Set[int]:
    rows = db().execute(
        f"select worker_user_id from {TABLE} where report_id = ?", (report_id,)
    ).fetchall()
    return {int(row["worker_user_id"]) for row in rows}


def join_clause(enabled: bool) -> str:
    """工资拦截 SQL 用的 LEFT JOIN；未迁移时返回空串以保持旧查询可用。"""
    if not enabled:
        return ""
    return (
        "left join service_report_mileage_zero_waivers wz "
        "       on wz.report_id = sr.id and wz.worker_user_id = srw.user_id\n"
    )


def not_waived_clause(enabled: bool) -> str:
    if not enabled:
        return ""
    return "and wz.worker_user_id is null\n"
