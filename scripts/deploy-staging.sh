#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Deploy the current GitHub branch to the staging environment on Debian.
#
# Usage (on the Debian server):
#   sh scripts/deploy-staging.sh [branch]
#
# Default branch: main
# - pulls the branch into /opt/invoice-tool-test
# - rebuilds + restarts the staging app (port 8090)
# - health check
#
# Data: the staging DB in /srv/invoice-tool-test/data is NEVER touched by this
# script. Use --refresh-db to re-copy the production DB (drops staging data):
#   sh scripts/deploy-staging.sh main --refresh-db
# ─────────────────────────────────────────────────────────────────────────────
set -eu

TEST_APP_DIR="${INVOICE_TOOL_TEST_DIR:-/opt/invoice-tool-test}"
STATE_DIR="${INVOICE_TOOL_TEST_STATE_DIR:-/srv/invoice-tool-test}"
PROD_STATE_DIR="${INVOICE_TOOL_STATE_DIR:-/srv/invoice-tool}"
PORT="${INVOICE_TOOL_TEST_PORT:-8090}"

BRANCH="${1:-main}"
REFRESH_DB=0
for arg in "$@"; do
  [ "$arg" = "--refresh-db" ] && REFRESH_DB=1
done

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

[ -d "$TEST_APP_DIR/.git" ] || { log "error: $TEST_APP_DIR is not a git clone; run setup-staging.sh first"; exit 1; }

# ── 1. Pull code ────────────────────────────────────────────────────────────
cd "$TEST_APP_DIR"
log "fetching origin/$BRANCH"
git fetch origin
git checkout -q "$BRANCH" 2>/dev/null || git checkout -q -b "$BRANCH" "origin/$BRANCH"
git reset -q --hard "origin/$BRANCH"
log "now at $(git rev-parse --short HEAD) ($BRANCH)"

# ── 2. Optional DB refresh ──────────────────────────────────────────────────
if [ "$REFRESH_DB" = "1" ]; then
  log "refreshing staging DB from production (staging data will be replaced)"
  sqlite3 "$PROD_STATE_DIR/data/invoices.db" ".backup '$STATE_DIR/data/invoices.db'"
fi

# ── 3. Build + restart ──────────────────────────────────────────────────────
cd "$STATE_DIR"
docker compose up -d --build invoice-tool-test

# ── 4. Health check ─────────────────────────────────────────────────────────
i=0
while [ "$i" -lt 30 ]; do
  if curl -fsS --max-time 5 "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    log "staging deploy OK: http://127.0.0.1:$PORT/ (https://test.invoice.prasinospower.com)"
    exit 0
  fi
  i=$((i + 1))
  sleep 2
done
log "staging health check failed; logs:"
docker compose -f "$STATE_DIR/docker-compose.yml" logs --tail 40 invoice-tool-test || true
exit 1
