"""Travel tools: reusable route-map evidence and hotel finder.

Reuses the Google-API patterns of ai_daily_report (Routes / Static Maps)
and adds multi-stop routing (intermediates), Places lodging search and
spherical geometry to locate a random hotel near a derived origin point.

Public API:
    register_travel_tools_routes(app, api)  - Flask wiring (repo pattern)
    build_route_map(config, ...)            - flow 1 orchestrator
    find_hotel_near_origin(config, ...)     - flow 2 orchestrator
    TravelToolConfig                        - Google API key bundle
"""
from .geo import (
    COMPASS_BEARINGS,
    derive_origin_point,
    destination_point,
    haversine_meters,
    meters_to_miles,
    miles_to_meters,
)
from .places_service import PlacesService, PlaceResult
from .routes_service import MultiStopRouteResult, MultiStopRoutesService
from .service import (
    DEFAULT_SEARCH_RADIUS_MILES,
    HotelOutcome,
    RouteMapOutcome,
    TravelToolConfig,
    build_route_map,
    find_hotel_near_origin,
)
from .static_maps import FlexStaticMapsService, StaticMapResult
from .web import register_travel_tools_routes

__all__ = [
    "COMPASS_BEARINGS",
    "DEFAULT_SEARCH_RADIUS_MILES",
    "FlexStaticMapsService",
    "HotelOutcome",
    "MultiStopRouteResult",
    "MultiStopRoutesService",
    "PlaceResult",
    "PlacesService",
    "RouteMapOutcome",
    "StaticMapResult",
    "TravelToolConfig",
    "build_route_map",
    "derive_origin_point",
    "destination_point",
    "find_hotel_near_origin",
    "haversine_meters",
    "meters_to_miles",
    "miles_to_meters",
    "register_travel_tools_routes",
]
