import hashlib
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from image_processing import compress_image, create_thumbnail, is_supported_image


ROOT = Path(os.environ.get("SHARED_PHOTOS_DIR", "/app/shared-photos"))
POLL_SECONDS = max(10, int(os.environ.get("PHOTO_WORKER_INTERVAL_SECONDS", "60")))
STABLE_SECONDS = max(30, int(os.environ.get("PHOTO_WORKER_STABLE_SECONDS", "120")))
BATCH_SIZE = max(1, int(os.environ.get("PHOTO_WORKER_BATCH_SIZE", "10")))
BACKUP_DAYS = max(0, int(os.environ.get("PHOTO_BACKUP_DAYS", "7")))
STATE_DIRS = {"pictures", "thumbnails", "processing", "failed", "original_backup"}
IGNORED_DIRS = {"@eadir"}


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def is_ignored(path):
    return any(part.casefold() in IGNORED_DIRS for part in path.parts)


def order_directories():
    if not ROOT.is_dir():
        return []
    return sorted(
        (
            path
            for path in ROOT.iterdir()
            if path.is_dir()
            and path.name.upper().startswith("SO")
            and not path.name.startswith(".")
            and path.name.casefold() not in IGNORED_DIRS
        ),
        key=lambda path: path.name.casefold(),
    )


def source_relative_path(order_dir, source):
    relative = source.relative_to(order_dir)
    if relative.parts and relative.parts[0].casefold() in {"incoming", "processing"}:
        relative = Path(*relative.parts[1:])
    return relative


def pending_sources(order_dir):
    cutoff = time.time() - STABLE_SECONDS
    sources = []
    for source in order_dir.rglob("*"):
        if not source.is_file() or source.name.startswith(".") or not is_supported_image(source):
            continue
        relative = source.relative_to(order_dir)
        if is_ignored(relative):
            continue
        if relative.parts and relative.parts[0].casefold() in STATE_DIRS:
            continue
        try:
            if source.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        sources.append(source)
    return sorted(sources, key=lambda path: path.stat().st_mtime)


def interrupted_sources(order_dir):
    processing_dir = order_dir / "processing"
    if not processing_dir.is_dir():
        return []
    cutoff = time.time() - STABLE_SECONDS
    sources = []
    for source in processing_dir.rglob("*"):
        if not source.is_file() or source.name.startswith(".") or not is_supported_image(source):
            continue
        try:
            if source.stat().st_mtime <= cutoff:
                sources.append(source)
        except OSError:
            continue
    return sorted(sources, key=lambda path: path.stat().st_mtime)


def unique_path(path, source):
    if not path.exists():
        return path
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:8]
    candidate = path.with_name(f"{path.stem}-{digest}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{digest}-{counter}{path.suffix}")
        counter += 1
    return candidate


def unique_datetime_path(path):
    if not path.exists():
        return path
    counter = 2
    candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
    while candidate.exists():
        counter += 1
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
    return candidate


def image_taken_datetime(path):
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            for tag in (36867, 36868, 306):
                value = exif.get(tag)
                if not value:
                    continue
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                value = str(value).strip()
                for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        return datetime.strptime(value[:19], pattern)
                    except ValueError:
                        pass
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.now()


def timestamp_output_relative(relative, source):
    timestamp = image_taken_datetime(source).strftime("%Y%m%d_%H%M%S")
    return relative.parent / f"{timestamp}.jpg"


def original_backup_for_picture(order_dir, picture_relative):
    backup_dir = order_dir / "original_backup" / picture_relative.parent
    exact = backup_dir / picture_relative.name
    if exact.is_file() and is_supported_image(exact):
        return exact
    if not backup_dir.is_dir():
        return None
    stem = picture_relative.stem.casefold()
    candidates = [
        path
        for path in backup_dir.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.stem.casefold() == stem
        and is_supported_image(path)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.suffix.casefold() not in {".heic", ".heif"}, path.name.casefold()))[0]


def is_datetime_named_picture(path):
    return re.fullmatch(r"\d{8}_\d{6}(?:-\d+)?\.jpe?g", path.name, re.IGNORECASE) is not None


def rename_existing_pictures_by_datetime(order_dir):
    pictures_dir = order_dir / "pictures"
    thumbnails_dir = order_dir / "thumbnails"
    if not pictures_dir.is_dir():
        return 0
    renamed = 0
    for path in sorted(pictures_dir.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or is_ignored(path.relative_to(pictures_dir)):
            continue
        if is_datetime_named_picture(path):
            continue
        relative = path.relative_to(pictures_dir)
        original = original_backup_for_picture(order_dir, relative)
        if not original:
            log(f"skipped legacy picture rename without original backup: {path}")
            continue
        target_relative = timestamp_output_relative(relative, original)
        target = unique_datetime_path(pictures_dir / target_relative)
        if target == path:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            old_thumbnail = thumbnails_dir / relative
            new_thumbnail = thumbnails_dir / target.relative_to(pictures_dir)
            path.rename(target)
            if old_thumbnail.exists():
                new_thumbnail.parent.mkdir(parents=True, exist_ok=True)
                old_thumbnail.rename(unique_datetime_path(new_thumbnail))
            renamed += 1
            log(f"renamed picture {path} -> {target}; timestamp source {original}")
        except OSError as error:
            log(f"unable to rename picture {path}: {error}")
    if renamed:
        clean_empty_directories(pictures_dir)
        clean_empty_directories(thumbnails_dir)
    return renamed


def quarantine_pictures_without_thumbnails(order_dir):
    pictures_dir = order_dir / "pictures"
    thumbnails_dir = order_dir / "thumbnails"
    quarantine_dir = order_dir / "failed" / "orphaned_pictures"
    if not pictures_dir.is_dir():
        return 0
    moved = 0
    for path in sorted(pictures_dir.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or is_ignored(path.relative_to(pictures_dir)):
            continue
        relative = path.relative_to(pictures_dir)
        if (thumbnails_dir / relative).is_file():
            continue
        try:
            target = move_file(path, quarantine_dir / relative)
            moved += 1
            log(f"moved picture without thumbnail {path} -> {target}")
        except OSError as error:
            log(f"unable to move picture without thumbnail {path}: {error}")
    if moved:
        clean_empty_directories(pictures_dir)
    return moved


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_duplicate_name(path):
    stem = re.sub(r"\s*\(\d+\)$", "", path.stem).strip().casefold()
    return path.parent, stem, path.suffix.casefold()


def duplicate_keep_rank(path):
    match = re.search(r"\s*\((\d+)\)$", path.stem)
    duplicate_number = int(match.group(1)) if match else 0
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0
    return duplicate_number, modified, path.name.casefold()


def clean_duplicate_pictures(order_dir):
    pictures_dir = order_dir / "pictures"
    thumbnails_dir = order_dir / "thumbnails"
    if not pictures_dir.is_dir():
        return 0
    groups = {}
    for path in pictures_dir.rglob("*"):
        if not path.is_file() or path.name.startswith(".") or is_ignored(path.relative_to(pictures_dir)):
            continue
        groups.setdefault(normalized_duplicate_name(path.relative_to(pictures_dir)), []).append(path)

    removed = 0
    for candidates in groups.values():
        if len(candidates) < 2:
            continue
        by_hash = {}
        for path in candidates:
            try:
                by_hash.setdefault(file_sha256(path), []).append(path)
            except OSError as error:
                log(f"unable to hash picture {path}: {error}")
        for duplicates in by_hash.values():
            if len(duplicates) < 2:
                continue
            keep = sorted(duplicates, key=duplicate_keep_rank)[0]
            for duplicate in duplicates:
                if duplicate == keep:
                    continue
                try:
                    relative = duplicate.relative_to(pictures_dir)
                    duplicate.unlink()
                    (thumbnails_dir / relative).unlink(missing_ok=True)
                    removed += 1
                    log(f"removed duplicate picture {duplicate}; kept {keep}")
                except OSError as error:
                    log(f"unable to remove duplicate picture {duplicate}: {error}")
    if removed:
        clean_empty_directories(pictures_dir)
        clean_empty_directories(thumbnails_dir)
    return removed


def move_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    target = unique_path(target, source)
    shutil.move(str(source), str(target))
    return target


def process_source(order_dir, source):
    relative = source_relative_path(order_dir, source)
    try:
        source.relative_to(order_dir / "processing")
        processing_path = source
    except ValueError:
        processing_path = move_file(source, order_dir / "processing" / relative)
    output_relative = timestamp_output_relative(relative, processing_path)
    output_path = unique_datetime_path(order_dir / "pictures" / output_relative)
    try:
        compress_image(processing_path, output_path)
        processed_relative = output_path.relative_to(order_dir / "pictures")
        create_thumbnail(output_path, order_dir / "thumbnails" / processed_relative)
        backup_path = move_file(processing_path, order_dir / "original_backup" / relative)
        log(f"processed {source} -> {output_path}; backup {backup_path}")
    except Exception as error:
        output_path.unlink(missing_ok=True)
        failed_path = move_file(processing_path, order_dir / "failed" / relative)
        failed_path.with_suffix(f"{failed_path.suffix}.error.txt").write_text(
            f"{datetime.now().isoformat()}\n{type(error).__name__}: {error}\n",
            encoding="utf-8",
        )
        log(f"failed {source}: {error}")


def clean_empty_directories(root):
    if not root.is_dir():
        return
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def clean_backups(order_dir):
    backup_dir = order_dir / "original_backup"
    if not backup_dir.is_dir():
        return
    cutoff = datetime.now() - timedelta(days=BACKUP_DAYS)
    for path in backup_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            if BACKUP_DAYS == 0 or modified < cutoff:
                path.unlink()
        except OSError as error:
            log(f"unable to remove backup {path}: {error}")
    clean_empty_directories(backup_dir)


def run_once():
    processed = 0
    for order_dir in order_directories():
        clean_backups(order_dir)
        clean_duplicate_pictures(order_dir)
        rename_existing_pictures_by_datetime(order_dir)
        quarantine_pictures_without_thumbnails(order_dir)
        for source in [*interrupted_sources(order_dir), *pending_sources(order_dir)]:
            process_source(order_dir, source)
            processed += 1
            if processed >= BATCH_SIZE:
                return processed
    return processed


def main():
    log(
        f"photo worker started root={ROOT} interval={POLL_SECONDS}s "
        f"stable={STABLE_SECONDS}s batch={BATCH_SIZE} backup_days={BACKUP_DAYS}"
    )
    while True:
        try:
            processed = run_once()
            if processed:
                log(f"batch complete: {processed} file(s)")
        except Exception as error:
            log(f"worker cycle failed: {type(error).__name__}: {error}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
