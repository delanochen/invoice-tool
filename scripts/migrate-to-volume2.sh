#!/bin/sh
set -u

SOURCE_DIR="${INVOICE_TOOL_SOURCE_DIR:-/volume1/docker/invoice-tool}"
TARGET_DIR="${INVOICE_TOOL_TARGET_DIR:-/volume2/docker/invoice-tool}"
DOCKER="/usr/local/bin/docker"
COMPOSE="/var/packages/ContainerManager/target/usr/bin/docker-compose"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
TARGET_PARENT="$(dirname "$TARGET_DIR")"
STAGE_DIR="$TARGET_PARENT/.invoice-tool-migrating-$TIMESTAMP"
TARGET_BACKUP="$TARGET_PARENT/invoice-tool-before-migration-$TIMESTAMP"
FAILED_TARGET="$TARGET_PARENT/invoice-tool-failed-migration-$TIMESTAMP"
SOURCE_BACKUP="$(dirname "$SOURCE_DIR")/invoice-tool-migrated-$TIMESTAMP"
LOG_FILE="$TARGET_PARENT/invoice-tool-volume-migration.log"
TARGET_STARTED=0
TARGET_ACTIVATED=0

mkdir -p "$TARGET_PARENT"
exec >>"$LOG_FILE" 2>&1

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

compose() {
    app_dir="$1"
    shift
    version="$(tr -d '\r\n' < "$app_dir/VERSION")"
    APP_VERSION="$version" "$COMPOSE" \
        -f "$app_dir/docker-compose.yml" \
        --env-file "$app_dir/.env" \
        "$@"
}

cleanup_stage() {
    if [ -d "$STAGE_DIR" ]; then
        rm -rf "$STAGE_DIR"
    fi
}

fail() {
    log "error: $*"
    if [ "$TARGET_STARTED" -eq 1 ]; then
        compose "$TARGET_DIR" down >/dev/null 2>&1 || true
    fi
    if [ "$TARGET_ACTIVATED" -eq 1 ] && [ -d "$TARGET_DIR" ]; then
        mv "$TARGET_DIR" "$FAILED_TARGET" || true
    fi
    if [ -d "$TARGET_BACKUP" ] && [ ! -e "$TARGET_DIR" ]; then
        mv "$TARGET_BACKUP" "$TARGET_DIR" || true
    fi
    if [ -d "$SOURCE_DIR" ]; then
        compose "$SOURCE_DIR" up -d --remove-orphans >/dev/null 2>&1 || true
    fi
    cleanup_stage
    exit 1
}

case "$SOURCE_DIR" in
    /volume1/docker/invoice-tool) ;;
    *) log "error: unexpected source directory: $SOURCE_DIR"; exit 1 ;;
esac
case "$TARGET_DIR" in
    /volume2/docker/invoice-tool) ;;
    *) log "error: unexpected target directory: $TARGET_DIR"; exit 1 ;;
esac

log "migration requested: $SOURCE_DIR -> $TARGET_DIR"

if [ ! -d "$SOURCE_DIR/.git" ] || [ ! -f "$SOURCE_DIR/docker-compose.yml" ] \
   || [ ! -f "$SOURCE_DIR/.env" ] || [ ! -f "$SOURCE_DIR/data/invoices.db" ]; then
    fail "source checkout or database is incomplete"
fi
if [ ! -x "$DOCKER" ] || [ ! -x "$COMPOSE" ] || ! command -v curl >/dev/null 2>&1; then
    fail "Container Manager executables are unavailable"
fi

DATA_MOUNT="$($DOCKER inspect invoice-tool \
    --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' \
    2>/dev/null || true)"
if [ "$DATA_MOUNT" = "$TARGET_DIR/data" ]; then
    log "already migrated: invoice-tool uses $DATA_MOUNT"
    exit 0
fi
if [ -n "$DATA_MOUNT" ] && [ "$DATA_MOUNT" != "$SOURCE_DIR/data" ]; then
    fail "running container uses unexpected data directory: $DATA_MOUNT"
fi

cleanup_stage
mkdir -p "$STAGE_DIR" || fail "cannot create staging directory"
log "copying application, database and uploads to Volume2"
cp -a "$SOURCE_DIR/." "$STAGE_DIR/" || fail "copy to staging directory failed"

if [ ! -d "$STAGE_DIR/.git" ] || [ ! -f "$STAGE_DIR/docker-compose.yml" ] \
   || [ ! -s "$STAGE_DIR/data/invoices.db" ]; then
    fail "staged copy verification failed"
fi

log "stopping Volume1 deployment"
compose "$SOURCE_DIR" down || fail "failed to stop Volume1 deployment"

log "refreshing database and uploads after shutdown"
rm -rf "$STAGE_DIR/data" || fail "failed to clear staged data directory"
cp -a "$SOURCE_DIR/data" "$STAGE_DIR/data" || fail "final data copy failed"
SOURCE_DB_SIZE="$(wc -c < "$SOURCE_DIR/data/invoices.db" | tr -d ' ')"
TARGET_DB_SIZE="$(wc -c < "$STAGE_DIR/data/invoices.db" | tr -d ' ')"
if [ "$SOURCE_DB_SIZE" != "$TARGET_DB_SIZE" ]; then
    fail "database size verification failed"
fi

if [ -e "$TARGET_DIR" ]; then
    log "preserving previous Volume2 folder as $TARGET_BACKUP"
    mv "$TARGET_DIR" "$TARGET_BACKUP" || fail "failed to preserve previous Volume2 folder"
fi
mv "$STAGE_DIR" "$TARGET_DIR" || fail "failed to activate Volume2 folder"
TARGET_ACTIVATED=1

log "starting deployment from Volume2"
compose "$TARGET_DIR" up -d --build --remove-orphans || fail "Volume2 deployment failed to start"
TARGET_STARTED=1

attempt=1
while [ "$attempt" -le 45 ]; do
    HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/ 2>/dev/null || true)"
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ]; then
        break
    fi
    sleep 2
    attempt=$((attempt + 1))
done
if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "302" ]; then
    fail "Volume2 health check failed"
fi

NEW_DATA_MOUNT="$($DOCKER inspect invoice-tool \
    --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' \
    2>/dev/null || true)"
if [ "$NEW_DATA_MOUNT" != "$TARGET_DIR/data" ]; then
    fail "container data mount did not switch to Volume2: $NEW_DATA_MOUNT"
fi

log "preserving Volume1 checkout as $SOURCE_BACKUP"
mv "$SOURCE_DIR" "$SOURCE_BACKUP" || fail "failed to preserve Volume1 checkout"
mkdir -p "$SOURCE_DIR/scripts" || fail "failed to create compatibility launcher directory"
cat > "$SOURCE_DIR/scripts/auto-update.sh" <<'EOF'
#!/bin/sh
exec env \
  INVOICE_TOOL_DIR=/volume2/docker/invoice-tool \
  INVOICE_TOOL_UPDATE_LOG=/volume2/docker/invoice-tool-auto-update.log \
  /bin/sh /volume2/docker/invoice-tool/scripts/auto-update.sh
EOF
chmod 755 "$SOURCE_DIR/scripts/auto-update.sh"

log "success: website now runs from $TARGET_DIR (HTTP $HTTP_CODE)"
log "existing scheduled command remains compatible; preferred command: /bin/sh $TARGET_DIR/scripts/auto-update.sh"
log "Volume1 backup retained at $SOURCE_BACKUP"
if [ -d "$TARGET_BACKUP" ]; then
    log "previous Volume2 folder retained at $TARGET_BACKUP"
fi
