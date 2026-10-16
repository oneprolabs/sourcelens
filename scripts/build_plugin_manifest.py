"""Freeze Plugin source hashes after copying release files into the runtime image."""

import argparse
import hashlib
import json
from pathlib import Path


def build_manifest(root):
    """Write a deterministic build manifest covering all installed Python sources."""

    root = Path(root).resolve(strict=True)
    files = {}
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
            raise ValueError("Plugin source must be a regular file inside the build root")
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
        raise ValueError("Plugin source files are required")
    payload = {"schema_version": 1, "files": files}
    target = root / ".runtime-manifest.json"
    target.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return target


def main():
    """Build the manifest for the single Plugin root installed in the image."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    arguments = parser.parse_args()
    build_manifest(arguments.root)


if __name__ == "__main__":
    main()
