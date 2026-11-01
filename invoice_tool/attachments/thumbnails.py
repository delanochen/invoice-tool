"""Bounded image thumbnails for already-authorized attachment paths."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from flask import abort, make_response, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError

THUMBNAIL_SIZE = (160, 160)


def attachment_thumbnail_response(path):
    """Never send the original as an <img> fallback; callers enforce access first."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        abort(404)
    etag = sha256(f'{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}:thumb-v1'.encode()).hexdigest()
    if request.if_none_match.contains_weak(etag):
        response = make_response('', 304)
        response.set_etag(etag)
    else:
        try:
            with Image.open(path) as source:
                # JPEG can decode at reduced resolution instead of expanding a 24MP photo.
                source.draft('RGB', THUMBNAIL_SIZE)
                source.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                image = ImageOps.exif_transpose(source).convert('RGBA')
                background = Image.new('RGB', image.size, 'white')
                background.paste(image, mask=image.getchannel('A'))
                output = BytesIO()
                background.save(output, format='JPEG', quality=75, optimize=True)
            output.seek(0)
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            abort(404)
        response = send_file(output, mimetype='image/jpeg', etag=etag, conditional=True)
    response.headers['Cache-Control'] = 'private, max-age=3600'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.vary.add('Cookie')
    return response
