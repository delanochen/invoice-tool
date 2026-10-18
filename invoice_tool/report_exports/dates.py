"""Legacy-compatible iso_date helpers; explicit runtime dependencies."""

def parsed_iso_date(value, *, date):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
