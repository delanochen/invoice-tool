"""Travel tools - pure geometry helpers.

Bearing conventions:
- Bearings are degrees clockwise from true north (0 = N, 90 = E).
- The hotel finder takes the bearing FROM the destination TOWARD the origin
  (i.e. "the origin sits NW of the destination" -> bearing 315).

All distance math uses the spherical earth model; accuracy (~0.3%) is
sufficient for locating a lodging area, never for billing mileage.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

METERS_PER_MILE = 1609.344
EARTH_RADIUS_METERS = 6371008.8

# Eight-wind compass accepted by the hotel finder UI.
COMPASS_BEARINGS = {
    "N": 0,
    "NE": 45,
    "E": 90,
    "SE": 135,
    "S": 180,
    "SW": 225,
    "W": 270,
    "NW": 315,
}


def miles_to_meters(miles: float) -> float:
    return float(miles) * METERS_PER_MILE


def meters_to_miles(meters: float) -> float:
    return float(meters) / METERS_PER_MILE


def normalize_bearing(bearing_deg: float) -> float:
    """Normalize any bearing to [0, 360)."""
    return float(bearing_deg) % 360.0


def bearing_from_input(bearing_label: str = "", bearing_deg: Optional[float] = None) -> Optional[float]:
    """Resolve user bearing input (compass label wins only when degrees missing).

    Returns None when no valid input was supplied.
    """
    if bearing_deg is not None:
        try:
            value = float(bearing_deg)
        except (TypeError, ValueError):
            return None
        if math.isnan(value) or math.isinf(value):
            return None
        return normalize_bearing(value)
    label = (bearing_label or "").strip().upper()
    if label in COMPASS_BEARINGS:
        return float(COMPASS_BEARINGS[label])
    return None


def destination_point(
    lat: float, lng: float, bearing_deg: float, distance_meters: float
) -> Tuple[float, float]:
    """Spherical direct problem: point at distance/bearing from (lat, lng)."""
    if distance_meters < 0:
        raise ValueError("distance_meters must be >= 0")
    angular = distance_meters / EARTH_RADIUS_METERS
    phi1 = math.radians(lat)
    lambda1 = math.radians(lng)
    theta = math.radians(normalize_bearing(bearing_deg))

    sin_phi1 = math.sin(phi1)
    cos_phi1 = math.cos(phi1)
    sin_angular = math.sin(angular)
    cos_angular = math.cos(angular)

    phi2 = math.asin(sin_phi1 * cos_angular + cos_phi1 * sin_angular * math.cos(theta))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * sin_angular * cos_phi1,
        cos_angular - sin_phi1 * math.sin(phi2),
    )
    lng2 = (math.degrees(lambda2) + 540.0) % 360.0 - 180.0
    return math.degrees(phi2), lng2


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in meters."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(a)))


def derive_origin_point(
    dest_lat: float,
    dest_lng: float,
    bearing_deg: float,
    distance_miles: float,
) -> Tuple[float, float]:
    """Derive the approximate origin point.

    bearing_deg is the direction FROM the destination TOWARD the origin
    ("origin sits NW of destination" -> 315). Returns (lat, lng).
    """
    return destination_point(dest_lat, dest_lng, bearing_deg, miles_to_meters(distance_miles))


def format_latlng(lat: float, lng: float) -> str:
    return f"{lat:.6f},{lng:.6f}"


def format_hours(duration_seconds: Optional[float]) -> str:
    """Human readable duration, e.g. '3h 25m'."""
    if not duration_seconds or duration_seconds <= 0:
        return "-"
    total_minutes = int(round(duration_seconds / 60.0))
    hours, minutes = divmod(total_minutes, 60)
    if hours <= 0:
        return f"{minutes}m"
    return f"{hours}h {minutes:02d}m"
