"""Legacy-compatible attachment_response helpers; explicit runtime dependencies."""

def safe_attachment_response(path, original_filename, *, Image, send_file):
    """Return an attachment response based only on server-side content checks."""
    try:
        with Image.open(path) as image:
            image.verify()
            image_format = image.format
        image_mimetype = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
            "GIF": "image/gif",
        }.get(image_format)
        if image_mimetype:
            return send_file(
                path,
                as_attachment=False,
                download_name=original_filename,
                mimetype=image_mimetype,
                conditional=True,
            )
    except Exception:
        pass

    try:
        with open(path, "rb") as file:
            is_pdf = file.read(5) == b"%PDF-"
    except Exception:
        is_pdf = False
    if is_pdf:
        return send_file(
            path,
            as_attachment=False,
            download_name=original_filename,
            mimetype="application/pdf",
            conditional=True,
        )
    return send_file(
        path,
        as_attachment=True,
        download_name=original_filename,
        mimetype="application/octet-stream",
        conditional=True,
    )
