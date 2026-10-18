"""Legacy-compatible workspace helpers; explicit runtime dependencies."""

def customer_reimbursement_gate_error(reimbursement, *, customer_reimbursement_pending_sources, money):
    """三出口 gate：提交/开票/发邮件/审核前校验未计入的报销。
    - 已审核未计入且未标记忽略（includable 非空）→ 硬拦截
    - 待审核（pending 非空）→ 硬拦截
    - 已退回（returned 非空）→ 不拦（退回提示由横幅呈现）
    返回 (error_code, message) 或 None。"""
    sources = customer_reimbursement_pending_sources(reimbursement)
    if sources["includable"]:
        return (
            "pending_sources",
            f"仍有 {len(sources['includable'])} 笔已审核报销未计入工单结算"
            f"（合计 {money(sources['includable_total'])}）。请先在结算页面点击"
            f"「计入并重算」，或将该报销明细标记为无需计入后再继续。",
        )
    if sources["pending"]:
        return (
            "pending_approval",
            f"有 {len(sources['pending'])} 笔报销仍在待审核状态"
            f"（合计 {money(sources['pending_total'])}）。确认要忽略待审核报销并继续吗？",
        )
    return None


def customer_reimbursement_person_days(order_id, *, db):
    row = db().execute(
        """
        select count(*) as count
        from (
            select service_report_workers.user_id,
                   date(coalesce(service_reports.actual_work_date, service_reports.report_date)) as work_date
            from service_reports
            join service_report_workers on service_report_workers.report_id = service_reports.id
            where service_reports.service_order_id = ?
            group by service_report_workers.user_id,
                     date(coalesce(service_reports.actual_work_date, service_reports.report_date))
        ) person_days
        """,
        (order_id,),
    ).fetchone()
    return int(row["count"] or 0)


def customer_reimbursement_lodging_range(order_id, *, date, db):
    """Estimate a reasonable person-night range from daily-report attendance.

    Report-free calendar gaps (including weekends) remain inside a stay when a
    worker appears on the adjacent report dates.  A stay is split only when an
    intervening report exists for the order and that worker is absent.  The
    lower bound assumes departure after the final work day; the upper bound
    allows one final night's stay for each distinct stay.  Travel mode is
    intentionally ignored because a round trip may be hotel-to-site travel.
    """
    rows = db().execute(
        """
        select distinct
               date(coalesce(service_reports.actual_work_date, service_reports.report_date)) as work_date,
               service_report_workers.user_id
        from service_reports
        join service_report_workers on service_report_workers.report_id = service_reports.id
        where service_reports.service_order_id = ?
          and coalesce(service_reports.actual_work_date, service_reports.report_date) is not null
        order by work_date, service_report_workers.user_id
        """,
        (order_id,),
    ).fetchall()
    if not rows:
        return {"minimum": 0, "maximum": 0}

    report_dates = sorted({date.fromisoformat(str(row["work_date"])[:10]) for row in rows})
    report_date_index = {work_date: index for index, work_date in enumerate(report_dates)}
    worker_dates = {}
    for row in rows:
        work_date = date.fromisoformat(str(row["work_date"])[:10])
        worker_dates.setdefault(row["user_id"], set()).add(work_date)

    minimum = 0
    maximum = 0
    for dates in worker_dates.values():
        ordered_dates = sorted(dates)
        stay_start = ordered_dates[0]
        previous_date = ordered_dates[0]
        previous_index = report_date_index[previous_date]
        for work_date in ordered_dates[1:]:
            current_index = report_date_index[work_date]
            if current_index != previous_index + 1:
                minimum += (previous_date - stay_start).days
                maximum += (previous_date - stay_start).days + 1
                stay_start = work_date
            previous_date = work_date
            previous_index = current_index
        minimum += (previous_date - stay_start).days
        maximum += (previous_date - stay_start).days + 1

    return {"minimum": minimum, "maximum": maximum}


def customer_reimbursement_workspace_data(order_id, reimbursement_id, saved_items, *, customer_reimbursement_seed_rows, date, db, settlement_expense_candidates):
    """Build live source summaries and snapshot differences for settlement tabs."""
    live_items = customer_reimbursement_seed_rows(order_id)
    hours_fields = (
        "standard_hours", "overtime_hours", "transport_hours",
        "holiday_hours", "public_transport_hours",
    )
    rate_fields = {
        "standard_hours": "standard_rate", "overtime_hours": "overtime_rate",
        "transport_hours": "transport_rate", "holiday_hours": "holiday_rate",
        "public_transport_hours": "public_transport_rate",
    }
    labor_rows = {}
    mileage_rows = {}
    labor_detail_rows = []
    mileage_detail_rows = []
    for item in live_items:
        worker_id = item.get("source_worker_user_id")
        key = worker_id or item.get("worker_name")
        labor = labor_rows.setdefault(key, {
            "worker_id": worker_id, "worker_name": item.get("worker_name") or "未指定员工",
            **{field: 0.0 for field in hours_fields},
            **{field.replace("_hours", "_amount"): 0.0 for field in hours_fields},
            "amount": 0.0,
        })
        for field in hours_fields:
            labor[field] += float(item.get(field) or 0)
            labor[field.replace("_hours", "_amount")] += (
                float(item.get(field) or 0) * float(item.get(rate_fields[field]) or 0)
            )
        labor["amount"] += float(item.get("labor_total") or 0)
        mileage = mileage_rows.setdefault(key, {
            "worker_id": worker_id, "worker_name": item.get("worker_name") or "未指定员工",
            "miles": 0.0, "amount": 0.0,
        })
        mileage["miles"] += float(item.get("miles") or 0)
        mileage["amount"] += float(item.get("mileage_total") or 0)
        detail = dict(item)
        for field in hours_fields:
            detail[field.replace("_hours", "_amount")] = (
                float(item.get(field) or 0) * float(item.get(rate_fields[field]) or 0)
            )
        labor_detail_rows.append(detail)
        if float(item.get("miles") or 0) or float(item.get("mileage_total") or 0):
            mileage_detail_rows.append(detail)

    lodging_rows = []
    attendance = db().execute(
        """
        select distinct users.id as worker_id, users.name as worker_name,
               date(coalesce(service_reports.actual_work_date, service_reports.report_date)) as work_date
        from service_reports
        join service_report_workers on service_report_workers.report_id = service_reports.id
        join users on users.id = service_report_workers.user_id
        where service_reports.service_order_id = ?
          and coalesce(service_reports.actual_work_date, service_reports.report_date) is not null
        order by work_date, users.name
        """,
        (order_id,),
    ).fetchall()
    all_dates = sorted({date.fromisoformat(str(row["work_date"])[:10]) for row in attendance})
    date_indexes = {value: index for index, value in enumerate(all_dates)}
    by_worker = {}
    for row in attendance:
        bucket = by_worker.setdefault(row["worker_id"], {
            "worker_name": row["worker_name"], "dates": set(),
        })
        bucket["dates"].add(date.fromisoformat(str(row["work_date"])[:10]))
    for worker_id, bucket in by_worker.items():
        dates = sorted(bucket["dates"])
        minimum = maximum = 0
        stays = []
        start = previous = dates[0]
        previous_index = date_indexes[previous]
        for current in dates[1:]:
            current_index = date_indexes[current]
            if current_index != previous_index + 1:
                stay_minimum = (previous - start).days
                stay_maximum = stay_minimum + 1
                minimum += stay_minimum
                maximum += stay_maximum
                stays.append({"entry_date": start, "exit_date": previous,
                              "minimum": stay_minimum, "maximum": stay_maximum})
                start = current
            previous = current
            previous_index = current_index
        stay_minimum = (previous - start).days
        stay_maximum = stay_minimum + 1
        minimum += stay_minimum
        maximum += stay_maximum
        stays.append({"entry_date": start, "exit_date": previous,
                      "minimum": stay_minimum, "maximum": stay_maximum})
        lodging_rows.append({
            "worker_id": worker_id, "worker_name": bucket["worker_name"],
            "minimum": minimum, "maximum": maximum, "stays": stays,
        })

    compare_fields = hours_fields + ("miles", "labor_total", "mileage_total")
    live_by_source = {
        (item.get("source_report_id"), item.get("source_worker_user_id")): item
        for item in live_items
    }
    saved_by_source = {
        (item.get("source_report_id"), item.get("source_worker_user_id")): item
        for item in saved_items if item.get("source_report_id") and item.get("source_worker_user_id")
    }
    added = set(live_by_source) - set(saved_by_source)
    removed = set(saved_by_source) - set(live_by_source)
    modified = {
        key for key in set(live_by_source) & set(saved_by_source)
        if any(abs(float(live_by_source[key].get(field) or 0) - float(saved_by_source[key].get(field) or 0)) > 0.005
               for field in compare_fields)
    }
    report_changes = []
    for change_type, keys in (("新增", added), ("已删除", removed), ("数值变化", modified)):
        for key in sorted(keys, key=lambda value: (str(value[0]), str(value[1]))):
            source = live_by_source.get(key) or saved_by_source.get(key) or {}
            report_changes.append({
                "type": change_type,
                "worker_name": source.get("worker_name") or "未指定员工",
                "project_date": source.get("project_date") or "",
            })
    candidates = settlement_expense_candidates(globals(), order_id, reimbursement_id)
    reimbursement_snapshot = db().execute(
        "select created_at, expense_transfer_cutoff_at from customer_reimbursements where id = ?",
        (reimbursement_id,),
    ).fetchone()
    baseline = str(
        (reimbursement_snapshot["expense_transfer_cutoff_at"] or reimbursement_snapshot["created_at"])
        if reimbursement_snapshot else ""
    )
    for candidate in candidates:
        source_timestamp = str(
            candidate.get("reviewed_at") or candidate.get("expense_updated_at")
            or candidate.get("expense_created_at") or ""
        )
        candidate["new_since_snapshot"] = bool(
            baseline and source_timestamp and source_timestamp > baseline and not candidate["selected"]
        )
    return {
        "labor_rows": sorted(labor_rows.values(), key=lambda row: row["worker_name"]),
        "labor_detail_rows": sorted(
            labor_detail_rows,
            key=lambda row: (row.get("worker_name") or "", row.get("project_date") or "", row.get("source_report_id") or 0),
        ),
        "mileage_rows": sorted(mileage_rows.values(), key=lambda row: row["worker_name"]),
        "mileage_detail_rows": sorted(
            mileage_detail_rows,
            key=lambda row: (row.get("worker_name") or "", row.get("project_date") or "", row.get("source_report_id") or 0),
        ),
        "lodging_rows": sorted(lodging_rows, key=lambda row: row["worker_name"]),
        "lodging_candidates": sorted(
            (row for row in candidates if row["group_key"] == "lodging"),
            key=lambda row: (row.get("worker_name") or "", row.get("expense_date") or "", row["expense_item_id"]),
        ),
        "other_candidates": sorted(
            (row for row in candidates if row["group_key"] != "lodging"),
            key=lambda row: (row.get("worker_name") or "", row.get("group_label") or "", row.get("expense_date") or "", row["expense_item_id"]),
        ),
        "report_change_count": len(added) + len(removed) + len(modified),
        "report_changes": report_changes,
        "expense_change_count": sum(
            1 for row in candidates
            if row["new_since_snapshot"]
            or (row["selected"] and (not row["selectable"] or row["source_changed"]))
        ),
    }
