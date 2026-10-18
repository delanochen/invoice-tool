"""Legacy-compatible setting_float helpers; explicit runtime dependencies."""

def setting_float(key, default=0, *, get_setting):
    try:
        return float(get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        return float(default)


def setting_int(key, default=0, *, get_setting):
    try:
        return int(float(get_setting(key, str(default)) or default))
    except (TypeError, ValueError):
        return int(default)


def positive_int(value, default=1):
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return max(1, int(default))
