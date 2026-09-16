#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Setup a staging copy of invoice-tool on the Debian server (one-time).
#
# What it creates:
#   /opt/invoice-tool-test/          git clone of the app code (build context)
#   /srv/invoice-tool-test/          staging state directory
#     ├── data/invoices.db           independent DB (safe copy of production)
#     ├── .env                       staging env (inherited from production)
#     └── docker-compose.yml         staging compose (port 8090, read-only photos)
#
# Staging is deliberately isolated:
#   - own DB file, own container (invoice-tool-test), port 8090
#   - shared-photos mounted READ-ONLY from the production tree
#   - NO photo-worker (never writes into the production photo tree)
#   - REQUIRE_DATA_DIRECTORY_IDENTITY=0 (no production identity marker needed)
#
# Cloudflare (do once, in the dashboard):
#   Zero Trust -> Networks -> Tunnels -> Create a tunnel (e.g. invoice-tool-test)
#   Public Hostname: test.invoice.prasinospower.com
#   Service:         http://invoice-tool-test:8000
#   Copy the tunnel token into /srv/invoice-tool-test/.env as TEST_TUNNEL_TOKEN.
#
# Then run:  sh scripts/setup-staging.sh
# Test URL:  https://test.invoice.prasinospower.com
# ─────────────────────────────────────────────────────────────────────────────
set -eu

TEST_APP_DIR="${INVOICE_TOOL_TEST_DIR:-/opt/invoice-tool-test}"
STATE_DIR="${INVOICE_TOOL_TEST_STATE_DIR:-/srv/invoice-tool-test}"
PROD_APP_DIR="${INVOICE_TOOL_DIR:-/opt/invoice-tool}"
PROD_STATE_DIR="${INVOICE_TOOL_STATE_DIR:-/srv/invoice-tool}"
PORT="${INVOICE_TOOL_TEST_PORT:-8090}"
REPO_URL="${INVOICE_TOOL_REPO_URL:-https://github.com/delanochen/invoice-tool.git}"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

# ── 1. Code copy ────────────────────────────────────────────────────────────
if [ ! -d "$TEST_APP_DIR/.git" ]; then
  log "cloning $REPO_URL -> $TEST_APP_DIR"
  git clone "$REPO_URL" "$TEST_APP_DIR"
else
  log "code copy already exists at $TEST_APP_DIR"
fi

# ── 2. State directory ──────────────────────────────────────────────────────
mkdir -p "$STATE_DIR/data"
chmod 750 "$STATE_DIR" "$STATE_DIR/data"

# ── 3. Independent database (safe copy of production, only on first setup) ──
if [ ! -f "$STATE_DIR/data/invoices.db" ]; then
  if command -v sqlite3 >/dev/null 2>&1 && [ -f "$PROD_STATE_DIR/data/invoices.db" ]; then
    log "copying production database -> $STATE_DIR/data/invoices.db"
    sqlite3 "$PROD_STATE_DIR/data/invoices.db" ".backup '$STATE_DIR/data/invoices.db'"
  else
    log "warning: no production DB found (or sqlite3 missing); starting with empty DB"
  fi
else
  log "staging database already exists (kept as-is)"
fi

# ── 4. Staging .env (inherit production, append test-specific overrides) ───
if [ ! -f "$STATE_DIR/.env" ]; then
  if [ -f "$PROD_APP_DIR/.env" ]; then
    cp "$PROD_APP_DIR/.env" "$STATE_DIR/.env"
    log "copied production .env -> $STATE_DIR/.env"
  else
    : > "$STATE_DIR/.env"
  fi
  {
    printf '\n# ── Staging overrides ──\n'
    printf 'REQUIRE_DATA_DIRECTORY_IDENTITY=0\n'
    printf 'APP_VERSION=0.0.0\n'
    printf 'TEST_TUNNEL_TOKEN=CHANGE_ME_CLOUDFLARE_TUNNEL_TOKEN\n'
  } >> "$STATE_DIR/.env"
  log "please set TEST_TUNNEL_TOKEN in $STATE_DIR/.env (Cloudflare tunnel token)"
fi

# ── 5. Staging compose file ─────────────────────────────────────────────────
cat > "$STATE_DIR/docker-compose.yml" <<YAML
services:
  invoice-tool-test:
    build:
      context: $TEST_APP_DIR
      args:
        APP_VERSION: "\${APP_VERSION:-0.0.0}"
    image: invoice-tool-test:latest
    container_name: invoice-tool-test
    restart: unless-stopped
    ports:
      - "$PORT:8000"
    environment:
      SECRET_KEY: "\${SECRET_KEY:?Set SECRET_KEY in .env}"
      ADMIN_EMAIL: "\${ADMIN_EMAIL:?Set ADMIN_EMAIL in .env}"
      ADMIN_PASSWORD: "\${ADMIN_PASSWORD:?Set ADMIN_PASSWORD in .env}"
      SHARED_PHOTOS_DIR: "/app/shared-photos"
      GOOGLE_MAPS_BROWSER_API_KEY: "\${GOOGLE_MAPS_BROWSER_API_KEY:-}"
      GOOGLE_GEOCODING_API_KEY: "\${GOOGLE_GEOCODING_API_KEY:-}"
      REQUIRE_DATA_DIRECTORY_IDENTITY: "0"
      APP_VERSION: "\${APP_VERSION:-0.0.0}"
    volumes:
      - "$STATE_DIR/data:/app/data"
      - "$PROD_STATE_DIR/shared-photos:/app/shared-photos:ro"

  cloudflared-test:
    image: cloudflare/cloudflared:latest
    container_name: invoice-tool-test-cloudflared
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: "\${TEST_TUNNEL_TOKEN:?Set TEST_TUNNEL_TOKEN in .env}"
    depends_on:
      - invoice-tool-test
YAML
log "wrote $STATE_DIR/docker-compose.yml (port $PORT, photos read-only)"

# ── 6. Start ────────────────────────────────────────────────────────────────
cd "$STATE_DIR"
if grep -q "CHANGE_ME_CLOUDFLARE_TUNNEL_TOKEN" .env; then
  log "TEST_TUNNEL_TOKEN is still a placeholder; starting app only (cloudflared will stay down)"
  docker compose up -d --build invoice-tool-test
else
  docker compose up -d --build
fi

# ── 7. Health check ─────────────────────────────────────────────────────────
i=0
while [ "$i" -lt 30 ]; do
  if curl -fsS --max-time 5 "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    log "staging is up at http://127.0.0.1:$PORT/ (https://test.invoice.prasinospower.com)"
    exit 0
  fi
  i=$((i + 1))
  sleep 2
done
log "health check failed after 60s; check: docker compose -f $STATE_DIR/docker-compose.yml logs invoice-tool-test"
exit 1
