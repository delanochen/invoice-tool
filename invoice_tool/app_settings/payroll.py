"""Legacy-compatible subsidy_settings helpers; explicit runtime dependencies."""

def payroll_subsidy_settings(*, get_setting, setting_float):
    car_method = get_setting("payroll_car_allowance_method", "daily")
    if car_method not in {"daily", "mileage"}:
        car_method = "daily"
    return {
        "cycle_start": get_setting("payroll_cycle_start", "2026-07-06"),
        "car_allowance_method": car_method,
        "car_daily_amount": setting_float("payroll_car_daily_amount", 60),
        "car_mileage_rate": setting_float("payroll_car_mileage_rate", 0.5),
        "report_writing_fee": setting_float("payroll_report_writing_fee", 20),
        "lodging_limit": setting_float("payroll_lodging_limit", 0),
    }
