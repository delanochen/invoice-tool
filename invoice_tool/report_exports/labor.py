"""Legacy-compatible labor_entries helpers; explicit runtime dependencies."""

def labor_report_entries(date_from="", date_to="", worker_id="", date_mode="actual", *, db, split_report_labor_hours):
    if date_mode == "attendance":
        report_date_expr = "date(coalesce(service_reports.actual_work_date, service_reports.report_date))"
    elif date_mode == "report":
        report_date_expr = "date(service_reports.report_date)"
    else:
        report_date_expr = "coalesce(service_reports.actual_work_date, service_reports.report_date)"
    clauses = ["1 = 1"]
    params = []
    if date_from:
        clauses.append(f"{report_date_expr} >= ?")
        params.append(date_from)
    if date_to:
        clauses.append(f"{report_date_expr} <= ?")
        params.append(date_to)
    if worker_id and str(worker_id).isdigit():
        clauses.append("users.id = ?")
        params.append(int(worker_id))
    rows = db().execute(
        f"""
        with worker_counts as (
            select report_id, count(*) as worker_count
            from service_report_workers
            group by report_id
        )
        select service_reports.id as report_id,
               service_reports.report_date,
               {report_date_expr} as actual_work_date,
               {report_date_expr} as attendance_date,
               service_reports.total_service_hours,
               service_reports.travel_hours,
               service_reports.public_transport_hours,
               service_report_workers.driving_miles as worker_driving_miles,
               service_report_workers.travel_mode as worker_travel_mode,
               service_report_workers.travel_hours as worker_travel_hours,
               service_report_workers.public_transport_hours as worker_public_transport_hours,
               service_reports.arrival_time,
               service_reports.departure_time,
               service_orders.id as service_order_id,
               service_orders.order_number,
               service_orders.client_name,
               coalesce(owners.name, buyers.owner) as buyer_owner,
               users.id as worker_id,
               users.name as worker_name,
               employee_grades.grade_name,
               employee_grades.base_salary,
               employee_grades.meal_daily_amount,
               employee_grades.car_allowance_method,
               employee_grades.car_mileage_rate,
               employee_grades.rental_driving_hourly_rate,
               employee_grades.standard_hourly_rate,
               employee_grades.transport_hourly_rate,
               employee_grades.overtime_hourly_rate,
               employee_grades.holiday_hourly_rate,
               coalesce(worker_counts.worker_count, 1) as worker_count
        from service_reports
        join service_orders on service_orders.id = service_reports.service_order_id
        join service_report_workers on service_report_workers.report_id = service_reports.id
        join users on users.id = service_report_workers.user_id
        left join employee_grades on employee_grades.id = users.employee_grade_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join owners on owners.id = buyers.owner_id
        left join worker_counts on worker_counts.report_id = service_reports.id
        where {" and ".join(clauses)}
        order by actual_work_date desc, users.name, service_orders.order_number
        """,
        params,
    ).fetchall()
    entries = []
    for row in rows:
        hours = split_report_labor_hours(row, row["worker_count"])
        entry = dict(row)
        entry.update(hours)
        entry["work_hours"] = hours["standard_hours"] + hours["overtime_hours"] + hours["holiday_hours"]
        entries.append(entry)
    return entries
