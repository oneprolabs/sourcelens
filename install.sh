#!/usr/bin/env bash
# =============================================================================
# SourceLens one-command installer (standalone single-instance deployment)
#
# Drives docker-compose.standalone.yml — the simple production shape (one
# backend-api, one frontend, plain `up -d`, no blue/green). This is a SEPARATE
# path from scripts/install.sh, which does zero-downtime blue/green against
# docker-compose.yml. Pick ONE production shape per host: both share the
# "sourcelens" compose project.
#
# See README.md for the Linux, macOS, and Windows Git Bash commands.
#
# What it does, every single run (idempotent; re-running upgrades):
#   1. Fetches the small set of declarative deploy files (docker-compose
#      .standalone.yml, nginx/postgres config, env.sample) straight from the
#      repository at the given tag — not the whole repo.
#   2. Generates a production .env with random secrets (never overwrites an
#      existing .env; known placeholder values are replaced).
#   3. Patches the compose file: pins the release version tag and, for the
#      cn/gitee channel, rewrites the image registry to Aliyun ACR.
#   4. Generates a self-signed TLS certificate if missing, creates the data
#      directory layout, pulls images, starts the stack and health-checks it.
#   5. When no active system model exists, offers an interactive AI model
#      setup with hidden API-key input and a connection test.
#
# Channels: the download source (github/gitee) selects where release files come
# from AND which registry the application images are pulled from:
#   github -> release files from GitHub, images from Docker Hub (oneprolabs/*)
#   gitee  -> release files from Gitee,  images from Aliyun ACR
# Docker Hub is always used for the infrastructure images (postgres/redis/nginx).
#
# Requirements: Docker + Docker Compose V2 (`docker compose`). Compose V1
# (docker-compose 1.x) is NOT supported — the compose files use V2-only features
# (top-level `name`, `depends_on.condition`).
#
# --source mode — test local installer/deployment files while still pulling
# released application images from the selected registry:
#
#   ./install.sh --source /path/to/sourcelens-repo [tag]
# =============================================================================

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME="sourcelens"
GITHUB_REPO="oneprolabs/sourcelens"
GITHUB_API="https://api.github.com/repos/${GITHUB_REPO}"
GITHUB_RAW_BASE="https://raw.githubusercontent.com/${GITHUB_REPO}"
GITEE_REPO="oneprolabs/sourcelens"
GITEE_API="https://gitee.com/api/v5/repos/${GITEE_REPO}"
GITEE_RAW_BASE="https://gitee.com/${GITEE_REPO}/raw"
DEFAULT_HTTP_PORT=10083
DEFAULT_HTTPS_PORT=10443
# Image registry prefixes. Docker Hub uses just the namespace; Aliyun ACR uses
# host/namespace. Both carry sourcelens-{backend,frontend,lensnode}.
REGISTRY_GITHUB="oneprolabs"
REGISTRY_CN="registry.cn-beijing.aliyuncs.com/oneprolabs"
COMPOSE_FILE="docker-compose.standalone.yml"
MODEL_SETUP_OVERRIDE="docker-compose.model-setup.yml"
INSTALLER_VERSION="0.1.0"
HEALTH_TIMEOUT=240
PULL_PARALLELISM=4

# Release files fetched directly from the repository tag. Only what
# docker-compose.standalone.yml actually mounts/needs is included.
RELEASE_FILES=(
  docker-compose.standalone.yml
  env.sample
  docker/nginx/default.standalone.conf
  docker/nginx/certs/README.md
  docker/postgresql/etc/postgresql.conf
  docker/postgresql/initdb.d/000-create-databases.sql
  docker/postgresql/initdb.d/001-grant-schema-privileges.sh
  docker/postgresql/initdb.d/002-setup-log-permissions.sh
)

# Detect the host platform before selecting platform-specific paths and
# preflight checks.
case "$(uname -s)" in
  Darwin)                PLATFORM="macos" ;;
  Linux)                 PLATFORM="linux" ;;
  MINGW*|MSYS*|CYGWIN*)  PLATFORM="windows" ;;
  *)                     PLATFORM="unknown" ;;
esac

default_install_dir_for_platform() {
  case "$1" in
    macos) printf '/Users/Shared/%s' "${APP_NAME}" ;;
    windows) printf '%s/%s' "${HOME}" "${APP_NAME}" ;;
    *) printf '/opt/%s' "${APP_NAME}" ;;
  esac
}

DEFAULT_INSTALL_DIR="$(default_install_dir_for_platform "${PLATFORM}")"
LEGACY_DEFAULT_INSTALL_DIR="/opt/${APP_NAME}"
if [[ "${PLATFORM}" == "macos" && \
      -f "${LEGACY_DEFAULT_INSTALL_DIR}/${COMPOSE_FILE}" ]]; then
  DEFAULT_INSTALL_DIR="${LEGACY_DEFAULT_INSTALL_DIR}"
fi

# ---------------------------------------------------------------------------
# Defaults (overridable via SOURCELENS_* environment variables / CLI flags)
# ---------------------------------------------------------------------------
INSTALL_DIR="${SOURCELENS_INSTALL_DIR:-${DEFAULT_INSTALL_DIR}}"
INSTALL_DIR_OVERRIDE=0
[[ -n "${SOURCELENS_INSTALL_DIR:-}" ]] && INSTALL_DIR_OVERRIDE=1

HTTP_PORT=""
HTTP_PORT_OVERRIDE=0
[[ -n "${SOURCELENS_HTTP_PORT:-}" ]] && { HTTP_PORT="${SOURCELENS_HTTP_PORT}"; HTTP_PORT_OVERRIDE=1; }
HTTPS_PORT="${SOURCELENS_HTTPS_PORT:-${DEFAULT_HTTPS_PORT}}"
CHANNEL="${SOURCELENS_CHANNEL:-}"
GITHUB_REACHABLE_PENDING=1
GITHUB_REACHABLE=0
DOWNLOAD_SOURCE="${SOURCELENS_DOWNLOAD_SOURCE:-}"
VERSION="${SOURCELENS_VERSION:-}"
REGISTRY="${SOURCELENS_REGISTRY:-}"
DOMAIN="${SOURCELENS_DOMAIN:-}"
DOMAIN_OVERRIDE=0
[[ -n "${SOURCELENS_DOMAIN:-}" ]] && DOMAIN_OVERRIDE=1
DOCKER_MIRROR="${SOURCELENS_DOCKER_MIRROR:-}"

HTTPS="${SOURCELENS_HTTPS:-false}"
ADMIN_USERNAME="${SOURCELENS_ADMIN_USER:-admin}"
ADMIN_EMAIL="${SOURCELENS_ADMIN_EMAIL:-}"
ASSUME_YES=0
[[ "${SOURCELENS_YES:-}" == "1" ]] && ASSUME_YES=1
FORCE=0
SOURCE_DIR=""
INSTALL_ARGS=()
MODEL_CONFIGURED=0
MODEL_SETUP_UNAVAILABLE=0

SCHEME="http"
PORT_SUFFIX=""
LOG_FILE=""
COMPOSE_CMD=()
EXISTING=0
INSTALLED_VERSION=""
PORTS_FROM_EXISTING=0
ENV_EXISTS=0
ADMIN_PASSWORD=""

# ---------------------------------------------------------------------------
# Colored logging helpers
# ---------------------------------------------------------------------------
c_red=$'\033[31m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'
c_cyan=$'\033[34m'; c_bold=$'\033[1m'; c_reset=$'\033[0m'
if [[ ! -t 1 || "${NO_COLOR:-}" == "1" || "${TERM:-}" == "dumb" ]]; then
  c_red=""; c_green=""; c_yellow=""; c_cyan=""; c_bold=""; c_reset=""
fi

log_line() {
  [[ -n "${LOG_FILE}" ]] || return 0
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >>"${LOG_FILE}"
}

log_info()  { printf '%s%s%s\n' "${c_cyan}" "$1" "${c_reset}"; log_line "[INFO]  $1"; }
log_ok()    { printf '%s%s%s\n' "${c_green}" "$1" "${c_reset}"; log_line "[OK]    $1"; }
log_warn()  { printf '%sWARN: %s%s\n' "${c_yellow}" "$1" "${c_reset}"; log_line "[WARN]  $1"; }
log_error() { printf '%sERROR: %s%s\n' "${c_red}" "$1" "${c_reset}" >&2; log_line "[ERROR] $1"; }
log_step()  { printf '\n%s=== %s ===%s\n' "${c_bold}${c_cyan}" "$1" "${c_reset}"; log_line "[STEP]  $1"; }

abort() {
  log_error "$1"
  log_line "install aborted"
  exit 1
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
  cat <<EOF
Usage: install.sh [options] [tag]

Installs/upgrades the standalone (single-instance) SourceLens stack by driving
docker-compose.standalone.yml. Supported platforms: Linux, macOS, and Windows
through Git Bash. Requires Docker and Docker Compose V2 (\`docker compose\`).

Options:
  -d, --dir DIR            Install directory (default: ${DEFAULT_INSTALL_DIR})
  -p, --port PORT          HTTP port (default: ${DEFAULT_HTTP_PORT})
      --https-port PORT    HTTPS port (default: ${DEFAULT_HTTPS_PORT})
  -c, --channel CH         Distribution channel: github | cn (default: auto)
      --download-source SRC  Release-file download source: github | gitee
                             (default: auto; also selects the image registry:
                             github -> Docker Hub, gitee -> Aliyun ACR)
  -v, --version VER        Release version to install (default: latest tag)
      --source DIR         Use local installer/deployment files while still
                           pulling application images from the registry
  -r, --registry REG       Override the application image registry prefix
      --domain HOST        Public hostname / IP (default: auto-detect)
      --admin-user USER    Initial admin username (default: admin)
      --admin-email EMAIL  Initial admin email (default: admin@<domain>)
      --https              Configure URLs for HTTPS behind a TLS proxy
      --docker-mirror URL  Configure a Docker Hub registry mirror (linux)
  -y, --yes                Accept install defaults and skip model setup
      --force              Upgrade without confirmation
  -h, --help               Show this help

Environment overrides: SOURCELENS_INSTALL_DIR, SOURCELENS_HTTP_PORT,
SOURCELENS_HTTPS_PORT, SOURCELENS_VERSION, SOURCELENS_REGISTRY,
SOURCELENS_DOMAIN, SOURCELENS_CHANNEL, SOURCELENS_DOWNLOAD_SOURCE,
SOURCELENS_DOCKER_MIRROR, SOURCELENS_ADMIN_USER, SOURCELENS_ADMIN_EMAIL,
SOURCELENS_YES=1, SOURCELENS_HTTPS=true
EOF
}

# ---------------------------------------------------------------------------
# Interaction helpers
# ---------------------------------------------------------------------------
confirm() {
  local prompt="$1" answer=""
  [[ "${ASSUME_YES}" == "1" ]] && return 0
  while :; do
    if [[ ! -t 0 ]]; then
      if [[ -e /dev/tty ]]; then
        printf '%s [yes/No] ' "${prompt}" >/dev/tty
        read -r answer </dev/tty || answer="no"
      else
        return 0 # fully non-interactive: proceed with defaults
      fi
    else
      printf '%s [yes/No] ' "${prompt}"
      read -r answer || answer="no"
    fi
    answer="$(printf '%s' "${answer}" | tr '[:upper:]' '[:lower:]')"
    case "${answer}" in
      yes) return 0 ;;
      ""|no) return 1 ;;
      *)
        if [[ ! -t 0 && -e /dev/tty ]]; then
          printf 'Enter yes or no.\n' >/dev/tty
        else
          printf 'Enter yes or no.\n'
        fi
        ;;
    esac
  done
}

prompt_value() {
  local var_name="$1" label="$2" default="$3" answer=""
  if [[ "${ASSUME_YES}" == "1" || ! -t 0 ]]; then
    printf -v "${var_name}" '%s' "${default}"
    return 0
  fi
  printf '%s [%s]: ' "${label}" "${default}"
  read -r answer || true
  printf -v "${var_name}" '%s' "${answer:-${default}}"
}

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
require_root() {
  if [[ "${PLATFORM}" == "windows" ]]; then
    log_info "Running in Windows Git Bash; Docker Desktop provides the engine"
    return 0
  fi
  if [[ "$(id -u)" -eq 0 ]]; then return 0; fi
  if command -v sudo >/dev/null 2>&1 && [[ -f "$0" && "$0" != "bash" && "$0" != "-bash" ]]; then
    log_warn "not running as root, re-executing with sudo"
    exec sudo -E bash "$0" "${INSTALL_ARGS[@]}"
  fi
  abort "install.sh must run as root (or via sudo). e.g. sudo bash install.sh"
}

detect_os() {
  OS_ID="unknown"; OS_NAME="unknown"
  if [[ "${PLATFORM}" == "macos" ]]; then
    OS_ID="macos"; OS_NAME="macOS $(sw_vers -productVersion 2>/dev/null)"
  elif [[ "${PLATFORM}" == "windows" ]]; then
    OS_ID="windows"; OS_NAME="Windows (Git Bash)"
  elif [[ -r /etc/os-release ]]; then
    OS_ID=$(. /etc/os-release; printf '%s' "${ID:-unknown}")
    OS_NAME=$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-unknown}")
  fi
  log_info "OS: ${OS_NAME} (${OS_ID})"
  if [[ "${PLATFORM}" != "macos" && "${PLATFORM}" != "linux" && \
        "${PLATFORM}" != "windows" ]]; then
    local message="unsupported platform '${PLATFORM}'; supported: Linux, "
    message+="macOS, Windows Git Bash"
    abort "${message}"
  fi
}

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64)   ARCH="amd64" ;;
    aarch64|arm64)  ARCH="arm64" ;;
    *) abort "unsupported CPU architecture: $(uname -m) (supported: amd64, arm64)" ;;
  esac
  log_info "Architecture: ${ARCH}"
}

check_memory() {
  local mem_kb=0 mem_gb=0
  if [[ "${PLATFORM}" == "macos" ]]; then
    local pagesize=0 pf=0 pi=0 ps=0
    pagesize="$(sysctl -n hw.pagesize 2>/dev/null || echo 4096)"
    pf="$(vm_stat 2>/dev/null | awk '/Pages free/ {print $3}' | tr -d '.')"
    pi="$(vm_stat 2>/dev/null | awk '/Pages inactive/ {print $3}' | tr -d '.')"
    ps="$(vm_stat 2>/dev/null | awk '/Pages speculative/ {print $3}' | tr -d '.')"
    pf="${pf:-0}"; pi="${pi:-0}"; ps="${ps:-0}"
    mem_kb="$(( (pf + pi + ps) * pagesize / 1024 ))"
  elif [[ "${PLATFORM}" == "windows" ]]; then
    log_warn "memory detection is unavailable in Git Bash; skipping check"
    return 0
  else
    mem_kb="$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  fi
  mem_kb="${mem_kb:-0}"
  [[ "${mem_kb}" =~ ^[0-9]+$ ]] || mem_kb=0
  mem_gb=$((mem_kb / 1024 / 1024))
  log_info "Memory: ${mem_gb} GB available"
  if ((mem_gb < 2)); then
    abort "available memory is too low (${mem_gb} GB); at least 2 GB required (4 GB recommended)"
  elif ((mem_gb < 4)); then
    log_warn "memory below the 4 GB recommendation (${mem_gb} GB)"
  fi
}

check_disk() {
  local check_dir="${INSTALL_DIR}" free_kb=0 free_gb=0
  while [[ ! -d "${check_dir}" && "${check_dir}" != "/" ]]; do
    check_dir="$(dirname "${check_dir}")"
  done
  free_kb="$(df -Pk "${check_dir}" 2>/dev/null | awk 'NR==2 {print $4}')"
  free_kb="${free_kb:-0}"
  free_gb=$((free_kb / 1024 / 1024))
  log_info "Disk space on ${check_dir} (target ${INSTALL_DIR}): ${free_gb} GB free"
  if ((free_gb < 5)); then
    abort "insufficient disk space (${free_gb} GB free); at least 5 GB required (20 GB recommended)"
  elif ((free_gb < 20)); then
    log_warn "disk space below the 20 GB recommendation (${free_gb} GB free)"
  fi
}

check_tools() {
  local tool
  for tool in curl tar gzip openssl; do
    command -v "${tool}" >/dev/null 2>&1 || abort "required tool not found: ${tool}"
  done
  if [[ "${PLATFORM}" == "windows" ]] && \
     ! command -v cygpath >/dev/null 2>&1; then
    abort "cygpath not found; run this installer from Windows Git Bash"
  fi
  log_ok "Required tools present (curl, tar, gzip, openssl)"
}

prompt_install_dir() {
  if [[ "${INSTALL_DIR_OVERRIDE}" != "1" && "${ASSUME_YES}" != "1" && -t 0 ]]; then
    prompt_value INSTALL_DIR "Install directory" "${INSTALL_DIR}"
  fi
  INSTALL_DIR="${INSTALL_DIR%/}"
}

# ---------------------------------------------------------------------------
# Channel detection & network connectivity
# ---------------------------------------------------------------------------
probe_github_reachable() {
  if [[ "${GITHUB_REACHABLE_PENDING}" == "1" ]]; then
    if curl -fsSI --max-time 8 -o /dev/null https://github.com >/dev/null 2>&1; then
      GITHUB_REACHABLE=1
    else
      GITHUB_REACHABLE=0
    fi
    GITHUB_REACHABLE_PENDING=0
  fi
}

detect_channel() {
  if [[ -z "${CHANNEL}" ]]; then
    probe_github_reachable
    if [[ "${GITHUB_REACHABLE}" == "1" ]]; then
      CHANNEL="github"
    else
      CHANNEL="cn"
    fi
  fi
  CHANNEL="$(printf '%s' "${CHANNEL}" | tr '[:upper:]' '[:lower:]')"
  case "${CHANNEL}" in
    github|cn) ;;
    *) abort "invalid channel '${CHANNEL}' (supported: github, cn)" ;;
  esac
}

detect_download_source() {
  if [[ -z "${DOWNLOAD_SOURCE}" ]]; then
    if [[ "${CHANNEL}" == "cn" ]]; then
      DOWNLOAD_SOURCE="gitee"
    else
      probe_github_reachable
      if [[ "${GITHUB_REACHABLE}" == "1" ]]; then
        DOWNLOAD_SOURCE="github"
      elif curl -fsSI --max-time 8 -o /dev/null https://gitee.com >/dev/null 2>&1; then
        DOWNLOAD_SOURCE="gitee"
      else
        DOWNLOAD_SOURCE="github"
        log_warn "could not reach github.com or gitee.com; assuming github"
      fi
    fi
  fi
  DOWNLOAD_SOURCE="$(printf '%s' "${DOWNLOAD_SOURCE}" | tr '[:upper:]' '[:lower:]')"
  case "${DOWNLOAD_SOURCE}" in
    github|gitee) ;;
    *) abort "invalid download source '${DOWNLOAD_SOURCE}' (supported: github, gitee)" ;;
  esac
  # The download source selects the application image registry: github -> Docker
  # Hub, gitee -> Aliyun ACR. An explicit --channel cn also forces the CN path.
  if [[ -z "${REGISTRY}" ]]; then
    if [[ "${DOWNLOAD_SOURCE}" == "gitee" || "${CHANNEL}" == "cn" ]]; then
      REGISTRY="${REGISTRY_CN}"
    else
      REGISTRY="${REGISTRY_GITHUB}"
    fi
  fi
  REGISTRY="${REGISTRY%/}"
}

# ---------------------------------------------------------------------------
# Docker & Docker Compose
# ---------------------------------------------------------------------------
check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    abort "Docker is not installed. Install Docker (https://docs.docker.com/engine/install/) or Docker Desktop, then re-run this installer."
  fi
  DOCKER_VERSION="$(docker --version 2>/dev/null | sed -n 's/^Docker version \([0-9][0-9.]*\).*/\1/p')"
  log_info "Docker: ${DOCKER_VERSION:-unknown}"
  docker info >/dev/null 2>&1 \
    || abort "docker daemon is not running; start it (Docker Desktop on ${OS_NAME}) and re-run the installer"
  log_ok "Docker daemon is running"
  configure_docker_mirror
}

configure_docker_mirror() {
  [[ -n "${DOCKER_MIRROR}" ]] || return 0
  log_info "Configuring Docker Hub mirror: ${DOCKER_MIRROR}"
  if [[ "${PLATFORM}" != "linux" ]]; then
    log_warn "Docker Desktop does not read /etc/docker/daemon.json; configure the mirror in Docker Desktop settings (registry-mirrors) manually"
    return 0
  fi
  mkdir -p /etc/docker
  if [[ -f /etc/docker/daemon.json ]]; then
    if command -v python3 >/dev/null 2>&1; then
      python3 -c 'import json, sys
p = "/etc/docker/daemon.json"
d = json.load(open(p))
m = d.setdefault("registry-mirrors", [])
for x in sys.argv[1:]:
    if x not in m:
        m.append(x)
json.dump(d, open(p, "w"), indent=2)' "${DOCKER_MIRROR}"
    else
      log_warn "daemon.json exists but python3 is unavailable; mirror config not merged"
      return 0
    fi
  else
    printf '{\n  "registry-mirrors": ["%s"]\n}\n' "${DOCKER_MIRROR}" >/etc/docker/daemon.json
  fi
  systemctl restart docker >/dev/null 2>&1 || service docker restart >/dev/null 2>&1 || true
  sleep 3
  docker info >/dev/null 2>&1 || log_warn "docker daemon did not come back after mirror configuration"
  log_ok "Docker Hub mirror configured"
}

check_compose() {
  COMPOSE_CMD=()
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    abort "Docker Compose v1 (docker-compose) is installed, but Docker Compose V2 (docker compose) is required: the compose files use V2-only features (top-level 'name' field, 'depends_on.condition' health gating). Install the Compose V2 plugin and re-run."
  else
    abort "Docker Compose is not installed. Docker Compose V2 (docker compose) is required; install the plugin and re-run."
  fi
  "${COMPOSE_CMD[@]}" version >/dev/null 2>&1 || abort "Docker Compose is not usable"
  log_ok "Compose: $("${COMPOSE_CMD[@]}" version | head -n 1)"
}

# The standalone stack shares project name "sourcelens" with the blue/green
# stack; refuse to run on a host where blue/green is already active.
check_no_bluegreen() {
  local c=""
  if [[ -n "${SOURCE_DIR}" && "${INSTALL_DIR}" != "${DEFAULT_INSTALL_DIR}" ]]; then
    return 0
  fi
  for c in sourcelens-api-blue sourcelens-api-green; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "${c}" 2>/dev/null)" == "true" ]]; then
      abort "blue/green stack is active on this host (${c} is running). The standalone installer shares project 'sourcelens' with it; use scripts/install.sh instead, or install on a separate host."
    fi
  done
}

# ---------------------------------------------------------------------------
# Interactive configuration
# ---------------------------------------------------------------------------
configure() {
  if [[ -z "${DOMAIN}" ]]; then
    if [[ "${PLATFORM}" == "linux" ]]; then
      DOMAIN="$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}' | head -n1)"
      [[ -z "${DOMAIN}" ]] && DOMAIN="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    elif [[ "${PLATFORM}" == "macos" ]]; then
      DOMAIN="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
    else
      DOMAIN="127.0.0.1"
    fi
    [[ -z "${DOMAIN}" ]] && DOMAIN="$(hostname -f 2>/dev/null || hostname)"
    [[ -z "${DOMAIN}" ]] && DOMAIN="127.0.0.1"
  fi
  [[ "${DOMAIN}" =~ ^[A-Za-z0-9._:-]+$ ]] || abort "invalid domain/host: ${DOMAIN}"
  if [[ -z "${ADMIN_EMAIL}" ]]; then ADMIN_EMAIL="admin@${DOMAIN}"; fi
  if [[ "${HTTPS}" == "true" ]]; then SCHEME="https"; fi
}

# ---------------------------------------------------------------------------
# Port conflict detection
# ---------------------------------------------------------------------------
port_in_use() {
  local port="$1"
  if [[ "${PLATFORM}" == "macos" ]] && command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if [[ "${PLATFORM}" == "windows" ]] && \
     command -v netstat >/dev/null 2>&1; then
    local netstat_program=""
    netstat_program='$1 ~ /^TCP/ && $4 == "LISTENING" && $2 ~ p '
    netstat_program+='{found=1} END {exit !found}'
    netstat -ano 2>/dev/null \
      | awk -v p=":${port}$" \
          "${netstat_program}"
    return $?
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH 2>/dev/null | awk -v p=":${port}$" '$4 ~ p { found=1 } END { exit !found }'
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | awk -v p=":${port}$" '$4 ~ p { found=1 } END { exit !found }'
  else
    return 1
  fi
}

next_free_port() {
  local port="$1"
  while port_in_use "${port}"; do port=$((port + 1)); done
  printf '%s' "${port}"
}

validate_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535))
}

resolve_ports() {
  [[ -n "${HTTP_PORT}" ]] || HTTP_PORT="${DEFAULT_HTTP_PORT}"
  [[ -n "${HTTPS_PORT}" ]] || HTTPS_PORT="${DEFAULT_HTTPS_PORT}"
  validate_port "${HTTP_PORT}" || abort "invalid HTTP port: ${HTTP_PORT}"
  validate_port "${HTTPS_PORT}" || abort "invalid HTTPS port: ${HTTPS_PORT}"
  if [[ "${PORTS_FROM_EXISTING}" != "1" ]] && port_in_use "${HTTP_PORT}"; then
    if [[ "${ASSUME_YES}" != "1" && -t 0 ]]; then
      while port_in_use "${HTTP_PORT}"; do
        prompt_value HTTP_PORT "Port ${HTTP_PORT} is in use; enter a free port" "$((HTTP_PORT + 1))"
        validate_port "${HTTP_PORT}" || HTTP_PORT="$((HTTP_PORT + 1))"
      done
    else
      local old_http="${HTTP_PORT}"
      HTTP_PORT="$(next_free_port "$((old_http + 1))")"
      log_warn "port ${old_http} is in use; using ${HTTP_PORT} instead"
    fi
  fi
  if [[ "${PORTS_FROM_EXISTING}" != "1" ]] && port_in_use "${HTTPS_PORT}"; then
    local old_https="${HTTPS_PORT}"
    HTTPS_PORT="$(next_free_port "$((old_https + 1))")"
    log_warn "port ${old_https} is in use; using ${HTTPS_PORT} instead"
  fi
  PORT_SUFFIX=""
  if [[ "${SCHEME}" == "https" ]]; then
    [[ "${HTTP_PORT}" != "443" ]] && PORT_SUFFIX=":${HTTP_PORT}"
  else
    [[ "${HTTP_PORT}" != "80" ]] && PORT_SUFFIX=":${HTTP_PORT}"
  fi
  log_info "Ports: HTTP=${HTTP_PORT} HTTPS=${HTTPS_PORT}"
}

# ---------------------------------------------------------------------------
# Existing installation detection
# ---------------------------------------------------------------------------
detect_existing() {
  EXISTING=0; INSTALLED_VERSION=""; PORTS_FROM_EXISTING=0
  [[ -f "${INSTALL_DIR}/docker-compose.standalone.yml" ]] || return 0
  EXISTING=1
  log_warn "Existing standalone installation detected at ${INSTALL_DIR}"
  if [[ -f "${INSTALL_DIR}/install-info.env" ]]; then
    INSTALLED_VERSION="$(info_key "${INSTALL_DIR}/install-info.env" SOURCELENS_VERSION)"
    [[ -n "${INSTALLED_VERSION}" ]] && log_info "Installed version: ${INSTALLED_VERSION}"
    local ports_found=0
    if [[ "${HTTP_PORT_OVERRIDE}" == "0" ]]; then
      HTTP_PORT="$(info_key "${INSTALL_DIR}/install-info.env" SOURCELENS_HTTP_PORT)"
      [[ -n "${HTTP_PORT}" ]] && ports_found=1
    fi
    if [[ "${DOMAIN_OVERRIDE}" == "0" ]]; then
      DOMAIN="$(info_key "${INSTALL_DIR}/install-info.env" SOURCELENS_DOMAIN)"
      [[ -n "${DOMAIN}" ]] && DOMAIN_OVERRIDE=1
    fi
    if ((ports_found)); then PORTS_FROM_EXISTING=1; fi
  fi
}

# ---------------------------------------------------------------------------
# Release version resolution & artifact download
# ---------------------------------------------------------------------------
resolve_version() {
  if [[ -z "${VERSION}" ]]; then
    if [[ -n "${SOURCE_DIR}" ]]; then
      local v=""
      v="$(git -C "${SOURCE_DIR}" describe --tags --abbrev=0 2>/dev/null || true)"
      v="${v#v}"
      if [[ -n "${v}" ]]; then
        VERSION="${v}"
        log_info "Using version v${VERSION} from local repository ${SOURCE_DIR}"
      fi
    fi
    if [[ -z "${VERSION}" && "${EXISTING}" == "1" && -n "${INSTALLED_VERSION}" ]]; then
      VERSION="${INSTALLED_VERSION#v}"
      log_info "Reusing installed version v${VERSION} (rerun)"
    elif [[ -z "${VERSION}" ]]; then
      log_info "Resolving latest release version..."
      local api="" url
      for url in "${GITHUB_API}/tags?per_page=1" "${GITEE_API}/tags?per_page=1&sort=updated&direction=desc"; do
        api="$(curl -fsSL --max-time 20 "${url}" 2>/dev/null \
          | grep -oE '"name": *"[^"]*"' | head -n 1 | sed -E 's/.*"name": *"([^"]*)".*/\1/' || true)"
        [[ -n "${api}" ]] && { VERSION="${api#v}"; break; }
      done
      [[ -z "${VERSION}" ]] && abort "could not resolve the latest release tag; pass --version explicitly"
      log_info "Latest release: v${VERSION}"
    fi
  fi
  VERSION="${VERSION#v}"
  [[ "${VERSION}" =~ ^[0-9a-zA-Z][0-9a-zA-Z._-]*$ ]] || abort "invalid version: ${VERSION}"
  TAG="v${VERSION}"
  if [[ "${INSTALLER_VERSION}" != "${VERSION}" ]]; then
    log_warn "installer v${INSTALLER_VERSION} is installing release v${VERSION}"
  fi
}

release_raw_url() {
  local rel="$1"
  if [[ "${DOWNLOAD_SOURCE}" == "gitee" ]]; then
    printf '%s/%s/%s' "${GITEE_RAW_BASE}" "${TAG}" "${rel}"
  else
    printf '%s/%s/%s' "${GITHUB_RAW_BASE}" "${TAG}" "${rel}"
  fi
}

# Download a single release file, transparently falling back to gitee when the
# github transfer is slow or fails. Sets LAST_HTTP to the winning attempt's
# code. Returns 0 once a source either succeeds (200) or reports 404.
download_release_file() {
  local rel="$1" url="" code=""
  url="$(release_raw_url "${rel}")"
  code="$(curl -sSL --retry 2 --retry-delay 2 \
    --speed-limit 409600 --speed-time 15 --max-time 600 \
    -o "${INSTALL_DIR}/${rel}" -w '%{http_code}' "${url}" 2>/dev/null || true)"
  if [[ "${code}" == "200" ]] || [[ "${code}" == "404" ]]; then
    LAST_HTTP="${code}"
    return 0
  fi
  if [[ "${DOWNLOAD_SOURCE}" != "gitee" ]]; then
    log_warn "github download of ${rel} is slow/failed (HTTP ${code:-timeout}); switching to gitee"
    DOWNLOAD_SOURCE="gitee"
    rm -f "${INSTALL_DIR}/${rel}"
    url="${GITEE_RAW_BASE}/${TAG}/${rel}"
    code="$(curl -sSL --retry 2 --retry-delay 2 \
      --speed-limit 409600 --speed-time 15 --max-time 120 \
      -o "${INSTALL_DIR}/${rel}" -w '%{http_code}' "${url}" 2>/dev/null || true)"
    LAST_HTTP="${code}"
    return 0
  fi
  LAST_HTTP="${code}"; return 1
}

fetch_release_files() {
  log_step "Installing release files (source: ${DOWNLOAD_SOURCE})"
  local rel="" src="" dir="" target="" http="" done=0
  local total="${#RELEASE_FILES[@]}"
  for rel in "${RELEASE_FILES[@]}"; do
    dir="${INSTALL_DIR}/$(dirname "${rel}")"
    target="${INSTALL_DIR}/${rel}"
    mkdir -p "${dir}"
    if [[ -d "${target}" ]]; then
      rmdir "${target}" 2>/dev/null \
        || abort "expected release file but found a non-empty directory: ${target}"
      log_warn "replacing an empty directory with release file ${rel}"
    fi
    if [[ -n "${SOURCE_DIR}" ]]; then
      src="${SOURCE_DIR%/}/${rel}"
      [[ -f "${src}" ]] || abort "source directory ${SOURCE_DIR} is missing ${rel}"
      cp -f "${src}" "${target}"
      done=$((done + 1))
    else
      download_release_file "${rel}" || true
      http="${LAST_HTTP}"
      if [[ "${http}" != "200" ]]; then
        rm -f "${INSTALL_DIR}/${rel}"
        if [[ "${http}" == "404" ]]; then
          if [[ "${rel}" == "docker-compose.standalone.yml" ]]; then
            if [[ "${DOWNLOAD_SOURCE}" == "gitee" ]]; then
              abort "failed to download ${rel} (${url}): tag ${TAG} not found on gitee — the mirror has not synced version ${VERSION}; sync it from GitHub or use --download-source github / an available --version"
            fi
            abort "failed to download ${rel} (${url}): tag ${TAG} does not contain ${rel} (HTTP 404)"
          fi
          if [[ "${DOWNLOAD_SOURCE}" == "gitee" ]]; then
            log_warn "skipping ${rel}: not present in tag ${TAG} on gitee (the mirror may not have synced version ${VERSION})"
          else
            log_warn "skipping ${rel}: not present in tag ${TAG} (HTTP 404)"
          fi
        elif [[ "${rel}" == "docker-compose.standalone.yml" ]]; then
          abort "failed to download ${rel} (${url}): network error (HTTP ${http:-timeout}); check the version and network"
        else
          log_warn "skipping ${rel}: network error while downloading (HTTP ${http:-timeout})"
        fi
      else
        done=$((done + 1))
      fi
    fi
    if [[ -t 1 ]]; then
      printf '\r%sDownloading config files: %s/%s%s' "${c_cyan}" "${done}" "${total}" "${c_reset}"
    fi
  done
  [[ -t 1 ]] && printf '\n'
  # Postgres initdb shell scripts must stay executable for the official image.
  chmod +x "${INSTALL_DIR}/docker/postgresql/initdb.d"/*.sh 2>/dev/null || true
  log_ok "Release files installed"
}

prepare_model_setup_overlay() {
  local command_rel="backend/core/management/commands/setup_ai_model.py"
  local command_src="${SOURCE_DIR%/}/${command_rel}"
  local overlay_dir="${INSTALL_DIR}/docker/model-setup"
  local overlay="${INSTALL_DIR}/${MODEL_SETUP_OVERRIDE}"
  rm -f "${overlay}" "${overlay_dir}/setup_ai_model.py"
  [[ -n "${SOURCE_DIR}" && -f "${command_src}" ]] || return 0

  mkdir -p "${overlay_dir}"
  cp -f "${command_src}" "${overlay_dir}/setup_ai_model.py"
  {
    printf 'services:\n'
    printf '  backend-api:\n'
    printf '    volumes:\n'
    printf '      - ./docker/model-setup/setup_ai_model.py:'
    printf '/opt/backend/core/management/commands/setup_ai_model.py:ro\n'
  } >"${overlay}"
  log_info "Model setup is enabled for this local installer test"
}

# ---------------------------------------------------------------------------
# Configuration generation (secrets are never prompted for)
# ---------------------------------------------------------------------------
gen_secret()   { openssl rand -hex 48; }
gen_password() { openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20; }

sed_inplace() {
  local file="$1"; shift
  sed -i.bak "$@" "${file}" && rm -f "${file}.bak"
}

set_env_key() {
  local file="$1" key="$2" value="$3"
  if grep -qE "^${key}=" "${file}"; then
    sed_inplace "${file}" -E "s|^${key}=.*|${key}=${value}|"
  else
    printf '%s=%s\n' "${key}" "${value}" >>"${file}"
  fi
}

read_env_key() {
  grep -E "^$2=" "$1" | tail -n1 | cut -d= -f2- | tr -d "'\"" || true
}

info_key() {
  grep -E "^$2=" "$1" | tail -n1 | cut -d= -f2- || true
}

# Replace known insecure placeholder values left over from env.sample so a
# seeded .env is never deployed with default credentials.
secure_placeholder() {
  local env_file="$1" key="$2" placeholder="$3"
  local current=""
  current="$(read_env_key "${env_file}" "${key}")"
  if [[ -n "${current}" && "${current}" == "${placeholder}" ]]; then
    set_env_key "${env_file}" "${key}" "$(gen_password)"
    log_info "Replaced placeholder ${key}"
  fi
}

generate_env() {
  log_step "Generating configuration"
  local env_file="${INSTALL_DIR}/.env"
  if [[ -f "${env_file}" ]]; then
    ENV_EXISTS=1
    log_warn ".env exists — preserving it (the installer never overwrites .env)"
    secure_placeholder "${env_file}" SECRET_KEY "change-me"
    secure_placeholder "${env_file}" POSTGRES_PASSWORD "postgres"
    secure_placeholder "${env_file}" LENSNODE_TOKEN "change-me-lensnode-token"
    secure_placeholder "${env_file}" DJANGO_SUPERUSER_PASSWORD "adminpassword"
    set_env_key "${env_file}" NGINX_HTTP_PORT "${HTTP_PORT}"
    set_env_key "${env_file}" NGINX_HTTPS_PORT "${HTTPS_PORT}"
    ensure_turnstile_boots "${env_file}"
    local stored_admin_username stored_admin_email
    stored_admin_username="$(
      read_env_key "${env_file}" DJANGO_SUPERUSER_USERNAME
    )"
    stored_admin_email="$(
      read_env_key "${env_file}" DJANGO_SUPERUSER_EMAIL
    )"
    if [[ -n "${stored_admin_username}" ]]; then
      ADMIN_USERNAME="${stored_admin_username}"
    fi
    if [[ -n "${stored_admin_email}" ]]; then
      ADMIN_EMAIL="${stored_admin_email}"
    fi
    ADMIN_PASSWORD="$(read_env_key "${env_file}" DJANGO_SUPERUSER_PASSWORD)"
    return 0
  fi

  [[ -f "${INSTALL_DIR}/env.sample" ]] || abort "env.sample not found at ${INSTALL_DIR}/env.sample"
  cp "${INSTALL_DIR}/env.sample" "${env_file}"
  chmod 600 "${env_file}"

  set_env_key "${env_file}" SECRET_KEY "$(gen_secret)"
  set_env_key "${env_file}" DJANGO_DEBUG false
  set_env_key "${env_file}" ALLOWED_HOSTS "${DOMAIN},localhost,127.0.0.1"
  set_env_key "${env_file}" CSRF_TRUSTED_ORIGINS "${SCHEME}://${DOMAIN}${PORT_SUFFIX},${SCHEME}://127.0.0.1${PORT_SUFFIX}"
  set_env_key "${env_file}" CORS_ALLOWED_ORIGINS "${SCHEME}://${DOMAIN}${PORT_SUFFIX}"
  set_env_key "${env_file}" SITE_DOMAIN "${DOMAIN}${PORT_SUFFIX}"
  set_env_key "${env_file}" FRONTEND_URL "${SCHEME}://${DOMAIN}${PORT_SUFFIX}"
  set_env_key "${env_file}" ACCOUNT_DEFAULT_HTTP_PROTOCOL "${SCHEME}"
  set_env_key "${env_file}" NGINX_HTTP_PORT "${HTTP_PORT}"
  set_env_key "${env_file}" NGINX_HTTPS_PORT "${HTTPS_PORT}"
  set_env_key "${env_file}" POSTGRES_PASSWORD "$(gen_password)"
  set_env_key "${env_file}" LENSNODE_NAME "${DOMAIN}"
  set_env_key "${env_file}" LENSNODE_TOKEN "$(gen_password)"
  set_env_key "${env_file}" DJANGO_SUPERUSER_USERNAME "${ADMIN_USERNAME}"
  set_env_key "${env_file}" DJANGO_SUPERUSER_EMAIL "${ADMIN_EMAIL}"
  ADMIN_PASSWORD="$(gen_password)"
  set_env_key "${env_file}" DJANGO_SUPERUSER_PASSWORD "${ADMIN_PASSWORD}"
  ensure_turnstile_boots "${env_file}"
  log_ok ".env generated from env.sample with random secrets (chmod 600)"
}

# Turnstile needs real Cloudflare keys; with an empty secret and DJANGO_DEBUG
# false the backend refuses to start (fail-fast guard in
# core/settings/accounts.py). The one-command installer cannot mint real keys,
# so disable the check unless the operator has already configured a secret —
# login then works without the Turnstile widget, and they can enable it later.
ensure_turnstile_boots() {
  local env_file="$1" ts_secret=""
  ts_secret="$(read_env_key "${env_file}" TURNSTILE_SECRET_KEY)"
  if [[ -z "${ts_secret}" ]]; then
    set_env_key "${env_file}" TURNSTILE_ENABLED false
    log_warn "TURNSTILE_SECRET_KEY is empty — set TURNSTILE_ENABLED=false so the backend can start (login runs without Cloudflare Turnstile). To enable it later, set a real secret and frontend site key, then flip TURNSTILE_ENABLED=true."
  fi
}

# ---------------------------------------------------------------------------
# Compose patching per channel & install parameters
# ---------------------------------------------------------------------------
patch_compose() {
  log_step "Patching compose file (channel: ${CHANNEL}, source: ${DOWNLOAD_SOURCE})"
  local compose="${INSTALL_DIR}/${COMPOSE_FILE}"
  [[ -f "${compose}" ]] || abort "compose file missing at ${compose}"
  # Pin version-consistent image tags and rewrite the registry prefix.
  # github -> REGISTRY_GITHUB ("oneprolabs", a no-op rewrite);
  # gitee/cn -> Aliyun ACR host/namespace.
  # Delimiter is '#' (not '|'): '|' is the ERE alternation operator inside the
  # pattern and would close the expression early on BSD sed.
  sed_inplace "${compose}" -E \
    "s#image:[[:space:]]*oneprolabs/(sourcelens-(backend|frontend|lensnode)):.*\$#image: ${REGISTRY}/\1:${VERSION}#"
  if ! grep -q "image: ${REGISTRY}/sourcelens-backend:${VERSION}" "${compose}"; then
    abort "failed to pin image references in ${compose}"
  fi
  patch_platform_compose "${compose}"
  if [[ -n "${SOURCE_DIR}" && "${INSTALL_DIR}" != "${DEFAULT_INSTALL_DIR}" ]]; then
    local project_name=""
    project_name="$(basename "${INSTALL_DIR}" \
      | tr '[:upper:]' '[:lower:]' \
      | tr -cs 'a-z0-9_-' '-')"
    project_name="${project_name#-}"
    project_name="${project_name%-}"
    [[ -n "${project_name}" ]] \
      || abort "could not derive a test project name from ${INSTALL_DIR}"
    sed_inplace "${compose}" -E "s/^name:.*$/name: ${project_name}/"
    sed_inplace "${compose}" -E '/^[[:space:]]*container_name:/d'
    log_info "Local test environment is isolated from the existing installation"
  fi
  log_ok "Compose patched (registry: ${REGISTRY}, images tagged :${VERSION})"
}

patch_platform_compose() {
  local compose="$1"
  if [[ "${PLATFORM}" == "windows" ]]; then
    local pg_pattern="" redis_pattern="" log_pattern=""
    pg_pattern='s#^([[:space:]]*)-[[:space:]]+\./data/postgresql/'
    pg_pattern+='data:/var/lib/postgresql/data[[:space:]]*$#'
    pg_pattern+='\1- postgresql_data:/var/lib/postgresql/data#'
    redis_pattern='s#^([[:space:]]*)-[[:space:]]+\./data/redis:'
    redis_pattern+='/data[[:space:]]*$#\1- redis_data:/data#'
    log_pattern='/^[[:space:]]*-[[:space:]]+\.\/data\/logs\/'
    log_pattern+='(postgresql|redis):\/var\/log\/'
    log_pattern+='(postgresql|redis)[[:space:]]*$/d'
    sed_inplace "${compose}" -E "${pg_pattern}"
    sed_inplace "${compose}" -E "${redis_pattern}"
    sed_inplace "${compose}" -E "${log_pattern}"
    {
      printf '\nvolumes:\n'
      printf '  postgresql_data:\n'
      printf '  redis_data:\n'
    } >>"${compose}"
    grep -q 'postgresql_data:/var/lib/postgresql/data' "${compose}" \
      || abort "failed to configure PostgreSQL storage on Windows"
    grep -q 'redis_data:/data' "${compose}" \
      || abort "failed to configure Redis storage on Windows"
    log_info "PostgreSQL and Redis use Docker volumes on Windows"
    return 0
  fi
  [[ "${PLATFORM}" == "macos" ]] || return 0
  local log_pattern=""
  log_pattern='^[[:space:]]*-[[:space:]]+\.\/data\/logs\/postgresql:'
  log_pattern+='\/var\/log\/postgresql[[:space:]]*$'
  sed_inplace "${compose}" -E "/${log_pattern}/d"
  if grep -qE "${log_pattern}" "${compose}"; then
    abort "failed to disable the PostgreSQL log bind mount on macOS"
  fi
  local message="PostgreSQL logs use container storage on macOS"
  message+=" (view with: docker logs sourcelens-postgres)"
  log_info "${message}"
}

# ---------------------------------------------------------------------------
# TLS certificate (self-signed for the nginx HTTPS server block)
# ---------------------------------------------------------------------------
generate_certs() {
  log_step "Ensuring TLS certificate"
  local certs_dir="${INSTALL_DIR}/docker/nginx/certs"
  mkdir -p "${certs_dir}"
  if [[ ! -f "${certs_dir}/nginx-selfsigned.crt" || ! -f "${certs_dir}/nginx-selfsigned.key" ]]; then
    log_info "Generating self-signed certificate for ${DOMAIN} (replace with a real certificate for production)"
    if [[ "${PLATFORM}" == "windows" ]]; then
      (
        cd "${certs_dir}"
        MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 \
          -nodes -days 3650 \
          -keyout nginx-selfsigned.key \
          -out nginx-selfsigned.crt \
          -subj "/CN=${DOMAIN}" \
          -addext \
          "subjectAltName=DNS:${DOMAIN},DNS:localhost,IP:127.0.0.1" \
          2>/dev/null \
          || MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 \
               -nodes -days 3650 \
               -keyout nginx-selfsigned.key \
               -out nginx-selfsigned.crt \
               -subj "/CN=${DOMAIN}"
      )
    else
      docker run --rm -v "${certs_dir}:/certs" \
        alpine/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -keyout /certs/nginx-selfsigned.key \
        -out /certs/nginx-selfsigned.crt -subj "/CN=${DOMAIN}" \
        -addext \
        "subjectAltName=DNS:${DOMAIN},DNS:localhost,IP:127.0.0.1" \
        2>/dev/null \
        || docker run --rm -v "${certs_dir}:/certs" \
             alpine/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
             -keyout /certs/nginx-selfsigned.key \
             -out /certs/nginx-selfsigned.crt -subj "/CN=${DOMAIN}"
    fi
    chmod 600 "${certs_dir}/nginx-selfsigned.key"
  fi
  log_ok "TLS certificate ready"
}

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
create_dirs() {
  log_step "Creating directories"
  mkdir -p "${INSTALL_DIR}"/{data,logs}
  mkdir -p "${INSTALL_DIR}/data"/{django/staticfiles,storage,document-attachments,deliverables,workspace,postgresql/data,redis}
  mkdir -p "${INSTALL_DIR}/data"/logs/{api,worker,scheduler,nginx,postgresql,redis}
  log_ok "Directories created under ${INSTALL_DIR}"
}

macos_docker_owner() {
  local uid="${SUDO_UID:-}" gid="${SUDO_GID:-}" console_owner=""
  if [[ "${uid}" =~ ^[0-9]+$ && "${gid}" =~ ^[0-9]+$ && \
        "${uid}" != "0" ]]; then
    printf '%s:%s' "${uid}" "${gid}"
    return 0
  fi
  console_owner="$(stat -f '%u:%g' /dev/console 2>/dev/null || true)"
  if [[ "${console_owner}" =~ ^[1-9][0-9]*:[0-9]+$ ]]; then
    printf '%s' "${console_owner}"
    return 0
  fi
  return 1
}

prepare_macos_mount_permissions() {
  [[ "${PLATFORM}" == "macos" ]] || return 0
  [[ ! -f "${INSTALL_DIR}/install-info.env" ]] || return 0
  local owner=""
  owner="$(macos_docker_owner)" \
    || abort "could not identify the macOS Docker Desktop user"
  chown -R "${owner}" "${INSTALL_DIR}/data" \
    "${INSTALL_DIR}/docker/nginx/certs" \
    || abort "could not prepare macOS bind-mount permissions"
  log_info "Docker Desktop bind mounts prepared for macOS user ${owner}"
}

# ---------------------------------------------------------------------------
# Docker Compose lifecycle
# ---------------------------------------------------------------------------
docker_host_path() {
  if [[ "${PLATFORM}" == "windows" ]] && \
     command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

run_compose() {
  local project_dir=""
  local -a compose_files
  project_dir="$(docker_host_path "${INSTALL_DIR}")"
  compose_files=(-f \
    "$(docker_host_path "${INSTALL_DIR}/${COMPOSE_FILE}")")
  if [[ -n "${SOURCE_DIR}" && \
        -f "${INSTALL_DIR}/${MODEL_SETUP_OVERRIDE}" ]]; then
    compose_files+=(-f \
      "$(docker_host_path "${INSTALL_DIR}/${MODEL_SETUP_OVERRIDE}")")
  fi
  "${COMPOSE_CMD[@]}" --project-directory "${project_dir}" \
    "${compose_files[@]}" "$@" 2>&1 | tee -a "${LOG_FILE}"
}

run_compose_quiet() {
  local project_dir=""
  local -a compose_files
  project_dir="$(docker_host_path "${INSTALL_DIR}")"
  compose_files=(-f \
    "$(docker_host_path "${INSTALL_DIR}/${COMPOSE_FILE}")")
  if [[ -n "${SOURCE_DIR}" && \
        -f "${INSTALL_DIR}/${MODEL_SETUP_OVERRIDE}" ]]; then
    compose_files+=(-f \
      "$(docker_host_path "${INSTALL_DIR}/${MODEL_SETUP_OVERRIDE}")")
  fi
  "${COMPOSE_CMD[@]}" --project-directory "${project_dir}" \
    "${compose_files[@]}" "$@"
}

pull_one() {
  local img="$1" output_file="$2"
  printf '[PULL] %s\n' "${img}" >"${output_file}"
  docker pull "${img}" >>"${output_file}" 2>&1
}

pull_image_label() {
  case "$1" in
    *sourcelens-backend*) printf 'SourceLens Backend' ;;
    *sourcelens-frontend*) printf 'SourceLens Frontend' ;;
    *sourcelens-lensnode*) printf 'SourceLens LensNode' ;;
    postgres:*) printf 'PostgreSQL' ;;
    redis:*) printf 'Redis' ;;
    nginx:*) printf 'Nginx' ;;
    alpine/openssl*) printf 'TLS Helper' ;;
    *) printf '%s' "${1##*/}" ;;
  esac
}

tls_helper_image_required() {
  [[ "${PLATFORM}" != "windows" && \
     (! -f "${INSTALL_DIR}/docker/nginx/certs/nginx-selfsigned.crt" || \
      ! -f "${INSTALL_DIR}/docker/nginx/certs/nginx-selfsigned.key") ]]
}

pull_layer_progress() {
  local output_file="$1"
  [[ -f "${output_file}" ]] || { printf '0 0'; return 0; }
  awk '
    {
      gsub(/\r/, "")
      layer = $1
      sub(/:$/, "", layer)
      if ($0 ~ /: Pulling fs layer$/) total[layer] = 1
      if ($0 ~ /: (Download complete|Pull complete)$/ ||
          $0 ~ /: (Already exists|Layer already exists)$/) {
        total[layer] = 1
        done[layer] = 1
      }
    }
    END {
      total_count = 0
      done_count = 0
      for (layer in total) total_count++
      for (layer in done) done_count++
      printf "%d %d", done_count, total_count
    }
  ' "${output_file}"
}

pull_progress_bar() {
  local percent="$1" width=16 filled=0 index=0 bar=""
  filled=$((percent * width / 100))
  for ((index = 0; index < width; index++)); do
    if ((index < filled)); then
      bar+="█"
    else
      bar+="░"
    fi
  done
  printf '%s' "${bar}"
}

_pull_dashboard_cursor_hidden=0

pull_dashboard_hide_cursor() {
  [[ -t 1 ]] || return 0
  printf '\033[?25l'
  _pull_dashboard_cursor_hidden=1
}

pull_dashboard_show_cursor() {
  [[ "${_pull_dashboard_cursor_hidden}" == "1" ]] || return 0
  printf '\033[?25h'
  _pull_dashboard_cursor_hidden=0
}

render_pull_dashboard() {
  local total="$1" started="$2" tick="$3"
  local index=0 state="" output_file="" metrics="" detail=""
  local done_layers=0 total_layers=0 percent=0 elapsed=0
  local minutes=0 seconds=0 indicator="" color="" bar=""
  local chars='/-\|'
  [[ -t 1 ]] || return 0

  if [[ "${_pull_dashboard_rendered}" == "1" ]]; then
    printf '\033[%sA' "${total}"
  fi
  elapsed=$((SECONDS - started))
  minutes=$((elapsed / 60))
  seconds=$((elapsed % 60))

  for ((index = 0; index < total; index++)); do
    state="${image_states[index]}"
    output_file="${image_logs[index]:-}"
    percent=0
    indicator="·"
    color="${c_cyan}"
    detail="waiting"
    if [[ "${state}" == "active" ]]; then
      indicator="${chars:$((tick % 4)):1}"
      metrics="$(pull_layer_progress "${output_file}")"
      done_layers="${metrics%% *}"
      total_layers="${metrics##* }"
      if ((total_layers > 0)); then
        percent=$((done_layers * 100 / total_layers))
        ((percent >= 100)) && percent=95
        detail="${done_layers}/${total_layers} layers"
      else
        detail="starting"
      fi
    elif [[ "${state}" == "done" ]]; then
      indicator="✓"
      color="${c_green}"
      percent=100
      detail="complete"
    elif [[ "${state}" == "retrying" ]]; then
      indicator="!"
      color="${c_yellow}"
      detail="retrying"
    elif [[ "${state}" == "failed" ]]; then
      indicator="✗"
      color="${c_red}"
      detail="failed"
    fi
    bar="$(pull_progress_bar "${percent}")"
    printf '\r\033[K%s%s  %-20s [%s]  %3d%%  %-12s  %02d:%02d%s\n' \
      "${color}" "${indicator}" "${image_labels[index]}" "${bar}" \
      "${percent}" "${detail}" "${minutes}" "${seconds}" "${c_reset}"
  done
  _pull_dashboard_rendered=1
}

pull_images() {
  log_step "Downloading SourceLens components"
  run_compose config --quiet || abort "invalid docker-compose configuration; see ${LOG_FILE}"

  local -a images=()
  local img=""
  while IFS= read -r img; do
    [[ -n "${img}" ]] && images+=("${img}")
  done < <(run_compose_quiet config --images 2>/dev/null | sort -u)
  if tls_helper_image_required; then
    images+=("alpine/openssl")
  fi

  local total="${#images[@]}" attempt=1 max_attempts=3
  local completed=0 cursor=0 job=0 image_index=0 img=""
  local running=0 found=0 started="${SECONDS}" tick=0 rc=0
  local status_file="" output_file=""
  local failure_message="" retry_message=""
  local status_dir="${INSTALL_DIR}/logs/.pull-status-$$"
  local -a pending=() failed=() pids=() batch=() statuses=()
  local -a image_states=() image_logs=() image_labels=()
  if ((total == 0)); then
    log_warn "No components need to be downloaded"
    return 0
  fi
  for ((image_index = 0; image_index < total; image_index++)); do
    pending+=("${image_index}")
    image_states+=("waiting")
    image_logs+=("")
    image_labels+=("$(pull_image_label "${images[image_index]}")")
  done
  log_info \
    "Downloading ${total} components, up to ${PULL_PARALLELISM} in parallel"
  mkdir -p "${status_dir}"
  _pull_dashboard_rendered=0
  pull_dashboard_hide_cursor

  while ((${#pending[@]} > 0)); do
    failed=()
    cursor=0
    running=0
    pids=()
    batch=()
    statuses=()
    while ((cursor < ${#pending[@]} || running > 0)); do
      while ((running < PULL_PARALLELISM && cursor < ${#pending[@]})); do
        image_index="${pending[cursor]}"
        img="${images[image_index]}"
        status_file="${status_dir}/${attempt}-${image_index}.status"
        output_file="${status_dir}/${attempt}-${image_index}.log"
        image_states[image_index]="active"
        image_logs[image_index]="${output_file}"
        (
          if pull_one "${img}" "${output_file}"; then
            printf '0\n' >"${status_file}"
          else
            printf '1\n' >"${status_file}"
          fi
        ) &
        pids+=("$!")
        batch+=("${image_index}")
        statuses+=("${status_file}")
        cursor=$((cursor + 1))
        running=$((running + 1))
      done

      found=0
      for ((job = 0; job < ${#pids[@]}; job++)); do
        status_file="${statuses[job]:-}"
        if [[ -n "${status_file}" && -f "${status_file}" ]]; then
          image_index="${batch[job]}"
          rc="$(head -n 1 "${status_file}")"
          wait "${pids[job]}" || true
          output_file="${image_logs[image_index]}"
          [[ -f "${output_file}" ]] \
            && cat "${output_file}" >>"${LOG_FILE:-/dev/null}"
          if [[ "${rc}" == "0" ]]; then
            image_states[image_index]="done"
            completed=$((completed + 1))
            log_line "[PROGRESS] ${completed}/${total} components downloaded"
          else
            image_states[image_index]="retrying"
            failed+=("${image_index}")
          fi
          rm -f "${status_file}" "${output_file}"
          image_logs[image_index]=""
          statuses[job]=""
          running=$((running - 1))
          found=1
        fi
      done
      tick=$((tick + 1))
      render_pull_dashboard "${total}" "${started}" "${tick}"
      ((found == 1)) || sleep 0.2
    done

    if ((${#failed[@]} == 0)); then
      break
    fi
    if ((attempt >= max_attempts)); then
      for image_index in "${failed[@]}"; do
        image_states[image_index]="failed"
      done
      render_pull_dashboard "${total}" "${started}" "${tick}"
      pull_dashboard_show_cursor
      failure_message="failed to download ${#failed[@]} component(s) after "
      failure_message+="${max_attempts} attempts; check registry access "
      failure_message+="(see ${LOG_FILE})"
      abort "${failure_message}"
    fi
    retry_message="${#failed[@]} component download(s) failed on attempt "
    retry_message+="${attempt}/${max_attempts}; retrying in 10s"
    log_line "[WARN]  ${retry_message}"
    render_pull_dashboard "${total}" "${started}" "${tick}"
    sleep 10
    pending=("${failed[@]}")
    attempt=$((attempt + 1))
  done
  rmdir "${status_dir}" 2>/dev/null || true
  render_pull_dashboard "${total}" "${started}" "${tick}"
  pull_dashboard_show_cursor
  if [[ -t 1 ]]; then
    log_line "[OK]    All ${total} components downloaded"
  else
    log_ok "All ${total} components downloaded"
  fi
}

_spinner_pid=""
_spinner_on=0

spinner_start() {
  [[ -t 1 ]] || return 0
  _spinner_on=1
  (
    local label="$1" chars='/-\|' i=0 c="" started="${SECONDS}"
    local elapsed=0 minutes=0 seconds=0
    while :; do
      c="${chars:$((i % 4)):1}"
      elapsed=$((SECONDS - started))
      minutes=$((elapsed / 60))
      seconds=$((elapsed % 60))
      printf '\r\033[K%s  %s  %02d:%02d' \
        "${label}" "${c}" "${minutes}" "${seconds}"
      i=$((i + 1))
      sleep 0.2
    done
  ) &
  _spinner_pid=$!
}

spinner_stop() {
  [[ "${_spinner_on}" == "1" ]] || return 0
  kill "${_spinner_pid}" 2>/dev/null || true
  wait "${_spinner_pid}" 2>/dev/null || true
  printf '\r\033[K'
  _spinner_on=0
}

stale_mount_namespace_detected() {
  local container="" health_output=""
  while IFS= read -r container; do
    [[ -n "${container}" ]] || continue
    health_output="$(
      docker inspect --format \
        '{{if .State.Health}}{{range .State.Health.Log}}'\
'{{println .Output}}{{end}}{{end}}' \
        "${container}" 2>/dev/null || true
    )"
    local stale_message="current working directory is outside of container "
    stale_message+="mount namespace root"
    if [[ "${health_output}" == *"${stale_message}"* ]]; then
      return 0
    fi
  done < <(run_compose_quiet ps -aq 2>/dev/null || true)
  return 1
}

start_stack() {
  log_step "Starting SourceLens"
  local attempt=1 max_attempts=5 backoff=20
  local -a up_args
  up_args=(up -d --no-build --remove-orphans)
  if [[ "${EXISTING}" == "0" || \
        ! -f "${INSTALL_DIR}/install-info.env" ]]; then
    up_args+=(--force-recreate)
    log_info "Fresh or incomplete install will recreate stale containers"
  elif stale_mount_namespace_detected; then
    up_args+=(--force-recreate)
    local stale_warning="Docker reported a stale bind-mount working "
    stale_warning+="directory; "
    stale_warning+="recreating containers while preserving SourceLens data"
    log_warn "${stale_warning}"
  fi
  if [[ -t 1 ]]; then
    log_info "Starting SourceLens (details logged to ${LOG_FILE})"
    spinner_start "Starting SourceLens"
  fi
  until run_compose_quiet "${up_args[@]}" >>"${LOG_FILE}" 2>&1; do
    up_args=(up -d --no-build --remove-orphans)
    spinner_stop
    if ((attempt >= max_attempts)); then
      log_error "SourceLens failed to start after ${max_attempts} attempts"
      run_compose_quiet ps >>"${LOG_FILE}" 2>&1 || true
      run_compose_quiet logs --tail=100 --no-color \
        >>"${LOG_FILE}" 2>&1 || true
      abort "SourceLens failed to start; see ${LOG_FILE}"
    fi
    log_info "dependencies still warming up; retrying in ${backoff}s (attempt ${attempt}/${max_attempts})"
    sleep "${backoff}"
    ((backoff < 120)) && backoff=$((backoff * 2))
    attempt=$((attempt + 1))
    spinner_start "Starting SourceLens"
  done
  spinner_stop
  log_ok "SourceLens started"
}

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
background_services_ready() {
  local url="$1" payload="" services="" service=""
  payload="$(curl -sS --max-time 3 "${url}/health/celery" 2>/dev/null)" \
    || return 1
  [[ "${payload}" == *'"missing_consumers": []'* ]] || return 1
  [[ "${payload}" == *'"overloaded_queues": []'* ]] || return 1
  [[ "${payload}" != *'"broker_error"'* ]] || return 1

  services="$(run_compose_quiet ps --status running --services 2>/dev/null)" \
    || return 1
  for service in backend-worker backend-scheduler frontend lensnode nginx; do
    printf '%s\n' "${services}" | grep -qx "${service}" || return 1
  done
}

health_check() {
  log_step "Checking SourceLens health"
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  local url="http://127.0.0.1:${HTTP_PORT}"
  local web_ready=0
  spinner_start "Preparing SourceLens web service"
  while :; do
    if [[ "${web_ready}" == "0" ]] && \
       curl -fsS --max-time 5 "${url}/health" >/dev/null 2>&1; then
      web_ready=1
      spinner_stop
      log_ok "SourceLens web service is ready"
      spinner_start "Preparing SourceLens background services"
    fi
    if [[ "${web_ready}" == "1" ]] && \
       background_services_ready "${url}"; then
      break
    fi
    if ((SECONDS >= deadline)); then
      spinner_stop
      log_error "health check timed out after ${HEALTH_TIMEOUT}s"
      run_compose_quiet ps >>"${LOG_FILE}" 2>&1 || true
      run_compose_quiet logs --tail=100 --no-color \
        >>"${LOG_FILE}" 2>&1 || true
      abort "SourceLens health check failed; see ${LOG_FILE}"
    fi
    sleep 3
  done
  spinner_stop
  log_ok "SourceLens is healthy"
}

# ---------------------------------------------------------------------------
# First-time AI model setup
# ---------------------------------------------------------------------------
model_setup_is_available() {
  run_compose_quiet exec -T backend-api \
    python manage.py help setup_ai_model >/dev/null 2>&1
}

model_is_configured() {
  run_compose_quiet exec -T backend-api \
    python manage.py setup_ai_model --check >/dev/null 2>&1
}

configure_ai_model() {
  local skip_message="" unavailable_message=""
  log_step "AI model setup"
  if ! model_setup_is_available; then
    MODEL_SETUP_UNAVAILABLE=1
    unavailable_message="The installed SourceLens release does not include "
    unavailable_message+="the AI model setup wizard; existing model settings "
    unavailable_message+="were left unchanged"
    log_warn "${unavailable_message}"
    return 0
  fi
  if model_is_configured; then
    MODEL_CONFIGURED=1
    log_ok "An active system AI model is already configured"
    return 0
  fi

  if [[ "${ASSUME_YES}" == "1" ]]; then
    skip_message="AI model setup needs an interactive terminal and was "
    skip_message+="skipped because --yes is enabled"
    log_warn "${skip_message}"
    return 0
  fi
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    log_warn "No interactive terminal is available; AI model setup was skipped"
    return 0
  fi

  if ! run_compose_quiet exec backend-api \
    python manage.py setup_ai_model </dev/tty >/dev/tty 2>/dev/tty; then
    log_warn "Interactive AI model setup did not complete"
  fi
  if model_is_configured; then
    MODEL_CONFIGURED=1
    log_ok "System AI model is ready"
  else
    log_warn "No active system AI model is configured yet"
  fi
}

# ---------------------------------------------------------------------------
# Installation metadata & summary
# ---------------------------------------------------------------------------
write_install_info() {
  local info="${INSTALL_DIR}/install-info.env"
  {
    printf '# SourceLens installation metadata — generated by install.sh v%s\n' "${INSTALLER_VERSION}"
    printf 'SOURCELENS_VERSION=%s\n' "${VERSION}"
    printf 'SOURCELENS_INSTALL_TIME=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'SOURCELENS_CHANNEL=%s\n' "${CHANNEL}"
    printf 'SOURCELENS_DOWNLOAD_SOURCE=%s\n' "${DOWNLOAD_SOURCE}"
    printf 'SOURCELENS_INSTALL_DIR=%s\n' "${INSTALL_DIR}"
    printf 'SOURCELENS_URL=%s\n' "${SCHEME}://${DOMAIN}${PORT_SUFFIX}"
    printf 'SOURCELENS_USERNAME=%s\n' "${ADMIN_USERNAME}"
    printf 'SOURCELENS_EMAIL=%s\n' "${ADMIN_EMAIL}"
    printf 'SOURCELENS_INITIAL_PASSWORD=%s\n' "${ADMIN_PASSWORD}"
    printf 'SOURCELENS_REGISTRY=%s\n' "${REGISTRY}"
    printf 'SOURCELENS_HTTP_PORT=%s\n' "${HTTP_PORT}"
    printf 'SOURCELENS_HTTPS_PORT=%s\n' "${HTTPS_PORT}"
    printf 'SOURCELENS_DOMAIN=%s\n' "${DOMAIN}"
    printf 'SOURCELENS_HTTPS=%s\n' "${HTTPS}"
    printf 'SOURCELENS_INSTALLER_VERSION=%s\n' "${INSTALLER_VERSION}"
  } >"${info}"
  chmod 600 "${info}"
  log_ok "Installation metadata saved (${info}, chmod 600)"
}

show_summary() {
  log_step "Installation summary"
  log_info "  Platform:     ${OS_NAME} (${PLATFORM}/${ARCH})"
  log_info "  Version:      v${VERSION}"
  log_info "  Install dir:  ${INSTALL_DIR}"
  log_info "  URL:          ${SCHEME}://${DOMAIN}${PORT_SUFFIX}"
  log_info "  HTTP port:    ${HTTP_PORT}"
  if [[ "${EXISTING}" == "1" ]]; then
    log_info "  Mode:         rerun/upgrade (existing installation, .env preserved)"
  fi
}

final_summary() {
  local local_url="${SCHEME}://localhost${PORT_SUFFIX}"
  local model_url="${SCHEME}://${DOMAIN}${PORT_SUFFIX}/management/llm/config"
  local local_model_url="${local_url}/management/llm/config"
  local model_warning="" vm_hint=""
  log_step "Installation complete"
  log_ok "SourceLens v${VERSION} installed at ${INSTALL_DIR}"
  log_info "URL:              ${SCHEME}://${DOMAIN}${PORT_SUFFIX}"
  log_info "Email:            ${ADMIN_EMAIL}"
  printf '%sInitial password: %s%s\n' \
    "${c_cyan}" "${ADMIN_PASSWORD}" "${c_reset}"
  log_info "Install dir:      ${INSTALL_DIR}"
  log_info "Config file:      ${INSTALL_DIR}/.env"
  log_info "Install info:     ${INSTALL_DIR}/install-info.env"
  log_info "Install log:      ${LOG_FILE}"
  log_info "AI model settings: ${model_url}"
  if [[ "${MODEL_CONFIGURED}" != "1" && \
        "${MODEL_SETUP_UNAVAILABLE}" != "1" ]]; then
    model_warning="AI model setup is incomplete; assistants cannot run "
    model_warning+="until a model is configured"
    log_warn "${model_warning}"
  fi
  vm_hint="NAT virtual machine: forward host TCP port ${HTTP_PORT} to guest "
  vm_hint+="TCP port ${HTTP_PORT}, then open ${local_url}"
  log_info "${vm_hint}"
  log_info "NAT virtual machine AI model settings: ${local_model_url}"
  if [[ "${ENV_EXISTS}" == "1" ]]; then
    log_warn "An existing .env was preserved; the admin password above is the one stored in it"
  fi
  if [[ "${HTTPS}" != "true" ]]; then
    log_warn "HTTPS is not enabled; put a TLS-terminating reverse proxy in front for production, or set --https"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  INSTALL_ARGS=("$@")
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      -d|--dir) INSTALL_DIR="${2:?--dir requires an argument}"; INSTALL_DIR_OVERRIDE=1; shift 2 ;;
      -p|--port) HTTP_PORT="${2:?--port requires an argument}"; HTTP_PORT_OVERRIDE=1; shift 2 ;;
      --https-port) HTTPS_PORT="${2:?--https-port requires an argument}"; shift 2 ;;
      -c|--channel) CHANNEL="$2"; shift 2 ;;
      --download-source) DOWNLOAD_SOURCE="$2"; shift 2 ;;
      -v|--version) VERSION="$2"; shift 2 ;;
      --source) SOURCE_DIR="$2"; shift 2 ;;
      -r|--registry) REGISTRY="$2"; shift 2 ;;
      --domain) DOMAIN="$2"; DOMAIN_OVERRIDE=1; shift 2 ;;
      --admin-user) ADMIN_USERNAME="$2"; shift 2 ;;
      --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
      --https) HTTPS="true"; shift ;;
      --docker-mirror) DOCKER_MIRROR="$2"; shift 2 ;;
      -y|--yes) ASSUME_YES=1; shift ;;
      --force) FORCE=1; shift ;;
      --) shift; break ;;
      -*)
        if [[ "${1#-}" =~ ^[0-9] ]]; then
          VERSION="${1#-}"; shift
        else
          log_error "unknown option: $1"; usage; exit 1
        fi
        ;;
      *) VERSION="${1#v}"; shift ;;
    esac
  done

  if [[ -n "${SOURCE_DIR}" ]]; then
    [[ -d "${SOURCE_DIR}" ]] \
      || abort "source directory does not exist: ${SOURCE_DIR}"
    SOURCE_DIR="$(cd "${SOURCE_DIR}" && pwd -P)"
  fi

  trap 'pull_dashboard_show_cursor' EXIT
  trap 'log_line "=== install failed at line ${LINENO} (exit $?) ==="' ERR

  # Preflight
  require_root
  detect_os
  detect_arch
  check_memory
  prompt_install_dir

  mkdir -p "${INSTALL_DIR}/logs"
  LOG_FILE="${INSTALL_DIR}/logs/install.log"
  touch "${LOG_FILE}"
  chmod 600 "${LOG_FILE}"
  log_line "=== install.sh v${INSTALLER_VERSION} started ($(date -u '+%Y-%m-%dT%H:%M:%SZ')) ==="
  log_line "argv: $*"

  check_disk
  check_tools

  detect_existing
  detect_channel
  detect_download_source
  check_docker
  check_compose
  check_no_bluegreen

  configure
  resolve_version
  resolve_ports
  show_summary
  if [[ "${EXISTING}" == "1" && -n "${INSTALLED_VERSION}" && "${INSTALLED_VERSION#v}" != "${VERSION}" && "${FORCE}" != "1" ]]; then
    log_warn "upgrade: installed v${INSTALLED_VERSION#v} -> v${VERSION}"
  fi
  confirm "Proceed?" || abort "installation cancelled"

  create_dirs
  if [[ -n "${SOURCE_DIR}" ]]; then
    log_info "Using local release files from ${SOURCE_DIR%/}"
  fi
  fetch_release_files
  prepare_model_setup_overlay
  generate_env
  patch_compose
  pull_images
  generate_certs
  prepare_macos_mount_permissions
  start_stack
  health_check
  configure_ai_model
  write_install_info
  final_summary
}

if [[ "${BASH_SOURCE[0]:-}" == "$0" || -z "${BASH_SOURCE[0]:-}" ]]; then
  main "$@"
fi
