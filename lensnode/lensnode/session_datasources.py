"""Materialize Run-bound datasource versions without shared filesystems."""

import shutil
import tempfile
import uuid
from pathlib import Path


def materialize_datasources(
    config, command, runtime_root, cancel_event=None, on_activity=None,
):
    """Link selected local datasource directories into the Run workspace."""

    snapshots = command.get("datasource_snapshots") or []
    if not snapshots:
        return
    run_uuid = str(uuid.UUID(command["run_uuid"]))
    workspace_root = Path(config.workspace_path)
    target = workspace_root / "sessions" / run_uuid
    directories = []
    names = set()

    def check_activity():
        """Stop cancelled downloads and report measurable progress."""

        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("RUN_CANCELLED")
        if on_activity is not None:
            on_activity()

    with tempfile.TemporaryDirectory(dir=runtime_root) as temporary:
        staging = Path(temporary) / "sources"
        staging.mkdir()
        for snapshot in snapshots:
            check_activity()
            name = snapshot["mount_name"]
            if (not name or name in {".", ".."} or "/" in name
                    or "\\" in name or name in names):
                raise ValueError("SESSION_MOUNT_NAME_CONFLICT")
            names.add(name)
            local_target = _local_datasource_target(
                snapshot.get("target_path"), workspace_root,
            )
            if local_target is not None:
                destination = staging / name
                destination.symlink_to(local_target, target_is_directory=True)
                directories.append({"name": name, "path": str(target / name)})
                continue
            raise RuntimeError("DATASOURCE_SYNC_REQUIRED")
        check_activity()
        if target.is_symlink():
            raise ValueError("SESSION_WORKSPACE_PATH_INVALID")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging), str(target))
    command["target_dirs"] = directories
    command["workspace_path"] = str(target)


def _local_datasource_target(value, workspace_root):
    """Return an existing node datasource directory safe to symlink."""

    raw = str(value or "").strip()
    if not raw:
        return None
    source = Path(raw)
    if not source.is_absolute():
        source = workspace_root / source
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(workspace_root.resolve())
    except (OSError, ValueError):
        return None
    if resolved == workspace_root.resolve() or not resolved.is_dir():
        return None
    return resolved
