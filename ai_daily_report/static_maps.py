"""AI Daily Report - GoogleStaticMapsService (Phase 3B)

Fetches a Google Static Map image with route polyline drawn on it.

Endpoint: GET https://maps.googleapis.com/maps/api/staticmap
Parameters:
  size=WxH
  markers=color:green|label:A|<origin>
  markers=color:red|label:B|<destination>
  path=enc:<encoded_polyline>
  key=<API_KEY>

This service ONLY fetches the map image. It does NOT:
- Re-calculate routes (that's GoogleRoutesService, Phase 3A)
- Generate the info panel (that's MileageEvidenceService)
- Store or cache Google content beyond the evidence file

Security:
- API key never logged
- Full request URL (which contains key) never logged
- Full addresses never logged
- Response content-type validated (must be image/*)
- Max response size enforced

Google attribution:
- Static Map image contains Google attribution by default (in the image itself)
- MileageEvidenceService must PRESERVE it completely:
  NOT crop, NOT cover, NOT obscure, NOT blur, NOT overlay, NOT modify
- Do NOT recreate or re-draw Google attribution ourselves
- Only add additional attribution if Google's current policy explicitly requires it
"""
from __future__ import annotations

import io
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"
DEFAULT_MAP_SIZE = "640x400"
DEFAULT_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB max

# Allowed content types for Static Maps response
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/jpg"}


class StaticMapResult:
    """Result from Google Static Maps API."""
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


class GoogleStaticMapsService:
    """Google Static Maps API client with retry and error handling."""

    def __init__(self, api_key: str, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def get_route_map(
        self,
        origin: str,
        destination: str,
        encoded_polyline: str,
        size: str = DEFAULT_MAP_SIZE,
    ) -> StaticMapResult:
        """Fetch a Static Map with route polyline and markers.

        Args:
            origin: Origin address (for marker A)
            destination: Destination address (for marker B)
            encoded_polyline: Encoded polyline from Phase 3A Google Routes
            size: Map size, e.g. "640x400"

        Returns:
            StaticMapResult with image_bytes or error status.
        """
        if not self.is_available():
            return StaticMapResult(
                success=False,
                status="verification_required",
                error="static_maps_api_not_configured",
            )

        if not encoded_polyline:
            return StaticMapResult(
                success=False,
                status="verification_required",
                error="missing_route_polyline",
            )

        # Build query parameters (key is included but never logged)
        params = {
            "size": size,
            "maptype": "roadmap",
            "key": self.api_key,
        }
        query_string = urllib.parse.urlencode(params)

        # Markers and path are added separately (they can contain special chars)
        origin_marker = f"markers=color:green%7Clabel:A%7C{urllib.parse.quote(origin)}"
        dest_marker = f"markers=color:red%7Clabel:B%7C{urllib.parse.quote(destination)}"
        path_param = f"path=enc:{urllib.parse.quote(encoded_polyline)}"

        full_url = f"{STATIC_MAPS_URL}?{query_string}&{origin_marker}&{dest_marker}&{path_param}"

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(full_url, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    content_type = resp.headers.get("Content-Type", "").lower()

                    # Validate content type - log only, error code is internal-safe
                    if not any(ct in content_type for ct in ALLOWED_CONTENT_TYPES):
                        logger.warning("Static Maps invalid content-type")
                        return StaticMapResult(
                            success=False,
                            status="failed",
                            error="static_maps_invalid_content_type",
                        )

                    # Read with size limit
                    image_bytes = resp.read(MAX_IMAGE_BYTES + 1)
                    if len(image_bytes) > MAX_IMAGE_BYTES:
                        return StaticMapResult(
                            success=False,
                            status="failed",
                            error="image_too_large",
                        )

                    if not image_bytes:
                        return StaticMapResult(
                            success=False,
                            status="failed",
                            error="empty_response",
                        )

                    return StaticMapResult(
                        success=True,
                        image_bytes=image_bytes,
                        content_type=content_type,
                        status="success",
                    )

            except urllib.error.HTTPError as e:
                status_code = e.code
                # Log only status code, NEVER the URL (contains API key)
                logger.warning("Static Maps HTTP %s (attempt %s)", status_code, attempt + 1)

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
                    return StaticMapResult(success=False, status="failed", error=f"static_maps_server_error_{status_code}")

                if status_code == 403:
                    return StaticMapResult(success=False, status="failed", error="static_maps_auth_failed")

                if status_code == 400:
                    return StaticMapResult(success=False, status="verification_required", error="static_maps_bad_request")

                return StaticMapResult(success=False, status="failed", error=f"static_maps_http_{status_code}")

            except urllib.error.URLError as e:
                last_error = f"Network error: {type(e).__name__}"
                logger.warning("Static Maps network error (attempt %s): %s", attempt + 1, type(e).__name__)
                if attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt)
                    continue
                return StaticMapResult(success=False, status="failed", error="static_maps_timeout")

            except Exception as e:
                logger.warning("Static Maps unexpected error: %s", type(e).__name__)
                return StaticMapResult(success=False, status="failed", error="static_maps_unexpected_error")

        return StaticMapResult(success=False, status="failed", error=last_error or "static_maps_unknown_error")

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        """Exponential backoff: 1s, 2s, 4s..."""
        delay = RETRY_BASE_DELAY * (2 ** attempt)
        time.sleep(delay)
