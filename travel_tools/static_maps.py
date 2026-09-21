"""Travel tools - flexible Google Static Maps client.

Generalizes the ai_daily_report GoogleStaticMapsService (Phase 3B) so any
number of labeled markers can be drawn together with the route polyline:
origin (A, green), numbered stop-overs (1..9, blue), destination (B, red),
or a derived hotel/origin point pair for the hotel finder.

Security (same rules as ai_daily_report.static_maps):
- API key never logged, full request URL never logged
- Response content-type validated (image/*)
- Max response size enforced (5 MB)
- Retry on 429/5xx only, exponential backoff

URL budget: route polylines are simplified locally (Douglas-Peucker) when
the request URL would exceed the Google 8192-character limit.
"""
from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"
DEFAULT_MAP_SIZE = "640x400"
DEFAULT_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/jpg"}
# Google Static Maps URLs are limited to 8192 characters. Keep a safety
# margin; the dominant risk is a long route polyline (a cross-country route
# encodes to ~17k chars and every such request failed with HTTP 400).
MAX_URL_LENGTH = 8000

MARKER_COLORS = {"origin": "green", "stop": "blue", "destination": "red", "hotel": "green"}


class StaticMapResult:
    def __init__(
        self,
        success: bool,
        image_bytes: Optional[bytes] = None,
        content_type: Optional[str] = None,
        status: str = "failed",
        error: Optional[str] = None,
    ):
        self.success = success
        self.image_bytes = image_bytes
        self.content_type = content_type
        self.status = status  # success / failed / verification_required
        self.error = error


class FlexStaticMapsService:
    """Google Static Maps client with arbitrary labeled markers."""

    def __init__(self, api_key: str, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def build_markers(
        origin: Optional[str] = None,
        destination: Optional[str] = None,
        stops: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Build the standard marker list: A origin, numbered stops, B destination."""
        markers: List[Dict[str, str]] = []
        if origin:
            markers.append({"label": "A", "color": MARKER_COLORS["origin"], "position": origin})
        for index, stop in enumerate(stops or [], start=1):
            if stop:
                markers.append(
                    {"label": str(index % 10), "color": MARKER_COLORS["stop"], "position": stop}
                )
        if destination:
            markers.append(
                {"label": "B", "color": MARKER_COLORS["destination"], "position": destination}
            )
        return markers

    def get_map(
        self,
        markers: List[Dict[str, str]],
        encoded_polyline: Optional[str] = None,
        size: str = DEFAULT_MAP_SIZE,
        scale: Optional[int] = None,
        center: Optional[str] = None,
        zoom: Optional[int] = None,
    ) -> StaticMapResult:
        """Fetch a static map image.

        Args:
            markers: list of {"label": "A", "color": "green", "position": addr|lat,lng}
                ("label" is optional; when omitted/empty the marker is drawn
                without a label character)
            encoded_polyline: route polyline (path=enc:...)
            size: "WxH"
            scale: optional Google Static Maps scale (1 or 2); 2 returns a
                double-resolution image at the same price tier
            center: optional "lat,lng"; omit to let Google auto-fit markers
            zoom: optional zoom level (used together with center). Providing
                both pins the viewport (e.g. a fixed country-wide view) so
                filtering no longer changes how far the map is zoomed in;
                markers outside the viewport are not drawn by Google.
        """
        if not self.is_available():
            return StaticMapResult(
                success=False, status="verification_required", error="static_maps_api_not_configured"
            )
        if not markers and not encoded_polyline:
            return StaticMapResult(
                success=False, status="verification_required", error="missing_map_content"
            )

        params = {"size": size, "maptype": "roadmap", "key": self.api_key}
        if scale in (1, 2):
            params["scale"] = str(scale)
        if center:
            params["center"] = center
        if zoom is not None:
            params["zoom"] = str(zoom)
        query_string = urllib.parse.urlencode(params)

        parts: List[str] = []
        for marker in markers:
            position = urllib.parse.quote(str(marker.get("position", "")))
            label = str(marker.get("label", ""))[:1]
            color = str(marker.get("color", "red"))
            if label:
                parts.append(f"markers=color:{color}%7Clabel:{label}%7C{position}")
            else:
                parts.append(f"markers=color:{color}%7C{position}")
        if encoded_polyline:
            fitted = self._fit_path(encoded_polyline, self._path_budget(query_string, parts))
            if fitted is None:
                logger.warning(
                    "Travel tools Static Maps path too long even simplified (%s source chars)",
                    len(encoded_polyline),
                )
                return StaticMapResult(
                    success=False, status="failed", error="static_maps_url_too_long"
                )
            parts.append(f"path=enc:{urllib.parse.quote(fitted)}")

        full_url = f"{STATIC_MAPS_URL}?{query_string}&" + "&".join(parts)
        if len(full_url) > MAX_URL_LENGTH:
            # Defensive: pathological marker addresses can also blow the limit.
            logger.warning("Travel tools Static Maps URL too long: %s chars", len(full_url))
            return StaticMapResult(
                success=False, status="failed", error="static_maps_url_too_long"
            )

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(full_url, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    content_type = resp.headers.get("Content-Type", "").lower()
                    if not any(ct in content_type for ct in ALLOWED_CONTENT_TYPES):
                        logger.warning("Travel tools Static Maps invalid content-type")
                        return StaticMapResult(
                            success=False, status="failed", error="static_maps_invalid_content_type"
                        )
                    image_bytes = resp.read(MAX_IMAGE_BYTES + 1)
                    if len(image_bytes) > MAX_IMAGE_BYTES:
                        return StaticMapResult(
                            success=False, status="failed", error="image_too_large"
                        )
                    if not image_bytes:
                        return StaticMapResult(
                            success=False, status="failed", error="empty_response"
                        )
                    return StaticMapResult(
                        success=True,
                        image_bytes=image_bytes,
                        content_type=content_type,
                        status="success",
                    )

            except urllib.error.HTTPError as e:
                status_code = e.code
                logger.warning("Travel tools Static Maps HTTP %s (attempt %s)", status_code, attempt + 1)
                if status_code == 429:
                    last_error = "Rate limited (429)"
                    if attempt < MAX_RETRIES:
                        self._sleep_backoff(attempt)
                        continue
                    return StaticMapResult(success=False, status="failed", error="static_maps_rate_limit")
                if status_code >= 500:
                    last_error = f"Server error ({status_code})"
                    if attempt < MAX_RETRIES:
                        self._sleep_backoff(attempt)
                        continue
                    return StaticMapResult(
                        success=False,
                        status="failed",
                        error=f"static_maps_server_error_{status_code}",
                    )
                if status_code == 403:
                    return StaticMapResult(success=False, status="failed", error="static_maps_auth_failed")
                if status_code == 400:
                    return StaticMapResult(
                        success=False, status="verification_required", error="static_maps_bad_request"
                    )
                return StaticMapResult(
                    success=False, status="failed", error=f"static_maps_http_{status_code}"
                )

            except urllib.error.URLError as e:
                last_error = f"Network error: {type(e).__name__}"
                logger.warning(
                    "Travel tools Static Maps network error (attempt %s): %s",
                    attempt + 1,
                    type(e).__name__,
                )
                if attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt)
                    continue
                return StaticMapResult(success=False, status="failed", error="static_maps_timeout")

            except Exception as e:  # noqa: BLE001 - mirror existing defensive behaviour
                logger.warning("Travel tools Static Maps unexpected error: %s", type(e).__name__)
                return StaticMapResult(success=False, status="failed", error="static_maps_unexpected_error")

        return StaticMapResult(
            success=False, status="failed", error=last_error or "static_maps_unknown_error"
        )

    def _path_budget(self, query_string: str, parts: List[str]) -> int:
        """Characters available for the quoted `path=enc:...` part."""
        overhead = len(f"{STATIC_MAPS_URL}?{query_string}&")
        overhead += sum(len(part) + 1 for part in parts)
        overhead += len("path=enc:")
        return MAX_URL_LENGTH - overhead

    def _fit_path(self, encoded_polyline: str, budget: int) -> Optional[str]:
        """Return a polyline whose URL-quoted form fits `budget`, else None.

        The original polyline is kept when it already fits; otherwise it is
        simplified with Douglas-Peucker at increasing tolerances. At evidence
        map resolution the simplified geometry is visually equivalent.
        """
        if len(urllib.parse.quote(encoded_polyline)) <= budget:
            return encoded_polyline
        from .polyline import SIMPLIFY_TOLERANCES_M, decode_polyline, douglas_peucker, encode_polyline

        try:
            points = decode_polyline(encoded_polyline)
        except ValueError:
            return None
        if len(points) < 2:
            return None
        for tolerance in SIMPLIFY_TOLERANCES_M:
            candidate = encode_polyline(douglas_peucker(points, tolerance))
            if len(urllib.parse.quote(candidate)) <= budget:
                logger.info(
                    "Travel tools Static Maps polyline simplified: %s -> %s chars (tolerance %sm)",
                    len(encoded_polyline),
                    len(candidate),
                    tolerance,
                )
                return candidate
        return None

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
