"""Travel tools - Google Places / Geocoding services.

Uses official Google APIs only:
- Places API (New) searchNearby for lodging around a point:
    POST https://places.googleapis.com/v1/places:searchNearby
    Headers: X-Goog-Api-Key, X-Goog-FieldMask
    Body: includedTypes ["lodging"], locationRestriction circle
- Geocoding API for address <-> coordinates (destination geocoding and
  best-effort reverse geocoding of the derived origin point).

Security:
- API key never logged, never returned to the client
- Only status codes logged on HTTP errors
- Retry on 429/5xx (max 2, exponential backoff); 4xx -> no retry
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DEFAULT_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0
MAX_RADIUS_METERS = 50000  # searchNearby hard limit
DEFAULT_RADIUS_METERS = 40000
MAX_RESULTS = 20  # searchNearby hard limit

PLACES_FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.rating,"
    "places.userRatingCount,"
    "places.location"
)


class PlaceResult:
    """Wrapper for a lodging search / geocode batch call."""

    def __init__(
        self,
        success: bool,
        places: Optional[List[Dict[str, Any]]] = None,
        error: Optional[str] = None,
        status: str = "failed",
    ):
        self.success = success
        self.places = places or []
        self.error = error
        self.status = status  # success / failed / verification_required


class PlacesService:
    """Lodging search + geocoding helpers (all server side, key stays here)."""

    def __init__(self, api_key: str, geocoding_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = (api_key or "").strip()
        self.geocoding_key = (geocoding_key or self.api_key or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    # ─── Lodging search ─────────────────────────────────────────────────

    def search_lodging(
        self,
        lat: float,
        lng: float,
        radius_meters: float = DEFAULT_RADIUS_METERS,
        max_results: int = MAX_RESULTS,
    ) -> PlaceResult:
        """Search lodging (hotels/motels/inns) within a circle around a point."""
        if not self.is_available():
            return PlaceResult(success=False, error="places_api_not_configured", status="verification_required")
        radius = min(max(float(radius_meters), 100.0), MAX_RADIUS_METERS)
        count = max(1, min(int(max_results or MAX_RESULTS), MAX_RESULTS))

        body = json.dumps(
            {
                "includedTypes": ["lodging"],
                "maxResultCount": count,
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": float(lat), "longitude": float(lng)},
                        "radius": radius,
                    }
                },
                "rankPreference": "POPULARITY",
            }
        ).encode("utf-8")

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    PLACES_SEARCH_URL,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": self.api_key,
                        "X-Goog-FieldMask": PLACES_FIELD_MASK,
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return self._parse_places(data)

            except urllib.error.HTTPError as e:
                status_code = e.code
                logger.warning("Travel tools Places HTTP %s (attempt %s)", status_code, attempt + 1)
                if status_code == 429:
                    last_error = "places_rate_limit"
                    if attempt < MAX_RETRIES:
                        self._sleep_backoff(attempt)
                        continue
                    return PlaceResult(success=False, error="places_rate_limit")
                if status_code >= 500:
                    last_error = "places_server_error"
                    if attempt < MAX_RETRIES:
                        self._sleep_backoff(attempt)
                        continue
                    return PlaceResult(success=False, error="places_server_error")
                if status_code == 403:
                    return PlaceResult(success=False, error="places_auth_failed")
                if status_code == 400:
                    return PlaceResult(success=False, error="places_bad_request", status="verification_required")
                return PlaceResult(success=False, error=f"places_http_{status_code}")

            except urllib.error.URLError as e:
                logger.warning(
                    "Travel tools Places network error (attempt %s): %s", attempt + 1, type(e).__name__
                )
                last_error = "places_network_timeout"
                if attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt)
                    continue
                return PlaceResult(success=False, error="places_network_timeout")

            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.warning("Travel tools Places invalid response: %s", type(e).__name__)
                return PlaceResult(success=False, error="places_invalid_response")

        return PlaceResult(success=False, error=last_error or "places_unknown_error")

    def _parse_places(self, data: Dict[str, Any]) -> PlaceResult:
        raw_places = data.get("places")
        if raw_places is None:
            # No results is a valid empty response
            return PlaceResult(success=True, places=[], status="success")
        if not isinstance(raw_places, list):
            return PlaceResult(success=False, error="places_invalid_response")

        places: List[Dict[str, Any]] = []
        for item in raw_places:
            if not isinstance(item, dict):
                continue
            display = item.get("displayName") or {}
            location = item.get("location") or {}
            places.append(
                {
                    "id": item.get("id") or "",
                    "name": (display.get("text") or "").strip(),
                    "address": (item.get("formattedAddress") or "").strip(),
                    "rating": self._safe_float(item.get("rating")),
                    "user_ratings": self._safe_int(item.get("userRatingCount")),
                    "lat": self._safe_float(location.get("latitude")),
                    "lng": self._safe_float(location.get("longitude")),
                }
            )
        return PlaceResult(success=True, places=places, status="success")

    # ─── Geocoding ──────────────────────────────────────────────────────

    def geocode_address(self, address: str) -> Tuple[bool, Optional[Tuple[float, float]], Optional[str]]:
        """Geocode an address to (lat, lng). Returns (ok, coords, error_code)."""
        address = (address or "").strip()
        if not address:
            return False, None, "empty_address"
        if not self.geocoding_key:
            return False, None, "geocoding_api_not_configured"
        params = urllib.parse.urlencode(
            {"address": address, "key": self.geocoding_key}
        )
        url = f"{GEOCODE_URL}?{params}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning("Travel tools Geocoding HTTP %s", e.code)
            return False, None, "geocoding_http_error"
        except urllib.error.URLError:
            logger.warning("Travel tools Geocoding network error")
            return False, None, "geocoding_network_error"
        except (json.JSONDecodeError, ValueError):
            return False, None, "geocoding_invalid_response"

        if data.get("status") != "OK":
            code = str(data.get("status") or "geocoding_failed")
            logger.warning("Travel tools Geocoding status %s", code)
            if code == "ZERO_RESULTS":
                return False, None, "geocoding_no_result"
            # Surface the raw Google status (REQUEST_DENIED / OVER_QUERY_LIMIT /
            # INVALID_REQUEST ...) so the UI can tell key problems apart from
            # unresolvable addresses.
            return False, None, code

        results = data.get("results") or []
        if not results:
            return False, None, "geocoding_no_result"
        location = results[0].get("geometry", {}).get("location", {})
        lat = self._safe_float(location.get("lat"))
        lng = self._safe_float(location.get("lng"))
        if lat is None or lng is None:
            return False, None, "geocoding_invalid_location"
        return True, (lat, lng), None

    def reverse_geocode_area(self, lat: float, lng: float) -> str:
        """Best-effort human readable area label for a coordinate ('' when unknown).

        Never raises; purely cosmetic for the UI.
        """
        if not self.geocoding_key:
            return ""
        params = urllib.parse.urlencode(
            {"latlng": f"{float(lat):.6f},{float(lng):.6f}", "key": self.geocoding_key}
        )
        url = f"{GEOCODE_URL}?{params}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            return ""
        if data.get("status") != "OK":
            return ""
        results = data.get("results") or []
        if not results:
            return ""
        # Prefer the most specific short label that is not a street address
        for result in results:
            types = result.get("types") or []
            if any(t in {"locality", "administrative_area_level_3"} for t in types):
                return str(result.get("formatted_address") or "")
        for result in results:
            types = result.get("types") or []
            if any(t in {"administrative_area_level_2", "administrative_area_level_1"} for t in types):
                return str(result.get("formatted_address") or "")
        return str(results[0].get("formatted_address") or "")

    # ─── Random pick ────────────────────────────────────────────────────

    @staticmethod
    def pick_random_hotel(
        places: List[Dict[str, Any]],
        exclude_names: Optional[List[str]] = None,
        rng=None,
    ) -> Optional[Dict[str, Any]]:
        """Randomly pick one hotel, excluding previously shown names.

        `rng` is injectable for tests; defaults to the module-level random.
        """
        import random as _random

        chooser = rng or _random
        excluded = {(name or "").strip().casefold() for name in (exclude_names or [])}
        candidates = [
            p for p in places if (p.get("name") or "").strip().casefold() not in excluded
        ]
        if not candidates:
            return None
        return chooser.choice(candidates)

    # ─── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
