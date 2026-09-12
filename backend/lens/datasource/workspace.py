"""Build isolated, reproducible datasource workspaces for Sessions."""

import json
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings


class SessionWorkspaceError(RuntimeError):
    """Raised when a session workspace cannot be built safely."""


def session_workspace_path(session_uuid):
    """Return a session directory confined to the configured root."""

    root = Path(settings.LENS_SESSION_WORKSPACE_ROOT).resolve()
    workspace = root / str(session_uuid)
    if workspace.is_symlink() or workspace.resolve().parent != root:
        raise SessionWorkspaceError("SESSION_WORKSPACE_PATH_INVALID")
    return workspace


def session_source_dirs(session):
    """Return only datasource mounts frozen for this session."""

    sources = session_workspace_path(session.uuid) / "sources"
    directories = []
    for snapshot in session.datasource_snapshots.all():
        name = snapshot.mount_name
        if not name or name in {".", ".."} or Path(name).name != name:
            raise SessionWorkspaceError("SESSION_MOUNT_NAME_CONFLICT")
        directories.append({"path": str(sources / name), "name": name})
    return directories


def cleanup_session_workspace(session_uuid):
    """Remove one session workspace without following datasource links."""

    workspace = session_workspace_path(session_uuid)
    if not workspace.exists():
        return False
    shutil.rmtree(workspace)
    return True


def build_session_workspace(session):
    """Link immutable datasource versions into a session for retrieval."""

    workspace = session_workspace_path(session.uuid)
    workspace.mkdir(parents=True, exist_ok=True)
    sources = workspace / "sources"
    if sources.is_symlink():
        raise SessionWorkspaceError("SESSION_WORKSPACE_PATH_INVALID")
    sources.mkdir(exist_ok=True)
    session_source_dirs(session)
    media_root = Path(settings.MEDIA_ROOT).resolve()
    entries = []
    created_links = []
    try:
        for snapshot in session.datasource_snapshots.select_related(
            "item", "datasource"
        ):
            source = media_root / snapshot.storage_key
            target = source.resolve()
            if target == media_root or not target.is_relative_to(media_root):
                raise SessionWorkspaceError(
                    "DATASOURCE_PATH_OUTSIDE_STORAGE_ROOT"
                )
            if source.is_symlink() or not target.is_dir():
                raise SessionWorkspaceError("DATASOURCE_TARGET_UNAVAILABLE")
            link = sources / snapshot.mount_name
            try:
                link.symlink_to(target, target_is_directory=True)
            except FileExistsError:
                if not link.is_symlink() or link.resolve() != target:
                    raise SessionWorkspaceError("SESSION_MOUNT_NAME_CONFLICT")
            else:
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
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=workspace, delete=False,
    ) as temporary:
        json.dump(
            {"session_uuid": str(session.uuid), "items": entries},
            temporary, indent=2,
        )
    Path(temporary.name).replace(workspace / "manifest.json")
    return workspace
