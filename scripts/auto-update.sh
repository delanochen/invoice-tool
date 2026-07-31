#!/bin/sh
set -u

APP_DIR="${INVOICE_TOOL_DIR:-/volume1/docker/invoice-tool}"
BRANCH="${INVOICE_TOOL_BRANCH:-main}"
LOG_FILE="${INVOICE_TOOL_UPDATE_LOG:-/volume1/docker/invoice-tool-auto-update.log}"
LOCK_DIR="/tmp/invoice-tool-auto-update.lock"
DOCKER="/usr/local/bin/docker"
COMPOSE="/var/packages/ContainerManager/target/usr/bin/docker-compose"
GIT_IMAGE="${INVOICE_TOOL_GIT_IMAGE:-alpine/git:2.49.1}"

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

if [ "$APP_DIR" != "/volume1/docker/invoice-tool" ]; then
    log "error: unexpected application directory: $APP_DIR"
    exit 1
fi
if [ ! -d "$APP_DIR/.git" ] || [ ! -f "$APP_DIR/docker-compose.yml" ] || [ ! -d "$APP_DIR/data" ]; then
    log "error: application checkout is incomplete"
    exit 1
fi
if [ ! -x "$DOCKER" ] || [ ! -x "$COMPOSE" ]; then
    log "error: Container Manager executables are unavailable"
    exit 1
fi

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
        "$GIT_BIN" "$@"
    else
        "$DOCKER" run --rm \
            -v "$APP_DIR:/repo" \
            -w /repo \
            "$GIT_IMAGE" \
            -c safe.directory=/repo "$@"
    fi
}

cd "$APP_DIR"
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
OLD_VERSION="$(git_repo rev-parse --short=12 "$OLD_COMMIT")"
NEW_VERSION="$(git_repo rev-parse --short=12 "$NEW_COMMIT")"

if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
    log "up-to-date: $OLD_VERSION"
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

attempt=1
while [ "$attempt" -le 30 ]; do
    HTTP_CODE="$("$CURL" -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/ 2>/dev/null || true)"
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ]; then
        log "success: running $NEW_VERSION (HTTP $HTTP_CODE)"
        exit 0
    fi
    sleep 2
    attempt=$((attempt + 1))
done

log "error: health check failed"
rollback
exit 1
