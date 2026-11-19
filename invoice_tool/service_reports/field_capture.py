"""Photo-group capture: container check digits, short position codes, and watermark fields.

Recognition reads a photo after the shutter. The confirmed values are copied onto
every photo in the group only when the technician saves.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


# --- Server-side fallback watermark -----------------------------------------
# Mirrors the phone-side card in static/field-watermark.js. The server only
# stamps photos the phone did not watermark itself, so this card must carry the
# same fields (header, device rows, address, coordinates, footer) and render
# Chinese glyphs instead of falling back to the bitmap default font.

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_FONT_REGULAR = [
    os.path.join(_FONT_DIR, "NotoSansCJK-Regular.ttc"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/NotoSansSC-VF.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_FONT_BOLD = [
    os.path.join(_FONT_DIR, "NotoSansCJK-Bold.ttc"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/NotoSansSC-VF.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_font_cache = {}


def _watermark_font(size, bold=False):
    key = (round(size, 1), bold)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    candidates = _FONT_BOLD if bold else _FONT_REGULAR
    font = None
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                font = ImageFont.truetype(path, size=size)
                break
            except OSError:
                continue
    if font is None:
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _local_time(value, timezone_name=""):
    """Format an ISO timestamp in the photo's own timezone, like the phone card."""
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        tz = ZoneInfo(timezone_name) if timezone_name else timezone.utc
        local = parsed.astimezone(tz)
        offset = local.utcoffset() or timedelta(0)
        minutes = int(offset.total_seconds() // 60)
        sign = "+" if minutes >= 0 else "-"
        hours, rem = divmod(abs(minutes), 60)
        offset_label = f"GMT{sign}{hours}" + (f":{rem:02d}" if rem else "")
        return f"{local:%Y-%m-%d %H:%M:%S} {offset_label}"
    except (ValueError, TypeError, ZoneInfoNotFoundError, OverflowError):
        return str(value)


def _wrap_text(text, font, max_width, max_lines):
    chars = list(re.sub(r"\s+", " ", str(text or "")).strip())
    lines, line = [], ""
    for char in chars:
        trial = line + char
        if line and font.getlength(trial) > max_width:
            lines.append(line.rstrip())
            line = char.lstrip()
        else:
            line = trial
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and font.getlength(lines[-1] + "…") > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1] + "…"
    return lines or ["—"]


def _watermark_rows(fields):
    """Card rows in the same order as the phone-side template."""
    fields = fields or {}
    photo_type = fields.get("photo_type") or ""
    type_label = fields.get("photo_type_label") or _PHOTO_TYPE_LABEL.get(photo_type, photo_type or "—")
    technician = fields.get("technician_name") or fields.get("employee_name") or "—"
    address = fields.get("site_address") or fields.get("site_name") or "未填写地址"
    rows = []
    if str(fields.get("equipment_number") or "").strip():
        rows.append(("Machine No.", str(fields["equipment_number"]).strip(), 2))
    if str(fields.get("equipment_model") or "").strip():
        rows.append(("Model", str(fields["equipment_model"]).strip(), 2))
    if str(fields.get("position_number") or "").strip():
        rows.append(("Location No.", str(fields["position_number"]).strip(), 1))
    if str(fields.get("container_number") or "").strip():
        rows.append(("Container No.", str(fields["container_number"]).strip(), 1))
    if str(fields.get("fault_note") or "").strip():
        rows.append(("Fault", str(fields["fault_note"]).strip(), 2))
    if photo_type == "equipment" and str(fields.get("pump_fuse_numbers") or "").strip():
        rows.append(("Pump Fuse", str(fields["pump_fuse_numbers"]).strip(), 1))
    rows.append(("Type", type_label, 1))
    rows.append(("Technician", technician, 2))
    rows.append(("Time", _local_time(fields.get("when") or fields.get("watermark_at") or fields.get("captured_at"),
                                     fields.get("timezone_name") or ""), 2))
    rows.append(("Address", address, 3))
    if str(fields.get("note") or "").strip():
        rows.append(("备注", str(fields["note"]).strip(), 2))
    return rows


def stamp_group_watermark(content, fields):
    """Draw the confirmed group info onto a copy of the original photo."""
    fields = fields or {}
    with Image.open(BytesIO(content)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    width, height = image.size

    font_size = max(11, min(width, height) * 0.021)
    pad = font_size * 0.68
    label_width = font_size * 5.8
    line_height = font_size * 1.18
    header_font = font_size * 1.22
    company_font = font_size * 0.90
    footer_font = font_size * 0.72

    card_width = width * (0.50 if width > height else 0.65)
    inner_width = card_width - pad * 2
    value_width = inner_width - label_width

    title = str(fields.get("site_name") or "Prasinos Power").strip() or "Prasinos Power"
    rows = _watermark_rows(fields)
    body_font = _watermark_font(font_size)
    label_font = _watermark_font(font_size * 0.86)
    rendered = []
    body_height = 0
    for label, value, max_lines in rows:
        wrapped = _wrap_text(value, body_font, value_width, max_lines)
        rendered.append((label, wrapped))
        body_height += len(wrapped) * line_height + font_size * 0.20

    title_font = _watermark_font(header_font, bold=True)
    title_lines = _wrap_text(title, title_font, inner_width - font_size * 2.12 - font_size * 0.55, 2)
    header_height = pad * 1.65 + len(title_lines) * header_font * 1.08 + company_font * 1.28

    lat = fields.get("latitude")
    lng = fields.get("longitude")
    accuracy = fields.get("accuracy")
    coords = ""
    try:
        if lat is not None and lng is not None and float(lat) and float(lng):
            coords = f"{float(lat):.5f}, {float(lng):.5f}"
            if accuracy is not None:
                coords += f"  ·  ±{round(float(accuracy))}m"
    except (TypeError, ValueError):
        coords = ""
    footer_lines = []
    if coords:
        footer_lines.append(coords)
    footer_lines.append("现场拍摄 · 设备时间" if fields.get("source") == "camera"
                        else "系统相机 / 选图 · 本次记录时间")

    card_height = header_height + pad * 1.5 + body_height + footer_font * 2.75
    after_repair = fields.get("photo_type") == "equipment" and fields.get("repair_phase") == "after"
    header_color = (27, 61, 110, 230) if after_repair else (9, 83, 73, 225)

    # Draw the whole card in logical coordinates on its own layer, then scale it
    # once — matches the phone-side translate/scale and keeps text in proportion.
    card = Image.new("RGBA", (max(1, int(card_width + 0.5)), max(1, int(card_height + 0.5))), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    radius = font_size * 0.4
    draw.rounded_rectangle((0, 0, card_width, card_height), radius=radius, fill=(255, 255, 255, 214))
    draw.rectangle((0, 0, card_width, header_height), fill=header_color)
    draw.rectangle((0, header_height - font_size * 0.13, card_width, header_height),
                   fill=(141, 198, 63, 230))

    title_x = pad
    for index, line in enumerate(title_lines):
        draw.text((title_x, pad + index * header_font * 1.08), line,
                  fill=(255, 255, 255, 255), font=title_font)
    draw.text((title_x, pad + len(title_lines) * header_font * 1.08 + company_font * 0.10),
              "Prasinos Power", fill=(225, 242, 236, 255), font=_watermark_font(company_font))

    row_y = header_height + pad * 0.75
    for label, wrapped in rendered:
        draw.text((pad, row_y + font_size * 0.08), label,
                  fill=(83, 100, 94, 255), font=label_font)
        for index, line in enumerate(wrapped):
            draw.text((pad + label_width, row_y + index * line_height), line,
                      fill=(20, 46, 40, 255), font=body_font)
        row_y += len(wrapped) * line_height + font_size * 0.20

    draw.rectangle((pad, row_y, card_width - pad, row_y + max(1, font_size * 0.035)),
                   fill=(15, 118, 110, 46))
    footer_y = row_y + font_size * 0.4
    for line in footer_lines:
        draw.text((pad, footer_y), line, fill=(88, 107, 99, 255), font=_watermark_font(footer_font))
        footer_y += footer_font * 1.4

    scale = min(1, height * 0.43 / card_height)
    margin = max(6, min(width, height) * 0.018)
    x = margin
    y = height - margin - card_height * scale
    if scale < 1:
        card = card.resize((max(1, int(card_width * scale)), max(1, int(card_height * scale))),
                           Image.LANCZOS)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay.paste(card, (int(x), int(y)), card)
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()
