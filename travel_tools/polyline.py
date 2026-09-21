"""Google encoded-polyline utilities for travel tools.

Why this exists: Google Static Maps is a GET API whose URLs are limited to
8192 characters. A cross-country driving route from Google Routes
(polylineQuality HIGH_QUALITY is the effective default) can encode to
~17k characters, which makes every Static Maps request fail with HTTP 400
regardless of polylineQuality=OVERVIEW (verified empirically: Google
returned a byte-identical polyline).

Fix strategy: decode the encoded polyline, simplify it locally with
Douglas-Peucker at increasing tolerances until it fits the URL budget, and
re-encode. At a 640x400 evidence map the simplified geometry is visually
indistinguishable, and distances/durations shown in the evidence panel keep
coming from the untouched Routes response.

Reference algorithm: https://developers.google.com/maps/documentation/utilities/polylinealgorithm
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Point = Tuple[float, float]  # (latitude, longitude)

# Tolerances (meters) tried in order when a polyline must shrink.
SIMPLIFY_TOLERANCES_M = (50, 100, 250, 500, 1000, 2000, 5000, 10000)

_M_PER_DEG_LAT = 111_320.0


def decode_polyline(encoded: str) -> List[Point]:
    """Decode a Google encoded polyline string into [(lat, lng), ...]."""
    points: List[Point] = []
    index = lat = lng = 0
    length = len(encoded)
    while index < length:
        for target in ("lat", "lng"):
            result = shift = 0
            while True:
                if index >= length:
                    raise ValueError("truncated encoded polyline")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            value = ~(result >> 1) if result & 1 else result >> 1
            if target == "lat":
                lat += value
            else:
                lng += value
        points.append((lat / 1e5, lng / 1e5))
    return points


def encode_polyline(points: Sequence[Point]) -> str:
    """Encode [(lat, lng), ...] into a Google encoded polyline string."""
    encoded: List[str] = []
    prev_lat = prev_lng = 0
    for lat, lng in points:
        d_lat = int(round(lat * 1e5)) - prev_lat
        d_lng = int(round(lng * 1e5)) - prev_lng
        prev_lat += d_lat
        prev_lng += d_lng
        encoded.append(_encode_value(d_lat))
        encoded.append(_encode_value(d_lng))
    return "".join(encoded)


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    chunks: List[str] = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def douglas_peucker(points: Sequence[Point], tolerance_m: float) -> List[Point]:
    """Simplify a polyline with Douglas-Peucker using planar meter distances."""
    if len(points) <= 2 or tolerance_m <= 0:
        return list(points)

    # Local tangent-plane approximation anchored at a mid-route latitude.
    lat0 = points[len(points) // 2][0]
    cos_lat = math.cos(math.radians(lat0))

    def to_xy(point: Point) -> Tuple[float, float]:
        return (point[1] * _M_PER_DEG_LAT * cos_lat, point[0] * _M_PER_DEG_LAT)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        ax, ay = to_xy(points[start])
        bx, by = to_xy(points[end])
        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        max_dist_sq = -1.0
        max_index = -1
        for i in range(start + 1, end):
            px, py = to_xy(points[i])
            if seg_len_sq <= 1e-12:
                dist_sq = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
                cx, cy = ax + t * dx, ay + t * dy
                dist_sq = (px - cx) ** 2 + (py - cy) ** 2
            if dist_sq > max_dist_sq:
                max_dist_sq = dist_sq
                max_index = i
        if max_dist_sq > tolerance_m * tolerance_m and max_index > 0:
            keep[max_index] = True
            stack.append((start, max_index))
            stack.append((max_index, end))

    return [point for point, flag in zip(points, keep) if flag]
