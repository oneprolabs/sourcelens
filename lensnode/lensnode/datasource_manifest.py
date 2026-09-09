import json
from dataclasses import dataclass, field
from pathlib import Path

from .path_rules import is_excluded_path, is_sidecar_dir, remove_sidecar

MANIFEST_FILE = "manifest.json"
MARKER_FILE = ".sourcelens-datasource.json"


@dataclass
class SyncItem:
    """Unified local item produced by a datasource adapter."""

    source_id: str
    source_type: str
    source_path: str
    local_path: str
    name: str
    kind: str
    extension: str
    status: str
    metadata: dict = field(default_factory=dict)
    remote: dict = field(default_factory=dict)

    def get(self, key, default=None):
        """Return a manifest value using dict-compatible access."""

        return self.to_manifest().get(key, default)

    def to_manifest(self):
        """Return manifest-compatible item data."""

        payload = {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "local_path": self.local_path,
            "file": self.local_path,
            "name": self.name,
            "kind": self.kind,
            "type": self.kind,
            "extension": self.extension,
            "file_extension": self.extension,
            "status": self.status,
            "metadata": self.metadata,
            "remote": self.remote,
        }
        token = self.remote.get("token")
        if token:
            payload["token"] = token
        return payload


@dataclass
class SyncResult:
    """Unified datasource sync result."""

    items: list = field(default_factory=list)
    changed_paths: list = field(default_factory=list)
    deleted_paths: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    changed_only: bool = False


def write_datasource_marker(target, context):
    """Write a datasource root marker file."""

    payload = {
        "schema_version": 1,
        "datasource_uuid": context.get("datasource_uuid") or "",
        "name": context.get("name") or "",
        "source_type": context.get("source_type") or "",
        "target_path": str(Path(target).resolve()),
        "created_at": context.get("timestamp") or "",
    }
    (Path(target) / MARKER_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_manifest(target):
    """Read a datasource manifest file."""

    path = Path(target) / MANIFEST_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_manifest(target, payload):
    """Write a datasource manifest atomically."""

    target = Path(target)
    manifest_path = target / MANIFEST_FILE
    tmp_path = target / f"{MANIFEST_FILE}.tmp"
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(manifest_path)


def manifest_items(manifest):
    """Return manifest items from current or legacy schema."""

    if not isinstance(manifest, dict):
        return []
    return list(manifest.get("items") or manifest.get("documents") or [])


def manifest_source_id(item):
    """Return a stable source id for current or legacy item."""

    if hasattr(item, "to_manifest"):
        item = item.to_manifest()
    return (
        item.get("source_id")
        or (f"feishu:token:{item.get('token')}" if item.get("token") else "")
        or item.get("local_path")
        or item.get("file")
        or ""
    )


def manifest_local_path(item):
    """Return local path from current or legacy item."""

    if hasattr(item, "to_manifest"):
        item = item.to_manifest()
    return item.get("local_path") or item.get("file") or ""


def manifest_items_by_source_id(manifest):
    """Return manifest items keyed by source id."""

    result = {}
    for item in manifest_items(manifest):
        source_id = manifest_source_id(item)
        if source_id:
            result[source_id] = item
    return result


def manifest_items_by_token(manifest):
    """Return manifest items keyed by Feishu token."""

    result = {}
    for item in manifest_items(manifest):
        token = item.get("token") or (item.get("remote") or {}).get("token")
        if token:
            result[token] = item
    return result


def build_manifest(context, result):
    """Return a datasource manifest payload."""

    return {
        "schema_version": 1,
        "datasource_uuid": context.get("datasource_uuid") or "",
        "source_type": context.get("source_type") or "",
        "sync_mode": (context.get("config") or {}).get("sync_mode", ""),
        "synced_at": context.get("synced_at") or "",
        "items": [
            item.to_manifest() if hasattr(item, "to_manifest") else item
            for item in result.items
        ],
        "stats": result.stats,
    }


def should_skip_dir(path, current_datasource_uuid, excluded_roots):
    """Return whether a directory belongs to another datasource."""

    path = Path(path)
    if is_sidecar_dir(path) or is_excluded_path(path, excluded_roots):
        return True
    marker = path / MARKER_FILE
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("datasource_uuid") != current_datasource_uuid


def cleanup_deleted_sidecars(target, deleted_paths, excluded_roots):
    """Delete sidecars for deleted datasource items."""

    count = 0
    target = Path(target)
    for local_path in deleted_paths or []:
        path = (target / local_path).resolve()
        if is_excluded_path(path, excluded_roots):
            continue
        if remove_sidecar(path):
            count += 1
    return count
