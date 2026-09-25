"""AI Daily Report - GoogleRoutesService (Phase 3A)

Calls Google Routes API (official, not web scraping) to get driving route.

Endpoint: POST https://routes.googleapis.com/directions/v2:computeRoutes
Headers: X-Goog-Api-Key, X-Goog-FieldMask
Body: origin {address}, destination {address}, travelMode: DRIVE,
      routingPreference: TRAFFIC_UNAWARE, computeAlternativeRoutes: false

routingPreference = TRAFFIC_UNAWARE:
- Mileage calculation depends on distanceMeters (stable, not traffic-dependent)
- duration is reference only; no need for real-time traffic
- Provides result stability for audit/compliance

Returns:
- distance_meters (raw, always meters)
- duration_seconds (TRAFFIC_UNAWARE: stable route duration, no live traffic)
- encoded_polyline
- origin_normalized / destination_normalized (from routes[0].legs[0].startAddress/endAddress,
  these ARE native computeRoutes response fields, not geocoding)
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


class GoogleRoutesService:
    """Google Routes API client with retry and error handling."""

    def __init__(self, api_key: str, timeout: int = DEFAULT_TIMEOUT, max_retries: int = MAX_RETRIES):
        self.api_key = api_key
        self.timeout = timeout
        # 同步 UI 流程（如工作日报里程佐证）会传更小的 max_retries / timeout，
        # 避免每个员工最坏情况拖到分钟级、被 gunicorn/网关掐断连接。
        self.max_retries = max_retries

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

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

        body = json.dumps({
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": "DRIVE",
            "routingPreference": ROUTING_PREFERENCE,
            "computeAlternativeRoutes": False,
        }).encode("utf-8")

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
                        "X-Goog-FieldMask": FIELD_MASK,
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return self._parse_response(data)

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

    def _parse_response(self, data: Dict[str, Any]) -> RouteResult:
        """Parse Google Routes API response.

        origin_normalized / destination_normalized are best-effort only: field mask
        no longer requests legs.startAddress/endAddress (invalid paths).
        """
        routes = data.get("routes")
        if not routes or not isinstance(routes, list) or len(routes) == 0:
            # routes=[] must not cause IndexError
            return RouteResult(success=False, status="verification_required", error="No route found (ZERO_RESULTS)")

        route = routes[0]

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

        return RouteResult(
            success=True,
            distance_meters=distance_meters,
            duration_seconds=duration_seconds,
            encoded_polyline=polyline,
            origin_normalized=origin_norm,
            destination_normalized=dest_norm,
            status="success",
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
