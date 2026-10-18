"""Legacy-compatible residual_expense_policy helpers; explicit runtime dependencies."""

def is_lodging_project_name(name):
    normalized = (name or "").strip().casefold()
    return "住宿" in normalized or "lodging" in normalized or "hotel" in normalized


def is_fuel_project_name(name):
    normalized = (name or "").strip().casefold()
    return "油费" in normalized or "加油" in normalized or "fuel" in normalized or "gas" in normalized


def validate_fuel_reimbursement_allowed(amount, label, *, payroll_subsidy_settings):
    if amount > 0 and payroll_subsidy_settings()["car_allowance_method"] == "mileage":
        raise ValueError(f"当前车补按里程计费，{label}不可报销。")
