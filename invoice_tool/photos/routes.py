"""Mobile clock-in and shared-photo access endpoints; preserve existing policies."""
import os
import re
import secrets
import tempfile
import zipfile
from io import BytesIO
from datetime import timedelta, timezone
from pathlib import Path
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename
from flask import abort, g, jsonify, render_template, request, send_file, url_for


def register_photo_capture_routes(app, *,
    resolve_ALLOWED_IMAGE_EXTENSIONS,
    app_timezone,
    clock_in_site_rows,
    count_shared_images,
    resolve_date,
    resolve_datetime,
    db,
    is_internal_user,
    log_action,
    login_required,
    now,
    order_photo_status,
    resolve_shared_photo,
    resolve_shared_picture,
    shared_photo_relative,
    shared_photos_root,
    valid_image_file,
):
    @login_required
    def mobile_clock_in():
        recent = db().execute(
            """
            select clock_in_photos.*, buyers.name as site_name
            from clock_in_photos
            join buyers on buyers.id = clock_in_photos.buyer_id
            where clock_in_photos.user_id = ?
            order by clock_in_photos.id desc limit 5
            """,
            (g.user["id"],),
        ).fetchall()
        return render_template(
            "mobile_clock_in.html",
            sites=clock_in_site_rows(),
            operator_name=g.user["name"],
            recent=recent,
        )


    @login_required
    def upload_mobile_clock_in():
        site_id = request.form.get("site_id", "")
        site = next((row for row in clock_in_site_rows() if str(row["id"]) == site_id), None)
        if not site:
            return jsonify({"ok": False, "error": "请选择有效站点。"}), 422
        try:
            latitude = float(request.form.get("latitude", ""))
            longitude = float(request.form.get("longitude", ""))
            accuracy = float(request.form.get("accuracy", "") or 0)
            offset_minutes = int(request.form.get("time_offset_minutes", "0"))
            captured_at = resolve_datetime().fromisoformat(request.form.get("captured_at", "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "时间或坐标格式不正确。"}), 422
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return jsonify({"ok": False, "error": "坐标超出有效范围。"}), 422
        if not (-1440 <= offset_minutes <= 1440):
            return jsonify({"ok": False, "error": "打卡时间最多只能前后调整 24 小时。"}), 422
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        expected_capture = resolve_datetime().now(timezone.utc) + timedelta(minutes=offset_minutes)
        if abs((captured_at.astimezone(timezone.utc) - expected_capture).total_seconds()) > 300:
            return jsonify({"ok": False, "error": "水印时间与调整值不一致，请刷新页面后重试。"}), 422
        photo = request.files.get("photo")
        if not photo:
            return jsonify({"ok": False, "error": "没有收到照片。"}), 422
        content = photo.read(15 * 1024 * 1024 + 1)
        if not content or len(content) > 15 * 1024 * 1024:
            return jsonify({"ok": False, "error": "照片无效或超过 15MB。"}), 422
        try:
            with Image.open(BytesIO(content)) as image:
                image.verify()
                if image.format not in {"JPEG", "PNG"}:
                    raise ValueError
        except (OSError, ValueError):
            return jsonify({"ok": False, "error": "只接受有效的 JPG 或 PNG 照片。"}), 422

        local_capture = captured_at.astimezone(app_timezone())
        folder = shared_photos_root() / "clock-ins" / local_capture.date().isoformat() / site["buyer_number"]
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{local_capture.strftime('%H%M%S')}-{g.user['id']}-{secrets.token_hex(4)}.jpg"
        target = folder / filename
        target.write_bytes(content)
        relative_path = target.relative_to(shared_photos_root()).as_posix()
        received_at = now()
        cursor = db().execute(
            """
            insert into clock_in_photos (
                buyer_id, user_id, captured_at, server_received_at, time_offset_minutes,
                latitude, longitude, location_accuracy, relative_path, original_filename
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site["id"], g.user["id"], captured_at.isoformat(), received_at, offset_minutes,
                latitude, longitude, accuracy, relative_path, photo.filename or "clock-in.jpg",
            ),
        )
        log_action("create", "clock_in_photo", cursor.lastrowid, site["name"], "移动打卡拍照")
        db().commit()
        return jsonify({"ok": True, "message": "打卡照片已上传到服务器。", "id": cursor.lastrowid})


    @login_required
    def browse_shared_photos():
        if not is_internal_user():
            abort(403)
        if not shared_photos_root().is_dir():
            return jsonify(
                {
                    "available": False,
                    "current": "",
                    "parent": None,
                    "folders": [],
                    "images": [],
                    "status": {"waiting": 0, "processing": 0, "completed": 0, "failed": 0},
                }
            )
        requested_path = request.args.get("path", "")
        requested_day = request.args.get("day", "").strip()
        if requested_day:
            try:
                requested_day = resolve_date().fromisoformat(requested_day).isoformat()
            except ValueError:
                abort(400)
        order_dir = resolve_shared_photo(requested_path, allow_missing=True)
        if not order_dir.is_dir():
            return jsonify(
                {
                    "available": True,
                    "folder_exists": False,
                    "current": requested_path,
                    "parent": None,
                    "folders": [],
                    "images": [],
                    "status": {"waiting": 0, "processing": 0, "completed": 0, "failed": 0},
                }
            )
        pictures_root = order_dir / "pictures"
        current = pictures_root / requested_day if requested_day else pictures_root
        folders = []
        if pictures_root.is_dir():
            try:
                day_folders = sorted(
                    (
                        entry
                        for entry in pictures_root.iterdir()
                        if entry.is_dir()
                        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.name)
                    ),
                    key=lambda entry: entry.name,
                    reverse=True,
                )
            except OSError:
                abort(403)
            folders = [
                {"name": folder.name, "count": count_shared_images(folder)}
                for folder in day_folders
            ]
        if requested_day and requested_day not in {folder["name"] for folder in folders}:
            folders.insert(0, {"name": requested_day, "count": 0})
        images = []
        if current.is_dir():
            try:
                entries = sorted(
                    (
                        entry
                        for entry in current.rglob("*")
                        if entry.is_file()
                        and not entry.name.startswith(".")
                        and entry.suffix.lower().lstrip(".") in resolve_ALLOWED_IMAGE_EXTENSIONS()
                        and "@eadir" not in {part.casefold() for part in entry.relative_to(current).parts}
                    ),
                    key=lambda item: item.relative_to(current).as_posix().casefold(),
                )
            except OSError:
                abort(403)
            for entry in entries:
                try:
                    entry.relative_to(current)
                except ValueError:
                    continue
                if not valid_image_file(entry):
                    continue
                relative = shared_photo_relative(entry)
                display_name = entry.relative_to(current).as_posix()
                images.append(
                    {
                        "name": display_name,
                        "path": relative,
                        "thumbnail": url_for("shared_photo_thumbnail", path=relative),
                        "preview": url_for("shared_photo_preview", path=relative),
                    }
                )
        return jsonify(
            {
                "available": True,
                "folder_exists": True,
                "current": f"{requested_path}/{requested_day}" if requested_day else requested_path,
                "parent": None,
                "folders": folders,
                "selected_day": requested_day,
                "images": images,
                "status": order_photo_status(order_dir),
            }
        )


    @login_required
    def download_shared_photos():
        if not is_internal_user():
            abort(403)
        selected_paths = list(dict.fromkeys(path for path in request.form.getlist("path") if path))
        if not selected_paths:
            abort(400)

        files = []
        for relative_path in selected_paths:
            source_path, relative_parts = resolve_shared_picture(relative_path)
            files.append((source_path, Path(*relative_parts[2:]).as_posix()))
        if not files:
            abort(400)

        archive_root = secure_filename(Path(selected_paths[0]).parts[0]) or "server-photos"
        used_names = set()
        archive_file = tempfile.NamedTemporaryFile(prefix=f"{archive_root}-", suffix=".zip", delete=False)
        archive_path = archive_file.name
        archive_file.close()
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for source_path, display_name in files:
                arcname = f"{archive_root}/{display_name}"
                if arcname in used_names:
                    stem, extension = os.path.splitext(display_name)
                    suffix = 2
                    while f"{archive_root}/{stem}-{suffix}{extension}" in used_names:
                        suffix += 1
                    arcname = f"{archive_root}/{stem}-{suffix}{extension}"
                used_names.add(arcname)
                archive.write(source_path, arcname=arcname)

        response = send_file(
            archive_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{archive_root}-photos.zip",
        )

        def remove_archive():
            try:
                os.remove(archive_path)
            except FileNotFoundError:
                pass

        response.call_on_close(remove_archive)
        return response


    @login_required
    def shared_photo_preview():
        if not is_internal_user():
            abort(403)
        source_path, _ = resolve_shared_picture(request.args.get("path", ""))
        return send_file(source_path, as_attachment=False, conditional=True)


    @login_required
    def shared_photo_thumbnail():
        if not is_internal_user():
            abort(403)
        source_path = resolve_shared_photo(request.args.get("path", ""), require_file=True)
        relative_parts = source_path.relative_to(shared_photos_root()).parts
        if len(relative_parts) >= 3 and relative_parts[1].casefold() == "pictures":
            thumbnail_path = shared_photos_root() / relative_parts[0] / "thumbnails" / Path(*relative_parts[2:])
            if thumbnail_path.is_file() and valid_image_file(thumbnail_path):
                return send_file(thumbnail_path, mimetype="image/jpeg", max_age=3600)
        buffer = BytesIO()
        try:
            with Image.open(source_path) as source:
                source.seek(0)
                image = ImageOps.exif_transpose(source)
                if image.mode not in {"RGB", "L"}:
                    background = Image.new("RGB", image.size, "white")
                    if "A" in image.getbands():
                        background.paste(image, mask=image.getchannel("A"))
                    else:
                        background.paste(image.convert("RGB"))
                    image = background
                else:
                    image = image.convert("RGB")
                image.thumbnail((420, 320), Image.Resampling.LANCZOS)
                image.save(buffer, format="JPEG", quality=70, optimize=True)
        except (OSError, ValueError):
            abort(404)
        buffer.seek(0)
        return send_file(buffer, mimetype="image/jpeg", max_age=3600)


    app.add_url_rule('/mobile-clock-in', endpoint='mobile_clock_in', view_func=mobile_clock_in, methods=['GET'])
    app.add_url_rule('/api/mobile-clock-in', endpoint='upload_mobile_clock_in', view_func=upload_mobile_clock_in, methods=['POST'])
    app.add_url_rule('/shared-photos/browse', endpoint='browse_shared_photos', view_func=browse_shared_photos, methods=['GET'])
    app.add_url_rule('/shared-photos/download', endpoint='download_shared_photos', view_func=download_shared_photos, methods=['POST'])
    app.add_url_rule('/shared-photos/preview', endpoint='shared_photo_preview', view_func=shared_photo_preview, methods=['GET'])
    app.add_url_rule('/shared-photos/thumbnail', endpoint='shared_photo_thumbnail', view_func=shared_photo_thumbnail, methods=['GET'])
    return {'mobile_clock_in': mobile_clock_in, 'upload_mobile_clock_in': upload_mobile_clock_in, 'browse_shared_photos': browse_shared_photos, 'download_shared_photos': download_shared_photos, 'shared_photo_preview': shared_photo_preview, 'shared_photo_thumbnail': shared_photo_thumbnail}
