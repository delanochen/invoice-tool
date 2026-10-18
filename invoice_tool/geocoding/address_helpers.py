"""Legacy-compatible residual_geo_values helpers; explicit runtime dependencies."""

def normalized_address(value):
    return " ".join(str(value or "").strip().split()).casefold()


def configured_country_codes(*, NOMINATIM_COUNTRY_CODES):
    return {code.strip().casefold() for code in NOMINATIM_COUNTRY_CODES.split(",") if code.strip()}


def address_expectations(address, *, US_STATE_CODES, re):
    uppercase_address = str(address or "").upper()
    state_codes = [token for token in re.findall(r"\b[A-Z]{2}\b", uppercase_address) if token in US_STATE_CODES]
    zip_codes = re.findall(r"\b(\d{5})(?:-\d{4})?\b", uppercase_address)
    return state_codes[-1] if state_codes else None, zip_codes[-1] if zip_codes else None
