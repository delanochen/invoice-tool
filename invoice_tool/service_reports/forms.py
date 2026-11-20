"""Legacy-compatible service-report forms; explicit dependencies only."""

def posted_report_time(prefix, *, request):
    hour = request.form.get(f"{prefix}_hour", "").strip()
    minute = request.form.get(f"{prefix}_minute", "").strip()
    if not hour and not minute:
        return request.form.get(prefix, "").strip()
    try:
        hour_value = int(hour)
        minute_value = int(minute)
    except (TypeError, ValueError):
        raise ValueError("请选择有效的现场时间。")
    if not 0 <= hour_value <= 23 or not 0 <= minute_value <= 59:
        raise ValueError("请选择有效的现场时间。")
    return f"{hour_value:02d}:{minute_value:02d}"


def report_worker_ids_from_form(*, request):
    return list(dict.fromkeys(value for value in request.form.getlist("worker_user_id") if value))


def report_worker_rows_from_form(*, REPORT_TRAVEL_MODES, normalize_trip_type, request, to_float,
                                 travel_autofiller=None):
    field_names = (
        "worker_user_id", "worker_travel_mode", "worker_driving_miles",
        "worker_travel_hours", "worker_public_transport_hours", "worker_work_description",
        "worker_origin", "worker_trip_type", "worker_zero_mileage_confirmed",
    )
    values = {name: request.form.getlist(name) for name in field_names}
    rows = []
    for index, raw_user_id in enumerate(values["worker_user_id"]):
        user_id = str(raw_user_id or "").strip()
        if not user_id:
            continue
        row = {
            name: (values[name][index].strip() if index < len(values[name]) else "")
            for name in field_names
        }
        row["worker_user_id"] = user_id
        mode = row["worker_travel_mode"]
        if mode not in REPORT_TRAVEL_MODES:
            raise ValueError("请选择有效的服务人员出行方式。")
        # 保存前用 Google 路线（与辅助填写同口径）回填留空的里程 / 时长。
        # 只填空值；没有 API key / 路线失败时由调用方按 0 落库并提示。
        if travel_autofiller is not None:
            travel_autofiller([row], request=request)
        miles = to_float(row["worker_driving_miles"])
        travel_hours = to_float(row["worker_travel_hours"])
        public_hours = to_float(row["worker_public_transport_hours"])
        # 0 英里 / 0 小时是合法值（随行、短途、顺路等）；负数非法。
        # 留空不再直接拒绝——保存链路会先尝试按 Google 路线自动计算回填，
        # 回填失败才按 0 落库，并由工资生成前的拦截提示用户补佐证。
        if mode in {"self_drive", "following", "rental_drive"}:
            if miles < 0:
                raise ValueError("自驾、随行和租车驾驶人员的里程不能为负数。")
            if travel_hours < 0:
                raise ValueError("自驾、随行和租车驾驶人员的交通时长不能为负数。")
        if mode == "flight":
            if not row["worker_public_transport_hours"]:
                raise ValueError("飞机出行必须填写公共交通时长。")
            if public_hours < 0:
                raise ValueError("飞机出行必须填写公共交通时长。")
        row.update(
            driving_miles=miles if mode != "flight" else 0,
            travel_hours=travel_hours if mode != "flight" else 0,
            public_transport_hours=public_hours if mode == "flight" else 0,
            origin_address=row["worker_origin"].strip(),
            # 空值 / 未知值一律归一为默认往返（唯一口径见 trip_policy）
            trip_type=normalize_trip_type(row["worker_trip_type"]),
            # 人工确认的 0 英里豁免；只有自驾/租车且里程确为 0 时有效。
            # 自动回填算出真实里程后此标记自动失效（is_applicable 会再校验）。
            worker_zero_mileage_confirmed=(
                str(row["worker_zero_mileage_confirmed"] or "").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
        )
        rows.append(row)
    if not rows:
        raise ValueError("服务人员清单至少需要添加一人。")
    if len({row["worker_user_id"] for row in rows}) != len(rows):
        raise ValueError("同一名服务人员不能重复添加。")
    return rows


def calculated_report_total_time(*, format_report_hours, report_worker_rows_from_form):
    rows = report_worker_rows_from_form()
    return format_report_hours(sum(row["travel_hours"] + row["public_transport_hours"] for row in rows))


def calculated_report_total_service_hours(arrival_time, departure_time, *, report_worker_rows_from_form, rounded_report_service_hours):
    return rounded_report_service_hours(arrival_time, departure_time) * len(report_worker_rows_from_form())


def calculated_report_driving_miles(*, report_billing_mileage, report_worker_rows_from_form, request):
    method = request.form.get("mileage_billing_method", "per_person")
    if method not in {"per_person", "per_vehicle"}:
        method = "per_person"
    return report_billing_mileage(report_worker_rows_from_form(), method)
