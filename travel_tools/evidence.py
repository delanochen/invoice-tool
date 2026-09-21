"""Travel tools - map + info panel composition (PIL).

Generalizes the ai_daily_report MileageEvidenceService panel layout:
the Google Static Maps image stays FULL (attribution never cropped,
covered or obscured) and a white info panel is appended underneath.

Panel text stays ASCII/English so the Debian production container does not
need CJK fonts (same decision as the existing mileage evidence panel).
"""
from __future__ import annotations

import io
import logging
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PANEL_BG_COLOR = (255, 255, 255)
PANEL_TEXT_COLOR = (30, 30, 30)
PANEL_TITLE_COLOR = (0, 80, 160)
PANEL_PADDING = 20
PANEL_TITLE_HEIGHT = 28
PANEL_LINE_HEIGHT = 22

_FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "arialbd.ttf",
    "segoeuib.ttf",
)
_FONT_CANDIDATES_BODY = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "arial.ttf",
    "segoeui.ttf",
)


def _load_fonts():
    from PIL import ImageFont

    for path in _FONT_CANDIDATES_BOLD:
        try:
            return ImageFont.truetype(path, 16), None
        except (OSError, IOError):
            continue
    font_title = ImageFont.load_default()

    for path in _FONT_CANDIDATES_BODY:
        try:
            return font_title, ImageFont.truetype(path, 13)
        except (OSError, IOError):
            continue
    return font_title, ImageFont.load_default()


def compose_map_with_panel(
    map_bytes: bytes,
    title: str,
    lines: Sequence[str],
) -> Optional[bytes]:
    """Compose map image (top) + info panel (bottom). Returns PNG bytes or None.

    The map is pasted unmodified at full size; attribution stays intact.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("PIL unavailable, cannot compose travel tool evidence")
        return None

    try:
        map_img = Image.open(io.BytesIO(map_bytes))
        map_img.load()
    except Exception:  # noqa: BLE001 - corrupt image -> caller falls back
        return None

    map_w, map_h = map_img.size
    panel_height = PANEL_PADDING * 2 + PANEL_TITLE_HEIGHT + len(lines) * PANEL_LINE_HEIGHT
    total_h = map_h + panel_height

    final = Image.new("RGB", (map_w, total_h), PANEL_BG_COLOR)
    if map_img.mode != "RGB":
        map_img = map_img.convert("RGB")
    final.paste(map_img, (0, 0))

    draw = ImageDraw.Draw(final)
    font_title, font_body = _load_fonts()

    y = map_h + PANEL_PADDING
    x = PANEL_PADDING

    draw.text((x, y), title, fill=PANEL_TITLE_COLOR, font=font_title)
    y += PANEL_TITLE_HEIGHT

    for line in lines:
        draw.text((x, y), line, fill=PANEL_TEXT_COLOR, font=font_body)
        y += PANEL_LINE_HEIGHT

    buf = io.BytesIO()
    final.save(buf, format="PNG")
    return buf.getvalue()


def build_route_lines(
    origin: str,
    stops: List[str],
    destination: str,
    leg_miles: List[Optional[float]],
    leg_hours: List[Optional[float]],
    total_miles: Optional[float],
    total_hours_text: str,
    provider: str,
) -> List[str]:
    """Info panel lines for the multi-stop route evidence."""
    lines: List[str] = []

    def fmt_point(index: int, name: str) -> str:
        label = "Origin" if index == 0 else f"Stop {index}"
        return f"{label}: {_short(name)}"

    points = [origin] + list(stops) + [destination]
    lines.append(fmt_point(0, points[0]))
    for i, stop in enumerate(stops, start=1):
        lines.append(fmt_point(i, stop))
    lines.append(f"Destination: {_short(destination)}")

    for i, miles in enumerate(leg_miles):
        seg = points[i] if i < len(points) else ""
        nxt = points[i + 1] if i + 1 < len(points) else ""
        miles_text = f"{miles:.2f} mi" if miles is not None else "n/a"
        hours = leg_hours[i] if i < len(leg_hours) else None
        hours_text = "" if hours is None else f" / {hours:.2f} h"
        lines.append(f"Leg {i + 1} ({_short(seg, 24)} -> {_short(nxt, 24)}): {miles_text}{hours_text}")

    total_text = f"{total_miles:.2f} mi" if total_miles is not None else "n/a"
    lines.append(f"Total Distance: {total_text} / Driving Time: {total_hours_text}")
    lines.append(f"Route Provider: {provider}")
    return lines


def build_hotel_route_lines(
    hotel_name: str,
    hotel_address: str,
    destination: str,
    actual_miles: Optional[float],
    actual_hours_text: str,
    provider: str,
) -> List[str]:
    """Route-style info panel for the hotel finder evidence.

    The PNG is deliberately presented as a plain origin -> destination route
    (same format as the mileage/route evidence): the picked hotel IS the
    origin. The panel never mentions the hotel-search process itself - no
    rating, no bearing, no target distance, no delta - those stay in the
    UI response only, as decision aids for the user.
    """
    origin_text = ", ".join(part for part in [(hotel_name or "").strip(), (hotel_address or "").strip()] if part)
    distance_text = f"{actual_miles:.2f} mi" if actual_miles is not None else "n/a"
    return [
        f"Origin: {_short(origin_text, 70) or 'n/a'}",
        f"Destination: {_short(destination, 70)}",
        f"One-way Distance: {distance_text}",
        f"Driving Time: {actual_hours_text}",
        f"Route Provider: {provider}",
    ]


def _short(text: Optional[str], limit: int = 70) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def png_bytes_to_data_url(png_bytes: Optional[bytes]) -> Optional[str]:
    """Encode PNG bytes as a data URL for inline display (key never exposed)."""
    if not png_bytes:
        return None
    import base64

    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def image_size(png_bytes: bytes) -> Tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as img:
        return img.size
