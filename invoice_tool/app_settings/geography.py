"""Legacy-compatible headquarters helpers; explicit runtime dependencies."""


def headquarters_coordinates(*, HEADQUARTERS_LATITUDE, HEADQUARTERS_LONGITUDE, math):
    try:
        latitude = float(HEADQUARTERS_LATITUDE)
        longitude = float(HEADQUARTERS_LONGITUDE)
    except (TypeError, ValueError):
        return None
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return None
    return {"latitude": latitude, "longitude": longitude}
