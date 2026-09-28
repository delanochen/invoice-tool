"""AI Daily Report - GoogleRoutesService (Phase 3A)

Calls Google Routes API (official, not web scraping) to get driving route.

Endpoint: POST https://routes.googleapis.com/directions/v2:computeRoutes
Headers: X-Goog-Api-Key, X-Goog-FieldMask
Body: origin {address}, destination {address}, travelMode: DRIVE,
      routingPreference: TRAFFIC_UNAWARE, computeAlternativeRoutes: false

routingPreference = TRAFFIC_UNAWARE (default):
- Mileage calculation depends on distanceMeters (stable, not traffic-dependent)
- duration is reference only; no need for real-time traffic
- Provides result stability for audit/compliance

routingPreference = TRAFFIC_AWARE_OPTIMAL + FUEL_EFFICIENT (opt-in via
prefer_fuel_efficient=True):
- Google Maps 手机端绿色叶子 = 省油路线语义（不是最短、也不是最长路线）
- 请求附带 routeModifiers.vehicleInfo.emissionType（默认 GASOLINE），
  Google 才会按该排放类型算省油路线
- 响应里省油路线标 routeLabels=["FUEL_EFFICIENT"]，默认路线标 ["DEFAULT_ROUTE"]
- 选路规则：优先 FUEL_EFFICIENT；Google 没返回省油路线时回退 DEFAULT_ROUTE。
  绝不无条件取 routes[0]，也不挑最长路线。
- 注意：绿色叶子语义下里程可能**比默认路线更长**，这是预期的；
  仍然直接采用 Google 原始 distanceMeters，不做人为加减。

Returns:
- distance_meters (raw, always meters)
- duration_seconds (TRAFFIC_UNAWARE: stable route duration, no live traffic)
- encoded_polyline
- origin_normalized / destination_normalized (from routes[0].legs[0].startAddress/endAddress,
  these ARE native computeRoutes response fields, not geocoding)
- route_labels (selected route's routeLabels, for audit; e.g. ["FUEL_EFFICIENT"])
- provider, query_time (UTC ISO8601 with Z suffix)

Error handling:
- timeout, 429, 5xx -> retry (max 2, exponential backoff)
- 4xx (bad request, auth) -> no retry, return failed
- ZERO_RESULTS / routes=[] -> verification_required
- API key not configured -> verification_required, reason=routes_api_not_configured
- Never generates fake data

Security:
- API key never logged, never in response, never in DeepSeek prompt
- Full addresses not logged (only masked)

Phase 3B TODO (Mileage Evidence):
- Generate map image from route_polyline + origin/destination
- Add text overlay: Employee, Date, Origin, Destination, One-way Distance,
  Reported Mileage, Overnight Stay
- MUST display Google attribution ("Powered by Google") per Google Maps
  Platform Terms of Service when using map imagery
- Use route_distance_meters/one_way_miles/reported_miles saved in Phase 3A;
  do NOT re-query Google Routes
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
DEFAULT_TIMEOUT = 15  # seconds
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0  # seconds, exponential backoff

# routingPreference: TRAFFIC_UNAWARE for stable mileage results
# (distanceMeters is not traffic-dependent; duration is reference only)
ROUTING_PREFERENCE = "TRAFFIC_UNAWARE"

# 省油路线（Google Maps 手机端绿色叶子）所需参数。
# 只有显式开启 prefer_fuel_efficient 的调用方才会用到，默认行为完全不变。
FUEL_EFFICIENT_ROUTING_PREFERENCE = "TRAFFIC_AWARE_OPTIMAL"
FUEL_EFFICIENT_REFERENCE_ROUTE = "FUEL_EFFICIENT"
DEFAULT_REFERENCE_ROUTE = "DEFAULT_ROUTE"
DEFAULT_EMISSION_TYPE = "GASOLINE"

# Field mask: only request what we need to reduce payload
# NOTE: routes.legs.startAddress / routes.legs.endAddress are NOT valid
# computeRoutes field paths (Google returns 400 INVALID_ARGUMENT for them).
# Normalized addresses are therefore not requested; downstream code falls
# back to the original user/service-order addresses when *_normalized is None.
FIELD_MASK = (
    "routes.distanceMeters,"
    "routes.duration,"
    "routes.polyline.encodedPolyline"
)

# 省油路线模式必须在 field mask 里带上 routes.routeLabels，
# 否则无法区分 FUEL_EFFICIENT 与 DEFAULT_ROUTE，也就无法按语义选路。
FUEL_EFFICIENT_FIELD_MASK = (
    "routes.distanceMeters,"
    "routes.duration,"
    "routes.polyline.encodedPolyline,"
    "routes.routeLabels"
)


class RouteResult:
    """Result from Google Routes API."""
    def __init__(
        self,
        success: bool,
        distance_meters: Optional[float] = None,
        duration_seconds: Optional[int] = None,
        encoded_polyline: Optional[str] = None,
        origin_normalized: Optional[str] = None,
        destination_normalized: Optional[str] = None,
        status: str = "failed",
        error: Optional[str] = None,
        provider: str = "google_routes",
        query_time: Optional[str] = None,
        route_labels: Optional[list] = None,
    ):
        self.success = success
        self.distance_meters = distance_meters
        self.duration_seconds = duration_seconds
        self.encoded_polyline = encoded_polyline
        self.origin_normalized = origin_normalized
        self.destination_normalized = destination_normalized
        self.status = status  # success / failed / verification_required
        self.error = error
        self.provider = provider
        # UTC ISO8601 with Z suffix (timezone-aware)
        self.query_time = query_time or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 被选中路线的 routeLabels（审计用）。省油路线模式下应含 FUEL_EFFICIENT；
        # 回退到默认路线时含 DEFAULT_ROUTE。默认模式下 Google 不返回该字段，为 []。
        self.route_labels = list(route_labels or [])

    @property
    def is_fuel_efficient(self) -> bool:
        """被选中的是否确为省油路线（供审计/日志确认选路结果）。"""
        return FUEL_EFFICIENT_REFERENCE_ROUTE in self.route_labels


class GoogleRoutesService:
    """Google Routes API client with retry and error handling."""

    def __init__(
        self,
        api_key: str,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        prefer_fuel_efficient: bool = False,
        emission_type: str = DEFAULT_EMISSION_TYPE,
    ):
        self.api_key = api_key
        self.timeout = timeout
        # 同步 UI 流程（如工作日报里程佐证）会传更小的 max_retries / timeout，
        # 避免每个员工最坏情况拖到分钟级、被 gunicorn/网关掐断连接。
        self.max_retries = max_retries
        # 省油路线（绿色叶子）语义。默认 False —— 只有工作日报里程佐证/
        # 辅助填写这两个入口显式开启，其他调用方行为完全不变。
        self.prefer_fuel_efficient = bool(prefer_fuel_efficient)
        self.emission_type = (emission_type or DEFAULT_EMISSION_TYPE).strip().upper() or DEFAULT_EMISSION_TYPE

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _field_mask(self) -> str:
        return FUEL_EFFICIENT_FIELD_MASK if self.prefer_fuel_efficient else FIELD_MASK

    def _build_request_body(self, origin: str, destination: str) -> Dict[str, Any]:
        """构造 computeRoutes 请求体。

        prefer_fuel_efficient=False 时与历史请求体逐字一致（默认行为不变）；
        开启时按省油路线语义请求，并带上排放类型，否则 Google 不会返回
        routeLabels=FUEL_EFFICIENT 的路线。
        """
        body: Dict[str, Any] = {
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": "DRIVE",
            "computeAlternativeRoutes": False,
        }
        if self.prefer_fuel_efficient:
            body["routingPreference"] = FUEL_EFFICIENT_ROUTING_PREFERENCE
            body["requestedReferenceRoutes"] = [FUEL_EFFICIENT_REFERENCE_ROUTE]
            body["routeModifiers"] = {"vehicleInfo": {"emissionType": self.emission_type}}
        else:
            body["routingPreference"] = ROUTING_PREFERENCE
        return body

    @staticmethod
    def _select_route(routes: list) -> Dict[str, Any]:
        """按语义选择路线。

        省油路线模式下 Google 会在同一响应里返回多条候选（默认路线与省油路线），
        省油那条带 routeLabels=["FUEL_EFFICIENT"]。规则：
          1. 优先第一条带 FUEL_EFFICIENT 标签的路线；
          2. 没有省油路线时，回退第一条带 DEFAULT_ROUTE 标签的路线；
          3. 都没有标签（默认模式 / 旧响应）时取 routes[0]。
        绝不按里程长短挑选，也不做任何加减系数。
        """
        for route in routes:
            labels = route.get("routeLabels") or []
            if isinstance(labels, list) and FUEL_EFFICIENT_REFERENCE_ROUTE in labels:
                return route
        for route in routes:
            labels = route.get("routeLabels") or []
            if isinstance(labels, list) and DEFAULT_REFERENCE_ROUTE in labels:
                return route
        return routes[0]


    def get_driving_route(self, origin: str, destination: str) -> RouteResult:
        """Get driving route from origin to destination.

        Args:
            origin: Origin address (user-provided, not normalized)
            destination: Destination address (from service_order)

        Returns:
            RouteResult with distance_meters, duration, polyline, or error status.
        """
        if not self.is_available():
            # Do NOT fall back to Browser Key or Geocoding Key
            return RouteResult(
                success=False,
                status="verification_required",
                error="routes_api_not_configured",
            )

        if not origin or not destination:
            return RouteResult(success=False, status="verification_required", error="Missing origin or destination")

        body = json.dumps(self._build_request_body(origin, destination)).encode("utf-8")
        field_mask = self._field_mask()

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    GOOGLE_ROUTES_URL,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": self.api_key,
                        "X-Goog-FieldMask": field_mask,
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return self._parse_response(data, select_fuel_efficient=self.prefer_fuel_efficient)

            except urllib.error.HTTPError as e:
                status_code = e.code
                # Log only status code, never API key or full address
                logger.warning("Google Routes HTTP %s (attempt %s)", status_code, attempt + 1)

                if status_code == 429:
                    # Rate limit - retry with backoff
                    last_error = f"Rate limited (429)"
                    if attempt < self.max_retries:
                        self._sleep_backoff(attempt)
                        continue
                    return RouteResult(success=False, status="failed", error="Google Routes rate limit exceeded")

                if status_code >= 500:
                    # Server error - retry
                    last_error = f"Server error ({status_code})"
                    if attempt < self.max_retries:
                        self._sleep_backoff(attempt)
                        continue
                    return RouteResult(success=False, status="failed", error=f"Google Routes server error ({status_code})")

                if status_code == 403:
                    return RouteResult(success=False, status="failed", error="Google Routes authentication failed (403)")

                if status_code == 400:
                    # Bad request - do NOT retry
                    return RouteResult(success=False, status="verification_required", error="Invalid address or request (400)")

                # Other 4xx - no retry
                return RouteResult(success=False, status="failed", error=f"Google Routes error ({status_code})")

            except urllib.error.URLError as e:
                # Network error / timeout - retry
                last_error = f"Network error: {type(e).__name__}"
                logger.warning("Google Routes network error (attempt %s): %s", attempt + 1, type(e).__name__)
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                return RouteResult(success=False, status="failed", error="Google Routes network timeout")

            except (json.JSONDecodeError, ValueError, KeyError) as e:
                return RouteResult(success=False, status="failed", error=f"Invalid Google Routes response: {type(e).__name__}")

        return RouteResult(success=False, status="failed", error=last_error or "Unknown error")

    def _parse_response(self, data: Dict[str, Any], select_fuel_efficient: bool = False) -> RouteResult:
        """Parse Google Routes API response.

        origin_normalized / destination_normalized are best-effort only: field mask
        no longer requests legs.startAddress/endAddress (invalid paths).

        select_fuel_efficient=True 时按 routeLabels 语义选路（省油优先，回退默认），
        否则保持历史行为取 routes[0]（默认模式响应里只有一条路线）。
        """
        routes = data.get("routes")
        if not routes or not isinstance(routes, list) or len(routes) == 0:
            # routes=[] must not cause IndexError
            return RouteResult(success=False, status="verification_required", error="No route found (ZERO_RESULTS)")

        route = self._select_route(routes) if select_fuel_efficient else routes[0]

        # distanceMeters may be string or number in API
        distance_raw = route.get("distanceMeters")
        if distance_raw is None:
            return RouteResult(success=False, status="verification_required", error="Route missing distance")
        try:
            distance_meters = float(distance_raw)
        except (ValueError, TypeError):
            return RouteResult(success=False, status="failed", error="Invalid distance format")

        # duration is protobuf Duration string: "1234s" or "3.5s"
        # Supports fractional seconds via float -> int (round to nearest second)
        duration_str = route.get("duration", "0s")
        duration_seconds = self._parse_duration(duration_str)

        polyline = None
        poly_data = route.get("polyline", {})
        if isinstance(poly_data, dict):
            polyline = poly_data.get("encodedPolyline")

        # Normalized addresses from legs (native computeRoutes fields)
        origin_norm = None
        dest_norm = None
        legs = route.get("legs", [])
        if legs and isinstance(legs, list) and len(legs) > 0:
            leg = legs[0]
            origin_norm = leg.get("startAddress")
            dest_norm = leg.get("endAddress")

        raw_labels = route.get("routeLabels")
        route_labels = raw_labels if isinstance(raw_labels, list) else []

        return RouteResult(
            success=True,
            distance_meters=distance_meters,
            duration_seconds=duration_seconds,
            encoded_polyline=polyline,
            origin_normalized=origin_norm,
            destination_normalized=dest_norm,
            status="success",
            route_labels=route_labels,
        )

    @staticmethod
    def _parse_duration(duration_str: Optional[str]) -> Optional[int]:
        """Parse protobuf Duration string ("1234s" or "3.5s") to integer seconds.

        Uses float -> round to support fractional seconds.
        Returns None if parsing fails.
        """
        if not duration_str:
            return None
        try:
            # Strip trailing 's', parse as float (supports "3.5s"), round to nearest int
            value = float(duration_str.rstrip("s"))
            return int(round(value))
        except (ValueError, AttributeError, TypeError):
            return None

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        """Exponential backoff: 1s, 2s, 4s..."""
        delay = RETRY_BASE_DELAY * (2 ** attempt)
        time.sleep(delay)
