#!/bin/bash
# SourceLens install / upgrade script — one command, no git required on the
# host. curl this file to bootstrap a brand-new host; the exact same command
# upgrades an existing one.
#
#   curl -fsSL https://raw.githubusercontent.com/HyperBDR/sourcelens/<tag>/scripts/install.sh \
#       -o install.sh && chmod +x install.sh && ./install.sh <tag>
#
# What it does, every single run (there is no separate "first run" mode):
#   1. Fetch the small set of declarative deploy files (docker-compose.yml,
#      nginx/postgres config, this script itself) straight from
#      raw.githubusercontent.com at the given tag — not the whole repo.
#   2. Bootstrap runtime-state files if missing (nginx upstream.conf,
#      .active_color, self-signed TLS cert) — never touched again after that.
#   3. Blue/green switch: bring up the idle color, health-check it, flip nginx's
#      upstream to it via `nginx -s reload` (no dropped requests), then retire
#      the previously-active color. On a genuinely first-ever install (nothing
#      running yet) there's no old color to switch from or retire, so this step
#      degrades to a plain health-gated bring-up.
#
# Migrations: the API image runs `sourcelens_init` (migrate + register periodic
# tasks + collectstatic) on startup, so bringing the deploy color up migrates it
# against the DB before it reports healthy — the health gate below covers a
# failed migration (the color never turns healthy, the deploy aborts, the
# current color stays live). See CLAUDE.md's "零停机部署" section for the
# expand/contract migration discipline this requires.
#
# --local mode — for testing this script itself, or debugging the blue/green
# flow directly on a host without touching GitHub:
#
#   ./scripts/install.sh --local [tag]
#
# Skips step 1 entirely (uses whatever's already on disk — e.g. your working
# tree when run from a repo checkout) and builds images from local source
# (`docker compose build`) instead of pulling from the registry, so the whole
# flow runs against your uncommitted changes with no network access to GitHub or
# the registry required. [tag] defaults to "local" and, unlike remote mode, is
# never used to skip an already-current deploy — local mode always runs the full
# switch, since re-testing the same tag is the point.
#
# Day-2 ops (status / restart-workers / rollback) are NOT here — this script is
# only for installing or upgrading. See scripts/sourcelensctl.sh, which this
# script installs/refreshes alongside itself (see ASSETS below) so it's always
# available once install.sh has run at least once.
set -euo pipefail

REPO="HyperBDR/sourcelens"
# Must match docker-compose.yml's `image:` lines — used only to prune old
# version tags after a remote deploy (see prune_old_image_tags below).
API_IMAGE_REPO="oneprolabs/sourcelens-backend"
UI_IMAGE_REPO="oneprolabs/sourcelens-frontend"
# VERSION_LABEL / color_image_version() come from scripts/lib/deploy-common.sh
# (sourced below, before either is used).

LOCAL_MODE=false
if [ "${1:-}" = "--local" ]; then
    LOCAL_MODE=true
    shift
fi

if [ "$LOCAL_MODE" = "true" ]; then
    GIT_REF="${1:-local}"
else
    GIT_REF="${1:?Usage: install.sh <git-tag-or-ref>, e.g. install.sh v1.2.3 (or: install.sh --local [tag] to test against local files)}"
fi
IMAGE_TAG="${GIT_REF#v}"  # docker/metadata-action strips the leading 'v'
DEPLOY_PATH="${DEPLOY_PATH:-$(pwd)}"
POST_SWITCH_OBSERVE_SECONDS="${POST_SWITCH_OBSERVE_SECONDS:-30}"
# GRACE_HEALTH_RETRIES/GRACE_HEALTH_INTERVAL are declared in
# scripts/lib/deploy-common.sh, sourced below once bootstrap_runtime_state
# guarantees it's present.

log() { echo -e "\033[1;36m[install]\033[0m $*"; }
die() { echo -e "\033[1;31m[install] ERROR:\033[0m $*" >&2; exit 1; }

cd "$DEPLOY_PATH"

# --- Single-flight lock: refuses to run two deploys at once on one host ---
# Acquire atomically with `set -o noclobber` (the redirect fails instead of
# truncating when the file already exists), so two installs starting at the same
# instant can't both pass a `[ -f ]` check and proceed — a real TOCTOU race
# given the CI concurrency group isn't the only caller (manual runs, ops
# commands). After MAX_WAIT we take the lock over anyway (a crashed run may have
# left a stale file).
LOCK_FILE="/tmp/sourcelens-install.lock"
MAX_WAIT=300; WAITED=0
while ! (set -o noclobber; echo "$$ $(date)" > "$LOCK_FILE") 2>/dev/null; do
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        log "Lock $LOCK_FILE still held after ${MAX_WAIT}s — taking it over"
        echo "$$ $(date)" > "$LOCK_FILE"
        break
    fi
    log "Another install is running (lock: $LOCK_FILE), waiting... (${WAITED}s)"
    sleep 5; WAITED=$((WAITED + 5))
done
trap 'rm -f "$LOCK_FILE"' EXIT

# --- Load host secrets if present (never fetched, never committed) ---
if [ -f "$DEPLOY_PATH/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$DEPLOY_PATH/.env"
    set +a
fi

AUTH_HEADER=()
if [ -n "${DEPLOY_GITHUB_TOKEN:-}" ]; then
    AUTH_HEADER=(-H "Authorization: token ${DEPLOY_GITHUB_TOKEN}")
fi

# --- Step 1: fetch declarative assets fresh from the target ref ---------
# Directory listing isn't a thing on raw.githubusercontent.com, so
# docker/postgresql/initdb.d/*'s file names are spelled out explicitly. Adding a
# new initdb script means adding a line here too.
ASSETS=(
    docker-compose.yml
    docker/nginx/conf.d/default.conf
    docker/nginx/upstream.conf.default
    docker/nginx/certs/README.md
    docker/postgresql/etc/postgresql.conf
    docker/postgresql/initdb.d/000-create-databases.sql
    docker/postgresql/initdb.d/001-grant-schema-privileges.sh
    docker/postgresql/initdb.d/002-setup-log-permissions.sh
    env.sample
    scripts/install.sh
    scripts/sourcelensctl.sh
    scripts/lib/deploy-common.sh
)

fetch_asset() {
    local path="$1"
    local dest="$DEPLOY_PATH/$path"
    mkdir -p "$(dirname "$dest")"
    # Atomic replace: curl writes to a temp file, then `mv` (rename) swaps it in.
    # This file may be scripts/install.sh itself — the currently-running process
    # keeps reading its already-open fd's old content until it exits, so this is
    # safe even for self-update. (A plain `curl -o "$dest"` truncates in place
    # instead and would risk corrupting a script reading itself mid-run.)
    curl -fsSL "${AUTH_HEADER[@]}" \
        "https://raw.githubusercontent.com/${REPO}/${GIT_REF}/${path}" \
        -o "${dest}.tmp"
    mv "${dest}.tmp" "$dest"
    # curl's temp file gets default (non-executable) permissions, and `mv`
    # preserves whatever it was — so every fetch would silently strip +x off
    # these scripts. The documented upgrade path is `./scripts/install.sh <tag>`
    # (no outer re-curl), so whatever this run leaves on disk is exactly what the
    # next invocation depends on — re-chmod the scripts here.
    case "$path" in
        scripts/install.sh|scripts/sourcelensctl.sh|scripts/lib/deploy-common.sh)
            chmod +x "$dest"
            ;;
    esac
}

if [ "$LOCAL_MODE" = "true" ]; then
    log "Local mode: using files already in $DEPLOY_PATH, skipping remote fetch"
else
    log "Fetching deploy assets at ${GIT_REF}..."
    for path in "${ASSETS[@]}"; do
        fetch_asset "$path"
    done
    log "Fetched ${#ASSETS[@]} file(s) into $DEPLOY_PATH"
fi

# --- First-run stop: need real secrets before starting anything ---------
if [ ! -f "$DEPLOY_PATH/.env" ]; then
    if [ -f "$DEPLOY_PATH/env.sample" ]; then
        cp "$DEPLOY_PATH/env.sample" "$DEPLOY_PATH/.env"
        chmod 600 "$DEPLOY_PATH/.env"
        log "No .env found — seeded one from env.sample at $DEPLOY_PATH/.env"
    fi
    die "Edit $DEPLOY_PATH/.env with real secrets (DB password, SECRET_KEY, ...), then re-run this command."
fi

# --- Step 2: bootstrap runtime state (only if missing, never overwritten) -
bootstrap_runtime_state() {
    # Lives at docker/nginx/conf.d/upstream.conf — inside the directory nginx
    # bind-mounts as a whole (see docker-compose.yml's nginx volumes comment for
    # why it can't be its own single-file mount).
    mkdir -p "$DEPLOY_PATH/docker/nginx/conf.d"
    if [ ! -f "$DEPLOY_PATH/docker/nginx/conf.d/upstream.conf" ]; then
        log "No upstream.conf yet — initializing from template (color: blue)"
        cp "$DEPLOY_PATH/docker/nginx/upstream.conf.default" \
            "$DEPLOY_PATH/docker/nginx/conf.d/upstream.conf"
    fi

    if [ ! -f "$DEPLOY_PATH/.active_color" ]; then
        log "No .active_color yet — defaulting to blue"
        echo "blue" > "$DEPLOY_PATH/.active_color"
    fi

    local cert_dir="$DEPLOY_PATH/docker/nginx/certs"
    if [ ! -f "$cert_dir/nginx-selfsigned.crt" ] || [ ! -f "$cert_dir/nginx-selfsigned.key" ]; then
        log "No TLS certificate found — generating a self-signed one"
        log "(harmless no-op if you terminate TLS externally, e.g. Nginx Proxy Manager)"
        mkdir -p "$cert_dir"
        docker run --rm -v "$cert_dir:/certs" alpine/openssl req -x509 \
            -newkey rsa:2048 -nodes -days 3650 \
            -keyout /certs/nginx-selfsigned.key \
            -out /certs/nginx-selfsigned.crt \
            -subj "/CN=${DOMAIN:-localhost}" \
            -addext "subjectAltName=DNS:${DOMAIN:-localhost},DNS:*.localhost,IP:127.0.0.1"
    fi
}
bootstrap_runtime_state

# Sourced only now — guaranteed present by this point, either just fetched above
# (remote mode) or already on disk (--local). Provides
# current_color/other_color/wait_for_healthy/switch_traffic, shared with
# scripts/sourcelensctl.sh (day-2 ops: status/restart-workers/restart/recreate/
# rollback — deliberately a separate script, not more subcommands bolted onto
# this one; see CLAUDE.md's "零停机部署" section for why).
# shellcheck source=./lib/deploy-common.sh
source "$DEPLOY_PATH/scripts/lib/deploy-common.sh"

log "Ensuring postgresql/redis are up..."
docker compose up -d postgresql redis

CURRENT_COLOR="$(current_color)"
NEXT_COLOR="$(other_color "$CURRENT_COLOR")"

# First install ever (nothing running yet) is a genuinely different case from a
# normal upgrade, not just "the idle color happens to be empty": there's no old
# color serving traffic to health-gate against or retire, so DEPLOY_COLOR is
# CURRENT_COLOR itself (always "blue", from bootstrap_runtime_state's default)
# rather than NEXT_COLOR.
FIRST_INSTALL=false
if [ "$(docker inspect -f '{{.State.Running}}' "sourcelens-api-${CURRENT_COLOR}" 2>/dev/null)" != "true" ]; then
    FIRST_INSTALL=true
    DEPLOY_COLOR="$CURRENT_COLOR"
    log "sourcelens-api-${CURRENT_COLOR} isn't running — first install, deploying directly to ${DEPLOY_COLOR} (nothing to switch from or retire)"
else
    DEPLOY_COLOR="$NEXT_COLOR"
    log "Current color: $CURRENT_COLOR, deploying to: $DEPLOY_COLOR"
fi

# --- Skip if the currently-active color is already this version ---------
# Only meaningful for a normal upgrade: not in local mode (re-running against the
# same "local" tag to pick up uncommitted changes is the point) and not on first
# install (nothing to compare against yet). Best-effort: if the image carries no
# version LABEL the reading falls back to 0.0.0 and never skips.
if [ "$LOCAL_MODE" != "true" ] && [ "$FIRST_INSTALL" != "true" ]; then
    CURRENT_VERSION=$(color_image_version "$CURRENT_COLOR")
    [ -z "$CURRENT_VERSION" ] && CURRENT_VERSION="0.0.0"
    # Skip when the live version is already >= target: equal (idempotent
    # re-deploy of the same tag) OR strictly newer (current is the max and
    # differs from target). The equal case needs the explicit first clause —
    # `sort -V | tail -1` returns target when they're equal, so the `!=` guard
    # alone would never no-op an equal re-deploy despite the ">=" wording.
    if [ "$CURRENT_VERSION" = "$IMAGE_TAG" ] || \
        [ "$(printf '%s\n%s\n' "$CURRENT_VERSION" "$IMAGE_TAG" | sort -V | tail -1)" != "$IMAGE_TAG" ]; then
        log "SKIP: sourcelens-api-${CURRENT_COLOR} already running $CURRENT_VERSION >= target $IMAGE_TAG"
        exit 0
    fi
fi

export APP_VERSION="$IMAGE_TAG"

# In local mode, build from the working tree instead of pulling a registry
# image — that's the only way local, uncommitted changes end up in the
# container. `docker compose build` is cheap to re-run (layer cache), so this
# stays fast on repeat local iterations too.
sync_images() {
    if [ "$LOCAL_MODE" = "true" ]; then
        log "Building locally: $*"
        docker compose build "$@"
    else
        log "Pulling: $*"
        docker compose pull "$@"
    fi
}

# --- Sync images for the deploy color -----------------------------------
sync_images "sourcelens-api-${DEPLOY_COLOR}" "sourcelens-ui-${DEPLOY_COLOR}"

# --- Bring up the deploy color (its startup runs migrate/collectstatic) --
if [ "$FIRST_INSTALL" != "true" ]; then
    log "Reminder: migrations in this release must be expand/contract-safe —"
    log "the outgoing color keeps serving traffic against the post-migration schema"
    log "for a short window until it's retired below."
fi
log "Starting sourcelens-api-${DEPLOY_COLOR} / sourcelens-ui-${DEPLOY_COLOR}..."
docker compose --profile "$DEPLOY_COLOR" up -d \
    "sourcelens-api-${DEPLOY_COLOR}" "sourcelens-ui-${DEPLOY_COLOR}"

# --- Health-gate before touching any traffic ---------------------------
log "Waiting for sourcelens-api-${DEPLOY_COLOR} to report healthy..."
if ! wait_for_healthy "sourcelens-api-${DEPLOY_COLOR}"; then
    log "Health check timed out"
    docker compose --profile "$DEPLOY_COLOR" stop \
        "sourcelens-api-${DEPLOY_COLOR}" "sourcelens-ui-${DEPLOY_COLOR}"
    if [ "$FIRST_INSTALL" = "true" ]; then
        die "sourcelens-api-${DEPLOY_COLOR} never became healthy; first install aborted"
    fi
    die "sourcelens-api-${DEPLOY_COLOR} never became healthy; deploy aborted, nothing switched, $CURRENT_COLOR stays live"
fi
log "sourcelens-api-${DEPLOY_COLOR} is healthy"

# --- Roll non-blue/green consumers before exposing the new producer ------
# The new worker accepts tasks from the outgoing API while the new LensNode
# advertises capabilities needed by the incoming API. This ordering also keeps
# Celery message-shape changes from reaching an old worker after traffic moves.
# CELERY_TASK_ACKS_LATE + prefetch + stop_grace_period make `up -d` a graceful
# drain-and-replace rather than a hard kill.
log "Rolling backend-worker / backend-scheduler / lensnode to ${IMAGE_TAG}..."
sync_images backend-worker backend-scheduler lensnode
docker compose up -d backend-worker backend-scheduler lensnode

# Always ensure nginx is actually running before doing anything that touches it.
# `switch_traffic` does `docker exec sourcelens-nginx ...`, which fails outright
# if nginx isn't running (crashed, manually stopped, or left half-started from a
# previous failed deploy); on a normal host nginx has been running continuously
# since the last deploy, so this is a no-op `up -d` in the common case.
docker compose up -d nginx

if [ "$FIRST_INSTALL" = "true" ]; then
    # upstream.conf's bootstrap template already points at blue (== DEPLOY_COLOR
    # here), so nginx starts straight into serving it — no switch, nothing to
    # retire.
    log "First install: nginx started serving sourcelens-api-${DEPLOY_COLOR} directly"
    echo "$DEPLOY_COLOR" > "$DEPLOY_PATH/.active_color"
else
    # --- Switch traffic: rewrite upstream.conf, validate, reload ---------
    switch_traffic "$CURRENT_COLOR" "$DEPLOY_COLOR"

    # Record the now-live color immediately — BEFORE the observe/retire window.
    # If the run is interrupted after the switch but before this write,
    # .active_color must already match what nginx serves, or a later
    # sourcelensctl.sh rollback would compute the wrong target.
    echo "$DEPLOY_COLOR" > "$DEPLOY_PATH/.active_color"

    # Record the outgoing color's version so rollback can recreate it from the
    # RIGHT image (its container is about to be removed; the version-tagged
    # image is still in the local cache). Read while it's still running.
    OUTGOING_VERSION="$(color_image_version "$CURRENT_COLOR")"
    if [ -n "$OUTGOING_VERSION" ]; then
        echo "$OUTGOING_VERSION" > "$DEPLOY_PATH/.rollback_version"
    fi

    log "Observing for ${POST_SWITCH_OBSERVE_SECONDS}s before retiring ${CURRENT_COLOR}..."
    sleep "$POST_SWITCH_OBSERVE_SECONDS"

    # --- Retire the previously-active color -------------------------------
    log "Stopping sourcelens-api-${CURRENT_COLOR} / sourcelens-ui-${CURRENT_COLOR}"
    docker compose --profile "$CURRENT_COLOR" stop \
        "sourcelens-api-${CURRENT_COLOR}" "sourcelens-ui-${CURRENT_COLOR}"
    docker compose --profile "$CURRENT_COLOR" rm -f \
        "sourcelens-api-${CURRENT_COLOR}" "sourcelens-ui-${CURRENT_COLOR}"
fi

# One-time cleanup of the legacy pre-blue/green single containers, if present.
# Migrating a host from the old single-container deploy (service backend-api /
# frontend, containers sourcelens-api / sourcelens-ui) to blue/green leaves those
# old containers running as orphans — they are not part of this compose's
# services, so `up` never touches them and they keep holding a DB connection and
# memory. Retire them here. No-op once the host is already on blue/green.
docker rm -f sourcelens-api sourcelens-ui >/dev/null 2>&1 || true

# --- Prune old version tags (remote mode only — --local only ever has a
# single "local"-ish tag, nothing to prune) ------------------------------
# Every deploy pulls a new version tag and never removes the old one, so without
# this, disk fills with retired versions until `docker compose pull`/`build`
# starts failing with "no space left on device". Keeps the 2 most recent version
# tags (current + one rollback target) plus `latest`, removes everything else.
prune_old_image_tags() {
    local repo="$1"
    # `|| true`: when a repo has no non-`latest` tag, `grep` exits 1 and (under
    # `set -o pipefail`) fails the whole pipeline, which with `set -e` would
    # abort the script right after an otherwise-successful deploy.
    docker images "$repo" --format "{{.Tag}}" \
        | grep -vE '^latest$' | sort -rV | tail -n +3 \
        | while read -r tag; do
            docker rmi "${repo}:${tag}" >/dev/null 2>&1 \
                && log "Pruned old image: ${repo}:${tag}"
        done || true
}
if [ "$LOCAL_MODE" != "true" ]; then
    prune_old_image_tags "$API_IMAGE_REPO"
    prune_old_image_tags "$UI_IMAGE_REPO"
    docker image prune -f >/dev/null
fi

log "Deploy complete: ${GIT_REF} (active color: ${DEPLOY_COLOR})"
