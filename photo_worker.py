import hashlib
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

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
    output_relative = relative.with_suffix(".jpg")
    output_path = unique_path(order_dir / "pictures" / output_relative, processing_path)
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
