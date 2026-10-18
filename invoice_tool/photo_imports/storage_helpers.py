"""Legacy-compatible photo_storage helpers; explicit runtime dependencies."""

def file_sha256(path, *, hashlib):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def service_order_photo_folder(order_number, *, secure_filename, shared_photos_root):
    root = shared_photos_root()
    folder = (root / secure_filename(order_number)).resolve()
    if not folder.is_relative_to(root):
        raise ValueError("工单照片目录无效。")
    return folder


def service_order_photo_summary(order_number, *, service_order_photo_folder):
    folder = service_order_photo_folder(order_number)
    if not folder.exists():
        return {"exists": False, "nonempty": False, "file_count": 0, "bytes": 0}
    files = [path for path in folder.rglob("*") if path.is_file()]
    return {
        "exists": True,
        "nonempty": any(folder.iterdir()),
        "file_count": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def valid_image_file(path, *, Image):
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False
