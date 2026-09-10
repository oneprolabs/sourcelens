#!/bin/bash
# Day-2 operations for an already-installed sourcelens — status, restarting
# or recreating runtime services, and rolling back a blue/green switch. NOT for
# installing or upgrading to a new version — that's scripts/install.sh.
# Deliberately a separate script rather than more subcommands on install.sh:
# "install a new version" and "operate on what's already running" have
# different risk profiles and don't belong behind the same entrypoint.
#
# Installed/refreshed automatically every time install.sh runs (it's in
# install.sh's ASSETS list) — no separate curl needed once install.sh has run at
# least once on this host.
#
#   ./scripts/sourcelensctl.sh status            # active color + health + which containers are up
#   ./scripts/sourcelensctl.sh restart-workers   # graceful restart, no image pull, no color switch
#   ./scripts/sourcelensctl.sh restart lensnode  # restart one runtime service
#   ./scripts/sourcelensctl.sh recreate runtime  # reread .env and recreate all
#   ./scripts/sourcelensctl.sh rollback          # flip traffic back to the other color, no pull/build/migrate
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-$(pwd)}"
cd "$DEPLOY_PATH"

log() { echo -e "\033[1;36m[sourcelensctl]\033[0m $*"; }
die() { echo -e "\033[1;31m[sourcelensctl] ERROR:\033[0m $*" >&2; exit 1; }

# --- Single-flight lock: shared with install.sh's lock file, so an ops command
# can't race a deploy (or another ops command) in flight.
acquire_deploy_lock() {
    local lock_file="/tmp/sourcelens-install.lock"
    local max_wait=300 waited=0
    # Atomic acquire via noclobber (see install.sh for why a `[ -f ]` check is a
    # TOCTOU race); take over a stale lock after max_wait.
    while ! (set -o noclobber; echo "$$ $(date)" > "$lock_file") 2>/dev/null; do
        if [ "$waited" -ge "$max_wait" ]; then
            log "Lock $lock_file still held after ${max_wait}s — taking it over"
            echo "$$ $(date)" > "$lock_file"
            break
        fi
        log "install.sh (or another ops command) is running, waiting... (${waited}s)"
        sleep 5; waited=$((waited + 5))
    done
    trap 'rm -f "'"$lock_file"'"' EXIT
}

[ -f "$DEPLOY_PATH/scripts/lib/deploy-common.sh" ] || die "scripts/lib/deploy-common.sh not found — run scripts/install.sh at least once first"
# shellcheck source=./lib/deploy-common.sh
source "$DEPLOY_PATH/scripts/lib/deploy-common.sh"

if [ -f "$DEPLOY_PATH/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$DEPLOY_PATH/.env"
    set +a
fi

cmd_status() {
    local color; color="$(current_color)"
    log "Active color: $color"
    echo
    docker compose ps \
        "sourcelens-api-${color}" "sourcelens-ui-${color}" \
        backend-worker backend-scheduler lensnode nginx \
        2>/dev/null || true
    echo
    if docker exec "sourcelens-api-${color}" \
        curl -fs http://127.0.0.1:8000/health >/dev/null 2>&1; then
        log "sourcelens-api-${color} (active): healthy"
    else
        log "sourcelens-api-${color} (active): NOT healthy"
    fi
}

cmd_restart_workers() {
    cmd_restart runtime
}

services_for_target() {
    case "$1" in
        workers) echo "backend-worker" ;;
        scheduler) echo "backend-scheduler" ;;
        lensnode) echo "lensnode" ;;
        runtime) echo "backend-worker backend-scheduler lensnode" ;;
        *) die "Unsupported target '$1'. Choose workers, scheduler,"\
            " lensnode, or runtime" ;;
    esac
}

cmd_restart() {
    local target="${1:-}"
    local services
    local -a service_args
    [ -n "$target" ] || die "Usage: $0 restart"\
        " <workers|scheduler|lensnode|runtime>"
    services="$(services_for_target "$target")"
    read -r -a service_args <<< "$services"

    acquire_deploy_lock
    log "Restarting ${target}: ${services}"
    log "(graceful: stop_grace_period lets in-flight work drain)"
    docker compose restart "${service_args[@]}"
    log "Done"
}

cmd_recreate() {
    local target="${1:-}"
    local services
    local -a service_args
    [ -n "$target" ] || die "Usage: $0 recreate"\
        " <workers|scheduler|lensnode|runtime>"
    services="$(services_for_target "$target")"
    read -r -a service_args <<< "$services"

    acquire_deploy_lock
    log "Recreating ${target}: ${services}"
    log "(no image pull; --no-deps prevents touching PostgreSQL or Redis)"
    docker compose up -d --force-recreate --no-deps "${service_args[@]}"
    log "Done"
}

cmd_rollback() {
    acquire_deploy_lock
    local active target rollback_version from_version
    active="$(current_color)"
    target="$(other_color "$active")"

    # The target color's container was removed when it was retired, so `up -d`
    # recreates it from the compose image ref `...:${APP_VERSION:-latest}`. Left
    # unset, that resolves to `latest` = the CURRENT (being-rolled-back-from)
    # release, so rollback would silently redeploy the bad version on the other
    # color. Pin APP_VERSION to the version install.sh recorded when it retired
    # that color.
    rollback_version=""
    if [ -f "$DEPLOY_PATH/.rollback_version" ]; then
        rollback_version="$(cat "$DEPLOY_PATH/.rollback_version")"
    fi
    if [ -z "$rollback_version" ]; then
        die "No .rollback_version recorded (need at least one prior upgrade on this host) — can't tell which image to roll back to. Redeploy the target version explicitly: ./scripts/install.sh <tag>"
    fi
    export APP_VERSION="$rollback_version"

    # Remember the version we're rolling back FROM so a subsequent rollback can
    # return to it (read now, while the active color is still up).
    from_version="$(color_image_version "$active")"

    log "Active color is ${active}; rolling back to ${target} (version ${rollback_version})"
    log "(no pull, no build, no migration — this only works if"
    log "sourcelens-api-${target}'s image ${rollback_version} is still present locally)"

    if ! docker compose --profile "$target" up -d \
        "sourcelens-api-${target}" "sourcelens-ui-${target}"; then
        die "Could not start sourcelens-api-${target}/sourcelens-ui-${target}. If that color's image ${rollback_version} was pruned, rollback isn't possible this way — redeploy the target version instead: ./scripts/install.sh <tag>"
    fi

    log "Waiting for sourcelens-api-${target} to report healthy..."
    if ! wait_for_healthy "sourcelens-api-${target}"; then
        docker compose --profile "$target" stop \
            "sourcelens-api-${target}" "sourcelens-ui-${target}"
        die "sourcelens-api-${target} never became healthy; rollback aborted, ${active} stays live"
    fi

    switch_traffic "$active" "$target"
    echo "$target" > "$DEPLOY_PATH/.active_color"
    if [ -n "$from_version" ]; then
        echo "$from_version" > "$DEPLOY_PATH/.rollback_version"
    fi

    log "Rolled back: active color is now ${target} (version ${rollback_version})."
    log "${active} was left running (not stopped) so you can inspect its logs."
    log "Once you've confirmed ${target} is good, retire it yourself:"
    log "  docker compose --profile ${active} stop sourcelens-api-${active} sourcelens-ui-${active}"
    log "  docker compose --profile ${active} rm -f sourcelens-api-${active} sourcelens-ui-${active}"
}

case "${1:-}" in
    status) cmd_status ;;
    restart-workers) cmd_restart_workers ;;
    restart) cmd_restart "${2:-}" ;;
    recreate) cmd_recreate "${2:-}" ;;
    rollback) cmd_rollback ;;
    *) die "Usage: $0 {status|restart-workers|restart|recreate|rollback}" ;;
esac
