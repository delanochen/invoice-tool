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


def get_google_places_api_key(*, GOOGLE_PLACES_API_KEY_ENV, get_google_geocoding_api_key, get_setting):
    """Server-side Google Places API (New) key.

    Falls back to the geocoding key because both are server-side keys of the
    same Google project; if the geocoding key is API-restricted the Places
    calls will fail with 403 and travel_tools surfaces a clear error.
    """
    explicit = get_setting("google_places_api_key", GOOGLE_PLACES_API_KEY_ENV).strip()
    return explicit or get_google_geocoding_api_key()
