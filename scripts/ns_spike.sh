#!/usr/bin/env bash
# ns_spike.sh — render, verify and tear down the throwaway Nightscout staging stack
# used by the M0 read-path spike (BUILD_PLAN M0-T1).
#
#   scripts/ns_spike.sh up       render /tmp/ns-spike, start the stack, wait for the API,
#                                POST one probe reading and read it back; prints the
#                                xDrip+ base URL (token) for pairing
#   scripts/ns_spike.sh render   render /tmp/ns-spike only (no docker calls)
#   scripts/ns_spike.sh url      print the xDrip+ base URL for the running stack
#   scripts/ns_spike.sh status   compose ps + probe reading count
#   scripts/ns_spike.sh down     stop the stack and delete its volumes (throwaway)
#
# The secret is generated once per spike dir, written 0600 to /tmp/ns-spike/secret.env,
# and never lives in the repo. `SPIKE_DIR` can be overridden; it must stay outside the
# repository so no key material can be committed by accident.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEMPLATE="$REPO_ROOT/spike/docker-compose.spike.yml"
SPIKE_DIR=${SPIKE_DIR:-/tmp/ns-spike}
SPIKE_PORT=${SPIKE_PORT:-1337}
PROBE_SGV=${PROBE_SGV:-137}
PROBE_DEVICE=spike-probe
WAIT_SECS=${WAIT_SECS:-300}

log() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

lan_ip() {
    local ip
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null \
        | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')
    [ -n "${ip:-}" ] || die "no LAN address found; set SPIKE_BIND_IP and retry"
    printf '%s' "$ip"
}

compose() {
    docker compose --env-file "$SPIKE_DIR/spike.env" -f "$SPIKE_DIR/docker-compose.yml" "$@"
}

token_of() { printf '%s' "$1" | sha1sum | cut -d' ' -f1; }

bind_ip() { sed -n 's/^SPIKE_BIND_IP=//p' "$SPIKE_DIR/spike.env"; }
api_token() { cat "$SPIKE_DIR/read.token"; }
base_url() { printf 'http://%s:%s' "$(bind_ip)" "$SPIKE_PORT"; }

require_rendered() {
    [ -f "$SPIKE_DIR/docker-compose.yml" ] || die "not rendered yet — run: $0 up"
}

render() {
    local bind secret
    case "$SPIKE_DIR" in
    "$REPO_ROOT" | "$REPO_ROOT"/*) die "SPIKE_DIR must be outside the repo: $SPIKE_DIR" ;;
    esac
    mkdir -p "$SPIKE_DIR"
    chmod 700 "$SPIKE_DIR"
    bind=${SPIKE_BIND_IP:-$(lan_ip)}

    if [ -f "$SPIKE_DIR/secret.env" ]; then
        secret=$(sed -n 's/^API_SECRET=//p' "$SPIKE_DIR/secret.env")
        [ -n "$secret" ] || die "secret.env exists but holds no API_SECRET"
    else
        secret=$(openssl rand -hex 32)
        (umask 077 && printf 'API_SECRET=%s\n' "$secret" >"$SPIKE_DIR/secret.env")
    fi
    chmod 600 "$SPIKE_DIR/secret.env"

    (umask 077 && printf 'SPIKE_API_SECRET=%s\nSPIKE_BIND_IP=%s\nSPIKE_PORT=%s\n' \
        "$secret" "$bind" "$SPIKE_PORT" >"$SPIKE_DIR/spike.env")
    chmod 600 "$SPIKE_DIR/spike.env"

    token_of "$secret" >"$SPIKE_DIR/read.token"
    chmod 600 "$SPIKE_DIR/read.token"

    cp "$TEMPLATE" "$SPIKE_DIR/docker-compose.yml"
    log "rendered $SPIKE_DIR (bind $bind:$SPIKE_PORT)"
}

exited_service() {
    compose ps -a --format '{{.Service}} {{.State}}' 2>/dev/null | awk '$2 == "exited" {print $1; exit}'
}

wait_for_api() {
    local base=$1 i code dead
    for i in $(seq 1 "$WAIT_SECS"); do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$base/" || true)
        if [ "$code" = "200" ]; then
            log "API ready after ${i}s"
            return 0
        fi
        dead=$(exited_service)
        if [ -n "$dead" ]; then
            printf 'service %s exited before the API came up; last logs:\n' "$dead" >&2
            compose logs --tail 25 "$dead" >&2 || true
            return 1
        fi
        sleep 1
    done
    compose logs --tail 20 >&2 || true
    return 1
}

probe_roundtrip() {
    local base=$1 token=$2 now
    now=$(( $(date +%s) * 1000 ))
    curl -sS -o "$SPIKE_DIR/post.json" -w '%{http_code}' -X POST \
        "$base/api/v1/entries.json" \
        -H 'Content-Type: application/json' \
        -H "api-secret: $token" \
        -d "[{\"sgv\": $PROBE_SGV, \"date\": $now, \"direction\": \"Flat\",
              \"device\": \"$PROBE_DEVICE\", \"type\": \"sgv\"}]" \
        >"$SPIKE_DIR/post.code"
    [ "$(cat "$SPIKE_DIR/post.code")" = "200" ] \
        || die "authorised POST /api/v1/entries.json -> $(cat "$SPIKE_DIR/post.code")"

    curl -sS "$base/api/v1/entries.json?count=10&token=$token" >"$SPIKE_DIR/get.json"
    grep -Eq "\"device\":\"$PROBE_DEVICE\"|\"device\": \"$PROBE_DEVICE\"" "$SPIKE_DIR/get.json" \
        || die "probe reading ($PROBE_DEVICE) not returned by GET /api/v1/entries.json"
    grep -Eq "\"sgv\": *$PROBE_SGV" "$SPIKE_DIR/get.json" \
        || die "probe sgv $PROBE_SGV not found in the GET payload"
    log "round-trip PASS: sgv $PROBE_SGV posted with the token and read back"
}

cmd_up() {
    local base unauth
    render
    compose up -d --quiet-pull
    base=$(base_url)
    wait_for_api "$base" || die "no HTTP 200 from $base within ${WAIT_SECS}s"

    unauth=$(curl -s -o /dev/null -w '%{http_code}' "$base/api/v1/entries.json?count=1")
    log "anonymous GET /api/v1/entries.json -> $unauth (unhardened spike default; M1-T3 flips this to 401)"

    probe_roundtrip "$base" "$(api_token)"
    log "stack up: $base"
    log "xDrip+ base URL: $(token_url)"
    log "tear down with: $0 down"
}

token_url() { printf 'http://%s@%s/api/v1' "$(api_token)" "$(bind_ip):$SPIKE_PORT"; }

cmd_url() {
    require_rendered
    printf '%s\n' "$(token_url)"
}

cmd_status() {
    require_rendered
    compose ps
    local n
    n=$(curl -s "$(base_url)/api/v1/entries.json?count=1000&token=$(api_token)" \
        | grep -o '"sgv"' | wc -l || true)
    log "entries returned by /api/v1/entries.json (max 1000): ${n:-0}"
}

cmd_down() {
    if [ -f "$SPIKE_DIR/docker-compose.yml" ]; then
        compose down -v --remove-orphans || true
    fi
    rm -rf "$SPIKE_DIR"
    log "stack down, $SPIKE_DIR removed"
}

case "${1:-}" in
up) cmd_up ;;
render) render ;;
url) cmd_url ;;
status) cmd_status ;;
down) cmd_down ;;
*)
    printf 'usage: %s {up|render|url|status|down}\n' "$0" >&2
    exit 2
    ;;
esac
