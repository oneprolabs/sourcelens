#!/usr/bin/env bash
# Copy src/admin to another Vue project. Usage: ./scripts/copy-admin-to.sh <target-project-path>
# Example: ./scripts/copy-admin-to.sh ../my-other-app

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${1:?Usage: $0 <target-project-path>}"

if [ ! -d "$REPO_ROOT/src/admin" ]; then
  echo "Error: $REPO_ROOT/src/admin not found."
  exit 1
fi

TARGET_SRC="$TARGET/src"
if [ ! -d "$TARGET_SRC" ]; then
  echo "Error: Target has no src/ directory: $TARGET_SRC"
  exit 1
fi

cp -r "$REPO_ROOT/src/admin" "$TARGET_SRC/"
echo "Copied src/admin to $TARGET_SRC/admin"
echo ""
echo "Next steps:"
echo "  1. Configure routing, i18n, and dependencies in the target project."
echo "  2. Or copy the admin-integration skill to the target and use it to integrate."
