#!/bin/sh
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP_DIR="${INVOICE_TOOL_DIR:-$(dirname "$SCRIPT_DIR")}"
COMPOSE_FILE="${INVOICE_TOOL_COMPOSE_FILE:-$APP_DIR/docker-compose.yml}"
ENV_FILE="${INVOICE_TOOL_ENV_FILE:-$APP_DIR/.env}"
CONTAINER="${INVOICE_TOOL_CONTAINER:-invoice-tool}"
DATA_DIR="${INVOICE_TOOL_DATA_DIR:-}"
PHOTOS_DIR="${INVOICE_TOOL_PHOTOS_DIR:-}"
BACKUP_DIR="${INVOICE_TOOL_BACKUP_DIR:-}"
FAILURES=0
WARNINGS=0

log() {
    printf '%s\n' "$*"
}

pass() {
    log "PASS: $*"
}

warning() {
    WARNINGS=$((WARNINGS + 1))
    log "WARNING: $*"
}

fail() {
    FAILURES=$((FAILURES + 1))
    log "FAIL: $*"
}

section() {
    log ""
    log "== $1 =="
}

env_value() {
    key="$1"
    if [ -f "$ENV_FILE" ]; then
        sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?${key}[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" |
            tail -n 1 | sed -e 's/[[:space:]]*#.*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
    fi
}

compose_default() {
    key="$1"
    fallback="$2"
    value="$(env_value "$key")"
    if [ -n "$value" ]; then
        printf '%s\n' "$value"
        return
    fi
    compose_value="$(sed -n "s/.*\${${key}:-\([^}]*\)}:.*/\1/p" "$COMPOSE_FILE" 2>/dev/null | head -n 1)"
    if [ -n "$compose_value" ]; then
        printf '%s\n' "$compose_value"
    else
        printf '%s\n' "$fallback"
    fi
}

if [ -n "${DATA_HOST_DIR:-}" ]; then
    DATA_DIR="$DATA_HOST_DIR"
elif [ -z "$DATA_DIR" ]; then
    DATA_DIR="$(compose_default DATA_HOST_DIR "/srv/invoice-tool/data")"
fi
if [ -n "${SHARED_PHOTOS_HOST_DIR:-}" ]; then
    PHOTOS_DIR="$SHARED_PHOTOS_HOST_DIR"
elif [ -z "$PHOTOS_DIR" ]; then
    PHOTOS_DIR="$(compose_default SHARED_PHOTOS_HOST_DIR "/srv/invoice-tool/shared-photos")"
fi
if [ -z "$BACKUP_DIR" ]; then
    BACKUP_DIR="$(compose_default INVOICE_TOOL_BACKUP_DIR "")"
fi

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

section "Git revision"
if [ -d "$APP_DIR/.git" ] && command -v git >/dev/null 2>&1; then
    revision="$(git -C "$APP_DIR" rev-parse --verify HEAD 2>/dev/null || true)"
    branch="$(git -C "$APP_DIR" branch --show-current 2>/dev/null || true)"
    if [ -n "$revision" ]; then
        pass "revision: $revision"
        log "branch: ${branch:-detached}"
    else
        fail "unable to read Git revision"
    fi
    if [ -z "$(git -C "$APP_DIR" status --porcelain 2>/dev/null)" ]; then
        pass "Git worktree is clean"
    else
        warning "Git worktree has tracked or untracked changes"
        git -C "$APP_DIR" status --porcelain 2>/dev/null | sed 's/^/git_status: /'
    fi
else
    fail "Git checkout or git command is unavailable: $APP_DIR"
fi

section "Compose summary"
if [ ! -f "$COMPOSE_FILE" ]; then
    fail "Compose file is missing: $COMPOSE_FILE"
elif ! command -v docker >/dev/null 2>&1; then
    warning "Docker is unavailable; Compose validation and service summary were not executed"
else
    compose_config() {
        if [ -f "$ENV_FILE" ]; then
            docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
        else
            docker compose -f "$COMPOSE_FILE" "$@"
        fi
    }
    if services="$(compose_config config --services 2>/dev/null)"; then
        pass "Compose services resolved"
        printf '%s\n' "$services" | sed 's/^/service: /'
    else
        fail "Compose configuration validation failed"
    fi
    if volumes="$(compose_config config --volumes 2>/dev/null)"; then
        pass "Compose volumes resolved"
        printf '%s\n' "$volumes" | sed 's/^/volume: /'
    else
        warning "Compose volume summary was not available"
    fi
fi

section "Database integrity"
if [ -z "$DATA_DIR" ]; then
    warning "DATA_HOST_DIR is not set in the environment or .env; database integrity check was not executed"
elif [ ! -f "$DATA_DIR/invoices.db" ]; then
    fail "database is missing: $DATA_DIR/invoices.db"
elif command -v python3 >/dev/null 2>&1; then
    if python3 - "$DATA_DIR/invoices.db" <<'PY'
import sqlite3
import sys

path = sys.argv[1]
db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
try:
    result = db.execute("PRAGMA integrity_check").fetchone()[0]
    print("database_integrity: " + result)
    raise SystemExit(0 if result == "ok" else 1)
finally:
    db.close()
PY
    then
        pass "SQLite integrity_check returned ok (read-only connection)"
    else
        fail "SQLite integrity_check did not return ok"
    fi
else
    warning "python3 is unavailable; database integrity check was not executed"
fi

section "Data and attachment paths"
for label_path in \
    "data|$DATA_DIR" \
    "shared_photos|$PHOTOS_DIR" \
    "backups|$BACKUP_DIR"; do
    label="${label_path%%|*}"
    path="${label_path#*|}"
    if [ -z "$path" ]; then
        warning "$label path is not configured; size check was not executed"
    elif [ -e "$path" ]; then
        if size="$(du -sh "$path" 2>/dev/null | awk '{print $1}')"; then
            pass "$label: $path (size=$size)"
        else
            warning "$label exists but its size could not be read: $path"
        fi
    else
        warning "$label path is missing: $path"
    fi
done

section "Mount and key-file status"
if command -v findmnt >/dev/null 2>&1; then
    if [ -n "$DATA_DIR" ] && mount_data="$(findmnt --target "$DATA_DIR" --noheadings --output SOURCE,FSTYPE,TARGET 2>/dev/null)"; then
        pass "host data mount resolved"
        log "data_mount: $mount_data"
    else
        warning "host data mount was not resolved"
    fi
    if [ -n "$PHOTOS_DIR" ] && mount_photos="$(findmnt --target "$PHOTOS_DIR" --noheadings --output SOURCE,FSTYPE,TARGET 2>/dev/null)"; then
        pass "host photos mount resolved"
        log "photos_mount: $mount_photos"
    else
        warning "host photos mount was not resolved"
    fi
else
    warning "findmnt is unavailable; host mount checks were not executed"
fi

mount_source() {
    destination="$1"
    mounts="$2"
    match="$(printf '%s\n' "$mounts" | awk -F '\t' -v destination="$destination" '
        $1 == destination {
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
    printf '%s\n' "$match"
}

check_bind_mount() {
    destination="$1"
    expected="$2"
    label="$3"
    expected_normalized="$(normalize_path "$expected" 2>/dev/null)" || {
        fail "$label expected path cannot be normalized: ${expected:-missing}"
        return
    }
    match="$(mount_source "$destination" "$MOUNTS" 2>/dev/null)" || {
        fail "$label mount destination is missing or duplicated: $destination"
        return
    }
    mount_type="$(printf '%s\n' "$match" | awk -F '\t' '{print $1}')"
    actual_source="$(printf '%s\n' "$match" | cut -f 2-)"
    if [ "$mount_type" != "bind" ]; then
        fail "$label mount is not a bind mount: ${mount_type:-missing}"
        return
    fi
    actual_normalized="$(normalize_path "$actual_source" 2>/dev/null)" || {
        fail "$label source cannot be normalized: ${actual_source:-missing}"
        return
    }
    if [ "$actual_normalized" != "$expected_normalized" ]; then
        fail "$label source mismatch: $actual_normalized != $expected_normalized"
        return
    fi
    pass "$label bind mount verified: $actual_normalized -> $destination"
}

if ! command -v docker >/dev/null 2>&1; then
    fail "Docker is unavailable; actual bind mounts cannot be inspected"
elif [ "$(docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null || true)" != "true" ]; then
    fail "invoice-tool container is missing or not running: $CONTAINER"
elif ! MOUNTS="$(docker inspect "$CONTAINER" \
    --format '{{range .Mounts}}{{printf "%s\t%s\t%s\n" .Destination .Type .Source}}{{end}}' \
    2>/dev/null)"; then
    fail "Docker inspect failed for container: $CONTAINER"
else
    pass "Docker bind mounts resolved (host source -> container destination)"
    printf '%s\n' "$MOUNTS" | sed 's/^/container_mount: /'
    check_bind_mount "/app/data" "$DATA_DIR" "data"
    check_bind_mount "/app/shared-photos" "$PHOTOS_DIR" "shared_photos"
fi

for key_file in "$COMPOSE_FILE" "$ENV_FILE" "$APP_DIR/VERSION" \
    "$SCRIPT_DIR/auto-update.sh" "$SCRIPT_DIR/migrate-to-volume2.sh"; do
    if [ -f "$key_file" ]; then
        pass "key file present: $key_file"
    else
        warning "key file missing: $key_file"
    fi
done

log ""
if [ "$FAILURES" -gt 0 ]; then
    log "baseline: FAIL ($FAILURES failure(s), $WARNINGS warning(s))"
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    log "baseline: WARNING ($WARNINGS warning(s); review checks above)"
    exit 0
else
    log "baseline: PASS (all checks completed)"
    exit 0
fi
