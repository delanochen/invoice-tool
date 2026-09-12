#!/bin/sh
set -eu

APP_DIR="${INVOICE_TOOL_DIR:-/opt/invoice-tool}"
STATE_DIR="${INVOICE_TOOL_STATE_DIR:-/srv/invoice-tool}"
BACKUP_DIR="${INVOICE_TOOL_BACKUP_DIR:-$STATE_DIR/backups}"
LOCK_DIR="/run/invoice-tool-auto-deploy.lock"
HEALTH_URL="${INVOICE_TOOL_HEALTH_URL:-http://127.0.0.1:8088/}"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
env_value() {
  key="$1"
  if [ -f "$APP_DIR/.env" ]; then
    sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?${key}[[:space:]]*=[[:space:]]*//p" "$APP_DIR/.env" |
      tail -n 1 | sed -e 's/[[:space:]]*#.*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
  fi
}
effective_data_dir() {
  if [ -n "${DATA_HOST_DIR:-}" ]; then
    printf '%s\n' "$DATA_HOST_DIR"
    return
  fi
  value="$(env_value DATA_HOST_DIR)"
  if [ -n "$value" ]; then
    printf '%s\n' "$value"
  else
    compose_value="$(sed -n 's/.*${DATA_HOST_DIR:-\([^}]*\)}:.*/\1/p' "$APP_DIR/docker-compose.yml" 2>/dev/null | head -n 1)"
    if [ -n "$compose_value" ]; then
      printf '%s\n' "$compose_value"
    else
      printf '%s\n' '/srv/invoice-tool/data'
    fi
  fi
}
normalize_path() {
  path="$1"
  [ -n "$path" ] || return 1
  case "$path" in
    /*) ;;
    *) path="$APP_DIR/$path" ;;
  esac
  [ -e "$path" ] || return 1
  normalized="$(readlink -f -- "$path" 2>/dev/null)" || return 1
  [ -n "$normalized" ] || return 1
  printf '%s\n' "$normalized"
}
running_data_source() {
  mounts="$1"
  match="$(printf '%s\n' "$mounts" | awk -F '\t' '
    $1 == "/app/data" {
      count++
      if (count == 1) {
        print $2 "\t" $3
      }
    }
    END {
      if (count != 1) {
        exit 1
      }
    }')" || return 1
  mount_type="$(printf '%s\n' "$match" | awk -F '\t' '{print $1}')"
  source="$(printf '%s\n' "$match" | cut -f 2-)"
  [ "$mount_type" = "bind" ] || return 1
  [ -n "$source" ] || return 1
  printf '%s\n' "$source"
}
prepare_database_for_deploy() {
  effective_dir="$(effective_data_dir)"
  expected_dir="$(normalize_path "$effective_dir")" || {
    log "error: effective DATA_HOST_DIR cannot be normalized: ${effective_dir:-missing}"
    return 1
  }
  allowed_dir="$(normalize_path "$STATE_DIR/data")" || {
    log "error: Debian data directory cannot be normalized: $STATE_DIR/data"
    return 1
  }
  if [ "$expected_dir" != "$allowed_dir" ]; then
    log "error: DATA_HOST_DIR is outside the Debian data directory: $expected_dir != $allowed_dir"
    return 1
  fi
  if [ "$(docker inspect invoice-tool --format '{{.State.Running}}' 2>/dev/null || true)" != "true" ]; then
    log "error: invoice-tool container is missing or not running"
    return 1
  fi
  mounts="$(docker inspect invoice-tool \
    --format '{{range .Mounts}}{{printf "%s\t%s\t%s\n" .Destination .Type .Source}}{{end}}' \
    2>/dev/null)" || {
    log "error: unable to inspect the running invoice-tool container"
    return 1
  }
  actual_dir="$(running_data_source "$mounts")" || {
    log "error: running invoice-tool container has no unique bind mount at /app/data"
    return 1
  }
  actual_dir="$(normalize_path "$actual_dir")" || {
    log "error: actual /app/data source cannot be normalized"
    return 1
  }
  if [ "$actual_dir" != "$expected_dir" ] || [ "$actual_dir" != "$allowed_dir" ]; then
    log "error: actual /app/data source mismatch: $actual_dir"
    return 1
  fi
  database="$actual_dir/invoices.db"
  if [ ! -f "$database" ] || [ ! -s "$database" ]; then
    log "error: production database is missing or empty: $database"
    return 1
  fi
  if ! docker compose exec -T invoice-tool python - "$database" <<'PY'
import sqlite3
import sys

db = sqlite3.connect("file:/app/data/invoices.db?mode=ro", uri=True)
try:
    result = db.execute("PRAGMA integrity_check").fetchone()[0]
    print("database_integrity: " + result)
    raise SystemExit(0 if result == "ok" else 1)
finally:
    db.close()
PY
  then
    log "error: production database integrity check failed"
    return 1
  fi
  BACKUP_SOURCE_DIR="$actual_dir"
  return 0
}
backup_database() {
  stamp="$1"
  temp_path="$(mktemp "$BACKUP_SOURCE_DIR/.pre-deploy-$stamp.XXXXXX.db")" || {
    log "error: unable to allocate a temporary database backup"
    return 1
  }
  trap 'rm -f "$temp_path"; cleanup' EXIT INT TERM
  if ! docker compose exec -T invoice-tool python - "$(basename "$temp_path")" <<'PY'
import sqlite3
import sys

target_name = sys.argv[1]
source = sqlite3.connect("file:/app/data/invoices.db?mode=ro", uri=True)
target = sqlite3.connect("/app/data/" + target_name)
try:
    source.backup(target)
    target.commit()
    integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit("backup integrity check failed: " + integrity)
finally:
    source.close()
    target.close()
PY
  then
    log "error: database backup or backup integrity check failed"
    rm -f "$temp_path"
    return 1
  fi
  backup_path="$BACKUP_DIR/invoices-$stamp.db"
  if [ -e "$backup_path" ]; then
    log "error: refusing to overwrite existing database backup: $backup_path"
    rm -f "$temp_path"
    return 1
  fi
  mv -n "$temp_path" "$backup_path" || {
    log "error: unable to move database backup into place"
    rm -f "$temp_path"
    return 1
  }
  if [ -e "$temp_path" ]; then
    log "error: refusing to overwrite existing database backup: $backup_path"
    rm -f "$temp_path"
    return 1
  fi
  if [ ! -s "$backup_path" ]; then
    log "error: database backup is missing or empty"
    rm -f "$backup_path"
    return 1
  fi
  if command -v python3 >/dev/null 2>&1; then
    if ! python3 - "$backup_path" <<'PY'
import sqlite3
import sys

path = sys.argv[1]
db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
try:
    result = db.execute("PRAGMA integrity_check").fetchone()[0]
    raise SystemExit(0 if result == "ok" else 1)
finally:
    db.close()
PY
    then
      log "error: final database backup integrity check failed"
      rm -f "$backup_path"
      return 1
    fi
  else
    log "error: python3 is unavailable for final database backup integrity check"
    rm -f "$backup_path"
    return 1
  fi
  trap cleanup EXIT INT TERM
  return 0
}
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
OLD_COMMIT="$(git rev-parse HEAD)"
STAMP="$(date +%Y%m%d-%H%M%S)"
prepare_database_for_deploy || exit 1
mkdir -p "$BACKUP_DIR"
backup_database "$STAMP" || exit 1

git fetch --quiet origin main
NEW_COMMIT="$(git rev-parse origin/main)"
if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
  log "already current: $OLD_COMMIT"
  exit 0
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
