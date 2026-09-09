"""Resolve imported photo dates: visible timestamp, EXIF, then explicit user date."""
import re
import subprocess
from datetime import datetime, timezone, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo
from PIL import Image, ImageOps

STAMP = re.compile(r'(?<!\d)(20\d{2})\s*[-/.:年]\s*(\d{1,2})\s*[-/.:月]\s*(\d{1,2})日?(?:[ T,]+(\d{1,2})\s*:\s*(\d{2})(?:\s*:\s*(\d{2}))?)?(?:\s*(?:GMT|UTC)\s*([+-])(\d{1,2})(?::?(\d{2}))?)?(?!\d)', re.I)

def watermark_dates(text, timezone_name):
    # Numeric day/month order is only automatic when unambiguous. Conflicting
    # interpretations remain candidates for the user's date picker.
    extra = []
    numeric = re.compile(r'(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](20\d{2})(\s+\d{1,2}:\d{2}(?::\d{2})?)?(?!\d)')
    for match in numeric.finditer(text):
        first, second, year, clock = match.groups()
        a, b = int(first), int(second)
        if a <= 12:
            extra.append(f'{year}-{a:02}-{b:02}{clock or ""}')
        if b <= 12:
            extra.append(f'{year}-{b:02}-{a:02}{clock or ""}')
    text += '\n' + '\n'.join(extra)
    result = {}
    for match in STAMP.finditer(text):
        year, month, day, hour, minute, second, sign, offset_hour, offset_minute = match.groups()
        try:
            zone = ZoneInfo(timezone_name)
            if sign:
                minutes = int(offset_hour)*60 + int(offset_minute or 0)
                if minutes > 14*60:
                    continue
                zone = timezone(timedelta(minutes=minutes * (-1 if sign == '-' else 1)))
            value = datetime(int(year), int(month), int(day), int(hour or 12), int(minute or 0), int(second or 0), tzinfo=zone)
        except ValueError:
            continue
        # Preserve the printed calendar date, even when it is a different UTC day.
        existing = result.get(value.date().isoformat())
        if existing is None or hour:
            result[value.date().isoformat()] = value
    return list(result.values())

def read_watermark(image):
    image = ImageOps.exif_transpose(image).convert('RGB')
    w, h = image.size
    views = [image, image.crop((0, int(h*.55), w, h))]
    texts = []
    for view, psm in zip(views, ('11', '6')):
        view.thumbnail((2600, 2600))
        gray = ImageOps.autocontrast(ImageOps.grayscale(view))
        payload = BytesIO()
        gray.save(payload, format='PNG')
        result = subprocess.run(['tesseract', 'stdin', 'stdout', '--psm', psm, '-l', 'eng'],
            input=payload.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
        if result.returncode:
            raise RuntimeError('Photo date OCR failed')
        texts.append(result.stdout.decode('utf-8', errors='replace'))
    return '\n'.join(texts)

def exif_capture_date(image, timezone_name):
    exif = image.getexif()
    try:
        values = dict(exif.get_ifd(0x8769))
    except (KeyError, TypeError, ValueError, SyntaxError):
        values = {}
    values = {**dict(exif), **values}
    for tag, offset_tag in [(36867, 36881), (36868, 36882)]:
        raw = values.get(tag)
        if isinstance(raw, bytes):
            raw = raw.decode('ascii', errors='ignore')
        try:
            value = datetime.strptime(str(raw).strip('\x00 '), '%Y:%m:%d %H:%M:%S')
            if not 2000 <= value.year <= 2099:
                continue
            zone = ZoneInfo(timezone_name)
            offset = str(values.get(offset_tag, '')).strip('\x00 ')
            if re.fullmatch(r'[+-]\d{2}:\d{2}', offset):
                minutes = int(offset[1:3])*60+int(offset[4:6])
                if minutes <= 14*60:
                    zone = timezone(timedelta(minutes=minutes*(-1 if offset[0]=='-' else 1)))
            return value.replace(tzinfo=zone)
        except (ValueError, TypeError):
            pass
    return None

def resolve_capture_date(content, timezone_name, manual_date=''):
    with Image.open(BytesIO(content)) as image:
        image.load()
        exif_date = exif_capture_date(image, timezone_name)
        dates = watermark_dates(read_watermark(image), timezone_name)
    if len(dates) == 1:
        return dates[0], 'watermark', []
    if not dates and exif_date:
        return exif_date, 'exif', []
    candidates = sorted({value.date().isoformat() for value in dates})
    if manual_date:
        try:
            value = datetime.strptime(manual_date, '%Y-%m-%d')
            if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', manual_date):
                raise ValueError
            return value.replace(hour=12, tzinfo=ZoneInfo(timezone_name)), 'manual', candidates
        except ValueError:
            raise ValueError('请选择有效的拍摄日期。')
    return None, None, candidates
