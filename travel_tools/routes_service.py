"""Travel tools - Google Routes API client with intermediate stops.

Extends the ai_daily_report GoogleRoutesService pattern (Phase 3A) with
`intermediates` support so a route can pass through stop-over points.

Endpoint: POST https://routes.googleapis.com/directions/v2:computeRoutes
Headers:  X-Goog-Api-Key, X-Goog-FieldMask
Body:     origin/destination {address}, travelMode DRIVE,
          routingPreference TRAFFIC_UNAWARE (stable, audit friendly),
          intermediates [{address, via: true}, ...]

Error handling mirrors ai_daily_report.google_routes:
- timeout / 429 / 5xx -> retry (max 2, exponential backoff)
- 4xx (bad request, auth) -> no retry
- ZERO_RESULTS -> verification_required
- API key missing -> verification_required
- Never generates fake data, never logs the key or full URLs.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0
ROUTING_PREFERENCE = "TRAFFIC_UNAWARE"
MAX_INTERMEDIATES = 9  # Google Routes allows up to 25; keep UI-driven stops bounded

FIELD_MASK = (
    "routes.distanceMeters,"
    "routes.duration,"
    "routes.polyline.encodedPolyline,"
    "routes.legs.distanceMeters,"
    "routes.legs.duration"
)


class MultiStopRouteResult:
    """Result from Google Routes API with optional intermediate stops."""

    def __init__(
        self,
        success: bool,
        total_distance_meters: Optional[float] = None,
        total_duration_seconds: Optional[int] = None,
        legs: Optional[List[Dict[str, Any]]] = None,
        encoded_polyline: Optional[str] = None,
        status: str = "failed",
        error: Optional[str] = None,
        provider: str = "google_routes",
        query_time: Optional[str] = None,
    ):
        self.success = success
        self.total_distance_meters = total_distance_meters
        self.total_duration_seconds = total_duration_seconds
        self.legs = legs or []
        self.encoded_polyline = encoded_polyline
        self.status = status  # success / failed / verification_required
        self.error = error
        self.provider = provider
        self.query_time = query_time or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MultiStopRoutesService:
    """Google Routes API client supporting stop-over waypoints."""

    def __init__(self, api_key: str, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_route(
        self,
        origin: str,
        destination: str,
        intermediates: Optional[List[str]] = None,
    ) -> MultiStopRouteResult:
        """Driving route origin -> [stops...] -> destination.

        Args:
            origin: origin address (user provided)
            destination: destination address (user provided)
            intermediates: optional stop-over addresses; each leg passes
                through them in order (via=true, like a fuel/food stop).
        """
        if not self.is_available():
            return MultiStopRouteResult(
                success=False, status="verification_required", error="routes_api_not_configured"
            )
        origin = (origin or "").strip()
        destination = (destination or "").strip()
        if not origin or not destination:
            return MultiStopRouteResult(
                success=False, status="verification_required", error="Missing origin or destination"
            )

        stops: List[Dict[str, Any]] = []
        for raw in (intermediates or [])[:MAX_INTERMEDIATES]:
            stop = (raw or "").strip()
            if stop:
                stops.append({"address": stop, "via": True})

        body_dict: Dict[str, Any] = {
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": "DRIVE",
            "routingPreference": ROUTING_PREFERENCE,
            "computeAlternativeRoutes": False,
        }
        if stops:
            body_dict["intermediates"] = stops
        body = json.dumps(body_dict).encode("utf-8")

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    ROUTES_URL,
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
                # Log only the status code, never the URL (contains the key)
                logger.warning("Travel tools Routes HTTP %s (attempt %s)", status_code, attempt + 1)
                if status_code == 429:
                    last_error = "Rate limited (429)"
                    if attempt < MAX_RETRIES:
                        self._sleep_backoff(attempt)
                        continue
                    return MultiStopRouteResult(success=False, status="failed", error="routes_rate_limit")
                if status_code >= 500:
                    last_error = f"Server error ({status_code})"
                    if attempt < MAX_RETRIES:
                        self._sleep_backoff(attempt)
                        continue
                    return MultiStopRouteResult(
                        success=False, status="failed", error=f"routes_server_error_{status_code}"
                    )
                if status_code == 403:
                    return MultiStopRouteResult(success=False, status="failed", error="routes_auth_failed")
                if status_code == 400:
                    return MultiStopRouteResult(
                        success=False,
                        status="verification_required",
                        error="routes_bad_request",
                    )
                return MultiStopRouteResult(
                    success=False, status="failed", error=f"routes_http_{status_code}"
                )

            except urllib.error.URLError as e:
                last_error = f"Network error: {type(e).__name__}"
                logger.warning(
                    "Travel tools Routes network error (attempt %s): %s", attempt + 1, type(e).__name__
                )
                if attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt)
                    continue
                return MultiStopRouteResult(success=False, status="failed", error="routes_network_timeout")

            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.warning("Travel tools Routes invalid response: %s", type(e).__name__)
                return MultiStopRouteResult(
                    success=False, status="failed", error="routes_invalid_response"
                )

        return MultiStopRouteResult(
            success=False, status="failed", error=last_error or "routes_unknown_error"
        )

    def _parse_response(self, data: Dict[str, Any]) -> MultiStopRouteResult:
        routes = data.get("routes")
        if not routes or not isinstance(routes, list) or not routes:
            return MultiStopRouteResult(
                success=False, status="verification_required", error="routes_zero_results"
            )
        route = routes[0]

        distance_raw = route.get("distanceMeters")
        if distance_raw is None:
            return MultiStopRouteResult(
                success=False, status="verification_required", error="routes_missing_distance"
            )
        try:
            total_distance = float(distance_raw)
        except (TypeError, ValueError):
            return MultiStopRouteResult(success=False, status="failed", error="routes_invalid_distance")

        total_duration = self._parse_duration(route.get("duration", "0s"))

        polyline = None
        poly_data = route.get("polyline", {})
        if isinstance(poly_data, dict):
            polyline = poly_data.get("encodedPolyline")
        if not polyline:
            return MultiStopRouteResult(
                success=False, status="verification_required", error="routes_missing_polyline"
            )

        legs: List[Dict[str, Any]] = []
        for leg in route.get("legs", []) or []:
            if not isinstance(leg, dict):
                continue
            legs.append(
                {
                    "distance_meters": self._safe_float(leg.get("distanceMeters")),
                    "duration_seconds": self._parse_duration(leg.get("duration")),
                }
            )

        return MultiStopRouteResult(
            success=True,
            total_distance_meters=total_distance,
            total_duration_seconds=total_duration,
            legs=legs,
            encoded_polyline=polyline,
            status="success",
        )

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_duration(duration_str: Optional[str]) -> Optional[int]:
        """Parse protobuf Duration string ("1234s" / "3.5s") to int seconds."""
        if not duration_str:
            return None
        try:
            return int(round(float(str(duration_str).rstrip("s"))))
        except (ValueError, AttributeError, TypeError):
            return None

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
