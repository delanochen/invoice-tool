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
configure_database_backend() {
  backend="${INVOICE_DATABASE_BACKEND:-$(env_value INVOICE_DATABASE_BACKEND)}"
  backend="${backend:-postgresql}"
  [ "$backend" = "postgresql" ] || { log "error: PostgreSQL is the only supported production backend"; return 1; }
  if ! docker exec invoice-tool python -c 'import os,sys; sys.exit(0 if os.environ.get("DATABASE_URL", "").startswith(("postgresql://", "postgres://")) else 1)'; then
    log "error: running application is not configured for PostgreSQL"
    return 1
  fi
  PG_DATABASE="${POSTGRES_DATABASE:-$(env_value POSTGRES_DATABASE)}"
  [ -n "$PG_DATABASE" ] || { log "error: PostgreSQL database name is required"; return 1; }
  [ -f "$APP_DIR/deploy/docker-compose.postgresql.yml" ] || return 1
  python3 "$APP_DIR/scripts/check_postgresql_runtime.py" --database "$PG_DATABASE" || return 1
}
compose() {
  docker compose -f "$APP_DIR/docker-compose.yml" -f "$APP_DIR/deploy/docker-compose.postgresql.yml" "$@"
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
  return 0
}
backup_database() {
  stamp="$1"
  python3 "$APP_DIR/scripts/backup_postgresql.py" --container invoice-tool-postgres \
    --database "$PG_DATABASE" --output "$BACKUP_DIR/invoices-$stamp.dump"
}
upgrade_postgresql_schema() {
  # Additive, idempotent schema upgrade for PostgreSQL. Runs on EVERY deploy
  # attempt (before the "already current" shortcut) so a code release that
  # needs new schema objects is never deployed ahead of its schema: the very
  # next timer run after the upgrade lands applies it, even if that run has
  # nothing new to pull. Failure aborts the deploy; the database keeps the
  # verified 0252 baseline (psql runs in a single transaction).
  python3 "$APP_DIR/scripts/upgrade_postgresql.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0281.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0282.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0283.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0284.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0285.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0286.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0287.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0288.py" --database "$PG_DATABASE" || return 1
  python3 "$APP_DIR/scripts/upgrade_postgresql_0289.py" --database "$PG_DATABASE" || return 1
}
build_current_version() {
  APP_VERSION="$(tr -d '\r\n' < VERSION)"
  if ! printf '%s' "$APP_VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    log "invalid release VERSION: $APP_VERSION"
    return 1
  fi
  export APP_VERSION
  compose up -d --build
}
trap cleanup EXIT INT TERM

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "another deployment is already running"
  exit 0
fi

cd "$APP_DIR"
configure_database_backend || exit 1
OLD_COMMIT="$(git rev-parse HEAD)"
STAMP="$(date +%Y%m%d-%H%M%S)"
prepare_database_for_deploy || exit 1
mkdir -p "$BACKUP_DIR"
backup_database "$STAMP" || exit 1

git fetch --quiet origin main
NEW_COMMIT="$(git rev-parse origin/main)"
if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
  upgrade_postgresql_schema || exit 1
  log "already current: $OLD_COMMIT"
  exit 0
fi

log "deploying $NEW_COMMIT"
git reset --hard "$NEW_COMMIT"
if ! upgrade_postgresql_schema; then
  log "schema upgrade failed; restoring application checkout to $OLD_COMMIT"
  git reset --hard "$OLD_COMMIT"
  exit 1
fi
if build_current_version && wait_ok=0; then
  i=0
  while [ "$i" -lt 30 ]; do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null; then
      if python3 "$APP_DIR/scripts/check_postgresql_runtime.py" --database "$PG_DATABASE"; then
        wait_ok=1; break
      fi
    fi
    i=$((i + 1)); sleep 2
  done
  [ "$wait_ok" -eq 1 ] && { log "deployment healthy"; exit 0; }
fi

log "deployment failed; rolling back to $OLD_COMMIT"
git reset --hard "$OLD_COMMIT"
build_current_version
exit 1
