"""Legacy-compatible quotation_rows helpers; explicit runtime dependencies."""

def build_quotation_settlement_rows(order, quotation_revision, *, _parse_quotation_revision_snapshot, calculate_customer_reimbursement_item, canonical_commercial_rate_key, db, driving_rate_missing, driving_transport_amount, money_decimal, money_float, normalize_driving_mode, normalize_settlement_mode, report_actual_date, split_report_labor_hours, to_float):
    """按报价单修订快照生成结算行。

    费率、自驾交通计费方式、折扣、结算方式全部来自报价单修订快照（不可变），
    保证结算可追溯；后续修改报价不影响已生成结算。
    自驾计费：自驾里程单价取自费率表 mileage（Per Mile），自驾交通工时单价取自
    费率表 Travel Time（内部 key 为 travel_hours，Per Hour）。启用项目但对应费率未明确填写时阻止生成，
    避免漏收费；明确填 0 视为已配置。
    """
    snapshot = _parse_quotation_revision_snapshot(quotation_revision)
    rate_schedule = snapshot.get("rate_schedule") or []
    by_key = {
        canonical_commercial_rate_key(item.get("key")): item
        for item in rate_schedule if isinstance(item, dict)
    }

    def qrate(key, default=0.0):
        item = by_key.get(key)
        if item and item.get("rate") not in (None, ""):
            return money_float(item["rate"])
        return default

    driving_mode = normalize_driving_mode(snapshot.get("driving_billing_mode"))
    settlement_mode = normalize_settlement_mode(snapshot.get("settlement_mode"))
    gross_total = money_decimal(snapshot.get("gross_total") or quotation_revision["total"] or 0)
    discount_amount = money_decimal(snapshot.get("discount_amount") or 0)
    mileage_rate = qrate("mileage")                 # 自驾里程单价（Per Mile）
    driving_time_rate = qrate("travel_hours")       # 自驾交通工时单价（Per Hour）
    missing = driving_rate_missing(driving_mode, mileage_rate, driving_time_rate)
    if missing:
        raise ValueError(
            "报价单自驾计费费率未配置：" + "、".join(missing)
            + "。请先在报价单费率表中填写对应费率后再保存工单结算。"
        )
    standard_rate = qrate("regular_hours")
    overtime_rate = qrate("overtime_hours")
    holiday_rate = qrate("holiday_hours")
    transport_rate = qrate("travel_hours")
    public_rate = qrate("public_transport_hours", 0.0)

    order_id = order["id"]
    reports = db().execute(
        """
        select service_reports.*
        from service_reports
        where service_reports.service_order_id = ?
        order by service_reports.report_date asc, service_reports.id asc
        """,
        (order_id,),
    ).fetchall()
    rows = []
    for report in reports:
        workers = db().execute(
            """
            select users.id as worker_user_id, users.name, service_report_workers.driving_miles,
                   service_report_workers.travel_mode, service_report_workers.travel_hours,
                   service_report_workers.public_transport_hours as worker_public_transport_hours
            from service_report_workers
            join users on users.id = service_report_workers.user_id
            where service_report_workers.report_id = ?
            order by users.name
            """,
            (report["id"],),
        ).fetchall()
        if not workers:
            continue
        for worker in workers:
            mode = worker["travel_mode"] or "legacy"
            worker_report = dict(report)
            worker_report["worker_travel_hours"] = (
                0 if mode in {"self_drive", "legacy"} else worker["travel_hours"]
            )
            worker_report["worker_public_transport_hours"] = worker["worker_public_transport_hours"]
            labor_hours = split_report_labor_hours(worker_report, len(workers))
            work_date = report_actual_date(report)
            # 自驾实际时长：仅取自「自驾 / 历史」模式（其交通时长未另按 transport 计费）。
            # following / 公共交通等模式的交通时长已计入 transport_hours×transport_rate，
            # 若再按自驾工时计费会双重收费，故不计入自驾时长。
            driving_hours = (
                to_float(worker["travel_hours"]) if mode in {"self_drive", "legacy"} else 0.0
            )
            if mode == "rental_drive":
                billing_miles = 0
            elif report["mileage_billing_method"] == "per_vehicle":
                billing_miles = worker["driving_miles"] if mode in {"self_drive", "legacy"} else 0
            else:
                billing_miles = worker["driving_miles"] if mode in {"self_drive", "following", "legacy"} else 0
            driving_amount = driving_transport_amount(
                driving_mode, billing_miles, driving_hours, mileage_rate, driving_time_rate
            )
            row = {
                "source_report_id": report["id"],
                "source_worker_user_id": worker["worker_user_id"],
                "worker_name": worker["name"],
                "project_date": work_date,
                "standard_hours": labor_hours["standard_hours"],
                "transport_hours": labor_hours["travel_hours"],
                "public_transport_hours": labor_hours["public_transport_hours"],
                "overtime_hours": labor_hours["overtime_hours"],
                "holiday_hours": labor_hours["holiday_hours"],
                "standard_rate": standard_rate,
                "transport_rate": transport_rate,
                "public_transport_rate": public_rate,
                "overtime_rate": overtime_rate,
                "holiday_rate": holiday_rate,
                "lodging": 0, "airfare": 0, "baggage": 0, "rental_car": 0, "fuel": 0,
                "parking": 0, "taxi": 0,
                "miles": billing_miles or 0,
                "mileage_rate": mileage_rate,
                "driving_amount": money_float(driving_amount),
                "other": 0,
            }
            rows.append(calculate_customer_reimbursement_item(row, len(rows)))
    return rows


def customer_reimbursement_seed_rows(order_id, *, build_quotation_settlement_rows, calculate_customer_reimbursement_item, db, report_actual_date, resolve_contract_rates, split_report_labor_hours):
    order = db().execute(
        "select id, settlement_basis, quotation_id from service_orders where id = ?",
        (order_id,),
    ).fetchone()
    if order and order["settlement_basis"] == "quotation":
        # 按报价单修订快照生成结算行（费率/自驾方式/折扣/结算方式取自不可变快照）
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
        return build_quotation_settlement_rows(order, quotation_revision)
    reports = db().execute(
        """
        select service_reports.*
        from service_reports
        where service_reports.service_order_id = ?
        order by service_reports.report_date asc, service_reports.id asc
        """,
        (order_id,),
    ).fetchall()
    rows = []
    for report in reports:
        workers = db().execute(
            """
            select users.id as worker_user_id, users.name, service_report_workers.driving_miles,
                   service_report_workers.travel_mode, service_report_workers.travel_hours,
                   service_report_workers.public_transport_hours as worker_public_transport_hours
            from service_report_workers
            join users on users.id = service_report_workers.user_id
            where service_report_workers.report_id = ?
            order by users.name
            """,
            (report["id"],),
        ).fetchall()
        if not workers:
            continue
        for worker in workers:
            mode = worker["travel_mode"] or "legacy"
            worker_report = dict(report)
            worker_report["worker_travel_hours"] = (
                0 if mode in {"self_drive", "legacy"} else worker["travel_hours"]
            )
            worker_report["worker_public_transport_hours"] = worker["worker_public_transport_hours"]
            labor_hours = split_report_labor_hours(worker_report, len(workers))
            work_date = report_actual_date(report)
            resolved_rates, _missing = resolve_contract_rates(order_id, work_date)
            standard_rate = resolved_rates["standard_rate"]
            travel_rate = resolved_rates["transport_rate"]
            public_rate = resolved_rates["public_transport_rate"]
            overtime_rate = resolved_rates["overtime_rate"]
            holiday_rate = resolved_rates["holiday_rate"]
            mileage_rate = resolved_rates["mileage_rate"]
            if mode == "rental_drive":
                billing_miles = 0
            elif report["mileage_billing_method"] == "per_vehicle":
                billing_miles = worker["driving_miles"] if mode in {"self_drive", "legacy"} else 0
            else:
                billing_miles = worker["driving_miles"] if mode in {"self_drive", "following", "legacy"} else 0
            row = {
                "source_report_id": report["id"],
                "source_worker_user_id": worker["worker_user_id"],
                "worker_name": worker["name"],
                "project_date": work_date,
                "standard_hours": labor_hours["standard_hours"],
                "transport_hours": labor_hours["travel_hours"],
                "public_transport_hours": labor_hours["public_transport_hours"],
                "overtime_hours": labor_hours["overtime_hours"],
                "holiday_hours": labor_hours["holiday_hours"],
                "standard_rate": standard_rate,
                "transport_rate": travel_rate,
                "public_transport_rate": public_rate,
                "overtime_rate": overtime_rate,
                "holiday_rate": holiday_rate,
                "lodging": 0,
                "airfare": 0,
                "baggage": 0,
                "rental_car": 0,
                "fuel": 0,
                "parking": 0,
                "taxi": 0,
                "miles": billing_miles or 0,
                "mileage_rate": mileage_rate,
                "other": 0,
            }
            rows.append(calculate_customer_reimbursement_item(row, len(rows)))
    return rows
