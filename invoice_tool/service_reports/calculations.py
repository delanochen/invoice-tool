"""Legacy-compatible service-report calculations; explicit dependencies only."""

def report_billing_mileage(worker_rows, method):
    if method == "per_vehicle":
        return round(sum(row["driving_miles"] for row in worker_rows if row["worker_travel_mode"] == "self_drive"), 2)
    return round(sum(row["driving_miles"] for row in worker_rows if row["worker_travel_mode"] in {"self_drive", "following"}), 2)


def parse_report_minutes(value):
    if not value or ":" not in str(value):
        return None
    try:
        hour_text, minute_text = str(value).split(":", 1)
        return int(hour_text) * 60 + int(minute_text)
    except ValueError:
        return None


def rounded_report_service_hours(arrival_time, departure_time, *, parse_report_minutes):
    arrival = parse_report_minutes(arrival_time)
    departure = parse_report_minutes(departure_time)
    if arrival is None or departure is None or departure <= arrival:
        return 0
    duration_minutes = departure - arrival
    rounded_minutes = (duration_minutes // 15) * 15
    if duration_minutes % 15 > 7:
        rounded_minutes += 15
    return round(rounded_minutes / 60, 2)


def format_report_hours(value):
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def report_duration_hours(report, worker_count=1, *, rounded_report_service_hours):
    rounded_hours = rounded_report_service_hours(report["arrival_time"], report["departure_time"])
    if rounded_hours:
        return rounded_hours
    fallback_hours = float(report["total_service_hours"] or 0)
    return round(fallback_hours / max(worker_count, 1), 2)


def report_actual_date(report):
    return report["actual_work_date"] or report["report_date"]
