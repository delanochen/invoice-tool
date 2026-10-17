"""Clock-in site queries and scoped shared-photo filesystem helpers."""

def clock_in_site_rows(*, db, g, is_external_user):
    if is_external_user():
        if not g.user["client_id"]:
            return []
        return db().execute(
            "select id, buyer_number, name, state_code, detailed_address from buyers where client_id = ? order by name",
            (g.user["client_id"],),
        ).fetchall()
    return db().execute(
        "select id, buyer_number, name, state_code, detailed_address from buyers order by name"
    ).fetchall()


def resolve_shared_photo(relative_path="", require_file=False, allow_missing=False, *, ALLOWED_IMAGE_EXTENSIONS, abort, shared_photos_root):
    root = shared_photos_root()
    candidate = (root / str(relative_path or "")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        abort(403)
    if allow_missing and not candidate.exists():
        return candidate
    if require_file:
        if not candidate.is_file() or candidate.suffix.lower().lstrip(".") not in ALLOWED_IMAGE_EXTENSIONS:
            abort(404)
    elif not candidate.is_dir():
        abort(404)
    return candidate


def shared_photo_relative(path, *, shared_photos_root):
    return path.resolve().relative_to(shared_photos_root()).as_posix()


def resolve_shared_picture(relative_path, *, abort, resolve_shared_photo, shared_photos_root):
    source_path = resolve_shared_photo(relative_path, require_file=True)
    relative_parts = source_path.relative_to(shared_photos_root()).parts
    if len(relative_parts) < 3 or relative_parts[1].casefold() != "pictures":
        abort(403)
    return source_path, relative_parts


def count_shared_images(path, excluded_dirs=None, *, ALLOWED_IMAGE_EXTENSIONS, valid_image_file):
    if not path.is_dir():
        return 0
    excluded = {name.casefold() for name in (excluded_dirs or set())}
    return sum(
        1
        for entry in path.rglob("*")
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.suffix.lower().lstrip(".") in ALLOWED_IMAGE_EXTENSIONS
        and not ({part.casefold() for part in entry.relative_to(path).parts} & ({"@eadir"} | excluded))
        and valid_image_file(entry)
    )


def order_photo_status(order_dir, *, ALLOWED_IMAGE_EXTENSIONS, count_shared_images):
    state_names = {"incoming", "pictures", "thumbnails", "processing", "failed", "original_backup"}
    waiting = count_shared_images(order_dir / "incoming")
    if order_dir.is_dir():
        waiting += sum(
            1
            for entry in order_dir.rglob("*")
            if entry.is_file()
            and not entry.name.startswith(".")
            and entry.suffix.lower().lstrip(".") in ALLOWED_IMAGE_EXTENSIONS
            and entry.relative_to(order_dir).parts[0].casefold() not in state_names
            and "@eadir" not in {part.casefold() for part in entry.relative_to(order_dir).parts}
        )
    return {
        "waiting": waiting,
        "processing": count_shared_images(order_dir / "processing"),
        "completed": count_shared_images(order_dir / "pictures"),
        "failed": count_shared_images(order_dir / "failed", excluded_dirs={"orphaned_pictures"}),
    }
