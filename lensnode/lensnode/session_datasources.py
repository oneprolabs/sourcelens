"""Materialize Run-bound datasource versions from local LensNode storage."""

import shutil
import tempfile
import uuid
from contextlib import nullcontext
from pathlib import Path

from .session_workspace import session_lock, session_root


def materialize_datasources(
    config, command, runtime_root, cancel_event=None, on_activity=None,
):
    """Link selected local datasource directories into the Run workspace."""

    snapshots = command.get("datasource_snapshots") or []
    if not snapshots:
        return
    workspace_root = Path(config.workspace_path)
    target = _session_workspace_target(command, config)
    directories = []
    names = set()

    def check_activity():
        """Stop cancelled downloads and report measurable progress."""

        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("RUN_CANCELLED")
        if on_activity is not None:
            on_activity()

    lock_context = (
        session_lock(config, command["session_uuid"])
        if command.get("session_uuid")
        else nullcontext()
    )
    with lock_context:
        with tempfile.TemporaryDirectory(dir=runtime_root) as temporary:
            staging = Path(temporary) / "workspace"
            sources = staging / "sources"
            sources.mkdir(parents=True)
            for snapshot in snapshots:
                check_activity()
                name = snapshot["mount_name"]
                if (not name or name in {".", ".."} or "/" in name
                        or "\\" in name or name in names):
                    raise ValueError("SESSION_MOUNT_NAME_CONFLICT")
                names.add(name)
                local_target = local_datasource_target(
                    snapshot.get("datasource_uuid"), workspace_root,
                )
                if local_target is not None:
                    destination = sources / name
                    destination.symlink_to(
                        local_target, target_is_directory=True,
                    )
                    directories.append({
                        "name": name,
                        "path": str(target / "sources" / name),
                    })
                    continue
                raise RuntimeError(
                    "DATASOURCE_TARGET_UNAVAILABLE:"
                    + str(snapshot.get("datasource_uuid") or "")
                )
            check_activity()
            if target.is_symlink():
                raise ValueError("SESSION_WORKSPACE_PATH_INVALID")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.mkdir(parents=True, exist_ok=True)
            destination = target / "sources"
            destination.mkdir(exist_ok=True)
            for link in sources.iterdir():
                existing = destination / link.name
                if existing.exists() or existing.is_symlink():
                    if existing.is_symlink() or existing.is_file():
                        existing.unlink()
                    else:
                        shutil.rmtree(existing)
                shutil.move(str(link), str(existing))
    command["target_dirs"] = directories
    command["workspace_path"] = str(target)


def _session_workspace_target(command, config):
    """Return the validated Session root supplied by the control plane."""

    session_uuid = command.get("session_uuid")
    if session_uuid:
        return session_root(config, session_uuid)
    workspace_root = Path(config.workspace_path).resolve()
    target_dirs = command.get("target_dirs") or []
    candidate = target_dirs[0].get("path") if target_dirs else ""
    if candidate:
        target = Path(candidate)
        try:
            resolved = target.resolve()
            resolved.relative_to(
                Path(getattr(config, "runtime_path", workspace_root))
                .resolve() / "sessions"
            )
        except (OSError, ValueError):
            resolved = None
        if resolved is not None and resolved != workspace_root / "sessions":
            return resolved
    run_uuid = str(uuid.UUID(command["run_uuid"]))
    return (
        Path(getattr(config, "runtime_path", workspace_root))
        / "sessions"
        / run_uuid
    )


def local_datasource_target(datasource_uuid, workspace_root):
    """Return the unique local directory for a datasource UUID."""

    workspace_root = Path(workspace_root)
    try:
        source_uuid = uuid.UUID(str(datasource_uuid))
    except (TypeError, ValueError, AttributeError):
        return None
    datasource_root = workspace_root / "datasources"
    candidates = sorted(datasource_root.glob(f"{source_uuid}-*"))
    exact = datasource_root / str(source_uuid)
    if exact.exists() and exact.is_dir():
        candidates.append(exact)
    candidates = [
        path for path in candidates
        if path.is_dir() and _contains_readable_file(path)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    try:
        resolved = candidates[0].resolve(strict=True)
        resolved.relative_to(workspace_root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _contains_readable_file(path):
    """Return whether a datasource directory contains a regular file."""

    try:
        return next(
            (item for item in path.rglob("*") if item.is_file()),
            None,
        ) is not None
    except OSError:
        return False


def default_datasource_target(datasource_uuid, workspace_root):
    """Return the deterministic directory used for a first sync."""

    workspace_root = Path(workspace_root)
    try:
        source_uuid = uuid.UUID(str(datasource_uuid))
    except (TypeError, ValueError, AttributeError):
        return None
    return Path(workspace_root) / "datasources" / str(source_uuid)
