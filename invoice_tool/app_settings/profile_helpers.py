"""Legacy-compatible residual_settings helpers; explicit runtime dependencies."""

def inspection_cycle_days(*, setting_int):
    return max(1, setting_int("inspection_cycle_days", 180))


def inspection_warning_days(*, setting_int):
    return max(1, setting_int("inspection_warning_days", 150))


def lodging_reimbursement_limit(*, payroll_subsidy_settings):
    return payroll_subsidy_settings()["lodging_limit"]


def get_company_profile(*, DEFAULT_COMPANY_PROFILE, get_setting):
    return {key: get_setting(f"company_{key}", value) for key, value in DEFAULT_COMPANY_PROFILE.items()}


def get_payment_instructions(*, DEFAULT_PAYMENT_INSTRUCTIONS, get_setting):
    return {key: get_setting(f"payment_{key}", value) for key, value in DEFAULT_PAYMENT_INSTRUCTIONS.items()}


def get_invoice_terms(*, DEFAULT_INVOICE_TERMS, get_setting):
    return get_setting("invoice_terms", DEFAULT_INVOICE_TERMS)


def get_smtp_settings(*, DEFAULT_SMTP_SETTINGS, get_setting):
    return {key: get_setting(f"smtp_{key}", value) for key, value in DEFAULT_SMTP_SETTINGS.items()}


def get_google_maps_browser_api_key(*, GOOGLE_MAPS_BROWSER_API_KEY_ENV, get_setting):
    return get_setting("google_maps_browser_api_key", GOOGLE_MAPS_BROWSER_API_KEY_ENV).strip()


def get_google_geocoding_api_key(*, GOOGLE_GEOCODING_API_KEY_ENV, get_setting):
    return get_setting("google_geocoding_api_key", GOOGLE_GEOCODING_API_KEY_ENV).strip()


def get_google_routes_api_key(*, GOOGLE_ROUTES_API_KEY_ENV, get_setting):
    """Server-side Google Routes API key. Never exposed to browser/JS/HTML."""
    return get_setting("google_routes_api_key", GOOGLE_ROUTES_API_KEY_ENV).strip()


def get_google_static_maps_api_key(*, GOOGLE_STATIC_MAPS_API_KEY_ENV, get_setting):
    """Server-side Google Static Maps API key. Never exposed to browser/JS/HTML."""
    return get_setting("google_static_maps_api_key", GOOGLE_STATIC_MAPS_API_KEY_ENV).strip()
