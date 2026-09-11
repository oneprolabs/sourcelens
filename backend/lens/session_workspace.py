"""Build isolated, reproducible datasource workspaces for Sessions."""

import json
import os
from pathlib import Path

from django.conf import settings


class SessionWorkspaceError(RuntimeError):
    """Raised when a session workspace cannot be built safely."""


def build_session_workspace(session):
    """Create datasource symlinks and an immutable manifest for a session."""

    root = Path(getattr(settings, "LENS_SESSION_WORKSPACE_ROOT", ""))
    if not root:
        root = Path(settings.MEDIA_ROOT) / "sessions"
    workspace = (root / str(session.uuid)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    sources = workspace / "sources"
    sources.mkdir(exist_ok=True)
    entries = []
    created_links = []
    try:
        for snapshot in session.datasource_snapshots.select_related("item"):
            target = (Path(settings.MEDIA_ROOT) / snapshot.storage_key).resolve()
            media_root = Path(settings.MEDIA_ROOT).resolve()
            if media_root not in target.parents and target != media_root:
                raise SessionWorkspaceError("DATASOURCE_PATH_OUTSIDE_STORAGE_ROOT")
            if not target.exists() or target.is_symlink():
                raise SessionWorkspaceError("DATASOURCE_TARGET_UNAVAILABLE")
            link = sources / snapshot.mount_name
            if link.is_symlink() and link.resolve() == target:
                continue
            if link.exists() or link.is_symlink():
                raise SessionWorkspaceError("SESSION_MOUNT_NAME_CONFLICT")
            os.symlink(target, link, target_is_directory=True)
            created_links.append(link)
            entries.append({
            "mount_name": snapshot.mount_name,
            "datasource_uuid": str(snapshot.datasource.uuid),
            "item_uuid": str(snapshot.item.uuid) if snapshot.item else None,
            "version": snapshot.datasource_version,
            "storage_key": snapshot.storage_key,
            })
    except Exception:
        for link in created_links:
            link.unlink(missing_ok=True)
        raise
    manifest = workspace / "manifest.json"
    temporary = workspace / ".manifest.tmp"
    temporary.write_text(
        json.dumps(
            {"session_uuid": str(session.uuid), "items": entries}, indent=2
        ),
        encoding="utf-8",
    )
    temporary.replace(manifest)
    return workspace
