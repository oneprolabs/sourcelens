#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/test-dev.sh"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

mkdir -p "$TEST_DIR/bin" "$TEST_DIR/current/backend" "$TEST_DIR/other worktree/backend"
git init -q "$TEST_DIR/current"
git -C "$TEST_DIR/current" symbolic-ref HEAD refs/heads/current-branch
git init -q "$TEST_DIR/other worktree"
git -C "$TEST_DIR/other worktree" symbolic-ref HEAD refs/heads/other-branch
ln -s "$TEST_DIR/current" "$TEST_DIR/current-link"

cat > "$TEST_DIR/bin/docker" <<'EOF'
#!/bin/bash
set -euo pipefail
case "$1" in
    inspect)
        [[ "$2" == sourcelens-api-dev ]]
        [[ "$4" == *'/opt/backend'* ]]
        if [[ "${INSPECT_FAIL:-0}" == 1 ]]; then
            echo "Cannot inspect container" >&2
            exit 1
        fi
        printf '%s\n' "$MOUNT_SOURCE"
        ;;
    exec)
        printf '%s\n' "$@" > "$DOCKER_CALLS"
        echo "Fake Django test suite executed"
        exit "${TEST_EXIT_CODE:-0}"
        ;;
    *) exit 99 ;;
esac
EOF
chmod +x "$TEST_DIR/bin/docker"
export PATH="$TEST_DIR/bin:$PATH"
export DOCKER_CALLS="$TEST_DIR/docker-calls"

cd "$TEST_DIR/current/backend"
export MOUNT_SOURCE="$TEST_DIR/current/backend"
output="$(bash "$SCRIPT" lens.tests.test_api --noinput 2>&1)"
[[ "$output" == *"Source under test: $MOUNT_SOURCE  (branch: current-branch)"* ]]
[[ "$output" != *'NOTE:'* ]]
[[ "$(cat "$DOCKER_CALLS")" == $'exec\n-w\n/opt/backend\nsourcelens-api-dev\npython\nmanage.py\ntest\nlens.tests.test_api\n--noinput' ]]

cd "$TEST_DIR/current-link/backend"
output="$(bash "$SCRIPT" 2>&1)"
[[ "$output" != *'NOTE:'* ]]

export MOUNT_SOURCE="$TEST_DIR/other worktree/backend"
output="$(WORKTREE_DIR="$TEST_DIR/current" bash "$SCRIPT" 2>&1)"
[[ "$output" == *"Source under test: $MOUNT_SOURCE  (branch: other-branch)"* ]]
[[ "$output" == *'NOTE: that is not this worktree.'* ]]
[[ "$output" == *'--force-recreate'* ]]
[[ "$output" == *'Fake Django test suite executed'* ]]

git -C "$TEST_DIR/other worktree" -c user.name=Test -c user.email=test@example.invalid \
    -c commit.gpgsign=false commit -q --allow-empty -m 'Test fixture'
git -C "$TEST_DIR/other worktree" checkout -q --detach
revision="$(git -C "$TEST_DIR/other worktree" rev-parse --short HEAD)"
output="$(bash "$SCRIPT" 2>&1)"
[[ "$output" == *"branch: detached at $revision"* ]]

rm "$DOCKER_CALLS"
if output="$(INSPECT_FAIL=1 bash "$SCRIPT" 2>&1)"; then
    echo "FAIL: inspection errors must stop the suite" >&2
    exit 1
fi
[[ ! -e "$DOCKER_CALLS" ]]

if output="$(MOUNT_SOURCE='' bash "$SCRIPT" 2>&1)"; then
    echo "FAIL: a missing source mount must stop the suite" >&2
    exit 1
fi
[[ "$output" == *'No /opt/backend bind mount'* ]]
[[ ! -e "$DOCKER_CALLS" ]]

status=0
TEST_EXIT_CODE=7 bash "$SCRIPT" > /dev/null 2>&1 || status=$?
[[ "$status" == 7 ]]

echo "Dev test source reporting tests passed"
