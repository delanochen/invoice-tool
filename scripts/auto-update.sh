#!/bin/sh
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEFAULT_APP_DIR="$(dirname "$SCRIPT_DIR")"
APP_DIR="${INVOICE_TOOL_DIR:-$DEFAULT_APP_DIR}"
BRANCH="${INVOICE_TOOL_BRANCH:-main}"
LOG_FILE="${INVOICE_TOOL_UPDATE_LOG:-$(dirname "$APP_DIR")/invoice-tool-auto-update.log}"
LOCK_DIR="/tmp/invoice-tool-auto-update.lock"
DOCKER="/usr/local/bin/docker"
COMPOSE="/var/packages/ContainerManager/target/usr/bin/docker-compose"
GIT_IMAGE="${INVOICE_TOOL_GIT_IMAGE:-alpine/git:2.49.1}"
EXPECTED_DATA_DIR="$APP_DIR/data"
BACKUP_DIR="${INVOICE_TOOL_BACKUP_DIR:-$(dirname "$APP_DIR")/invoice-tool-db-backups}"
DATA_DIRECTORY_IDENTITY="invoice-tool-primary-volume1-20260816"
DATA_IDENTITY_FILE="$EXPECTED_DATA_DIR/.invoice-tool-data-id"
LEGACY_DATA_DIR="/volume1/docker/invoice-tool/data"
SWITCHED_FROM_LEGACY=0
BACKUP_STAMP="$(date '+%Y%m%d-%H%M%S')"

mkdir -p "$(dirname "$LOG_FILE")"
exec >>"$LOG_FILE" 2>&1

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

finish() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "skip: another update is running"
    exit 0
fi
trap finish EXIT INT TERM

case "$APP_DIR" in
    /volume[0-9]*/docker/invoice-tool)
        ;;
    /volume1/invoice-tool/invoice-tool)
        ;;
    *)
        log "error: unexpected application directory: $APP_DIR"
        exit 1
        ;;
esac
log "application directory: $APP_DIR"
if [ ! -d "$APP_DIR/.git" ] || [ ! -f "$APP_DIR/docker-compose.yml" ] || [ ! -d "$APP_DIR/data" ]; then
    log "error: application checkout is incomplete"
    exit 1
fi
if [ ! -x "$DOCKER" ] || [ ! -x "$COMPOSE" ]; then
    log "error: Container Manager executables are unavailable"
    exit 1
fi

if [ ! -f "$EXPECTED_DATA_DIR/invoices.db" ]; then
    log "error: target database is missing: $EXPECTED_DATA_DIR/invoices.db"
    exit 1
fi
mkdir -p "$BACKUP_DIR"

container_data_dir() {
    "$DOCKER" inspect invoice-tool \
        --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' \
        2>/dev/null || true
}

backup_database() {
    LABEL="$1"
    SOURCE_DIR="$2"
    BACKUP_NAME="invoices-${BACKUP_STAMP}-${LABEL}.db"

    if [ ! -f "$SOURCE_DIR/invoices.db" ]; then
        log "error: database backup source is missing: $SOURCE_DIR/invoices.db"
        return 1
    fi

    log "backing up database ($LABEL): $SOURCE_DIR/invoices.db -> $BACKUP_DIR/$BACKUP_NAME"
    if ! "$DOCKER" run --rm \
        -v "$SOURCE_DIR:/source:ro" \
        -v "$BACKUP_DIR:/backup:rw" \
        --entrypoint python \
        invoice-tool:latest \
        -c 'import json, os, sqlite3, sys
source = sqlite3.connect("file:/source/invoices.db?mode=ro", uri=True)
source_integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
if source_integrity != "ok":
    raise SystemExit("source integrity check failed: " + source_integrity)
target_path = "/backup/" + sys.argv[1]
target = sqlite3.connect(target_path)
source.backup(target)
target.commit()
target_integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
if target_integrity != "ok":
    raise SystemExit("backup integrity check failed: " + target_integrity)
print(json.dumps({"backup": target_path, "bytes": os.path.getsize(target_path), "integrity": target_integrity}))
source.close()
target.close()' \
        "$BACKUP_NAME"; then
        log "error: database backup failed for $SOURCE_DIR"
        return 1
    fi
    if [ ! -s "$BACKUP_DIR/$BACKUP_NAME" ]; then
        log "error: database backup is empty: $BACKUP_DIR/$BACKUP_NAME"
        return 1
    fi
}

recover_catastrophic_parent_loss() {
    log "checking the production database for catastrophic parent-record loss"
    "$DOCKER" run --rm \
        -v "$EXPECTED_DATA_DIR:/target:rw" \
        -v "$BACKUP_DIR:/backups:rw" \
        --entrypoint python \
        invoice-tool:latest \
        -c 'import glob, json, os, sqlite3, sys

TABLES = ("service_orders", "service_reports", "expenses", "invoices")

def inspect(path):
    db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    try:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            return None
        counts = {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in TABLES}
        return counts
    except sqlite3.DatabaseError:
        return None
    finally:
        db.close()

target_path = "/target/invoices.db"
current = inspect(target_path)
if current is None:
    raise SystemExit("production database integrity/schema check failed")

catastrophic = current["service_orders"] == 0 and any(
    current[table] > 0 for table in ("service_reports", "expenses", "invoices")
)
if not catastrophic:
    print(json.dumps({"recovery": "not-required", "current": current}))
    raise SystemExit(0)

candidates = []
for path in glob.glob("/backups/invoices-*.db"):
    counts = inspect(path)
    if counts is None or counts["service_orders"] <= 0:
        continue
    if counts["service_reports"] < current["service_reports"]:
        continue
    if counts["expenses"] < current["expenses"]:
        continue
    candidates.append((
        counts["service_reports"], counts["expenses"], counts["service_orders"],
        counts["invoices"], os.path.getmtime(path), path, counts,
    ))

if not candidates:
    raise SystemExit("catastrophic parent-record loss detected, but no complete backup is eligible")

_, _, _, _, _, source_path, source_counts = max(candidates)
source = sqlite3.connect("file:" + source_path + "?mode=ro", uri=True)
# sqlite3 backup writes through SQLite itself, so the running application can
# finish readers safely; the compose reconciliation immediately afterward
# recreates the application connection against the recovered database.
target = sqlite3.connect(target_path, timeout=30)
source.backup(target)
target.commit()
target.close()
source.close()
recovered = inspect(target_path)
if recovered != source_counts:
    raise SystemExit("recovered database verification failed")
print(json.dumps({
    "recovery": "restored",
    "broken": current,
    "backup": source_path,
    "recovered": recovered,
}))'
}

validate_reviewed_legacy_switch() {
    if [ "$RUNNING_DATA_DIR" != "$LEGACY_DATA_DIR" ] || [ "$EXPECTED_DATA_DIR" != "/volume1/invoice-tool/invoice-tool/data" ]; then
        return 1
    fi
    log "validating the reviewed legacy database correction"
    "$DOCKER" run --rm \
        -v "$RUNNING_DATA_DIR:/running:ro" \
        -v "$EXPECTED_DATA_DIR:/target:ro" \
        --entrypoint python \
        invoice-tool:latest \
        -c 'import json, sqlite3
def inspect(path):
    db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit(path + " integrity check failed: " + integrity)
    result = {
        "reports": db.execute("SELECT COUNT(*) FROM service_reports").fetchone()[0],
        "expenses": db.execute("SELECT COUNT(*) FROM expenses").fetchone()[0],
    }
    db.close()
    return result
running = inspect("/running/invoices.db")
target = inspect("/target/invoices.db")
target_db = sqlite3.connect("file:/target/invoices.db?mode=ro", uri=True)
so2608006_reports = target_db.execute(
    "SELECT COUNT(*) FROM service_reports JOIN service_orders ON service_orders.id = service_reports.service_order_id WHERE service_orders.order_number = ?",
    ("SO2608006",),
).fetchone()[0]
target_db.close()
if target["reports"] < running["reports"] or target["expenses"] < running["expenses"]:
    raise SystemExit("target database has fewer business records than the running legacy database")
if so2608006_reports != 4:
    raise SystemExit("target database does not contain exactly four SO2608006 daily reports")
print(json.dumps({"running": running, "target": target, "SO2608006_reports": so2608006_reports}))'
}

retire_legacy_database() {
    if [ "$SWITCHED_FROM_LEGACY" != "1" ] && [ -f "$LEGACY_DATA_DIR/invoices.db" ]; then
        ACTIVE_DATA_DIR="$(container_data_dir)"
        if [ "$ACTIVE_DATA_DIR" != "$EXPECTED_DATA_DIR" ]; then
            return 0
        fi
        ORIGINAL_RUNNING_DATA_DIR="$RUNNING_DATA_DIR"
        RUNNING_DATA_DIR="$LEGACY_DATA_DIR"
        if ! validate_reviewed_legacy_switch; then
            RUNNING_DATA_DIR="$ORIGINAL_RUNNING_DATA_DIR"
            log "error: refusing to retire the legacy database because validation failed"
            return 1
        fi
        RUNNING_DATA_DIR="$ORIGINAL_RUNNING_DATA_DIR"
    fi
    if [ -f "$LEGACY_DATA_DIR/invoices.db" ]; then
        RETIRED_DB="$LEGACY_DATA_DIR/invoices.db.retired-$BACKUP_STAMP"
        mv "$LEGACY_DATA_DIR/invoices.db" "$RETIRED_DB"
        log "retired legacy database: $RETIRED_DB"
    fi
    if [ -f "$APP_DIR/.env" ] && grep -q '^DATA_HOST_DIR=' "$APP_DIR/.env"; then
        sed '/^DATA_HOST_DIR=/d' "$APP_DIR/.env" > "$APP_DIR/.env.tmp"
        mv "$APP_DIR/.env.tmp" "$APP_DIR/.env"
        log "removed obsolete DATA_HOST_DIR from .env"
    fi
}

prepare_database_for_deploy() {
    RUNNING_DATA_DIR="$(container_data_dir)"
    if [ -n "$RUNNING_DATA_DIR" ]; then
        log "running container data directory: $RUNNING_DATA_DIR"
        backup_database running "$RUNNING_DATA_DIR" || return 1
    else
        log "running container data directory: unavailable; backing up target database only"
    fi

    # Recover only an unmistakable corruption pattern: all parent work orders
    # vanished while dependent business rows still exist. The broken database
    # has already been preserved above, and a backup is accepted only when it
    # contains at least all current reports and expenses.
    recover_catastrophic_parent_loss || return 1

    if [ "$RUNNING_DATA_DIR" != "$EXPECTED_DATA_DIR" ]; then
        backup_database target "$EXPECTED_DATA_DIR" || return 1
        if validate_reviewed_legacy_switch; then
            SWITCHED_FROM_LEGACY=1
            log "automatically approved the validated legacy database correction"
        else
            log "error: refusing database path switch: ${RUNNING_DATA_DIR:-missing} -> $EXPECTED_DATA_DIR"
            return 1
        fi
        log "approved database path switch: ${RUNNING_DATA_DIR:-missing} -> $EXPECTED_DATA_DIR"
    fi

    if [ -f "$DATA_IDENTITY_FILE" ]; then
        STORED_DATA_IDENTITY="$(tr -d '\r\n' < "$DATA_IDENTITY_FILE")"
        if [ "$STORED_DATA_IDENTITY" != "$DATA_DIRECTORY_IDENTITY" ]; then
            log "error: target data directory has an unexpected identity marker"
            return 1
        fi
    else
        umask 077
        printf '%s\n' "$DATA_DIRECTORY_IDENTITY" > "$DATA_IDENTITY_FILE"
        log "created target data directory identity marker"
    fi

    export DATA_HOST_DIR="$EXPECTED_DATA_DIR"
}

verify_database_mount() {
    ACTUAL_DATA_DIR="$(container_data_dir)"
    if [ "$ACTUAL_DATA_DIR" != "$EXPECTED_DATA_DIR" ]; then
        log "error: container data mount mismatch after deploy: ${ACTUAL_DATA_DIR:-missing} != $EXPECTED_DATA_DIR"
        return 1
    fi
    log "verified container data mount: $ACTUAL_DATA_DIR"

    if ! "$DOCKER" exec invoice-tool python -c 'import json, sqlite3
db = sqlite3.connect("/app/data/invoices.db")
result = {
    "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
    "service_reports": db.execute("SELECT COUNT(*) FROM service_reports").fetchone()[0],
    "expenses": db.execute("SELECT COUNT(*) FROM expenses").fetchone()[0],
}
print(json.dumps(result))
raise SystemExit(0 if result["integrity"] == "ok" else 1)'; then
        log "error: deployed database verification failed"
        return 1
    fi
}

wait_for_health() {
    attempt=1
    while [ "$attempt" -le 30 ]; do
        HTTP_CODE="$("$CURL" -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/ 2>/dev/null || true)"
        if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ]; then
            log "health check passed (HTTP $HTTP_CODE)"
            return 0
        fi
        sleep 2
        attempt=$((attempt + 1))
    done
    log "error: health check failed"
    return 1
}

GIT_BIN="$(command -v git || true)"
CURL="$(command -v curl || true)"
if [ -z "$CURL" ]; then
    log "error: curl is unavailable"
    exit 1
fi

if [ -z "$GIT_BIN" ]; then
    if [ -x /var/packages/Git/target/bin/git ]; then
        GIT_BIN=/var/packages/Git/target/bin/git
    elif ! "$DOCKER" image inspect "$GIT_IMAGE" >/dev/null 2>&1; then
        log "preparing containerized git: $GIT_IMAGE"
        if ! "$DOCKER" pull "$GIT_IMAGE"; then
            log "error: unable to pull containerized git"
            exit 1
        fi
    fi
fi

git_repo() {
    if [ -n "$GIT_BIN" ]; then
        "$GIT_BIN" -c "safe.directory=$APP_DIR" "$@"
    else
        "$DOCKER" run --rm \
            -v "$APP_DIR:/repo" \
            -w /repo \
            "$GIT_IMAGE" \
            -c safe.directory=/repo "$@"
    fi
}

cd "$APP_DIR"

CONFIGURED_SHARED_PHOTOS=""
if [ -f "$APP_DIR/.env" ]; then
    CONFIGURED_SHARED_PHOTOS="$(sed -n 's/^SHARED_PHOTOS_HOST_DIR=//p' "$APP_DIR/.env" | tail -n 1)"
fi

if [ -z "$CONFIGURED_SHARED_PHOTOS" ] || [ ! -d "$CONFIGURED_SHARED_PHOTOS" ]; then
    VOLUME1_PHOTOS="/volume1/TeamFolder/PrasinosPower/甲方-三河同飞制冷股份有限公司/pictures"
    VOLUME2_PHOTOS="/volume2/TeamFolder/PrasinosPower/甲方-三河同飞制冷股份有限公司/pictures"
    PROJECT_SHARED_PHOTOS="$APP_DIR/shared-photos"
    PROJECT_PICTURES="$APP_DIR/pictures"

    has_order_folders() {
        for order_folder in "$1"/SO*; do
            if [ -d "$order_folder" ]; then
                return 0
            fi
        done
        return 1
    }

    SHARED_PHOTOS_HOST_DIR=""
    for candidate in "$VOLUME1_PHOTOS" "$PROJECT_SHARED_PHOTOS" "$PROJECT_PICTURES" "$VOLUME2_PHOTOS"; do
        if has_order_folders "$candidate"; then
            SHARED_PHOTOS_HOST_DIR="$candidate"
            break
        fi
    done
    if [ -z "$SHARED_PHOTOS_HOST_DIR" ]; then
        for candidate in "$VOLUME1_PHOTOS" "$PROJECT_SHARED_PHOTOS" "$PROJECT_PICTURES" "$VOLUME2_PHOTOS"; do
            if [ -d "$candidate" ]; then
                SHARED_PHOTOS_HOST_DIR="$candidate"
                break
            fi
        done
    fi
    SHARED_PHOTOS_HOST_DIR="${SHARED_PHOTOS_HOST_DIR:-$VOLUME1_PHOTOS}"
    export SHARED_PHOTOS_HOST_DIR
    log "shared photos auto-detected: $SHARED_PHOTOS_HOST_DIR"
else
    log "shared photos path loaded from .env: $CONFIGURED_SHARED_PHOTOS"
fi

REMOTE_URL="$(git_repo remote get-url origin 2>/dev/null || true)"
case "$REMOTE_URL" in
    https://github.com/delanochen/invoice-tool.git|git@github.com:delanochen/invoice-tool.git)
        ;;
    *)
        log "error: refusing unexpected git remote: $REMOTE_URL"
        exit 1
        ;;
esac

if ! git_repo diff --quiet || ! git_repo diff --cached --quiet; then
    log "skip: tracked local changes must be reviewed before automatic updates"
    exit 1
fi

log "checking origin/$BRANCH"
if ! git_repo fetch --prune origin "$BRANCH"; then
    log "error: git fetch failed"
    exit 1
fi

OLD_COMMIT="$(git_repo rev-parse HEAD)"
NEW_COMMIT="$(git_repo rev-parse "origin/$BRANCH")"

version_from_commit() {
    VERSION_VALUE="$(git_repo show "$1:VERSION" 2>/dev/null | tr -d '\r\n' || true)"
    if printf '%s' "$VERSION_VALUE" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
        printf '%s' "$VERSION_VALUE"
    else
        printf '%s' '0.0.0'
    fi
}

OLD_VERSION="$(version_from_commit "$OLD_COMMIT")"
NEW_VERSION="$(version_from_commit "$NEW_COMMIT")"

if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
    log "up-to-date: $OLD_VERSION; reconciling containers"
    if ! prepare_database_for_deploy; then
        exit 1
    fi
    RUNNING_VERSION="$("$DOCKER" inspect invoice-tool \
        --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | sed -n 's/^APP_VERSION=//p' \
        | tail -n 1)"
    if [ "$RUNNING_VERSION" != "$OLD_VERSION" ]; then
        log "container version mismatch: ${RUNNING_VERSION:-missing} -> $OLD_VERSION; rebuilding"
        if ! APP_VERSION="$OLD_VERSION" "$COMPOSE" \
            -f "$APP_DIR/docker-compose.yml" \
            --env-file "$APP_DIR/.env" \
            build invoice-tool photo-worker; then
            log "error: image rebuild failed"
            exit 1
        fi
    fi
    if ! APP_VERSION="$OLD_VERSION" "$COMPOSE" \
        -f "$APP_DIR/docker-compose.yml" \
        --env-file "$APP_DIR/.env" \
        up -d --remove-orphans; then
        log "error: unable to reconcile containers"
        exit 1
    fi
    if ! verify_database_mount || ! wait_for_health; then
        exit 1
    fi
    retire_legacy_database || exit 1
    log "success: running $OLD_VERSION"
    exit 0
fi

OLD_IMAGE_ID="$("$DOCKER" image inspect invoice-tool:latest --format '{{.Id}}' 2>/dev/null || true)"
if [ -n "$OLD_IMAGE_ID" ]; then
    "$DOCKER" tag "$OLD_IMAGE_ID" invoice-tool:rollback
fi

rollback() {
    log "rollback: restoring $OLD_VERSION"
    git_repo reset --hard "$OLD_COMMIT" || true
    if [ -n "$OLD_IMAGE_ID" ]; then
        "$DOCKER" tag invoice-tool:rollback invoice-tool:latest || true
        APP_VERSION="$OLD_VERSION" "$COMPOSE" \
            -f "$APP_DIR/docker-compose.yml" \
            --env-file "$APP_DIR/.env" \
            up -d --no-build --remove-orphans || true
    fi
}

if ! git_repo merge --ff-only "origin/$BRANCH"; then
    log "error: fast-forward update failed"
    exit 1
fi

if ! prepare_database_for_deploy; then
    git_repo reset --hard "$OLD_COMMIT" || true
    exit 1
fi

log "building: $OLD_VERSION -> $NEW_VERSION"
if ! APP_VERSION="$NEW_VERSION" "$COMPOSE" \
    -f "$APP_DIR/docker-compose.yml" \
    --env-file "$APP_DIR/.env" \
    build invoice-tool photo-worker; then
    log "error: image build failed"
    rollback
    exit 1
fi

if ! APP_VERSION="$NEW_VERSION" "$COMPOSE" \
    -f "$APP_DIR/docker-compose.yml" \
    --env-file "$APP_DIR/.env" \
    up -d --remove-orphans; then
    log "error: container update failed"
    rollback
    exit 1
fi

if verify_database_mount && wait_for_health; then
    retire_legacy_database || exit 1
    log "success: running $NEW_VERSION"
    exit 0
fi
rollback
exit 1
