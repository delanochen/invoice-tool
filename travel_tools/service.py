"""Travel tools - orchestration of route map and hotel finder flows.

Both flows are read-only with respect to the business database: they call
Google services and return images/data. Nothing is persisted, so no
Google-content storage rules are triggered (images are returned to the
browser transiently and the user decides what to keep, mirroring the
draft-stage evidence behaviour of the AI daily report).

Flow 1 - route map:
    origin + optional stop-overs + destination
    -> Google Routes (intermediates via=true)
    -> Google Static Maps (A / 1..9 / B markers + polyline)
    -> composed PNG (map + info panel)

Flow 2 - hotel finder:
    destination + trip distance + bearing ("origin sits NW of destination")
    -> derive approximate origin point (spherical math)
    -> reverse geocode area label (best effort)
    -> Places searchNearby lodging around the derived point
    -> random pick (excluding previously shown names)
    -> driving verification hotel -> destination (Google Routes)
    -> composed PNG (route map + info panel)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import evidence, geo
from .places_service import PlacesService
from .routes_service import MultiStopRouteResult, MultiStopRoutesService
from .static_maps import FlexStaticMapsService

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_RADIUS_MILES = 25.0
MAX_SEARCH_RADIUS_MILES = 30.0  # 50000 m hard limit of searchNearby


@dataclass
class TravelToolConfig:
    """Google API keys resolved by the caller (app settings / env)."""

    routes_api_key: str = ""
    static_maps_api_key: str = ""
    places_api_key: str = ""
    geocoding_api_key: str = ""


@dataclass
class RouteMapOutcome:
    success: bool
    error: Optional[str] = None
    error_code: Optional[str] = None
    origin: str = ""
    destination: str = ""
    stops: List[str] = field(default_factory=list)
    leg_miles: List[Optional[float]] = field(default_factory=list)
    leg_hours: List[Optional[float]] = field(default_factory=list)
    total_miles: Optional[float] = None
    total_hours_text: str = "-"
    provider: str = "google_routes"
    image_data_url: Optional[str] = None


@dataclass
class HotelOutcome:
    success: bool
    error: Optional[str] = None
    error_code: Optional[str] = None
    hotel: Optional[Dict[str, Any]] = None
    candidate_count: int = 0
    derived_lat: Optional[float] = None
    derived_lng: Optional[float] = None
    area_label: str = ""
    bearing_label: str = ""
    target_miles: Optional[float] = None
    straight_miles: Optional[float] = None
    actual_miles: Optional[float] = None
    actual_hours_text: str = "-"
    leg_miles: List[Optional[float]] = field(default_factory=list)
    leg_hours: List[Optional[float]] = field(default_factory=list)
    total_miles: Optional[float] = None
    total_hours_text: str = "-"
    destination: str = ""
    provider: str = "google_routes"
    image_data_url: Optional[str] = None


def _hours_text(duration_seconds: Optional[int]) -> str:
    return geo.format_hours(duration_seconds)


def build_route_map(
    config: TravelToolConfig,
    origin: str,
    destination: str,
    stops: Optional[List[str]] = None,
) -> RouteMapOutcome:
    """Flow 1: multi-stop route map evidence."""
    origin = (origin or "").strip()
    destination = (destination or "").strip()
    clean_stops = [(s or "").strip() for s in (stops or []) if (s or "").strip()][:9]

    if not origin or not destination:
        return RouteMapOutcome(success=False, error="起点和终点都必须填写", error_code="missing_endpoints")

    routes = MultiStopRoutesService(config.routes_api_key)
    if not routes.is_available():
        return RouteMapOutcome(
            success=False, error="未配置 Google Routes API Key", error_code="routes_api_not_configured"
        )

    route = routes.get_route(origin, destination, clean_stops)
    if not route.success:
        return RouteMapOutcome(
            success=False,
            error=_route_error_text(route.error),
            error_code=route.error,
        )

    leg_miles = [geo.meters_to_miles(leg["distance_meters"]) if leg.get("distance_meters") else None for leg in route.legs]
    leg_hours = [leg.get("duration_seconds") / 3600.0 if leg.get("duration_seconds") else None for leg in route.legs]
    total_miles = geo.meters_to_miles(route.total_distance_meters) if route.total_distance_meters else None
    total_hours_text = _hours_text(route.total_duration_seconds)

    static_maps = FlexStaticMapsService(config.static_maps_api_key)
    markers = FlexStaticMapsService.build_markers(origin, destination, clean_stops)
    map_result = static_maps.get_map(markers, route.encoded_polyline)
    if not map_result.success:
        # Route numbers are still useful without the map image
        return RouteMapOutcome(
            success=False,
            error=_static_error_text(map_result.error),
            error_code=map_result.error,
            origin=origin,
            destination=destination,
            stops=clean_stops,
            leg_miles=leg_miles,
            leg_hours=leg_hours,
            total_miles=total_miles,
            total_hours_text=total_hours_text,
            provider=route.provider,
        )

    lines = evidence.build_route_lines(
        origin,
        clean_stops,
        destination,
        leg_miles,
        leg_hours,
        total_miles,
        total_hours_text,
        route.provider,
    )
    composed = evidence.compose_map_with_panel(map_result.image_bytes, "Route Map", lines)
    if composed is None:
        return RouteMapOutcome(
            success=False, error="地图图片处理失败", error_code="image_composition_failed"
        )

    return RouteMapOutcome(
        success=True,
        origin=origin,
        destination=destination,
        stops=clean_stops,
        leg_miles=leg_miles,
        leg_hours=leg_hours,
        total_miles=total_miles,
        total_hours_text=total_hours_text,
        provider=route.provider,
        image_data_url=evidence.png_bytes_to_data_url(composed),
    )


def find_hotel_near_origin(
    config: TravelToolConfig,
    destination: str,
    trip_distance_miles: float,
    bearing_label: str = "",
    bearing_deg: Optional[float] = None,
    radius_miles: float = DEFAULT_SEARCH_RADIUS_MILES,
    exclude_names: Optional[List[str]] = None,
    rng=None,
) -> HotelOutcome:
    """Flow 2: random hotel near the derived origin point."""
    destination = (destination or "").strip()
    if not destination:
        return HotelOutcome(success=False, error="请填写终点", error_code="missing_destination")

    try:
        trip_miles = float(trip_distance_miles)
    except (TypeError, ValueError):
        return HotelOutcome(success=False, error="距离必须是数字（英里）", error_code="invalid_distance")
    if trip_miles <= 0 or trip_miles > 3000:
        return HotelOutcome(success=False, error="距离超出合理范围（0-3000 英里）", error_code="distance_out_of_range")

    resolved_bearing = geo.bearing_from_input(bearing_label, bearing_deg)
    if resolved_bearing is None:
        return HotelOutcome(success=False, error="请选择起点方位", error_code="invalid_bearing")

    bearing_label_text = _bearing_display(bearing_label, bearing_deg, resolved_bearing)

    places = PlacesService(config.places_api_key, config.geocoding_api_key)
    if not places.is_available():
        return HotelOutcome(success=False, error="未配置 Google Places API Key", error_code="places_api_not_configured")

    geocode_ok, coords, geocode_error = places.geocode_address(destination)
    if not geocode_ok or coords is None:
        return HotelOutcome(
            success=False,
            error=_geocode_error_text(geocode_error),
            error_code=geocode_error or "geocoding_failed",
        )
    dest_lat, dest_lng = coords

    derived_lat, derived_lng = geo.derive_origin_point(dest_lat, dest_lng, resolved_bearing, trip_miles)
    area_label = places.reverse_geocode_area(derived_lat, derived_lng)

    radius = min(max(float(radius_miles or DEFAULT_SEARCH_RADIUS_MILES), 1.0), MAX_SEARCH_RADIUS_MILES)
    search = places.search_lodging(derived_lat, derived_lng, geo.miles_to_meters(radius))
    if not search.success:
        return HotelOutcome(
            success=False,
            error=_places_error_text(search.error),
            error_code=search.error,
        )
    if not search.places:
        return HotelOutcome(
            success=False,
            error="附近没有找到宾馆，试试扩大搜索半径",
            error_code="no_lodging_found",
            derived_lat=derived_lat,
            derived_lng=derived_lng,
            area_label=area_label,
            bearing_label=bearing_label_text,
            target_miles=trip_miles,
        )

    pick = PlacesService.pick_random_hotel(search.places, exclude_names, rng)
    if pick is None:
        return HotelOutcome(
            success=False,
            error="候选宾馆都已展示过，清空后重试",
            error_code="all_candidates_excluded",
            candidate_count=len(search.places),
            derived_lat=derived_lat,
            derived_lng=derived_lng,
            area_label=area_label,
            bearing_label=bearing_label_text,
            target_miles=trip_miles,
        )

    # Driving verification: hotel -> destination with the same Routes client
    actual_miles: Optional[float] = None
    actual_hours: Optional[float] = None
    actual_hours_text = "-"
    provider = "google_routes"
    polyline: Optional[str] = None
    routes = MultiStopRoutesService(config.routes_api_key)
    if routes.is_available():
        route = routes.get_route(
            _position_text(pick), destination
        )
        if route.success:
            actual_miles = geo.meters_to_miles(route.total_distance_meters) if route.total_distance_meters else None
            actual_hours = route.total_duration_seconds / 3600.0 if route.total_duration_seconds else None
            actual_hours_text = _hours_text(route.total_duration_seconds)
            polyline = route.encoded_polyline
            provider = route.provider

    # Static map: hotel (A) -> destination (B) with the verified polyline
    image_data_url: Optional[str] = None
    static_maps = FlexStaticMapsService(config.static_maps_api_key)
    hotel_position = _position_text(pick)
    map_result = static_maps.get_map(
        [
            {"label": "A", "color": "green", "position": hotel_position},
            {"label": "B", "color": "red", "position": destination},
        ],
        polyline,
    )

    lines = evidence.build_hotel_route_lines(
        pick.get("name", ""),
        pick.get("address", ""),
        destination,
        actual_miles,
        actual_hours_text,
        provider,
    )
    if map_result.success:
        # Title stays "Route Map": the evidence reads as a normal
        # origin -> destination route, not a hotel-search artifact.
        composed = evidence.compose_map_with_panel(map_result.image_bytes, "Route Map", lines)
        image_data_url = evidence.png_bytes_to_data_url(composed)

    straight = geo.haversine_meters(
        derived_lat, derived_lng, pick.get("lat") or dest_lat, pick.get("lng") or dest_lng
    )
    straight_miles = geo.meters_to_miles(straight)

    return HotelOutcome(
        success=True,
        hotel=pick,
        candidate_count=len(search.places),
        derived_lat=derived_lat,
        derived_lng=derived_lng,
        area_label=area_label,
        bearing_label=bearing_label_text,
        target_miles=trip_miles,
        straight_miles=round(straight_miles, 2),
        actual_miles=round(actual_miles, 2) if actual_miles is not None else None,
        actual_hours_text=actual_hours_text,
        leg_miles=[actual_miles] if actual_miles is not None else [],
        leg_hours=[actual_hours] if actual_hours is not None else [],
        total_miles=actual_miles,
        total_hours_text=actual_hours_text,
        destination=destination,
        provider=provider,
        image_data_url=image_data_url,
    )


def _position_text(place: Dict[str, Any]) -> str:
    """Prefer coordinates (stable marker), fall back to the address."""
    lat, lng = place.get("lat"), place.get("lng")
    if lat is not None and lng is not None:
        return f"{float(lat):.6f},{float(lng):.6f}"
    return place.get("address") or ""


def _bearing_display(label: str, degrees: Optional[float], resolved: float) -> str:
    label = (label or "").strip().upper()
    if label in geo.COMPASS_BEARINGS and degrees is None:
        return f"{label} ({int(resolved)} deg)"
    return f"{resolved:.0f} deg"


def _geocode_error_text(code: Optional[str]) -> str:
    """Human-readable message for a failed destination geocode.

    The generic "无法解析" wording hid the real cause (missing key vs Google
    refusal vs genuinely unknown address); surface it instead.
    """
    mapping = {
        "empty_address": "请填写终点地址",
        "geocoding_api_not_configured": "未配置 Google Geocoding API Key（请在系统设置配置，或检查环境变量）",
        "geocoding_no_result": "Google 查无此地址：门牌号可能不存在。试试只写到「路名, 城市, 州」，或换一个相邻门牌号",
        "geocoding_http_error": "Google Geocoding 服务返回错误，请稍后重试",
        "geocoding_network_error": "Google Geocoding 网络超时，请稍后重试",
        "geocoding_invalid_response": "Google Geocoding 返回内容异常，请稍后重试",
        "geocoding_invalid_location": "Google 返回坐标异常，请换一种地址写法",
    }
    if code in mapping:
        return mapping[code]
    if code:
        # Raw Google status passed through (REQUEST_DENIED / OVER_QUERY_LIMIT / ...).
        return f"Google Geocoding 调用受限（{code}）：请检查密钥是否启用 Geocoding API、配额是否用尽"
    return "终点地址无法解析，请更具体（城市+州或完整地址）"


def _route_error_text(code: Optional[str]) -> str:
    mapping = {
        "routes_api_not_configured": "未配置 Google Routes API Key",
        "routes_bad_request": "地址无法识别或中途点太多，请检查后重试",
        "routes_zero_results": "找不到可行驾车路线",
        "routes_auth_failed": "Google Routes 鉴权失败（403）",
        "routes_rate_limit": "Google Routes 限流，请稍后重试",
        "routes_network_timeout": "Google Routes 网络超时",
    }
    return mapping.get(code or "", "路线计算失败，请稍后重试")


def _static_error_text(code: Optional[str]) -> str:
    mapping = {
        "static_maps_api_not_configured": "未配置 Google Static Maps API Key",
        "static_maps_bad_request": "地图无法渲染该请求（请检查地址写法或稍后重试）",
        "static_maps_url_too_long": "路线跨度过大，地图无法渲染；请减少停靠点或分段生成",
        "static_maps_auth_failed": "Google Static Maps 鉴权失败（403）",
        "static_maps_rate_limit": "Static Maps 限流，请稍后重试",
        "static_maps_timeout": "Static Maps 网络超时",
    }
    return mapping.get(code or "", "地图图片获取失败")


def _places_error_text(code: Optional[str]) -> str:
    mapping = {
        "places_api_not_configured": "未配置 Google Places API Key",
        "places_bad_request": "Places 请求无效（坐标或半径超出限制）",
        "places_auth_failed": "Google Places 鉴权失败（403）",
        "places_rate_limit": "Google Places 限流，请稍后重试",
        "places_network_timeout": "Google Places 网络超时",
    }
    return mapping.get(code or "", "宾馆搜索失败，请稍后重试")
