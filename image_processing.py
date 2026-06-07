import os
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener


ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "heic", "heif"}

register_heif_opener()


def is_supported_image(path):
    return Path(path).suffix.lower().lstrip(".") in ALLOWED_IMAGE_EXTENSIONS


def compress_image(source, target_path, max_size=1800, quality=78):
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with Image.open(source) as source_image:
            source_image.seek(0)
            image = ImageOps.exif_transpose(source_image)
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image.convert("RGB"))
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            image.save(temporary, format="JPEG", quality=quality, optimize=True, progressive=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def create_thumbnail(source, target_path, max_width=420, max_height=320, quality=70):
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with Image.open(source) as source_image:
            source_image.seek(0)
            image = ImageOps.exif_transpose(source_image).convert("RGB")
            image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            image.save(temporary, format="JPEG", quality=quality, optimize=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
