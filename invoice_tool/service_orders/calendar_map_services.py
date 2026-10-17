"""Work-order calendar and map queries and presentation helpers."""

def service_order_access_filters(table_alias="service_orders", *, g, is_external_employee, is_external_manager):
    clauses = []
    params = []
    if is_external_manager():
        if not g.user["client_id"]:
            clauses.append("1 = 0")
        else:
            clauses.append(f"{table_alias}.client_id = ?")
            params.append(g.user["client_id"])
    elif is_external_employee():
        clauses.append(
            f"{table_alias}.id in (select service_order_id from user_service_orders where user_id = ?)"
        )
        params.append(g.user["id"])
    return clauses, params


def service_order_calendar_rows(date_from, date_to, *, db, service_order_access_filters):
    clauses, params = service_order_access_filters("service_orders")
    clauses.append("service_orders.calendar_date between ? and ?")
    params.extend([date_from.isoformat(), date_to.isoformat()])
    return db().execute(
        f"""
        with report_dates as (
            select service_order_id,
                   date(coalesce(actual_work_date, report_date)) as calendar_date,
                   1 as has_report
            from service_reports
            where coalesce(actual_work_date, report_date) is not null
            group by service_order_id, date(coalesce(actual_work_date, report_date))
        ),
        report_counts as (
            select service_order_id, count(*) as report_count
            from service_reports
            group by service_order_id
        ),
        calendar_orders as (
            select service_orders.*,
                   report_dates.calendar_date,
                   1 as has_report
            from service_orders
            join report_dates on report_dates.service_order_id = service_orders.id
            union all
            select service_orders.*,
                   date(service_orders.start_date) as calendar_date,
                   0 as has_report
            from service_orders
            left join report_counts on report_counts.service_order_id = service_orders.id
            where coalesce(report_counts.report_count, 0) = 0
              and service_orders.start_date is not null
        )
        select service_orders.*,
               clients.name as customer_name,
               work_order_types.name as work_order_type_name,
               coalesce(owners.name, buyers.owner) as buyer_owner,
               (
                   select count(*) from invoices
                   where invoices.service_order_id = service_orders.id and invoices.status != 'void'
               ) as invoice_count,
               (
                   select count(*) from invoices
                   where invoices.service_order_id = service_orders.id
                     and invoices.status != 'void' and invoices.paid_at is not null
               ) as paid_invoice_count
        from calendar_orders as service_orders
        left join clients on clients.id = service_orders.client_id
        left join work_order_types on work_order_types.id = service_orders.work_order_type_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join owners on owners.id = buyers.owner_id
        where {" and ".join(clauses)}
        order by service_orders.calendar_date, service_orders.order_number
        """,
        params,
    ).fetchall()


def assign_service_order_calendar_lanes(week):
    """Keep each work order on one compact horizontal lane within a calendar week."""
    intervals = {}
    for day_index, day in enumerate(week):
        for event in day["events"]:
            interval = intervals.setdefault(
                event["order_id"],
                {
                    "order_id": event["order_id"],
                    "order_number": event.get("order_number") or "",
                    "start": day_index,
                    "end": day_index,
                    "events": [],
                },
            )
            interval["start"] = min(interval["start"], day_index)
            interval["end"] = max(interval["end"], day_index)
            interval["events"].append(event)

    lane_ends = []
    for interval in sorted(
        intervals.values(),
        key=lambda item: (item["start"], item["end"], item["order_number"], item["order_id"]),
    ):
        lane = next(
            (index for index, lane_end in enumerate(lane_ends) if lane_end < interval["start"]),
            len(lane_ends),
        )
        if lane == len(lane_ends):
            lane_ends.append(interval["end"])
        else:
            lane_ends[lane] = interval["end"]
        for event in interval["events"]:
            event["lane"] = lane

    lane_count = len(lane_ends)
    for day in week:
        day["lane_count"] = lane_count
        day["events"].sort(key=lambda event: (event.get("lane", 0), event.get("order_number") or ""))
    return week


def service_order_calendar_weeks(year, month, *, STATUS_LABELS, assign_service_order_calendar_lanes, client_order_number_warning, date, parsed_iso_date, service_order_calendar_rows, timedelta, url_for):
    month_start = date(year, month, 1)
    month_end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    grid_start = month_start - timedelta(days=month_start.weekday())
    grid_end = month_end + timedelta(days=6 - month_end.weekday())
    events_by_day = {}
    dates_by_order = {}
    for row in service_order_calendar_rows(grid_start, grid_end):
        event_date = parsed_iso_date(row["calendar_date"])
        if not event_date:
            continue
        dates_by_order.setdefault(row["id"], set()).add(event_date)
        invoice_count = int(row["invoice_count"] or 0)
        paid_invoice_count = int(row["paid_invoice_count"] or 0)
        billing_state = ""
        if row["status"] == "closed" and invoice_count and paid_invoice_count == invoice_count:
            billing_state = "closed-paid"
        elif row["status"] == "closed" and invoice_count:
            billing_state = "closed-invoiced"
        elif row["status"] != "closed" and invoice_count:
            billing_state = "open-invoiced"
        events_by_day.setdefault(event_date, []).append(
            {
                "order_id": row["id"],
                "order_number": row["order_number"],
                "client_order_number": row["client_order_number"],
                "client_order_number_warning": client_order_number_warning(row["customer_name"], row["client_order_number"]),
                "site_name": row["client_name"],
                "owner": row["buyer_owner"],
                "status": row["status"],
                "status_label": STATUS_LABELS.get(row["status"], row["status"]),
                "billing_state": billing_state,
                "work_order_type": row["work_order_type_name"],
                "start_date": row["start_date"],
                "calendar_date": row["calendar_date"],
                "has_report": bool(row["has_report"]),
                "detail_url": url_for("service_order_detail", order_id=row["id"]),
            }
        )
    for event_date, events in events_by_day.items():
        for event in events:
            order_dates = dates_by_order.get(event["order_id"], set())
            continues_from_previous = (event_date - timedelta(days=1)) in order_dates and event_date.weekday() != 0
            continues_to_next = (event_date + timedelta(days=1)) in order_dates and event_date.weekday() != 6
            if continues_from_previous and continues_to_next:
                continuation = "middle"
            elif continues_from_previous:
                continuation = "end"
            elif continues_to_next:
                continuation = "start"
            else:
                continuation = "single"
            event["continuation"] = continuation
    current = grid_start
    weeks = []
    while current <= grid_end:
        week = []
        for _ in range(7):
            week.append(
                {
                    "date": current,
                    "in_month": current.month == month,
                    "events": events_by_day.get(current, []),
                    "is_today": current == date.today(),
                }
            )
            current += timedelta(days=1)
        weeks.append(assign_service_order_calendar_lanes(week))
    return weeks


def buyer_map_payload(buyer, *, can_manage_buyers, can_view_invoices, date, inspection_cycle_days, inspection_warning_days, parsed_iso_date, url_for):
    warning_days = inspection_warning_days()
    cycle_days = inspection_cycle_days()
    last_actual_date = parsed_iso_date(buyer["last_actual_date"])
    days_since_last_actual = (date.today() - last_actual_date).days if last_actual_date else None
    if not buyer["work_order_total"]:
        inspection_status = "none"
    elif days_since_last_actual is None:
        inspection_status = "overdue"
    elif days_since_last_actual >= cycle_days:
        inspection_status = "overdue"
    elif days_since_last_actual >= warning_days:
        inspection_status = "warning"
    else:
        inspection_status = "fresh"
    payload = {
        "id": buyer["id"],
        "buyer_number": buyer["buyer_number"],
        "name": buyer["name"],
        "owner": buyer["owner_name"],
        "contact_name": buyer["contact_name"],
        "contact_details": buyer["contact_details"],
        "email": buyer["email"],
        "country": buyer["country_name"] or buyer["country_code"] or buyer["country"],
        "country_code": buyer["country_code"],
        "detailed_address": buyer["detailed_address"],
        "equipment_manufacturer": buyer["equipment_manufacturer"],
        "latitude": buyer["latitude"],
        "longitude": buyer["longitude"],
        "geocode_status": buyer["geocode_status"] or "pending",
        "work_order_total": buyer["work_order_total"],
        "work_order_completed": buyer["work_order_completed"],
        "last_actual_date": buyer["last_actual_date"],
        "days_since_last_actual": days_since_last_actual,
        "inspection_warning_days": warning_days,
        "inspection_cycle_days": cycle_days,
        "inspection_status": inspection_status,
        "status": (
            "completed"
            if buyer["work_order_total"] and buyer["work_order_total"] == buyer["work_order_completed"]
            else "active"
        ),
        "detail_url": url_for("service_orders", buyer_id=buyer["id"]),
    }
    if can_manage_buyers():
        payload["edit_url"] = url_for("edit_buyer", buyer_id=buyer["id"])
    if can_view_invoices():
        payload["paid_invoice_amount"] = buyer["paid_invoice_amount"]
        payload["completed_invoice_amount"] = buyer["completed_invoice_amount"]
    return payload


def buyer_map_rows(where_clause="1 = 1", params=(), *, current_language, db):
    return db().execute(
        f"""
        with order_stats as (
            select buyer_id,
                   count(*) as work_order_total,
                   sum(case when status = 'closed' then 1 else 0 end) as work_order_completed
            from service_orders
            where buyer_id is not null
            group by buyer_id
        ),
        last_report_dates as (
            select service_orders.buyer_id,
                   max(date(coalesce(service_reports.actual_work_date, service_reports.report_date, service_orders.start_date))) as last_actual_date
            from service_orders
            left join service_reports on service_reports.service_order_id = service_orders.id
            where service_orders.buyer_id is not null
            group by service_orders.buyer_id
        ),
        invoice_amounts as (
            select invoices.id, invoices.service_order_id, invoices.status, invoices.paid_at,
                   invoices.payment_amount,
                   coalesce(sum(invoice_items.amount * (1 + invoice_items.tax_rate / 100.0)), 0) as invoice_total
            from invoices
            left join invoice_items on invoice_items.invoice_id = invoices.id
            where invoices.status != 'void'
            group by invoices.id
        ),
        invoice_stats as (
            select service_orders.buyer_id,
                   sum(
                       case when invoice_amounts.paid_at is not null
                       then coalesce(invoice_amounts.payment_amount, invoice_amounts.invoice_total)
                       else 0 end
                   ) as paid_invoice_amount,
                   sum(
                       case when invoice_amounts.status = 'completed'
                       then invoice_amounts.invoice_total
                       else 0 end
                   ) as completed_invoice_amount
            from service_orders
            join invoice_amounts on invoice_amounts.service_order_id = service_orders.id
            where service_orders.buyer_id is not null
            group by service_orders.buyer_id
        )
        select buyers.*,
               coalesce(owners.name, buyers.owner) as owner_name,
               owners.owner_number as owner_number,
               coalesce(country_local.name, country_zh.name, country_en.name, buyers.country, buyers.country_code) as country_name,
               coalesce(order_stats.work_order_total, 0) as work_order_total,
               coalesce(order_stats.work_order_completed, 0) as work_order_completed,
               last_report_dates.last_actual_date as last_actual_date,
               coalesce(invoice_stats.paid_invoice_amount, 0) as paid_invoice_amount,
               coalesce(invoice_stats.completed_invoice_amount, 0) as completed_invoice_amount
        from buyers
        left join owners on owners.id = buyers.owner_id
        left join country_translations country_local
          on country_local.country_code = buyers.country_code and country_local.language_code = ?
        left join country_translations country_zh
          on country_zh.country_code = buyers.country_code and country_zh.language_code = 'zh-CN'
        left join country_translations country_en
          on country_en.country_code = buyers.country_code and country_en.language_code = 'en'
        left join order_stats on order_stats.buyer_id = buyers.id
        left join last_report_dates on last_report_dates.buyer_id = buyers.id
        left join invoice_stats on invoice_stats.buyer_id = buyers.id
        where {where_clause}
        order by owner_name, buyers.name, buyers.id
        """,
        (current_language(), *params),
    ).fetchall()


def request_country_code(*, request):
    """Two-letter country of the current visitor, or None when unknown.

    Cloudflare (including the Tunnel in front of this app) adds
    ``CF-IPCountry`` to every request. No GeoIP database is bundled, so an
    absent header simply means "unknown" and callers must fall back to the
    permissive behaviour.
    """
    code = (request.headers.get("CF-IPCountry") or "").strip().upper()
    return code or None
