"""Legacy-compatible residual_ai_values helpers; explicit runtime dependencies."""

def ai_search_like(value):
    return f"%{str(value or '').strip()}%"


def _ai_daily_report_business_date(*, app_timezone, datetime) -> str:
    """Get current business date in app timezone."""
    return datetime.now(app_timezone()).date().isoformat()
