#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/sourcelensctl.sh"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

ln -s "$SCRIPT_DIR/test_support/fake-docker" "$TEST_DIR/docker"

output="$(DEPLOY_PATH="$SCRIPT_DIR/.." "$SCRIPT" recreate postgresql 2>&1 || true)"
grep -q "Unsupported target" <<<"$output"

output="$(DEPLOY_PATH="$SCRIPT_DIR/.." "$SCRIPT" restart 2>&1 || true)"
grep -q "Usage:" <<<"$output"

grep -q "Usage: \$0 restart" "$SCRIPT"
grep -q "Usage: \$0 recreate" "$SCRIPT"
grep -q -- "--force-recreate --no-deps" "$SCRIPT"

DOCKER_CALLS="$TEST_DIR/docker-calls" \
PATH="$TEST_DIR:$PATH" \
DEPLOY_PATH="$SCRIPT_DIR/.." "$SCRIPT" restart workers
grep -q '^compose restart backend-worker$' "$TEST_DIR/docker-calls"

DOCKER_CALLS="$TEST_DIR/docker-calls" \
PATH="$TEST_DIR:$PATH" \
DEPLOY_PATH="$SCRIPT_DIR/.." "$SCRIPT" recreate runtime
grep -q '^compose up -d --force-recreate --no-deps backend-worker backend-scheduler lensnode$' \
    "$TEST_DIR/docker-calls"

echo "sourcelensctl command tests passed"
