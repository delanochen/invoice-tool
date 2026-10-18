"""Legacy-compatible timezone helpers; explicit runtime dependencies."""

def get_timezone_name(*, DEFAULT_TIMEZONE, get_setting):
    return get_setting("app_timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE


def app_timezone(*, DEFAULT_TIMEZONE, ZoneInfo, ZoneInfoNotFoundError, get_timezone_name):
    try:
        return ZoneInfo(get_timezone_name())
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)
