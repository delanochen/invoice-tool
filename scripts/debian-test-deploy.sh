#!/bin/sh
set -eu

APP_DIR="${INVOICE_TEST_DIR:-/opt/invoice-tool-test}"
STATE_DIR="${INVOICE_TEST_STATE_DIR:-/srv/invoice-tool-test}"
BACKUP_DIR="$STATE_DIR/backups"
LOCK_DIR="/run/invoice-tool-test-deploy.lock"
HEALTH_URL="${INVOICE_TEST_HEALTH_URL:-http://127.0.0.1:8089/}"
BRANCH="${INVOICE_TEST_BRANCH:-staging}"
DATABASE="invoice_test"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
compose() {
  docker compose -p invoice-tool-test -f "$APP_DIR/deploy/docker-compose.test.yml" "$@"
}
upgrade_schema() {
  python3 "$APP_DIR/scripts/upgrade_postgresql.py" --database "$DATABASE"
  for script in "$APP_DIR"/scripts/upgrade_postgresql_*.py; do
    python3 "$script" --database "$DATABASE"
  done
}
verify_runtime() {
  docker exec invoice-tool-test python -c '
import os, psycopg
url = os.environ.get("DATABASE_URL", "")
if not url.rstrip("/").split("/")[-1].split("?", 1)[0] == "invoice_test":
    raise SystemExit("test deployment is not configured for invoice_test")
with psycopg.connect(url, connect_timeout=10) as connection:
    database, role = connection.execute("select current_database(), current_user").fetchone()
    if database != "invoice_test" or role != "invoice_test":
        raise SystemExit("test database identity mismatch")
print("test database identity passed")
'
}
build_current_version() {
  APP_VERSION="$(tr -d '\r\n' < "$APP_DIR/VERSION")-test"
  export APP_VERSION
  compose up -d --build
}

trap cleanup EXIT INT TERM
mkdir "$LOCK_DIR" 2>/dev/null || { log "another test deployment is running"; exit 0; }
cd "$APP_DIR"
[ -f "$STATE_DIR/app.env" ] || { log "missing $STATE_DIR/app.env"; exit 1; }
mkdir -p "$BACKUP_DIR" "$STATE_DIR/data" "$STATE_DIR/shared-photos"

OLD_COMMIT="$(git rev-parse HEAD)"
git fetch --quiet origin "$BRANCH"
NEW_COMMIT="$(git rev-parse "origin/$BRANCH")"
if [ "$OLD_COMMIT" = "$NEW_COMMIT" ] && [ "$(docker inspect invoice-tool-test --format '{{.State.Running}}' 2>/dev/null || true)" = "true" ]; then
  log "already current: $OLD_COMMIT"
  exit 0
fi

log "deploying test commit $NEW_COMMIT"
python3 "$APP_DIR/scripts/backup_postgresql.py" --container invoice-tool-postgres \
  --database "$DATABASE" --output "$BACKUP_DIR/invoice-test-$(date +%Y%m%d-%H%M%S).dump"
git reset --hard "$NEW_COMMIT"
if upgrade_schema && build_current_version; then
  i=0
  while [ "$i" -lt 30 ]; do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null && verify_runtime; then
      log "test deployment healthy: $NEW_COMMIT"
      exit 0
    fi
    i=$((i + 1))
    sleep 2
  done
fi

log "test deployment failed; rolling code back to $OLD_COMMIT"
git reset --hard "$OLD_COMMIT"
build_current_version
exit 1
