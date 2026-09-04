#!/bin/sh
set -eu

APP_DIR="${INVOICE_TOOL_DIR:-/opt/invoice-tool}"
STATE_DIR="${INVOICE_TOOL_STATE_DIR:-/srv/invoice-tool}"
BACKUP_DIR="${INVOICE_TOOL_BACKUP_DIR:-$STATE_DIR/backups}"
LOCK_DIR="/run/invoice-tool-auto-deploy.lock"
HEALTH_URL="${INVOICE_TOOL_HEALTH_URL:-http://127.0.0.1:8088/}"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
build_current_version() {
  APP_VERSION="$(tr -d '\r\n' < VERSION)"
  if ! printf '%s' "$APP_VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    log "invalid release VERSION: $APP_VERSION"
    return 1
  fi
  export APP_VERSION
  docker compose up -d --build
}
trap cleanup EXIT INT TERM

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "another deployment is already running"
  exit 0
fi

cd "$APP_DIR"
git fetch --quiet origin main
OLD_COMMIT="$(git rev-parse HEAD)"
NEW_COMMIT="$(git rev-parse origin/main)"
if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
  log "already current: $OLD_COMMIT"
  exit 0
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
if [ -f "$STATE_DIR/data/invoices.db" ]; then
  docker compose exec -T invoice-tool python -c "import sqlite3; s=sqlite3.connect('/app/data/invoices.db'); d=sqlite3.connect('/app/data/pre-deploy-$STAMP.db'); s.backup(d); d.close(); s.close()"
  mv "$STATE_DIR/data/pre-deploy-$STAMP.db" "$BACKUP_DIR/invoices-$STAMP.db"
fi

log "deploying $NEW_COMMIT"
git reset --hard "$NEW_COMMIT"
if build_current_version && wait_ok=0; then
  i=0
  while [ "$i" -lt 30 ]; do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null; then wait_ok=1; break; fi
    i=$((i + 1)); sleep 2
  done
  [ "$wait_ok" -eq 1 ] && { log "deployment healthy"; exit 0; }
fi

log "deployment failed; rolling back to $OLD_COMMIT"
git reset --hard "$OLD_COMMIT"
build_current_version
exit 1
