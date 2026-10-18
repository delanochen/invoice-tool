"""Legacy-compatible report_location helpers; explicit runtime dependencies."""

def report_location_filters(table_alias="service_orders", *, country_rows, request):
    region_code = request.args.get("region_code", "").strip().lower()
    country_code = request.args.get("country_code", "").strip().upper()
    countries = country_rows(include_inactive=True)
    valid_country_codes = {country["code"] for country in countries}
    valid_region_codes = {country["region_code"] for country in countries}
    if country_code not in valid_country_codes:
        country_code = ""
    if region_code not in valid_region_codes:
        region_code = ""
    clauses = []
    params = []
    if region_code:
        clauses.append(f"{table_alias}.region_code = ?")
        params.append(region_code)
    if country_code:
        clauses.append(f"{table_alias}.country_code = ?")
        params.append(country_code)
    regions = []
    seen_regions = set()
    for country in countries:
        if country["region_code"] in seen_regions:
            continue
        seen_regions.add(country["region_code"])
        regions.append({"code": country["region_code"], "name": country["region_name"]})
    return region_code, country_code, countries, regions, clauses, params
