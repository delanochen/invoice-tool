"""Legacy-compatible residual_photo_paths helpers; explicit runtime dependencies."""

def allowed_attachment(filename, *, ALLOWED_ATTACHMENT_EXTENSIONS):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_ATTACHMENT_EXTENSIONS


def allowed_image(filename, *, ALLOWED_IMAGE_EXTENSIONS):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def shared_photos_root(*, Path, SHARED_PHOTOS_DIR):
    return Path(SHARED_PHOTOS_DIR).resolve()


def ensure_service_order_picture_folder(order_number, *, service_order_photo_folder):
    folder = service_order_photo_folder(order_number) / "pictures"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def service_order_incoming_folder(order_number, source_name, *, secure_filename, shared_photos_root):
    folder = shared_photos_root() / secure_filename(order_number) / "incoming" / secure_filename(source_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def is_google_photos_share_url(value, *, GOOGLE_PHOTOS_ALLOWED_HOSTS, urlsplit):
    parsed = urlsplit((value or "").strip())
    host = parsed.netloc.lower().split(":")[0]
    return parsed.scheme in {"http", "https"} and host in GOOGLE_PHOTOS_ALLOWED_HOSTS


def google_photo_url_key(url):
    return url.split("=", 1)[0]
