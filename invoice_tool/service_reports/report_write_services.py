"""Service-report save tokens, worker/parts persistence and form services."""

def claim_report_save_token(token, report_id=None, *, db, now):
    token = str(token or "").strip()
    if not token:
        raise ValueError("保存令牌无效，请刷新页面后重试。")
    cursor = db().execute(
        """
        insert or ignore into service_report_save_tokens (token, report_id, created_at)
        values (?, ?, ?)
        """,
        (token, report_id, now()),
    )
    return cursor.rowcount == 1


def finish_report_save_token(token, report_id, *, db):
    db().execute(
        "update service_report_save_tokens set report_id = ? where token = ?",
        (report_id, token),
    )


def service_report_workers(report_id, *, db):
    return db().execute(
        """
        select users.id, users.name, users.email, service_report_workers.driving_miles,
               service_report_workers.travel_mode, service_report_workers.travel_hours,
               service_report_workers.public_transport_hours, service_report_workers.work_description,
               service_report_workers.origin_address, service_report_workers.trip_type
        from service_report_workers
        join users on users.id = service_report_workers.user_id
        where service_report_workers.report_id = ?
        order by users.name
        """,
        (report_id,),
    ).fetchall()


def service_report_worker_options(order, report_id=None, *, db, g, is_external_employee):
    params = [order["country_code"]]
    historical_clause = ""
    if report_id:
        historical_clause = """
          or users.id in (
              select user_id from service_report_workers where report_id = ?
          )
        """
        params.append(report_id)
    role_clause = (
        "users.id = ?"
        if is_external_employee()
        else "users.role in ('manager', 'finance', 'employee', 'external_employee')"
    )
    if is_external_employee():
        params.insert(0, g.user["id"])
    return db().execute(
        f"""
        select users.id, users.name, users.email, users.country_code, users.address
        from users
        where {role_clause}
          and users.is_active = 1
          and (
              users.country_code = ?
              {historical_clause}
          )
        order by users.name
        """,
        params,
    ).fetchall()


def report_writer_options(*, db):
    return db().execute(
        """
        select id, name, email from users
        where is_active = 1
          and role in ('manager', 'finance', 'employee', 'external_employee')
        order by name
        """
    ).fetchall()


def posted_report_writer_id(*, db, normalize_customer_report_choice, request):
    customer_report_choice, value = normalize_customer_report_choice(
        request.form.get("has_customer_report", ""),
        request.form.get("report_writer_id", ""),
    )
    if customer_report_choice == "no":
        return None
    if not value.isdigit():
        raise ValueError("请选择有效的客户日报填写员工。")
    row = db().execute(
        """select id from users where id = ? and is_active = 1
           and role in ('manager', 'finance', 'employee', 'external_employee')""",
        (int(value),),
    ).fetchone()
    if not row:
        raise ValueError("请选择有效的客户日报填写员工。")
    return row["id"]


def report_parts(table, report_id, *, db):
    return db().execute(f"select * from {table} where report_id = ? order by sort_order, id", (report_id,)).fetchall()


def parse_part_rows(prefix, fields, *, request):
    values = [request.form.getlist(f"{prefix}_{field}") for field in fields]
    max_len = max([len(items) for items in values] + [0])
    rows = []
    for index in range(max_len):
        row = {field: values[pos][index].strip() if index < len(values[pos]) else "" for pos, field in enumerate(fields)}
        if any(row.values()):
            rows.append(row)
    return rows


def report_form_defaults(report=None, order=None, *, date):
    if report:
        data = dict(report)
        data["actual_work_date"] = data.get("actual_work_date") or data.get("report_date")
        # v0.1.243: early AI-generated reports stored ISO photo-timeline
        # timestamps (e.g. 2026-09-17T08:50:00) which broke the hour/minute
        # dropdowns. Normalize legacy values for rendering.
        try:
            from ai_daily_report.formal_save import normalize_report_time
            data["arrival_time"] = normalize_report_time(data.get("arrival_time")) or ""
            data["departure_time"] = normalize_report_time(data.get("departure_time")) or ""
        except Exception:
            pass
        return data
    return {
        "report_date": date.today().isoformat(),
        "actual_work_date": date.today().isoformat(),
        "total_service_hours": "",
        "travel_hours": "",
        "public_transport_hours": "",
        "driving_miles": "",
        "mileage_billing_method": "per_person",
        "departure_address": "",
        "site_address": order["site_address"] if order else "",
        "total_time": "",
        "cabinet_number": "",
        "arrival_time": "",
        "departure_time": "",
        "service_description": "",
        "report_writer_id": None,
    }


def save_report_detail_rows(report_id, *, db, parse_part_rows, report_worker_rows_from_form):
    worker_rows = report_worker_rows_from_form()
    worker_ids = [row["worker_user_id"] for row in worker_rows]
    placeholders = ",".join("?" for _ in worker_ids)
    report_order = db().execute(
        """
        select service_orders.country_code
        from service_reports
        join service_orders on service_orders.id = service_reports.service_order_id
        where service_reports.id = ?
        """,
        (report_id,),
    ).fetchone()
    if not report_order:
        raise ValueError("工作日报关联的工单不存在。")
    valid_workers = db().execute(
        f"""
        select id from users
        where role in ('manager', 'finance', 'employee', 'external_employee')
          and is_active = 1
          and (
              country_code = ?
              or id in (
                  select user_id from service_report_workers where report_id = ?
              )
          )
          and id in ({placeholders})
        """,
        (report_order["country_code"], report_id, *worker_ids),
    ).fetchall()
    valid_worker_ids = {str(row["id"]) for row in valid_workers}
    if valid_worker_ids != set(worker_ids):
        raise ValueError("服务人员清单包含无效用户，请重新选择。")

    db().execute("delete from service_report_workers where report_id = ?", (report_id,))
    for worker in worker_rows:
        db().execute(
            """
            insert into service_report_workers (
                report_id, user_id, driving_miles, travel_mode, travel_hours,
                public_transport_hours, work_description, origin_address, trip_type
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id, worker["worker_user_id"], worker["driving_miles"],
                worker["worker_travel_mode"], worker["travel_hours"],
                worker["public_transport_hours"], worker["worker_work_description"],
                worker["origin_address"], worker["trip_type"],
            ),
        )

    db().execute("delete from service_report_saved_parts where report_id = ?", (report_id,))
    for index, row in enumerate(parse_part_rows("saved", ["part_number", "part_name", "quantity", "status"])):
        db().execute(
            """
            insert into service_report_saved_parts (report_id, part_number, part_name, quantity, status, sort_order)
            values (?, ?, ?, ?, ?, ?)
            """,
            (report_id, row["part_number"], row["part_name"], row["quantity"], row["status"], index),
        )

    db().execute("delete from service_report_replaced_parts where report_id = ?", (report_id,))
    for index, row in enumerate(parse_part_rows("replaced", ["part_number", "part_name", "old_serial_number", "new_serial_number", "quantity"])):
        db().execute(
            """
            insert into service_report_replaced_parts (
                report_id, part_number, part_name, old_serial_number, new_serial_number, quantity, sort_order
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                row["part_number"],
                row["part_name"],
                row["old_serial_number"],
                row["new_serial_number"],
                row["quantity"],
                index,
            ),
        )
