"""Travel tools - Flask route registration.

Follows the repo feature-module pattern (profitability.py / rate_engine):
register_travel_tools_routes(app, api) receives app.py globals and wires
three endpoints:

    GET  /travel-tools              page (internal roles via menu permission)
    POST /travel-tools/api/route-map  multi-stop route map evidence (JSON)
    POST /travel-tools/api/hotel      random hotel near derived origin (JSON)

All Google calls happen server side; API keys never reach the browser.
Generated evidence PNGs are returned as data URLs and never stored.
"""
from __future__ import annotations

import logging

from flask import abort, jsonify, render_template, request

from . import service
from .service import TravelToolConfig

logger = logging.getLogger(__name__)


def register_travel_tools_routes(app, api):
    @app.get("/travel-tools")
    @api["login_required"]
    def travel_tools_page():
        if not api["is_internal_user"]():
            abort(403)
        if not api["has_menu_permission"]("travel_tools"):
            abort(403)
        return render_template(
            "travel_tools.html",
            keys_ready={
                "routes": bool(api["get_google_routes_api_key"]()),
                "static_maps": bool(api["get_google_static_maps_api_key"]()),
                "places": bool(api["get_google_places_api_key"]()),
            },
        )

    @app.post("/travel-tools/api/route-map")
    @api["login_required"]
    def travel_tools_route_map_api():
        if not api["is_internal_user"]() or not api["has_menu_permission"]("travel_tools"):
            abort(403)
        payload = request.get_json(silent=True) or {}
        config = _travel_tool_config(api)
        outcome = service.build_route_map(
            config,
            origin=str(payload.get("origin", "")),
            destination=str(payload.get("destination", "")),
            stops=[str(s) for s in (payload.get("stops") or [])][:12],
        )
        return jsonify(_outcome_payload(outcome))

    @app.post("/travel-tools/api/hotel")
    @api["login_required"]
    def travel_tools_hotel_api():
        if not api["is_internal_user"]() or not api["has_menu_permission"]("travel_tools"):
            abort(403)
        payload = request.get_json(silent=True) or {}
        bearing_deg = payload.get("bearing_deg")
        if bearing_deg in ("", None):
            bearing_deg = None
        else:
            try:
                bearing_deg = float(bearing_deg)
            except (TypeError, ValueError):
                bearing_deg = None
        exclude_names = [str(n)[:120] for n in (payload.get("exclude") or [])][:40]
        config = _travel_tool_config(api)
        outcome = service.find_hotel_near_origin(
            config,
            destination=str(payload.get("destination", "")),
            trip_distance_miles=payload.get("distance_miles"),
            bearing_label=str(payload.get("bearing", "")),
            bearing_deg=bearing_deg,
            radius_miles=payload.get("radius_miles") or service.DEFAULT_SEARCH_RADIUS_MILES,
            exclude_names=exclude_names,
        )
        return jsonify(_outcome_payload(outcome))


def _travel_tool_config(api) -> TravelToolConfig:
    return TravelToolConfig(
        routes_api_key=api["get_google_routes_api_key"](),
        static_maps_api_key=api["get_google_static_maps_api_key"](),
        places_api_key=api["get_google_places_api_key"](),
        geocoding_api_key=api["get_google_geocoding_api_key"](),
    )


def _outcome_payload(outcome) -> dict:
    """Serialize a RouteMapOutcome / HotelOutcome into a JSON-safe dict."""
    data = {
        "success": outcome.success,
        "error": outcome.error,
        "error_code": outcome.error_code,
        "image": outcome.image_data_url,
    }
    if hasattr(outcome, "leg_miles"):
        data.update(
            {
                "origin": outcome.origin,
                "destination": outcome.destination,
                "stops": outcome.stops,
                "leg_miles": [_round2(m) for m in outcome.leg_miles],
                "leg_hours": [_round2(h) for h in outcome.leg_hours],
                "total_miles": _round2(outcome.total_miles),
                "total_hours_text": outcome.total_hours_text,
                "provider": outcome.provider,
            }
        )
    if hasattr(outcome, "hotel"):
        data.update(
            {
                "hotel": outcome.hotel,
                "candidate_count": outcome.candidate_count,
                "derived": {
                    "lat": outcome.derived_lat,
                    "lng": outcome.derived_lng,
                    "area_label": outcome.area_label,
                },
                "bearing_label": outcome.bearing_label,
                "target_miles": _round2(outcome.target_miles),
                "straight_miles": _round2(outcome.straight_miles),
                "actual_miles": _round2(outcome.actual_miles),
                "actual_hours_text": outcome.actual_hours_text,
                "provider": outcome.provider,
            }
        )
    return data


def _round2(value):
    return None if value is None else round(float(value), 2)
