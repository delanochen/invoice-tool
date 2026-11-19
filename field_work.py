"""Field photo PWA endpoints; keeps the existing application permission model."""
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Response, abort, g, jsonify, render_template, request, send_file, session, url_for
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from werkzeug.exceptions import NotFound
from werkzeug.utils import secure_filename
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from image_processing import compress_image, create_thumbnail
from photo_capture_date import resolve_capture_date
from invoice_tool.service_reports.field_capture import (
    earliest_watermark, fields_for_watermark, position_token, read_container_image, shifted_watermark,
    should_server_watermark, stamp_group_watermark, validate_container_llm, validate_position_llm,
)
from invoice_tool.service_reports.field_repairs import (
    FAULT_TEXT_LIMIT, controller_fault_text, decode_ocr_output, machine_number_token,
    nameplate_model, nameplate_number_groups, nameplate_numbers,
)

logger = logging.getLogger("invoice.field_work")
NAMEPLATE_LLM_TIMEOUT = 15
NAMEPLATE_DEBUG_KEEP = 30
NAMEPLATE_DEBUG_TTL_SECONDS = 48 * 60 * 60
_NAMEPLATE_PROMPT = (
    "You read a Tongfei / Sanhe Tongfei Refrigeration equipment nameplate. "
    "Return only a JSON object with keys machine_number and equipment_model. "
    "machine_number is the Machine Number / 机器编号: exactly 13 letters or digits, copied from that field. Letters may appear among the digits. "
    "equipment_model is the Machine Type / Model / 型号, for example LCI-400CR-01AZ-3487U. "
    "Copy the engraved characters. Do not explain."
)

def json_utf8(payload, status=200):
    """JSON body with real UTF-8 characters so Simplified Chinese is not escaped."""
    return Response(
        json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8'),
        status=status,
        content_type='application/json; charset=utf-8',
    )


def _run_tesseract(payload, psm, language, *, chinese=False, extra=None):
    command = ['tesseract', 'stdin', 'stdout']
    if chinese:
        # LSTM only. The Debian chi_sim package does not ship a legacy model.
        command.extend(['--oem', '1'])
    command.extend(['--psm', psm, '-l', language])
    if chinese:
        command.extend(['-c', 'preserve_interword_spaces=1'])
    if extra:
        command.extend(extra)
    return subprocess.run(
        command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=25 if chinese else 20, check=False,
    )


def _chinese_language_missing(result):
    if result.returncode == 0:
        return False
    stderr = (result.stderr or b'').decode('utf-8', errors='replace').casefold()
    return 'chi_sim' in stderr or 'failed loading language' in stderr or 'error opening data file' in stderr


def _jpeg_bytes(image, quality=90):
    buffer = BytesIO()
    image.convert('RGB').save(buffer, format='JPEG', quality=quality)
    return buffer.getvalue()


def _png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def _upscale_nameplate(image, min_long=2400):
    long_side = max(image.size)
    if long_side >= min_long:
        return image
    scale = min(3.0, min_long / long_side)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)


def _bright_plate_box(image):
    """Bounding box of the bright metal plate, or None when it is not obvious."""
    small = ImageOps.grayscale(image)
    small.thumbnail((240, 240))
    pixels = small.tobytes()
    width, height = small.size
    if width < 8 or height < 8:
        return None
    bright = sum(1 for value in pixels if value >= 170)
    if bright < width * height * 0.12 or bright > width * height * 0.92:
        return None
    min_x, min_y, max_x, max_y = width, height, 0, 0
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        for x, value in enumerate(row):
            if value < 170:
                continue
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
    if max_x <= min_x or max_y <= min_y:
        return None
    scale_x = image.width / width
    scale_y = image.height / height
    pad_x = int((max_x - min_x) * scale_x * 0.04) + 4
    pad_y = int((max_y - min_y) * scale_y * 0.04) + 4
    return (
        max(0, int(min_x * scale_x) - pad_x),
        max(0, int(min_y * scale_y) - pad_y),
        min(image.width, int((max_x + 1) * scale_x) + pad_x),
        min(image.height, int((max_y + 1) * scale_y) + pad_y),
    )


def _deskew_score(gray):
    raw = gray.tobytes()
    width, height = gray.size
    score = 0
    previous = None
    for y in range(0, height, 2):
        row = raw[y * width:(y + 1) * width:6]
        mean = sum(row) / max(1, len(row))
        if previous is not None:
            score += abs(mean - previous)
        previous = mean
    return score


def _deskew_plate(image):
    probe = ImageOps.grayscale(image)
    probe.thumbnail((420, 420))
    best_angle, best_score = 0, _deskew_score(probe)
    for angle in range(-6, 7, 2):
        if angle == 0:
            continue
        score = _deskew_score(probe.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255))
        if score > best_score:
            best_angle, best_score = angle, score
    if best_angle == 0:
        return image
    return image.rotate(best_angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255))


def _nameplate_views(image):
    prepared = _deskew_plate(_upscale_nameplate(image))
    views = [prepared]
    box = _bright_plate_box(prepared)
    if box:
        cropped = prepared.crop(box)
        if cropped.width >= 200 and cropped.height >= 200:
            views.append(_upscale_nameplate(cropped))
    return views


def _vote(values):
    counts = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda item: (counts[item], len(item)))


def _passes_for(image):
    gray = ImageEnhance.Contrast(ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)).enhance(1.25)
    passes = [('11', _jpeg_bytes(image)), ('4', _png_bytes(gray))]
    box = _bright_plate_box(image)
    if box:
        cropped = image.crop(box)
        if 200 <= cropped.width < image.width - 8 and cropped.height >= 200:
            passes.append(('6', _png_bytes(ImageOps.autocontrast(ImageOps.grayscale(cropped), cutoff=1))))
    if max(image.size) < 2000:
        passes.append(('11', _jpeg_bytes(_upscale_nameplate(image))))
    return passes


def _number_ready(labeled, others):
    """A label hit, or a value with at most one letter, is enough to stop.

    Dimension and refrigerant lines can be 13 characters with several letters.
    Those stay in the list but do not hide the Machine Number.
    """
    if labeled:
        return True
    return any(sum(character.isdigit() for character in token) >= 12 for token in others)


def recognize_nameplate_photo(content):
    """OCR an upright nameplate. EXIF is applied before any pass.

    The model line is cropped from the plate, enlarged, and read with a
    character whitelist. Returns None when Tesseract cannot read the image.
    """
    image = load_upright_nameplate(content)
    line_text, word_text = _ocr_model_line(image)
    texts = [part for part in (line_text, word_text) if part]
    models = []
    shaped = nameplate_model(line_text) or nameplate_model('Machine Type ' + line_text)
    if shaped:
        models.append(shaped)
    labeled, others = nameplate_number_groups(line_text + '\n' + word_text)
    if not models or not _number_ready(labeled, others):
        for psm, payload in _passes_for(image):
            try:
                result = _run_tesseract(payload, psm, 'eng')
            except subprocess.TimeoutExpired:
                continue
            if result.returncode != 0:
                continue
            text = decode_ocr_output(result.stdout)
            texts.append(text)
            model = nameplate_model(text)
            if model:
                models.append(model)
            more_labeled, more_others = nameplate_number_groups(text)
            labeled.extend(token for token in more_labeled if token not in labeled)
            others.extend(token for token in more_others if token not in labeled and token not in others)
            if models and _number_ready(labeled, others):
                break
    if not texts:
        return None
    others.sort(key=lambda token: -sum(character.isdigit() for character in token))
    ordered = []
    for value in labeled + others:
        if value and value not in ordered:
            ordered.append(value)
    return {
        'text': '\n'.join(texts),
        'candidates': ordered[:5],
        'equipment_model': _vote([
            model for model in models
            if re.fullmatch(r'[A-Z]{3}-\d{3}[A-Z]{2}-\d{2}[A-Z]{2}-\d{4}[A-Z]', model or '')
        ] or models),
    }


def validate_nameplate_llm(payload):
    """Accept a vision model's JSON only when both fields match the plate rules."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    number = machine_number_token(str(payload.get("machine_number") or ""))
    raw_model = str(payload.get("equipment_model") or "").strip()
    model = nameplate_model("Machine Type " + raw_model) or nameplate_model(raw_model)
    if not number:
        return None
    if not re.fullmatch(r"[A-Z]{3}-\d{3}[A-Z]{2}-\d{2}[A-Z]{2}-\d{4}[A-Z]", model or ""):
        return None
    return {'candidates': [number], 'equipment_model': model, 'source': 'llm', 'text': raw_model}


def load_upright_nameplate(content):
    """Rotate using EXIF before any recognition or debug save. iPhone frames are Orientation=6."""
    with Image.open(BytesIO(content)) as image:
        if image.format not in {'JPEG', 'PNG', 'WEBP', 'HEIF', 'HEIC', 'AVIF'} or image.width * image.height > 40_000_000:
            raise ValueError('bad nameplate image')
        image.load()
        image = ImageOps.exif_transpose(image)
        upright = image.copy()
    return upright


def llm_nameplate_jpeg(image):
    """Small JPEG for the vision model. The upright original stays available for OCR."""
    preview = image.copy()
    preview.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    return _jpeg_bytes(preview, quality=85)


def _tsv_words(image):
    result = _run_tesseract(_jpeg_bytes(image, quality=90), '6', 'eng', extra=['tsv'])
    if result.returncode != 0:
        return []
    words = []
    for line in decode_ocr_output(result.stdout).splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) < 12 or not parts[11].strip():
            continue
        text = parts[11].strip()
        try:
            box = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
        except ValueError:
            continue
        try:
            conf = float(parts[10])
        except ValueError:
            conf = 0.0
        words.append((text, conf, *box))
    return words


def _model_word_score(text):
    """Prefer a Machine Type token over the hotline printed lower on the plate."""
    if re.search(r'hot\s*line', text or '', re.I):
        return 0
    norm = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2212]', '-', text or '')
    letters = len(re.findall(r'[A-Za-z]', norm))
    figures = len(re.findall(r'\d', norm))
    if '-' not in norm or letters < 3 or figures < 3:
        return 0
    score = letters * 10 + figures
    if nameplate_model(norm):
        score += 500
    return score


def _ocr_model_line(image):
    """Read the light model line near the top, then the rest of the plate for the number.

    A small thumbnail misses this engraving. The top band is enlarged before Tesseract.
    """
    line_text = ''
    band = image.crop((0, 0, image.width, max(1, int(image.height * 0.28))))
    band = band.resize((band.width * 2, band.height * 2), Image.Resampling.LANCZOS)
    try:
        band_words = _tsv_words(band)
    except subprocess.TimeoutExpired:
        band_words = []
    best = 0
    for text, *_box in band_words:
        score = _model_word_score(text)
        if score > best:
            best = score
            norm = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2212]', '-', text)
            line_text = nameplate_model(norm) or norm
    probe = image.copy()
    probe.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    try:
        words = _tsv_words(probe)
    except subprocess.TimeoutExpired:
        words = []
    if not nameplate_model(line_text):
        scale_x = image.width / probe.width
        scale_y = image.height / probe.height
        target = None
        ranked = 0
        for text, _conf, left, top, width, height in words:
            score = _model_word_score(text)
            if score > ranked:
                ranked = score
                target = (left, top, width, height)
        if target:
            left, top, width, height = target
            x0 = max(0, int((left - 24) * scale_x))
            y0 = max(0, int((top - 18) * scale_y))
            x1 = min(image.width, int((left + width + 160) * scale_x))
            y1 = min(image.height, int((top + height + 18) * scale_y))
            if x1 - x0 > 40 and y1 - y0 > 16:
                strip = ImageOps.autocontrast(ImageOps.grayscale(image.crop((x0, y0, x1, y1))))
                strip = strip.resize((strip.width * 3, strip.height * 3), Image.Resampling.LANCZOS)
                strip = ImageEnhance.Contrast(strip).enhance(1.5).filter(ImageFilter.SHARPEN)
                try:
                    line = _run_tesseract(
                        _png_bytes(strip), '7', 'eng',
                        extra=['-c', 'tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'],
                    )
                except subprocess.TimeoutExpired:
                    line = None
                if line is not None and line.returncode == 0:
                    line_text = decode_ocr_output(line.stdout)
    kept = []
    for text, conf, *_box in words:
        digits = re.sub(r'\D', '', text)
        punctuation = re.sub(r'[\d\s"\'“”‘’]', '', text)
        if len(digits) == 13 and not punctuation:
            if conf >= 90:
                kept.append(digits)
            continue
        kept.append(text)
    return line_text, '\n'.join(kept)


_CONTAINER_PROMPT = (
    "The container number is printed vertically on the container door. "
    "Rotate it in your reading if needed. Return only JSON with key container_number, "
    "exactly 4 letters, 6 digits and 1 check digit, such as CSQU3054383."
)
_POSITION_PROMPT = (
    "Read the site position or unit number, a short code such as 62-3 or 1A2. "
    "Return only JSON with key position_number."
)
_FIELD_FAULT_PROMPT = (
    "You read a photo of a field service controller screen: a HART communicator, controller HMI or drive panel, "
    "often in a bordered popup with a dimmed background, shot through glass glare with reflections of the phone, "
    "the hand, clothing and nearby equipment. A nameplate sticker may sit beside the screen. "
    "Read only the characters the panel itself displays. Ignore every reflection and object outside the panel, "
    "ignore the sticker, hands, cables, metal, background text, the back arrow, home icon, close X and status bar. "
    "The screen may show a list of active alarms. Each alarm may start with timestamp, severity and a code on one line, "
    "then a pump or component identifier and alarm description on the next. Keep those lines together in a single alarm entry. "
    "Do not merge adjacent entries, even if they have the same timestamp. The fraction at the bottom is page navigation. "
    "If a bordered popup is open, its message is the alarm text. "
    "Copy Simplified Chinese exactly as displayed and keep Latin tokens such as PWM, HART, VFD, MPa, Hz as shown. "
    "Copy leading zeros, letters and punctuation in codes exactly. Never infer a missing character from the equipment model or alarm wording. "
    "If a code is partly obscured, omit it from fault_codes. Preserve readable message lines. "
    "Do not translate, do not reword, do not add commentary. One screen line per array item. "
    "Return only a JSON object with keys: "
    "alarm_entries (array of arrays of verbatim screen lines, one inner array per alarm, in display order; empty when no alarm list), "
    "fault_codes (array of alarm/fault code strings exactly as shown; empty when none), "
    "fault_text (array of the popup or alarm message lines, verbatim), "
    "display_text (array of other readable screen headings or values, verbatim; empty when none). "
    "Use empty arrays when nothing is legible."
)
_SCREEN_CHROME = re.compile(r'(?:page\s*)?\d+\s*/\s*\d+|第?\s*\d+\s*页|[<>«»←→]\s*\d+\s*/\s*\d+', re.I)


_CONTROLLER_CODE = re.compile(r'(?:故障码|报码|alarm\s+code|fault\s+code)\s*[:：]?\s*[A-Z]*\d[A-Z0-9]*(?:[-.]\d+[A-Z0-9]*)*(?![A-Z0-9])', re.I)
_CONTROLLER_DESCRIPTION = re.compile(r'(?:水泵|风机|干磨|过温|过压|欠压|通信|故障|告警|报警|\bpump\b|\bfault\b|\balarm\b)', re.I)


def controller_ocr_candidate(text):
    """Keep complete code/description pairs from an individual OCR pass."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    pairs = []
    for header, description in zip(lines, lines[1:]):
        if not _CONTROLLER_CODE.search(header) or _CONTROLLER_CODE.search(description):
            continue
        if _SCREEN_CHROME.fullmatch(description.strip()) or not _CONTROLLER_DESCRIPTION.search(description):
            continue
        pairs.append((header, description))
    return '\n\n'.join('\n'.join(pair) for pair in dict.fromkeys(pairs))[:FAULT_TEXT_LIMIT]


def validate_fault_llm(payload):
    """Normalize the vision model's controller-screen reading into display text."""
    if not isinstance(payload, dict):
        return ''
    entries = payload.get('alarm_entries')
    if isinstance(entries, list) and entries:
        candidates = [controller_ocr_candidate('\n'.join(line for line in entry if isinstance(line, str)))
                      for entry in entries if isinstance(entry, list)]
        return '\n\n'.join(dict.fromkeys(candidate for candidate in candidates if candidate))[:FAULT_TEXT_LIMIT]
    def lines_of(key):
        raw = payload.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        found = []
        for item in raw:
            text = str(item or '').strip()
            if text and any(ch.isalnum() for ch in text) and not _SCREEN_CHROME.fullmatch(text):
                found.append(text)
        return found
    fault_lines = lines_of('fault_text')
    other_lines = [line for line in lines_of('display_text') if line not in fault_lines]
    # Screen headings (display_text) read best first, then the alarm lines.
    lines = list(dict.fromkeys(other_lines + fault_lines))
    codes = payload.get('fault_codes') or []
    if isinstance(codes, str):
        codes = [codes]
    codes = [str(code).strip() for code in codes if str(code).strip()] if isinstance(codes, list) else []
    joined = '\n'.join(lines)
    missing = [code for code in dict.fromkeys(codes) if code not in joined]
    if missing:
        lines.append('故障码 ' + '、'.join(missing))
    if not any(_CONTROLLER_DESCRIPTION.search(line) and
               not re.fullmatch(r'(?:当前故障|故障列表|故障记录|current faults?|alarms?)', line, re.I)
               for line in fault_lines):
        return ''
    return '\n'.join(lines)[:FAULT_TEXT_LIMIT]


def _first_vision_config(api):
    """Any enabled vision-capable provider, used when a scene is not mapped yet."""
    try:
        row = api['db']().execute(
            "select * from llm_configs where enabled = 1 and supports_vision = 1 "
            "and coalesce(api_key, '') <> '' order by id limit 1"
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {'model': row['model'], 'base_url': (row['base_url'] or '').rstrip('/'), 'api_key': row['api_key']}


def _vision_settings_for_scene(api, scene):
    """Vision provider mapped to a scene; falls back to any enabled vision provider.

    Mapping this scene in System Settings is the admin's explicit opt-in, so the
    daily-report privacy switch is not required here; the nameplate fallback still
    respects it through _vision_settings_for_nameplate.
    """
    cfg = None
    try:
        cfg = api['llm_config'].get_config(api['db'](), scene)
    except Exception:
        cfg = None
    if not (cfg and cfg.get('supports_vision') and cfg.get('api_key') and cfg.get('model')):
        cfg = _first_vision_config(api)
    if not (cfg and cfg.get('supports_vision') and cfg.get('api_key') and cfg.get('model')):
        return None
    return {'model': cfg['model'], 'base_url': cfg.get('base_url') or '', 'api_key': cfg['api_key']}


def recognize_controller_fault(api, content):
    """Vision model first for the controller screen; OCR only when that is unavailable."""
    image = load_upright_nameplate(content)
    preview = llm_nameplate_jpeg(image)
    settings = None
    reason = 'not_configured'
    if api:
        settings = _vision_settings_for_scene(api, 'field_fault') or _vision_settings_for_nameplate(api)
        if settings:
            reason = 'vision_failed'
    if settings:
        try:
            from urllib.request import Request, urlopen
            payload = read_vision_json(preview, settings, urlopen, Request, _FIELD_FAULT_PROMPT,
                                       timeout=20, max_tokens=900)
            text = validate_fault_llm(payload)
            if text:
                return {'fault_text': text, 'source': 'llm'}
            logger.info('controller fault vision returned no usable text')
        except Exception as error:
            logger.info('controller fault vision fallback: %s', type(error).__name__)
    return {'fault_text': '', 'source': 'ocr', 'reason': reason, 'image': image}


def read_vision_json(content, settings, urlopen, request_class, prompt, timeout=NAMEPLATE_LLM_TIMEOUT,
                     max_tokens=300):
    """Ask the configured vision model for a JSON object. Raises on timeout."""
    import base64
    encoded = base64.b64encode(content).decode('ascii')
    body = json.dumps({
        'model': settings['model'],
        'temperature': 0,
        'max_tokens': max_tokens,
        'response_format': {'type': 'json_object'},
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + encoded}},
            ],
        }],
    }).encode('utf-8')
    base = (settings.get('base_url') or 'https://api.deepseek.com').rstrip('/')
    request = request_class(
        base + '/chat/completions',
        data=body,
        headers={'Authorization': 'Bearer ' + settings['api_key'], 'Content-Type': 'application/json'},
        method='POST',
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode('utf-8'))
    message = ((result.get('choices') or [{}])[0].get('message') or {}).get('content')
    try:
        return json.loads(message) if isinstance(message, str) else message
    except (TypeError, ValueError):
        return None


def read_nameplate_llm(content, settings, urlopen, request_class, timeout=NAMEPLATE_LLM_TIMEOUT):
    """Call the configured vision chat endpoint. Raises on timeout or a bad response."""
    import base64
    encoded = base64.b64encode(content).decode('ascii')
    body = json.dumps({
        'model': settings['model'],
        'temperature': 0,
        'max_tokens': 300,
        'response_format': {'type': 'json_object'},
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': _NAMEPLATE_PROMPT},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + encoded}},
            ],
        }],
    }).encode('utf-8')
    base = (settings.get('base_url') or 'https://api.deepseek.com').rstrip('/')
    request = request_class(
        base + '/chat/completions',
        data=body,
        headers={'Authorization': 'Bearer ' + settings['api_key'], 'Content-Type': 'application/json'},
        method='POST',
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode('utf-8'))
    message = ((result.get('choices') or [{}])[0].get('message') or {}).get('content')
    return validate_nameplate_llm(message)


def _vision_settings_for_nameplate(api):
    """Configured vision provider, or None when photos must stay on the OCR path."""
    try:
        settings = api['vision_settings']()
    except Exception:
        return None
    if not settings.get('enabled') or not settings.get('api_key') or not settings.get('model'):
        return None
    try:
        cfg = api['llm_config'].get_config(api['db'](), 'daily_vision')
    except Exception:
        cfg = None
    if cfg is not None and not cfg.get('supports_vision'):
        return None
    if cfg:
        settings = {
            **settings,
            'model': cfg.get('model') or settings.get('model'),
            'base_url': cfg.get('base_url') or settings.get('base_url'),
            'api_key': cfg.get('api_key') or settings.get('api_key'),
        }
    return settings


def _ocr_image_text(image):
    try:
        result = _run_tesseract(_jpeg_bytes(image, quality=85), '6', 'eng')
    except subprocess.TimeoutExpired:
        return ''
    if result.returncode != 0:
        return ''
    return decode_ocr_output(result.stdout)


def recognize_container(api, content):
    """Vision model first. OCR tries the upright photo and both quarter turns."""
    image = load_upright_nameplate(content)
    preview = llm_nameplate_jpeg(image)
    settings = _vision_settings_for_nameplate(api) if api else None
    if settings:
        try:
            from urllib.request import Request, urlopen
            payload = read_vision_json(preview, settings, urlopen, Request, _CONTAINER_PROMPT)
            found = validate_container_llm(payload)
            if found:
                found['source'] = 'llm'
                return found
        except Exception as error:
            logger.info('container vision fallback: %s', type(error).__name__)
    found = read_container_image(image, _ocr_image_text)
    found['source'] = 'ocr'
    return found


def recognize_position(api, content):
    image = load_upright_nameplate(content)
    preview = llm_nameplate_jpeg(image)
    settings = _vision_settings_for_nameplate(api) if api else None
    if settings:
        try:
            from urllib.request import Request, urlopen
            payload = read_vision_json(preview, settings, urlopen, Request, _POSITION_PROMPT)
            found = validate_position_llm(payload)
            if found:
                found['source'] = 'llm'
                return found
        except Exception as error:
            logger.info('position vision fallback: %s', type(error).__name__)
    text = _ocr_image_text(image)
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{0,12}", text or ''):
        token = position_token(raw)
        if token:
            return {'position_number': token, 'source': 'ocr', 'text': text}
    return {'position_number': '', 'source': 'ocr', 'text': text}


def recognize_nameplate(api, content):
    """Vision model first, on a small upright JPEG. OCR runs only if that fails.

    Returns (result, upright_jpeg). The upright bytes are what should be stored.
    """
    image = load_upright_nameplate(content)
    upright = _jpeg_bytes(image, quality=92)
    settings = _vision_settings_for_nameplate(api) if api else None
    if settings:
        try:
            from urllib.request import Request, urlopen
            found = read_nameplate_llm(llm_nameplate_jpeg(image), settings, urlopen, Request)
            if found:
                found['upright'] = upright
                return found
            logger.info('nameplate vision returned an unusable plate reading')
        except Exception as error:
            logger.info('nameplate vision fallback: %s', type(error).__name__)
    found = recognize_nameplate_photo(upright)
    if not found:
        return {'candidates': [], 'equipment_model': '', 'source': 'ocr', 'text': '', 'upright': upright}
    found['source'] = 'ocr'
    found['upright'] = upright
    return found


def _store_nameplate_debug(api, content, found):
    try:
        directory = Path(api['DATA_DIR']) / 'nameplate-ocr-debug'
    except Exception:
        return
    payload = {
        'source': (found or {}).get('source') or '',
        'equipment_model': (found or {}).get('equipment_model') or '',
        'candidates': (found or {}).get('candidates') or [],
        'text': ((found or {}).get('text') or '')[:20000],
    }
    try:
        record_nameplate_debug(directory, content, payload)
    except OSError:
        logger.info('nameplate debug frame was not saved')


def record_nameplate_debug(directory, content, payload, now=None):
    """Keep the last 30 nameplate frames and their OCR/LLM result for 48 hours."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime('%Y%m%dT%H%M%S%fZ')
    (directory / f'{stamp}.jpg').write_bytes(content)
    (directory / f'{stamp}.json').write_text(
        json.dumps({'at': moment.isoformat(), **payload}, ensure_ascii=False),
        encoding='utf-8',
    )
    cutoff = moment.timestamp() - NAMEPLATE_DEBUG_TTL_SECONDS
    files = sorted(directory.glob('*.jpg'))
    for path in files:
        sidecar = path.with_suffix('.json')
        try:
            expired = path.stat().st_mtime < cutoff
        except OSError:
            expired = False
        if expired:
            path.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
    remaining = sorted(directory.glob('*.jpg'))
    for path in remaining[:-NAMEPLATE_DEBUG_KEEP]:
        path.unlink(missing_ok=True)
        path.with_suffix('.json').unlink(missing_ok=True)


PHOTO_TYPE_LABELS = {
    'equipment': '设备',
    'arrival': '进场',
    'departure': '离场',
    'safety': '自检',
    'general': '非设备',
    'legacy': '历史',
}
EDITABLE_PHOTO_TYPES = ('equipment', 'arrival', 'departure', 'safety')



def _repair_merge_key(entry):
    """同工单、同日期、同位置号才有合并资格；没有位置号的行保持原样。"""
    position = (entry['position_number'] or '').strip()
    if not position:
        return None
    return (entry['order_id'], entry['date'], position.casefold())


def _repair_rows_conflict(left, right):
    """铭牌号或柜号一边为空、一边有值时不算冲突；两边都有且不同说明不是同一台设备。"""
    for field in ('equipment_number', 'container_number'):
        value = (left.get(field) or '').strip().casefold()
        other = (right.get(field) or '').strip().casefold()
        if value and other and value != other:
            return True
    return False


def _merge_repair_entry(target, source):
    """把 source 会话并进 target：计数相加、编号去重，空缺栏位用 source 的值补齐。"""
    target['photo_count'] += source['photo_count']
    target['photo_ids'].extend(source['photo_ids'])
    for fuse in source['pump_fuses']:
        if fuse.casefold() not in {item.casefold() for item in target['pump_fuses']}:
            target['pump_fuses'].append(fuse)
    for note in source['notes']:
        if note not in target['notes']:
            target['notes'].append(note)
    for technician in source['technicians']:
        if technician not in target['technicians']:
            target['technicians'].append(technician)
    for field in ('equipment_number', 'container_number'):
        if not (target[field] or '').strip() and (source[field] or '').strip():
            target[field] = source[field].strip()
    for field in ('equipment_model', 'work_content'):
        values = []
        for value in (target.get(field) or '', source.get(field) or ''):
            value = value.strip()
            if value and value.casefold() not in {item.casefold() for item in values}:
                values.append(value)
        target[field] = ' / '.join(values)


def merge_repair_rows_by_position(entries):
    """同工单、同日期、同位置号的维修会话合成一行；铭牌号/柜号冲突的保持分开。"""
    rows = []
    slots = {}
    for entry in entries:
        key = _repair_merge_key(entry)
        bucket = slots.setdefault(key, []) if key else None
        if bucket is None:
            rows.append(entry)
            continue
        for existing in bucket:
            if not _repair_rows_conflict(existing, entry):
                _merge_repair_entry(existing, entry)
                break
        else:
            bucket.append(entry)
            rows.append(entry)
    return rows


def group_repair_photos(photos):
    """把设备类现场照片归并成「设备维修清单」行（与 /reports/field-repairs 同口径）。

    每台设备的一次拍摄会话（equipment_session）先归为一行：照片数量相加，
    水泵保险编号合并去重，备注用 ' / ' 连接；同一会话里先拍的铭牌照片没填
    位置号/柜号时，用同会话后面补拍的照片补齐。
    再按「同工单 + 同日期 + 同位置号」合并一次：同一位置号下的多个维修会话
    （维修前 / 维修后、重新拍铭牌、补拍柜号等）合成一行，空缺栏位用同位置的
    其它行补齐；铭牌号或柜号互相冲突时说明不是同一台设备，保持分开。
    没有会话号的老照片按铭牌号 + 位置号 + 柜号 + 维修人员归行。
    """
    grouped = {}
    for photo in photos:
        photo = dict(photo)
        technician = (photo['technician_name'] or photo['employee_name'] or '').strip()
        equipment_number = (photo['equipment_number'] or '').strip()
        position_number = (photo['position_number'] or '').strip()
        container_number = (photo['container_number'] or '').strip()
        repair_phase = photo.get('repair_phase')
        session = (photo['equipment_session'] or '').strip()
        if session:
            group_key = ('session', photo['order_id'], session, photo['capture_date'])
        elif equipment_number:
            group_key = (
                'device', photo['order_id'], photo['capture_date'], equipment_number.casefold(),
                position_number.casefold(), container_number.casefold(), technician.casefold(),
            )
        else:
            group_key = ('photo', photo['order_id'], photo['id'])
        entry = grouped.setdefault(group_key, dict(
            order_number=photo['order_number'], date=photo['capture_date'], position_number=position_number,
            container_number=container_number, equipment_number=equipment_number,
            equipment_model=photo.get('equipment_model') or '', work_content=photo.get('work_content') or '', pump_fuses=[],
            notes=[], technicians=[], photo_count=0, photo_ids=[], order_id=photo['order_id'],
        ))
        # 同一会话内先拍铭牌、后补位置号/柜号/型号时，用后面的值补齐空栏位。
        for field, value in (('position_number', position_number), ('container_number', container_number),
                             ('equipment_number', equipment_number),
                             ('equipment_model', photo.get('equipment_model') or ''),
                             ('work_content', photo.get('work_content') or '')):
            if not (entry.get(field) or '').strip() and value.strip():
                entry[field] = value.strip()
        if repair_phase and photo.get('completed_date'):
            entry['date'] = str(photo['completed_date'])
        entry['photo_count'] += 1
        entry['photo_ids'].append(photo['id'])
        for fuse in re.split(r'[/,，;；\s]+', (photo['pump_fuse_numbers'] or '').strip()):
            if fuse and fuse.casefold() not in {item.casefold() for item in entry['pump_fuses']}:
                entry['pump_fuses'].append(fuse)
        if photo['note'] and photo['note'] not in entry['notes']:
            entry['notes'].append(photo['note'])
        if technician and technician not in entry['technicians']:
            entry['technicians'].append(technician)
    rows = []
    for index, entry in enumerate(sorted(merge_repair_rows_by_position(grouped.values()), key=lambda item: (item['date'], item['order_number'], item['position_number'])), 1):
        entry['sequence'] = index
        pump_fuses = entry.pop('pump_fuses')
        pump_fuses.sort(key=lambda value: (0, int(value)) if value.isdigit() else (1, value.casefold()))
        entry['pump_fuse_numbers'] = '/'.join(pump_fuses)
        entry['note'] = ' / '.join(entry.pop('notes'))
        entry['technician'] = ' / '.join(entry.pop('technicians'))
        rows.append(entry)
    return rows


def repair_work_text(rows):
    """把设备维修清单行拼成日报工作内容文本：一台设备一行，位置号 + 铭牌号 + 备注。"""
    lines = []
    for row in rows:
        parts = []
        if row.get('position_number'):
            parts.append('位置号 ' + row['position_number'])
        if row.get('equipment_number'):
            parts.append('铭牌号 ' + row['equipment_number'])
        if row.get('work_content'):
            parts.append(row['work_content'])
        if row.get('note'):
            parts.append('备注 ' + row['note'])
        if parts:
            lines.append('，'.join(parts))
    return '\n'.join(lines)


def field_photo_filename(folder, position, stamp, fallback):
    try:
        if not re.fullmatch(r'[0-9]{8}_[0-9]{6}', stamp):
            return fallback
        datetime.strptime(stamp, '%Y%m%d_%H%M%S')
    except ValueError:
        return fallback
    position = re.sub(r'[^A-Za-z0-9_-]+', '_', position).strip('_')[:100]
    stem = f'{position}_{stamp}' if position else stamp
    filename = f'{stem}.jpg'
    suffix = 2
    while (folder / filename).exists():
        filename = f'{stem}_{suffix}.jpg'
        suffix += 1
    return filename


def photo_exif_filename_time(content):
    try:
        with Image.open(BytesIO(content)) as image:
            metadata = image.getexif()
            exif = metadata.get_ifd(0x8769)
            for value in (exif.get(0x9003), exif.get(0x9004), metadata.get(0x0132)):
                if isinstance(value, str):
                    try:
                        return datetime.strptime(value.strip('\x00 '), '%Y:%m:%d %H:%M:%S').strftime('%Y%m%d_%H%M%S')
                    except ValueError:
                        continue
    except (OSError, ValueError, TypeError, SyntaxError, Image.DecompressionBombError):
        pass
    return ''


def register_field_routes(app, api):
    @app.after_request
    def field_response_version(response):
        if request.path.startswith('/api/field/'):
            response.headers['X-Field-Version'] = api['APP_VERSION']
            response.headers['Cache-Control'] = 'no-store, private'
        return response

    def access(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                return jsonify(error='请联网登录员工账号后继续。'), 401
            if not api['has_menu_permission']('field_work') or not api['has_action_permission']('service_orders', 'view'):
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def token():
        if 'field_token' not in session:
            session['field_token'] = api['secrets'].token_urlsafe(32)
        return session['field_token']

    def check_write():
        if not api['secrets'].compare_digest(request.headers.get('X-Field-Token', ''), session.get('field_token') or '!'):
            abort(403)

    def order_rows():
        clauses, params = api['service_order_access_filters']()
        clauses.append("service_orders.status != 'closed'")
        clauses.append('''not exists (select 1 from customer_reimbursements
            where customer_reimbursements.service_order_id = service_orders.id)''')
        return api['db']().execute('''
            select service_orders.id, service_orders.order_number, service_orders.client_name,
                   service_orders.site_address, buyers.latitude, buyers.longitude,
                   clients.name as customer_name
            from service_orders left join buyers on buyers.id = service_orders.buyer_id
            left join clients on clients.id = service_orders.client_id
            where ''' + ' and '.join(clauses) + ' order by service_orders.id desc', params).fetchall()

    def photo_clauses():
        return [], []

    def photo_rows():
        clauses, params = photo_clauses()
        order_number = request.args.get('order_number', '').strip()
        if order_number:
            clauses.append('instr(lower(service_orders.order_number), lower(?)) > 0')
            params.append(order_number)
        technician = request.args.get('technician', '').strip()
        if technician:
            clauses.append("instr(lower(coalesce(nullif(trim(p.technician_name), ''), users.name)), lower(?)) > 0")
            params.append(technician)
        for key in ('equipment_number', 'position_number', 'container_number'):
            value = request.args.get(key, '').strip()
            if value:
                clauses.append(f'instr(lower(p.{key}), lower(?)) > 0')
                params.append(value)
        site = request.args.get('site', '').strip()
        if site:
            clauses.append('''(instr(lower(service_orders.client_name), lower(?)) > 0
                               or instr(lower(coalesce(service_orders.site_address, '')), lower(?)) > 0)''')
            params.extend([site, site])
        photo_type = request.args.get('photo_type', '').strip()
        if photo_type:
            clauses.append('p.photo_type = ?')
            params.append(photo_type)
        for key, column in [('order_id', 'p.order_id'), ('user_id', 'p.user_id'),
                            ('date_from', 'p.capture_date'), ('date_to', 'p.capture_date')]:
            value = request.args.get(key, '').strip()
            if value:
                op = '>=' if key == 'date_from' else '<=' if key == 'date_to' else '='
                clauses.append(f'{column} {op} ?')
                params.append(value)
        q = request.args.get('q', '').strip()
        if q:
            clauses.append('''(service_orders.order_number like ? or service_orders.client_name like ? or users.name like ?
                              or p.technician_name like ? or p.equipment_number like ? or p.position_number like ?
                              or p.container_number like ? or p.pump_fuse_numbers like ? or p.note like ? or clients.name like ?)''')
            params.extend(['%' + q + '%'] * 10)
        return api['db']().execute('''
            select p.*, r.equipment_model, r.work_content, r.completed_date, service_orders.order_number, service_orders.client_name as site_name, service_orders.site_address,
                   users.name as employee_name, clients.name as customer_name
            from field_photos p join service_orders on service_orders.id = p.order_id
            left join field_repairs r on r.id = p.equipment_session and r.order_id = p.order_id
            join users on users.id = p.user_id
            left join clients on clients.id = service_orders.client_id
            where ''' + (' and '.join(clauses) or '1=1') + ' order by p.captured_at desc, p.id desc limit 2001', params).fetchall()

    def photo_order_rows():
        return api['db']().execute('''
            select distinct service_orders.id, service_orders.order_number, service_orders.client_name
            from field_photos p join service_orders on service_orders.id = p.order_id
            order by service_orders.order_number desc''').fetchall()

    def photo_file(row, thumb=False):
        root = api['shared_photos_root']()
        relative = Path(row['relative_path'])
        if thumb:
            relative = Path(relative.parts[0], 'thumbnails', *relative.parts[2:])
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            abort(404)
        return target

    @app.get('/field/')
    def field_work():
        return render_template('field_work.html')

    @app.get('/field/manifest.webmanifest')
    def field_manifest():
        return jsonify(name='Prasinos Power 现场工作', short_name='Prasinos', id='/field/',
                       start_url='/field/', scope='/field/', display='standalone',
                       background_color='#f3f7f6', theme_color='#0f766e',
                       icons=[dict(src=f'/static/field-icon-{size}.png', sizes=f'{size}x{size}', type='image/png') for size in (192, 512)])

    @app.get('/field/sw.js')
    def field_service_worker():
        response = app.send_static_file('field-sw.js')
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.get('/api/field/session')
    @access
    def field_session():
        technicians = api['db']().execute('''
            select id, name from users
            where is_active = 1 and role in ('admin', 'manager', 'finance', 'employee', 'internal', 'user', 'external_employee')
            order by name collate nocase
        ''').fetchall()
        return jsonify(user=dict(id=g.user['id'], name=g.user['name']), csrf=token(),
                       technicians=[dict(row) for row in technicians],
                       orders=[dict(row) for row in order_rows()], version=api['APP_VERSION'],
                       ledger_orders=[dict(row) for row in photo_order_rows()],
                       can_capture=api['has_action_permission']('service_reports', 'create'),
                       create_order_url=url_for('new_service_order') if api['can_create_service_order']() else None,
                       distance_limit=max(100, min(10000, int(os.environ.get('FIELD_DISTANCE_METERS', '500')))))

    @app.post('/api/field/verify-watermark-password')
    @access
    def verify_watermark_password():
        check_write()
        # Verify against the password configured in 系统设置 (company settings),
        # never a hardcoded constant. An empty configured value is always invalid.
        configured = api['get_setting']('field_watermark_time_password', '')
        supplied = request.get_json(silent=True) or {}
        valid = bool(configured) and api['secrets'].compare_digest(str(supplied.get('password', '')), configured)
        return jsonify(ok=valid), (200 if valid else 403)

    @app.post('/api/field/photos')
    @access
    def upload_field_photo():
        check_write()
        if not api['has_action_permission']('service_reports', 'create'):
            abort(403)
        if not request.form or 'photo' not in request.files:
            app.logger.warning(
                'field_upload_parse_failed mime=%s length=%s transfer=%s terminated=%s fields=%d files=%d',
                request.mimetype, request.content_length,
                request.environ.get('HTTP_TRANSFER_ENCODING', ''),
                request.environ.get('wsgi.input_terminated', False),
                len(request.form), len(request.files))
            return jsonify(error='上传数据未完整解析，照片仍保留在本机。请刷新页面后重试。',
                           code='upload_body_invalid'), 400
        raw_key = request.form.get('client_id', '')[:500]
        source = request.form.get('source', '')
        watermark_source = request.form.get('watermark_source', 'system').strip()
        watermark_opt_out = request.form.get('watermark_opt_out', '').strip().lower() == 'true'
        if watermark_opt_out and source == 'camera':
            return jsonify(error='现场拍摄的照片必须加水印。'), 422
        if watermark_opt_out:
            watermark_source = 'original'
        if source not in {'camera', 'file'} and watermark_source == 'original':
            source = 'file'
        if source not in {'camera', 'file'}:
            app.logger.warning('field_upload_source_invalid source_present=%s watermark_present=%s fields=%d',
                               'source' in request.form, 'watermark_source' in request.form, len(request.form))
            return jsonify(error='照片来源无效。', code='upload_source_invalid'), 422
        if watermark_source not in {'system', 'original'} or (watermark_source == 'original' and source != 'file'):
            return jsonify(error='水印来源无效。'), 422
        original_import = source == 'file' and watermark_source == 'original' and not watermark_opt_out
        location_verified = False if original_import else request.form.get('location_verified', 'true') == 'true'
        try:
            order_id = int(request.form.get('order_id', ''))
        except (ValueError, TypeError):
            return jsonify(error='工单无效。'), 422
        order = api['require_service_order'](order_id)
        try:
            captured = datetime.fromisoformat(request.form.get('captured_at', '').replace('Z', '+00:00'))
            if captured.tzinfo is None or captured.year < 2000 or captured > datetime.now(timezone.utc) + timedelta(minutes=10):
                raise ValueError
            tz_name = request.form.get('timezone_name', 'UTC')
            local = captured.astimezone(ZoneInfo(tz_name))
            watermark = datetime.fromisoformat(request.form.get('watermark_at', request.form.get('captured_at', '')).replace('Z', '+00:00'))
            if watermark.tzinfo is None or watermark.year < 1900 or watermark.year > 2200:
                raise ValueError
            lat, lng, accuracy = (float(request.form.get(k, '')) for k in ('latitude', 'longitude', 'accuracy'))
            if not all(math.isfinite(v) for v in (lat, lng, accuracy)) or not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy <= 100000):
                raise ValueError
        except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
            if not original_import:
                return jsonify(error='拍摄时间、时区或定位数据不正确。'), 422
            captured = datetime.now(timezone.utc)
            watermark = captured
            tz_name = 'UTC'
            local = captured
            lat, lng, accuracy = 0.0, 0.0, 100000.0
        if not location_verified and source != 'file':
            # 选图上传（保留原图水印或生成水印）均不检查位置；拍照仍必须通过位置检查。
            return jsonify(error='只有选图上传可以跳过位置检查。'), 422
        note = request.form.get('note', '').strip()[:1000]
        has_device_metadata = any(key in request.form for key in ('equipment_number', 'position_number', 'container_number', 'pump_fuse_numbers',
                                                                   'equipment_session', 'no_equipment_number'))
        equipment_number = request.form.get('equipment_number', '').strip()[:200]
        position_number = request.form.get('position_number', '').strip().upper()[:200]
        container_number = request.form.get('container_number', '').strip().upper()[:200]
        pump_fuse_numbers = request.form.get('pump_fuse_numbers', '').strip()[:200]
        equipment_session = request.form.get('equipment_session', '').strip()[:64]
        repair_phase = request.form.get('repair_phase', '').strip()
        photo_type = request.form.get('photo_type', 'legacy').strip()
        if repair_phase:
            if photo_type != 'equipment' or repair_phase not in {'before', 'after'}:
                return jsonify(error='维修拍摄阶段无效。'), 422
            repair = api['db']().execute(
                'select * from field_repairs where id = ? and order_id = ? and user_id = ?',
                (equipment_session, order_id, g.user['id']),
            ).fetchone()
            if not repair:
                return jsonify(error='找不到对应维修记录。'), 404
            equipment_number, position_number, container_number = (
                repair['equipment_number'], repair['position_number'], repair['container_number'],
            )
        watermark_at = watermark.isoformat()
        batch_id = request.form.get('batch_id', '').strip()[:64]
        if photo_type not in {'equipment', 'general', 'legacy', 'arrival', 'departure', 'safety'}:
            return jsonify(error='照片类型无效。'), 422
        no_equipment_number = request.form.get('no_equipment_number') == 'true'
        if has_device_metadata and not equipment_session:
            equipment_session = batch_id or 'group-pending'
        if has_device_metadata and not equipment_number and not no_equipment_number:
            no_equipment_number = True
        if not has_device_metadata:
            equipment_session = 'legacy-pending-photo'
        technician_user_id = request.form.get('technician_user_id', str(g.user['id'])).strip()
        technician = None
        if technician_user_id.isdigit():
            technician = api['db']().execute('''
                select id, name from users where id = ? and is_active = 1
                and role in ('admin', 'manager', 'finance', 'employee', 'internal', 'user', 'external_employee')
            ''', (int(technician_user_id),)).fetchone()
        if not technician:
            return jsonify(error='请选择有效的施工员。'), 422
        technician_name = technician['name']
        location_note = request.form.get('location_note', '').strip()[:500]
        photo = request.files.get('photo')
        content = photo.read(40 * 1024 * 1024 + 1) if photo else b''
        if not content or len(content) > 40 * 1024 * 1024:
            return jsonify(error='照片为空或超过 40MB。'), 422
        digest = hashlib.sha256(content).hexdigest()
        if re.fullmatch(r'[0-9a-f]{32}', raw_key):
            key = raw_key
        else:
            identity = '|'.join((str(g.user['id']), raw_key, str(order_id), captured.isoformat(),
                                 batch_id, photo.filename or '', digest))
            key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP', 'HEIF', 'HEIC', 'AVIF'} or image.width * image.height > 40_000_000:
                    raise ValueError
                image.verify()
        except (OSError, ValueError, Image.DecompressionBombError):
            return jsonify(error='无法读取照片，请重新拍摄。'), 422
        db = api['db']()
        capture_date_source = 'camera'
        filename_time = request.form.get('filename_time', '').strip()
        if source == 'camera':
            filename_time = filename_time or local.strftime('%Y%m%d_%H%M%S')
            local = watermark.astimezone(ZoneInfo(tz_name))
            if watermark != captured:
                capture_date_source = 'watermark'
        if source == 'file':
            previous = db.execute('select * from field_photos where user_id = ? and client_id = ?', (g.user['id'], key)).fetchone()
            if previous:
                if previous['content_hash'] != digest or previous['order_id'] != order_id:
                    return jsonify(error='照片编号已用于不同的内容。'), 409
                return jsonify(ok=True, id=previous['id'], duplicate=True, capture_date=previous['capture_date'])
        if source == 'file':
            filename_time = photo_exif_filename_time(content) or filename_time
        if original_import:
            try:
                local, capture_date_source, candidates = resolve_capture_date(content, tz_name, request.form.get('manual_capture_date', ''))
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                return jsonify(error='照片日期识别暂时失败，请重试。'), 503
            except ValueError as error:
                return jsonify(error=str(error)), 422
            if local is None:
                return jsonify(error='请选择照片的拍摄日期。', needs_capture_date=True, date_candidates=candidates), 422
            captured = local
            watermark_at = local.isoformat()
        elif source == 'file':
            # 选图并生成水印：captured_at 为照片 EXIF 原始时间，watermark_at 为调整后的水印时间（由前端绘制到照片上）。
            local = watermark.astimezone(ZoneInfo(tz_name))
            capture_date_source = 'watermark' if watermark != captured else 'exif'
        db.execute('begin immediate')
        try:
            previous = db.execute('select * from field_photos where user_id = ? and client_id = ?', (g.user['id'], key)).fetchone()
            if previous:
                db.rollback()
                if previous['content_hash'] != digest or previous['order_id'] != order_id:
                    return jsonify(error='照片编号已用于不同的内容。'), 409
                return jsonify(ok=True, id=previous['id'], duplicate=True)
            root = api['shared_photos_root']()
            order_folder = secure_filename(order['order_number'])
            fallback = f"{local:%Y%m%d_%H%M%S}-u{g.user['id']}-{key}.jpg"
            folder = Path(order_folder, 'pictures', local.date().isoformat())
            position = position_number if photo_type in {'equipment', 'legacy'} else ''
            filename = field_photo_filename(root / folder, position, filename_time, fallback)
            relative = folder / filename
            target = (root / relative).resolve()
            thumb = (root / order_folder / 'thumbnails' / local.date().isoformat() / filename).resolve()
            if not target.is_relative_to(root) or not thumb.is_relative_to(root):
                abort(403)
            original_relative = ''
            original_upload = request.files.get('original')
            raw_original = original_upload.read(40 * 1024 * 1024 + 1) if original_upload else b''
            if raw_original and len(raw_original) > 40 * 1024 * 1024:
                raw_original = b''
            original_digest = hashlib.sha256(raw_original).hexdigest() if raw_original else ''
            if should_server_watermark(
                request.form.get('awaiting_watermark'),
                digest,
                original_digest,
                request.form.get('client_watermarked'),
                request.form.get('photo_role', ''),
                'true' if watermark_opt_out else '',
            ):
                try:
                    content = stamp_group_watermark(raw_original or content, {
                        'order_number': order['order_number'],
                        'technician_name': technician_name,
                        'when': watermark_at,
                        'photo_type': photo_type,
                        'repair_phase': repair_phase,
                        'equipment_number': equipment_number,
                        'equipment_model': request.form.get('equipment_model', '').strip()[:200],
                        'container_number': container_number,
                        'position_number': position_number,
                        'fault_note': request.form.get('fault_note', '').strip()[:4000],
                        'note': note,
                    })
                except (OSError, ValueError, Image.DecompressionBombError):
                    db.rollback()
                    app.logger.exception('field watermark stamp failed')
                    return jsonify(error='服务器暂时无法添加照片水印，草稿已保留，请重试。'), 503
            compress_image(BytesIO(content), target, max_size=1800, quality=78)
            create_thumbnail(target, thumb)
            if raw_original and len(raw_original) <= 40 * 1024 * 1024:
                original_name = filename.replace('.jpg', '.original.jpg')
                original_rel = Path(order_folder, 'pictures', local.date().isoformat(), original_name)
                original_target = (root / original_rel).resolve()
                if original_target.is_relative_to(root):
                    original_target.parent.mkdir(parents=True, exist_ok=True)
                    original_target.write_bytes(raw_original)
                    original_relative = original_rel.as_posix()
            photo_role = request.form.get('photo_role', '').strip()[:32]
            photo_model = request.form.get('equipment_model', '').strip()[:200]
            fault_note = request.form.get('fault_note', '').strip()[:4000]
            cursor = db.execute('''insert into field_photos
                (client_id, order_id, user_id, captured_at, received_at, capture_date, timezone_name,
                 latitude, longitude, accuracy, location_note, note, source, relative_path, content_hash, bytes,
                 equipment_number, position_number, container_number, pump_fuse_numbers, equipment_session, photo_type, watermark_at,
                 watermark_source, batch_id, technician_user_id, technician_name, location_verified, capture_date_source, repair_phase,
                 original_relative_path, photo_role, photo_model, fault_note)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (key, order_id, g.user['id'], captured.astimezone(timezone.utc).isoformat(), api['now'](),
                 local.date().isoformat(), tz_name, lat, lng, accuracy, location_note, note, source,
                 relative.as_posix(), digest, target.stat().st_size, equipment_number, position_number, container_number, pump_fuse_numbers,
                 equipment_session, photo_type, watermark_at, watermark_source, batch_id, technician['id'], technician_name, int(location_verified), capture_date_source, repair_phase,
                 original_relative, photo_role, photo_model, fault_note))
            api['log_action']('create', 'field_photo', cursor.lastrowid, order['order_number'], 'PWA 工单照片上传')
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify(ok=True, id=cursor.lastrowid, capture_date=local.date().isoformat(), capture_date_source=capture_date_source)

    @app.post('/api/field/photos/rewatermark')
    @access
    def rewatermark_group():
        """Redraw every original in a batch with the confirmed group info."""
        check_write()
        payload = request.get_json(silent=True) or {}
        batch_id = str(payload.get('batch_id') or '').strip()[:64]
        if not batch_id:
            return jsonify(error='缺少照片组。'), 422
        pending = {
            'equipment_number': str(payload.get('equipment_number') or '').strip()[:200],
            'equipment_model': str(payload.get('equipment_model') or '').strip()[:200],
            'container_number': str(payload.get('container_number') or '').strip().upper()[:200],
            'position_number': str(payload.get('position_number') or '').strip().upper()[:200],
            'fault': str(payload.get('fault') or '').strip()[:4000],
        }
        rows = api['db']().execute(
            '''select p.*, service_orders.order_number, service_orders.client_name as site_name
               from field_photos p join service_orders on service_orders.id = p.order_id
               where p.user_id = ? and p.batch_id = ? order by p.captured_at, p.id''',
            (g.user['id'], batch_id),
        ).fetchall()
        if not rows:
            return jsonify(error='找不到这组照片。'), 404
        new_start = str(payload.get('watermark_start') or '').strip()
        originals = [row['watermark_at'] or row['captured_at'] for row in rows]
        earliest = earliest_watermark(originals) if new_start else ''
        root = api['shared_photos_root']()
        updated = 0
        for row, original_when in zip(rows, originals):
            row_fields = dict(row)
            row_fields['equipment_model'] = row_fields.get('photo_model') or ''
            fields = fields_for_watermark(row_fields, pending)
            when = shifted_watermark(original_when, earliest, new_start) if new_start else original_when
            fields.update({
                'order_number': row['order_number'],
                'technician_name': row['technician_name'],
                'when': when,
                'photo_type': row['photo_type'],
                'repair_phase': row['repair_phase'],
                'note': row['note'],
            })
            original_rel = row['original_relative_path'] or row['relative_path']
            source_path = (root / original_rel).resolve()
            target = (root / row['relative_path']).resolve()
            if not source_path.is_relative_to(root) or not target.is_relative_to(root) or not source_path.is_file():
                continue
            stamped = stamp_group_watermark(source_path.read_bytes(), fields)
            target.write_bytes(stamped)
            try:
                relative = Path(row['relative_path'])
                thumb = (root / relative.parts[0] / 'thumbnails' / Path(*relative.parts[2:])).resolve()
                if len(relative.parts) >= 3 and thumb.is_relative_to(root):
                    create_thumbnail(target, thumb)
            except Exception:
                logger.info('group watermark thumbnail skipped for %s', row['id'])
            api['db']().execute(
                '''update field_photos set equipment_number = ?, position_number = ?, container_number = ?,
                   photo_model = ?, fault_note = ?, watermark_at = ? where id = ? and user_id = ?''',
                (fields['equipment_number'], fields['position_number'], fields['container_number'],
                 fields['equipment_model'], fields['fault_note'], when, row['id'], g.user['id']),
            )
            updated += 1
        api['db']().commit()
        return jsonify(ok=True, updated=updated)

    @app.post('/api/field/recognize-equipment')
    @access
    def recognize_equipment():
        check_write()
        mode = request.form.get('mode') or ''
        fault_mode = mode == 'controller_fault'
        subject = {'controller_fault': '手操器屏幕', 'container': '集装箱号', 'position': '位置号'}.get(mode, '铭牌')
        photo = request.files.get('photo')
        content = photo.read(10 * 1024 * 1024 + 1) if photo else b''
        if not content or len(content) > 10 * 1024 * 1024:
            return jsonify(error=f'{subject}照片为空或超过 10MB。'), 422
        try:
            if mode == 'container':
                found = recognize_container(api, content)
                return json_utf8({'ok': True, **{key: found.get(key) for key in ('container_number', 'check_digit_ok', 'candidates', 'source')}})
            if mode == 'position':
                found = recognize_position(api, content)
                return json_utf8({'ok': True, 'position_number': found.get('position_number') or '', 'source': found.get('source') or 'ocr'})
            if not fault_mode:
                found = recognize_nameplate(api, content)
                _store_nameplate_debug(api, found.pop('upright', content), found)
                if not found:
                    return jsonify(error='服务器暂时无法识别铭牌，请手工输入。'), 503
                logger.info(
                    'nameplate recognize source=%s model=%s candidates=%s bytes=%s',
                    found.get('source'), found.get('equipment_model') or '',
                    len(found.get('candidates') or []), len(content),
                )
                return json_utf8({
                    'ok': True,
                    'candidates': found['candidates'],
                    'equipment_model': found['equipment_model'],
                    'source': found.get('source') or 'ocr',
                })
            if fault_mode:
                found = recognize_controller_fault(api, content)
                reason = found.get('reason') or 'vision_failed'
                if found.get('source') == 'llm':
                    logger.info('controller fault recognize source=llm bytes=%s', len(content))
                    return json_utf8({'ok': True, 'fault_text': found['fault_text'], 'source': 'llm'})
                image = found.get('image')
                if image is None:
                    raise ValueError('bad controller screen image')
                enhanced = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
                scale = min(3, max(1, math.ceil(2400 / max(enhanced.width, enhanced.height))))
                if scale > 1:
                    enhanced = enhanced.resize((enhanced.width * scale, enhanced.height * scale), Image.Resampling.LANCZOS)
                enhanced = ImageEnhance.Contrast(enhanced).enhance(1.35).filter(ImageFilter.SHARPEN)
                processed = BytesIO()
                enhanced.save(processed, format='PNG')
                plain = BytesIO()
                ImageOps.grayscale(image).save(plain, format='PNG')
                passes = (('6', plain.getvalue()), ('4', processed.getvalue()))
            else:
                with Image.open(BytesIO(content)) as image:
                    if image.format not in {'JPEG', 'PNG', 'WEBP', 'HEIF', 'HEIC', 'AVIF'} or image.width * image.height > 40_000_000:
                        raise ValueError
                    image.load()
                    image = ImageOps.exif_transpose(image)
                    enhanced = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
                    scale = min(3, max(1, math.ceil(2400 / max(enhanced.width, enhanced.height))))
                    if scale > 1:
                        enhanced = enhanced.resize((enhanced.width * scale, enhanced.height * scale), Image.Resampling.LANCZOS)
                    enhanced = ImageEnhance.Contrast(enhanced).enhance(1.35).filter(ImageFilter.SHARPEN)
                    processed = BytesIO()
                    enhanced.save(processed, format='PNG')
                    plain = BytesIO()
                    ImageOps.grayscale(image).save(plain, format='PNG')
                    passes = (('6', plain.getvalue()), ('4', processed.getvalue()))
            results = [_run_tesseract(payload, psm, 'chi_sim+eng', chinese=True) for psm, payload in passes]
        except (OSError, ValueError, Image.DecompressionBombError):
            return jsonify(error=f'无法读取{subject}照片。'), 422
        except subprocess.TimeoutExpired:
            return jsonify(error=f'{subject}识别超时，请重试或手工输入。'), 504
        if not any(result.returncode == 0 for result in results):
            if any(_chinese_language_missing(result) for result in results):
                return jsonify(error='服务器未安装简体中文识别语言包，暂时无法识别手操器中文。'), 503
            return jsonify(error=f'服务器暂时无法识别{subject}，请手工输入。'), 503
        if fault_mode:
            candidates = [controller_ocr_candidate(decode_ocr_output(result.stdout))
                          for result in results if result.returncode == 0]
            text = max(candidates, key=lambda candidate: len(_CONTROLLER_CODE.findall(candidate)), default='')
            logger.info('controller fault recognize source=ocr reason=%s usable=%s bytes=%s',
                        reason, bool(text), len(content))
            if not text:
                return jsonify(error='未能可靠识别故障码和故障说明。照片已保留，请换个角度重拍或手工填写。'), 422
            return json_utf8({'ok': True, 'fault_text': text, 'source': 'ocr', 'reason': reason})
        text = '\n'.join(decode_ocr_output(result.stdout) for result in results if result.returncode == 0)
        return json_utf8({'ok': True, 'fault_text': controller_fault_text(text)})

    @app.route('/api/field/orders/<int:order_id>/repair-records', methods=['GET', 'POST'])
    @access
    def field_repair_records(order_id):
        order = api['require_service_order'](order_id)
        connection = api['db']()
        user_id = g.user['id']
        if request.method == 'GET':
            rows = connection.execute(
                'select * from field_repairs where order_id = ? and user_id = ? and completed_date is null order by created_at desc',
                (order_id, user_id),
            ).fetchall()
            return json_utf8({'rows': [dict(row) for row in rows]})

        check_write()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error='维修记录格式无效。'), 422
        def value(key, limit):
            return str(data.get(key) or '').strip()[:limit]

        repair_id = value('id', 64)
        if not re.fullmatch('[0-9a-f]{32}', repair_id):
            return jsonify(error='维修记录编号无效。'), 422
        if data.get('action') == 'update_device':
            # The group confirm sheet can correct the device details after the repair row
            # was created, so the repair ledger row must follow the corrected values.
            row = connection.execute(
                """update field_repairs set equipment_number = ?, equipment_model = ?, position_number = ?,
                   container_number = ?, fault = case when ? = '' then fault else ? end
                   where id = ? and order_id = ? and user_id = ? and completed_date is null returning id""",
                (value('equipment_number', 200), value('equipment_model', 200), value('position_number', 200),
                 value('container_number', 200).upper(), value('fault', FAULT_TEXT_LIMIT), value('fault', FAULT_TEXT_LIMIT),
                 repair_id, order_id, user_id),
            ).fetchone()
            if not row:
                return jsonify(error='维修记录不存在或已完成。'), 409
            return json_utf8({'ok': True, 'id': repair_id})

        if data.get('action') == 'save_work':
            work = value('work_content', 2000)
            if not work:
                return jsonify(error='请填写本次工作内容。'), 422
            row = connection.execute(
                """update field_repairs set work_content = ?
                   where id = ? and order_id = ? and user_id = ? and completed_date is null returning id""",
                (work, repair_id, order_id, user_id),
            ).fetchone()
            if not row:
                return jsonify(error='维修记录不存在或已完成。'), 409
            return json_utf8({'ok': True, 'id': repair_id})

        if data.get('action') == 'complete':
            work = value('work_content', 2000)
            if not work:
                return jsonify(error='请填写本次工作内容。'), 422
            photos = connection.execute(
                """select repair_phase, max(capture_date) as capture_date from field_photos
                   where order_id = ? and equipment_session = ? and photo_type = 'equipment'
                   group by repair_phase""",
                (order_id, repair_id),
            ).fetchall()
            dates = {row['repair_phase']: row['capture_date'] for row in photos}
            if not dates.get('before'):
                return jsonify(error='请先上传维修前照片，再完成本台维修。'), 422
            row = connection.execute(
                """update field_repairs set work_content = ?, result = ?, completed_date = ?
                   where id = ? and order_id = ? and user_id = ? and completed_date is null returning id""",
                (work, value('result', 1000), dates.get('after') or dates['before'], repair_id, order_id, user_id),
            ).fetchone()
            if not row:
                return jsonify(error='维修记录不存在或已完成。'), 409
        else:
            if order['status'] == 'closed' or connection.execute(
                'select 1 from customer_reimbursements where service_order_id = ? limit 1', (order_id,),
            ).fetchone():
                return jsonify(error='工单已关闭或已开始结算。'), 409
            if not value('fault', FAULT_TEXT_LIMIT):
                return jsonify(error='请先记录故障现象。'), 422
            number = value('equipment_number', 200)
            if not number and data.get('no_equipment_number') is not True:
                return jsonify(error='请输入设备编号或确认无编号。'), 422
            row = connection.execute(
                """insert into field_repairs (id, order_id, user_id, equipment_number, equipment_model,
                   position_number, container_number, fault, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   on conflict (id) do nothing returning id""",
                (repair_id, order_id, user_id, number, value('equipment_model', 200), value('position_number', 200),
                 value('container_number', 200).upper(), value('fault', FAULT_TEXT_LIMIT), api['now']()),
            ).fetchone()
            if not row and not connection.execute(
                'select 1 from field_repairs where id = ? and order_id = ? and user_id = ? and completed_date is null',
                (repair_id, order_id, user_id),
            ).fetchone():
                return jsonify(error='维修记录编号已使用。'), 409
        connection.commit()
        return jsonify(ok=True, id=repair_id)

    @app.get('/api/field/photos')
    @access
    def field_photo_list():
        rows = photo_rows()
        can_delete = api['has_action_permission']('service_reports', 'delete')
        allowed_orders = set()
        if can_delete:
            clauses, params = api['service_order_access_filters']()
            allowed_orders = {row['id'] for row in api['db']().execute(
                'select service_orders.id from service_orders where ' + (' and '.join(clauses) or '1=1'), params)}
        signer = URLSafeTimedSerializer(app.secret_key, salt='delete-ledger-photos')
        result = []
        for row in rows[:2000]:
            entry = dict(row) | dict(preview=url_for('field_photo_preview', photo_id=row['id']),
                                     thumbnail=url_for('field_photo_preview', photo_id=row['id'], thumb=1))
            entry['can_delete'] = can_delete and row['order_id'] in allowed_orders
            if entry['can_delete']:
                entry['delete_token'] = signer.dumps({'user': g.user['id'], 'ids': [row['id']]})
            result.append(entry)
        return jsonify(rows=result, truncated=len(rows) > 2000)

    @app.get('/field/photos/<int:photo_id>')
    @access
    def field_photo_preview(photo_id):
        row = api['db']().execute('select * from field_photos where id = ?', (photo_id,)).fetchone()
        if not row:
            abort(404)
        response = send_file(photo_file(row, request.args.get('thumb') == '1'), mimetype='image/jpeg',
                             as_attachment=request.args.get('download') == '1')
        response.headers['Cache-Control'] = 'no-store, private'
        return response

    @app.get('/reports/field-photos')
    @access
    def field_photo_query():
        _FILTER_KEYS = ('order_id', 'site', 'equipment_number', 'position_number',
                        'container_number', 'photo_type', 'technician', 'date_from', 'date_to')
        queried = any(request.args.get(k, '').strip() for k in _FILTER_KEYS)
        rows = photo_rows() if queried else []
        can_delete = api['has_action_permission']('service_reports', 'delete')
        allowed_orders = set()
        if can_delete:
            clauses, params = api['service_order_access_filters']()
            allowed_orders = {row['id'] for row in api['db']().execute(
                'select service_orders.id from service_orders where ' + (' and '.join(clauses) or '1=1'), params)}
        signer = URLSafeTimedSerializer(app.secret_key, salt='delete-ledger-photos')
        limited_rows = [dict(row) for row in rows[:2000]]
        for row in limited_rows:
            row['can_delete'] = can_delete and row['order_id'] in allowed_orders
            row['photo_type_label'] = PHOTO_TYPE_LABELS.get(row.get('photo_type'), row.get('photo_type') or '-')
            if row['can_delete']:
                row['delete_token'] = signer.dumps({'user': g.user['id'], 'ids': [row['id']]})
        return render_template('field_photo_query.html', rows=limited_rows, queried=queried, truncated=len(rows) > 2000,
                               photo_orders=photo_order_rows(), can_delete_photos=can_delete,
                               can_edit_photo_type=api['has_action_permission']('service_reports', 'create'),
                               field_csrf=token())

    @app.get('/api/field/photos.xlsx')
    @access
    def field_photo_export():
        rows = photo_rows()
        if len(rows) > 2000:
            return jsonify(error='结果超过 2000 条，请缩小日期或工单范围后导出。'), 422
        headers = ['照片', '照片类型', '工单', '客户', '站点', '铭牌号', '位置号', '集装箱号', '已更换水泵保险编号', '施工员', '实际拍摄账号', '拍摄日期',
                   '设备拍摄时间（UTC）', '水印时间', '水印来源', '归档时区', '上传时间', '纬度', '经度', '精度（米）',
                   '位置状态', '备注', '位置确认说明', '来源', '文件路径']
        keys = ['photo_type', 'order_number', 'customer_name', 'site_name', 'equipment_number', 'position_number', 'container_number', 'pump_fuse_numbers',
                'technician_name', 'employee_name', 'capture_date', 'captured_at', 'watermark_at', 'watermark_source', 'timezone_name',
                'received_at', 'latitude', 'longitude', 'accuracy', 'location_verified', 'note', 'location_note', 'source', 'relative_path']
        from openpyxl import Workbook
        from openpyxl.drawing.image import Image as ExcelImage
        from openpyxl.drawing.spreadsheet_drawing import TwoCellAnchor, AnchorMarker
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = '工单照片台账'
        worksheet.append(headers)
        worksheet.column_dimensions['A'].width = 11.5
        for row in rows:
            values = [str(row[key] or '') for key in keys]
            type_index = keys.index('photo_type')
            values[type_index] = PHOTO_TYPE_LABELS.get(values[type_index], values[type_index])
            source_index = keys.index('watermark_source')
            values[source_index] = '保留原图水印' if row['watermark_source'] == 'original' else '系统生成水印'
            location_index = keys.index('location_verified')
            values[location_index] = '已检查' if row['location_verified'] else '未检查'
            worksheet.append([''] + values)
            try:
                image_path = photo_file(row, thumb=True)
            except NotFound:
                image_path = photo_file(row)
            image = ExcelImage(str(image_path))
            image_row = worksheet.max_row
            marker_from = AnchorMarker(col=0, colOff=0, row=image_row - 1, rowOff=0)
            marker_to = AnchorMarker(col=1, colOff=0, row=image_row, rowOff=0)
            anchor = TwoCellAnchor(_from=marker_from, to=marker_to)
            anchor.editAs = 'twoCell'
            image.anchor = anchor
            worksheet.add_image(image)
            worksheet.row_dimensions[image_row].height = 60
        worksheet.freeze_panes = 'A2'
        worksheet.auto_filter.ref = worksheet.dimensions
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name='field-photos.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.get('/api/field/photos.zip')
    @access
    def field_photo_download_zip():
        rows = photo_rows()
        if len(rows) > 2000:
            return jsonify(error='结果超过 2000 张，请缩小日期或工单范围后下载。'), 422
        if not rows:
            return jsonify(error='没有符合条件的照片。'), 422
        paths = [(row, photo_file(row)) for row in rows]
        buffer = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode='w+b')
        try:
            with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
                for row, path in paths:
                    order = secure_filename(row['order_number']) or str(row['order_id'])
                    name = path.name
                    if re.fullmatch(r'\d{8}_\d{6}-u\d+-[0-9a-f]{32}\.jpg', name):
                        name = f"{row['id']}-{name}"
                    archive.write(path, f"{order}/{row['capture_date']}/{name}")
            buffer.seek(0)
            response = send_file(buffer, as_attachment=True, download_name='field-photos.zip', mimetype='application/zip')
            response.headers['Cache-Control'] = 'no-store, private'
            response.call_on_close(buffer.close)
            return response
        except Exception:
            buffer.close()
            raise

    def repair_table_rows():
        photos = [row for row in photo_rows() if row['photo_type'] == 'equipment']
        return group_repair_photos(photos)

    @app.get('/reports/field-repairs')
    @access
    def field_repair_report():
        rows = repair_table_rows()
        can_delete = api['has_action_permission']('service_reports', 'delete')
        allowed_orders = set()
        if can_delete:
            clauses, params = api['service_order_access_filters']()
            allowed_orders = {row['id'] for row in api['db']().execute(
                'select service_orders.id from service_orders where ' + (' and '.join(clauses) or '1=1'), params)}
        signer = URLSafeTimedSerializer(app.secret_key, salt='delete-repair-photos')
        for row in rows:
            row['can_delete'] = can_delete and row['order_id'] in allowed_orders
            if row['can_delete']:
                row['delete_token'] = signer.dumps({'user': g.user['id'], 'ids': row['photo_ids']})
        return render_template('field_repair_report.html', rows=rows, photo_orders=photo_order_rows(),
                               field_csrf=token(), can_delete_repairs=can_delete)

    @app.get('/api/field/repairs/order/<int:order_id>')
    @access
    def field_repair_devices(order_id):
        """按工单读取设备维修清单（位置号 / 铭牌号 / 备注），供日报「读取」按钮使用。

        可选 `?date=YYYY-MM-DD`：只统计当天拍摄的维修照片（日报按报告日期读取）。
        返回 items（一台设备一行）与 text（可直接写入工作内容的文本，多台设备换行继续）。
        """
        clauses, params = api['service_order_access_filters']()
        clauses.append('service_orders.id = ?')
        params.append(order_id)
        order = api['db']().execute(
            'select id, order_number from service_orders where ' + ' and '.join(clauses), params).fetchone()
        if not order:
            return jsonify(error='工单不存在或无权访问。'), 404
        report_date = (request.args.get('date') or '').strip()
        where_day = ''
        day_params = []
        if report_date:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', report_date):
                return jsonify(error='日期格式应为 YYYY-MM-DD。'), 400
            where_day = ' and coalesce(cast(r.completed_date as text), p.capture_date) = ?'
            day_params = [report_date]
        sql = '''
            select p.*, r.equipment_model, r.work_content, r.completed_date, service_orders.order_number, users.name as employee_name
            from field_photos p join service_orders on service_orders.id = p.order_id
            join users on users.id = p.user_id
            left join field_repairs r on r.id = p.equipment_session and r.order_id = p.order_id
            where p.order_id = ? and p.photo_type = 'equipment'
        '''
        if where_day:
            sql += where_day
        sql += ' order by p.captured_at, p.id'
        photos = api['db']().execute(sql, (order_id, *day_params)).fetchall()
        rows = group_repair_photos([dict(row) for row in photos])
        items = [dict(
            date=row['date'], position_number=row['position_number'], container_number=row['container_number'],
            equipment_number=row['equipment_number'], equipment_model=row['equipment_model'], work_content=row['work_content'], pump_fuse_numbers=row['pump_fuse_numbers'],
            note=row['note'], technician=row['technician'], photo_count=row['photo_count'],
        ) for row in rows]
        return jsonify(ok=True, order_id=order_id, order_number=order['order_number'],
                       items=items, count=len(items), text=repair_work_text(rows))

    @app.post('/api/field/repairs/delete')
    @access
    def delete_field_repair():
        check_write()
        if not api['has_action_permission']('service_reports', 'delete'):
            abort(403)
        signer = URLSafeTimedSerializer(app.secret_key, salt='delete-repair-photos')
        try:
            selection = signer.loads((request.get_json(silent=True) or {}).get('token', ''), max_age=7200)
        except (BadSignature, SignatureExpired, TypeError):
            return jsonify(error='删除确认已失效，请刷新清单后重试。'), 409
        if selection.get('user') != g.user['id']:
            abort(403)
        ids = selection.get('ids', [])
        if not ids or len(ids) > 2001 or any(type(value) is not int for value in ids):
            abort(400)
        db = api['db']()
        moved = []
        staging = None
        try:
            db.execute('begin immediate')
            rows = db.execute('select * from field_photos where id in (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
            if not rows:
                db.rollback()
                return jsonify(ok=True, deleted=0)
            if len(rows) != len(ids) or any(row['photo_type'] != 'equipment' for row in rows):
                db.rollback()
                return jsonify(error='清单记录已变化，请刷新后重新确认。'), 409
            for order_id in {row['order_id'] for row in rows}:
                api['require_service_order'](order_id)
            root = api['shared_photos_root']().resolve()
            paths = []
            for row in rows:
                relative = Path(row['relative_path'])
                if relative.is_absolute() or len(relative.parts) != 4 or relative.parts[1] != 'pictures' or '..' in relative.parts:
                    abort(409, description='照片路径异常，未执行删除。')
                for relative_file in (relative, Path(relative.parts[0], 'thumbnails', *relative.parts[2:])):
                    path = root / relative_file
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root) or any(parent.is_symlink() for parent in [path, *path.parents] if parent != root and parent.is_relative_to(root)):
                        abort(409, description='照片路径异常，未执行删除。')
                    if path.exists():
                        if not path.is_file():
                            abort(409, description='照片路径异常，未执行删除。')
                        paths.append(path)
            if paths:
                staging = Path(tempfile.mkdtemp(prefix='.repair-delete-', dir=root))
                for index, path in enumerate(dict.fromkeys(paths)):
                    staged = staging / str(index)
                    os.replace(path, staged)
                    moved.append((path, staged))
            db.execute('delete from field_photos where id in (' + ','.join('?' for _ in ids) + ')', ids)
            api['log_action']('delete', 'field_photo', ids[0], '设备维修清单',
                              '删除设备维修记录及照片，照片 ID：' + ','.join(map(str, ids)))
            db.commit()
        except Exception:
            db.rollback()
            for path, staged in reversed(moved):
                if staged.exists():
                    os.replace(staged, path)
            if staging is not None:
                staging.rmdir()
            raise
        cleanup_pending = False
        for _, staged in moved:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                cleanup_pending = True
                app.logger.exception('Repair photo deleted from register; staged file cleanup failed: %s', staged)
        if staging is not None and not cleanup_pending:
            staging.rmdir()
        return jsonify(ok=True, deleted=len(rows), cleanup_pending=cleanup_pending)

    @app.post('/api/field/photos/delete')
    @access
    def delete_field_photos():
        check_write()
        if not api['has_action_permission']('service_reports', 'delete'):
            abort(403)
        signer = URLSafeTimedSerializer(app.secret_key, salt='delete-ledger-photos')
        try:
            selection = signer.loads((request.get_json(silent=True) or {}).get('token', ''), max_age=7200)
        except (BadSignature, SignatureExpired, TypeError):
            return jsonify(error='删除确认已失效，请刷新后重试。'), 409
        if selection.get('user') != g.user['id']:
            abort(403)
        ids = selection.get('ids', [])
        if not ids or len(ids) > 2001 or any(type(value) is not int for value in ids):
            abort(400)
        db = api['db']()
        moved = []
        staging = None
        try:
            db.execute('begin immediate')
            rows = db.execute('select * from field_photos where id in (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
            if not rows:
                db.rollback()
                return jsonify(ok=True, deleted=0)
            if len(rows) != len(ids):
                db.rollback()
                return jsonify(error='记录已变化，请刷新后重新确认。'), 409
            for order_id in {row['order_id'] for row in rows}:
                api['require_service_order'](order_id)
            root = api['shared_photos_root']().resolve()
            paths = []
            for row in rows:
                relative = Path(row['relative_path'])
                if relative.is_absolute() or len(relative.parts) != 4 or relative.parts[1] != 'pictures' or '..' in relative.parts:
                    abort(409, description='照片路径异常，未执行删除。')
                for relative_file in (relative, Path(relative.parts[0], 'thumbnails', *relative.parts[2:])):
                    path = root / relative_file
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root) or any(parent.is_symlink() for parent in [path, *path.parents] if parent != root and parent.is_relative_to(root)):
                        abort(409, description='照片路径异常，未执行删除。')
                    if path.exists():
                        if not path.is_file():
                            abort(409, description='照片路径异常，未执行删除。')
                        paths.append(path)
            if paths:
                staging = Path(tempfile.mkdtemp(prefix='.ledger-delete-', dir=root))
                for index, path in enumerate(dict.fromkeys(paths)):
                    staged = staging / str(index)
                    os.replace(path, staged)
                    moved.append((path, staged))
            db.execute('delete from field_photos where id in (' + ','.join('?' for _ in ids) + ')', ids)
            api['log_action']('delete', 'field_photo', ids[0], '照片台账',
                              '删除照片，照片 ID：' + ','.join(map(str, ids)))
            db.commit()
        except Exception:
            db.rollback()
            for path, staged in reversed(moved):
                if staged.exists():
                    os.replace(staged, path)
            if staging is not None:
                staging.rmdir()
            raise
        cleanup_pending = False
        for _, staged in moved:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                cleanup_pending = True
                app.logger.exception('Ledger photo deleted; staged file cleanup failed: %s', staged)
        if staging is not None and not cleanup_pending:
            staging.rmdir()
        return jsonify(ok=True, deleted=len(rows), cleanup_pending=cleanup_pending)

    @app.post('/api/field/photos/<int:photo_id>/type')
    @access
    def update_field_photo_type(photo_id):
        check_write()
        if not api['has_action_permission']('service_reports', 'create'):
            abort(403)
        data = request.get_json(silent=True) or {}
        photo_type = str(data.get('photo_type', '')).strip()
        if photo_type not in EDITABLE_PHOTO_TYPES:
            return jsonify(error='照片类型无效，只能为 设备 / 进场 / 离场 / 自检。'), 422
        db = api['db']()
        row = db.execute(
            'select f.id, f.order_id, s.order_number, f.photo_type'
            ' from field_photos f join service_orders s on s.id = f.order_id'
            ' where f.id = ?',
            (photo_id,),
        ).fetchone()
        if not row:
            return jsonify(error='照片不存在。'), 404
        api['require_service_order'](row['order_id'])
        label = PHOTO_TYPE_LABELS[photo_type]
        if row['photo_type'] == photo_type:
            return jsonify(ok=True, photo_id=photo_id, photo_type=photo_type,
                           photo_type_label=label, unchanged=True)
        db.execute('update field_photos set photo_type = ? where id = ?', (photo_type, photo_id))
        api['log_action']('update', 'field_photo', photo_id, row['order_number'],
                          '照片类型改为：' + label)
        db.commit()
        return jsonify(ok=True, photo_id=photo_id, photo_type=photo_type,
                       photo_type_label=label)

    @app.get('/api/field/repairs.xlsx')
    @access
    def field_repair_export():
        rows = repair_table_rows()
        headers = ['序号', '日期', '地标号', '集装箱号', '同飞铭牌', '设备型号', '工作内容', '已更换水泵保险编号', '备注', '维修人员', '工单', '照片数量']
        keys = ['sequence', 'date', 'position_number', 'container_number', 'equipment_number', 'equipment_model', 'work_content',
                'pump_fuse_numbers', 'note', 'technician', 'order_number', 'photo_count']
        buffer = api['build_simple_xlsx'](headers, [[str(row[key] or '') for key in keys] for row in rows], sheet_name='设备维修清单')
        return send_file(buffer, as_attachment=True, download_name='field-repairs.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.post('/api/field/order-request')
    @access
    def field_order_request():
        check_write()
        data = request.get_json(silent=True) or {}
        detail = str(data.get('detail', '')).strip()[:1000]
        if not detail:
            return jsonify(error='请填写站点和工作说明。'), 422
        api['notify_role'](['admin', 'manager'], '现场员工申请新建工单', f"{g.user['name']}：{detail}", url_for('service_orders'))
        api['db']().commit()
        return jsonify(ok=True)

    @app.after_request
    def no_cache_field_data(response):
        if request.path.startswith('/api/field/'):
            response.headers['Cache-Control'] = 'no-store, private'
        return response

    def auto_map_field_fault_model():
        """Point the controller-screen scene at an existing vision model.

        Admins already map a vision model for nameplates or daily photos; reuse it
        for the controller screen instead of leaving the scene unconfigured (which
        silently fell back to the local OCR and produced garbled Chinese).
        """
        try:
            if api['get_setting']('llm_scene_field_fault', '').strip():
                return
            db = api['db']()
            chosen = None
            for scene in ('daily_vision', 'attachment_interpret', 'wechat_vision'):
                mapped = api['get_setting'](f'llm_scene_{scene}', '').strip()
                if not mapped or not mapped.isdigit():
                    continue
                row = db.execute(
                    'select id from llm_configs where id = ? and enabled = 1 and supports_vision = 1',
                    (int(mapped),),
                ).fetchone()
                if row:
                    chosen = row['id']
                    break
            if chosen is None:
                row = db.execute(
                    'select id from llm_configs where enabled = 1 and supports_vision = 1 order by id limit 1'
                ).fetchone()
                chosen = row['id'] if row else None
            if chosen is None:
                return
            api['set_setting']('llm_scene_field_fault', str(chosen))
            app.logger.info('field_fault scene mapped to vision config %s', chosen)
        except Exception:
            app.logger.exception('field_fault scene auto mapping failed')

    with app.app_context():
        auto_map_field_fault_model()

    def backfill_repair_container_numbers():
        """One-off repair for rows created before 0.1.519.

        The container number used to be written only while confirming a photo
        batch, so repair rows created before that fix stayed blank. Fill those
        rows from their own photos once, and only when the column is empty.
        """
        try:
            if api['get_setting']('field_repair_container_backfill_v1', '') == '1':
                return
            db = api['db']()
            updated = db.execute(
                """update field_repairs
                   set container_number = (
                     select p.container_number from field_photos p
                     where p.order_id = field_repairs.order_id
                       and p.equipment_session = field_repairs.id
                       and coalesce(p.container_number, '') <> ''
                     order by p.captured_at
                     limit 1)
                   where coalesce(container_number, '') = ''"""
            ).rowcount
            db.commit()
            api['set_setting']('field_repair_container_backfill_v1', '1')
            if updated:
                app.logger.info('field repair container backfill filled %s rows', updated)
        except Exception:
            app.logger.exception('field repair container backfill failed')

    with app.app_context():
        backfill_repair_container_numbers()
