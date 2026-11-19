"""Photo-group capture: container check digits, short position codes, and watermark fields.

Recognition reads a photo after the shutter. The confirmed values are copied onto
every photo in the group only when the technician saves.
"""
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

_OWNER_VALUE = {}
_next = 10
for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _OWNER_VALUE[_letter] = _next
    _next += 1
    if _next % 11 == 0:
        _next += 1

_CONTAINER = re.compile(r"[A-Z]{4}\d{7}")
_POSITION = re.compile(r"^[A-Z0-9]{1,6}(?:-[A-Z0-9]{1,6})?$")


def iso6346_check_digit(first_ten):
    """ISO 6346 check digit for the four letters and six serial digits."""
    token = re.sub(r"[^A-Za-z0-9]", "", first_ten or "").upper()
    if len(token) != 10:
        return ""
    total = 0
    for index, character in enumerate(token):
        value = _OWNER_VALUE.get(character, int(character) if character.isdigit() else None)
        if value is None:
            return ""
        total += value * (1 << index)
    remainder = total % 11
    return "0" if remainder == 10 else str(remainder)


def assess_container(value):
    """Return the 11-character number and whether its check digit matches."""
    token = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    if not re.fullmatch(r"[A-Z]{4}\d{7}", token):
        return {"container_number": "", "check_digit_ok": False}
    expected = iso6346_check_digit(token[:10])
    return {"container_number": token, "check_digit_ok": bool(expected) and token[-1] == expected}


def container_candidates(text):
    """Pull ISO-shaped numbers out of OCR text. Valid check digits come first."""
    found = []
    for token in _CONTAINER.findall(re.sub(r"[^A-Za-z0-9]", "", (text or "").upper())):
        if token not in found:
            found.append(token)
    found.sort(key=lambda token: (not assess_container(token)["check_digit_ok"], token))
    return found


def position_token(value):
    """A short site code such as 62-3 or 1A2."""
    token = re.sub(r"\s+", "", value or "").upper()
    if not token or len(token) > 13 or not _POSITION.fullmatch(token):
        return ""
    if token.isdigit() and len(token) >= 10:
        return ""
    return token


def validate_container_llm(payload):
    if isinstance(payload, str):
        import json
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    assessed = assess_container(payload.get("container_number") or "")
    if not assessed["container_number"]:
        return None
    return assessed


def validate_position_llm(payload):
    if isinstance(payload, str):
        import json
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    token = position_token(str(payload.get("position_number") or ""))
    if not token:
        return None
    return {"position_number": token}


def container_orientations(image):
    """Upright, then both quarter turns. Container numbers are printed vertically."""
    upright = ImageOps.exif_transpose(image)
    yield "upright", upright
    yield "clockwise", upright.rotate(-90, expand=True)
    yield "counterclockwise", upright.rotate(90, expand=True)


def read_container_image(image, read_text):
    """OCR each orientation and keep the best ISO 6346 candidate."""
    texts = []
    found = []
    for _name, oriented in container_orientations(image):
        text = read_text(oriented) or ""
        texts.append(text)
        for token in container_candidates(text):
            if token not in found:
                found.append(token)
    found.sort(key=lambda token: (not assess_container(token)["check_digit_ok"], token))
    best = found[0] if found else ""
    assessed = assess_container(best)
    return {**assessed, "candidates": found[:5], "text": "\n".join(texts)}


_PHOTO_TYPE_LABEL = {
    "arrival": "进场",
    "departure": "离场",
    "safety": "自检",
    "equipment": "设备",
    "general": "非设备",
    "legacy": "历史",
}


def _as_utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def earliest_watermark(values):
    present = [value for value in values if value]
    if not present:
        return ""
    return min(present, key=_as_utc)


def shifted_watermark(original, earliest, new_start):
    """Shift one photo by the same whole minutes as the group's earliest time."""
    if not new_start:
        return original or earliest
    origin = _as_utc(original or earliest)
    start = _as_utc(earliest).replace(second=0, microsecond=0)
    chosen = _as_utc(new_start).replace(second=0, microsecond=0)
    return (origin + (chosen - start)).isoformat()


def should_server_watermark(awaiting, photo_digest="", original_digest="", client_watermarked="", photo_role="", watermark_opt_out=""):
    """Stamp on the server unless the phone explicitly says it already drew the watermark.

    watermark_opt_out is an album-only choice to keep a photo that was already
    watermarked in another camera app. Every other upload still uses the safety net.
    """
    if str(watermark_opt_out).lower() == "true":
        return False
    if str(awaiting).lower() == "true":
        return True
    if str(client_watermarked).lower() == "false":
        return True
    if photo_role and str(client_watermarked).lower() != "true":
        return True
    return bool(photo_digest and original_digest and photo_digest == original_digest)


def watermark_lines(fields):
    """Lines burned into every saved photo. Device fields are omitted when empty."""
    fields = fields or {}
    photo_type = fields.get("photo_type") or ""
    lines = [
        f"工单 {fields.get('order_number') or '-'}",
        f"施工员 {fields.get('technician_name') or fields.get('employee_name') or '-'}",
        f"时间 {fields.get('when') or fields.get('watermark_at') or fields.get('captured_at') or '-'}",
        f"类型 {_PHOTO_TYPE_LABEL.get(photo_type, photo_type or '-')}",
    ]
    if fields.get("equipment_number"):
        lines.append(f"Machine No. {fields['equipment_number']}")
    if fields.get("equipment_model"):
        lines.append(f"Model {fields['equipment_model']}")
    if fields.get("container_number"):
        lines.append(f"Container {fields['container_number']}")
    if fields.get("position_number"):
        lines.append(f"Location {fields['position_number']}")
    if fields.get("fault_note"):
        lines.append(f"Fault {str(fields['fault_note'])[:80]}")
    if fields.get("note"):
        lines.append(f"备注 {str(fields['note'])[:80]}")
    return lines


def fields_for_watermark(photo, pending):
    """Confirmed group fields replace whatever was known when the photo was taken."""
    pending = pending or {}
    return {
        "equipment_number": pending.get("equipment_number") or photo.get("equipment_number") or "",
        "equipment_model": pending.get("equipment_model") or photo.get("equipment_model") or "",
        "container_number": pending.get("container_number") or photo.get("container_number") or "",
        "position_number": pending.get("position_number") or photo.get("position_number") or "",
        "fault_note": pending.get("fault") or photo.get("fault_note") or "",
        "watermark": True,
    }


def stamp_group_watermark(content, fields):
    """Draw the confirmed group info onto a copy of the original photo."""
    with Image.open(BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        lines = watermark_lines(fields)
        margin = 12
        line_height = 16
        height = line_height * len(lines) + 16
        top = max(0, image.height - height - margin)
        draw.rectangle((margin, top, min(image.width - margin, margin + 520), top + height), fill=(255, 255, 255))
        after_repair = fields.get("photo_type") == "equipment" and fields.get("repair_phase") == "after"
        header_color = (27, 61, 110) if after_repair else (9, 83, 73)
        draw.rectangle((margin, top, min(image.width - margin, margin + 520), top + 24), fill=header_color)
        for index, line in enumerate(lines):
            color = (255, 255, 255) if index == 0 else (20, 46, 40)
            draw.text((margin + 8, top + 8 + index * line_height), line, fill=color, font=font)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
