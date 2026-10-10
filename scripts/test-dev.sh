#!/bin/bash
set -euo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    echo "Usage: $0 [Django test labels and options]"
    echo "Print the dev API container's actual source mount, then run manage.py test."
    exit 0
fi

source_dir="$(docker inspect sourcelens-api-dev \
    --format '{{range .Mounts}}{{if and (eq .Destination "/opt/backend") (eq .Type "bind")}}{{.Source}}{{end}}{{end}}')"
if [[ -z "$source_dir" ]]; then
    echo "ERROR: No /opt/backend bind mount found on sourcelens-api-dev; tests were not run." >&2
    exit 1
fi

branch="$(git -C "$source_dir" symbolic-ref --short -q HEAD 2>/dev/null || true)"
if [[ -z "$branch" ]]; then
    revision="$(git -C "$source_dir" rev-parse --short HEAD 2>/dev/null || true)"
    branch="${revision:+detached at $revision}"
    branch="${branch:-unavailable}"
fi
printf 'Source under test: %s  (branch: %s)\n' "$source_dir" "$branch"

current_root="$(git rev-parse --show-toplevel)"
expected_source="$(cd "$current_root/backend" && pwd -P)"
mounted_source="$(cd "$source_dir" 2>/dev/null && pwd -P || printf '%s' "$source_dir")"
if [[ "$mounted_source" != "$expected_source" ]]; then
    echo "  NOTE: that is not this worktree. Current source: $expected_source" >&2
    echo "  To switch, run from the shared Compose directory:" >&2
    printf '  WORKTREE_DIR=%q docker compose -f docker-compose.dev.yml up -d --force-recreate\n' "$current_root" >&2
fi

exec docker exec -w /opt/backend sourcelens-api-dev python manage.py test "$@"
