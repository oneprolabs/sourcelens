"""Materialize Run-bound datasource versions without shared filesystems."""

import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from .tls import create_config_ssl_context


MAX_DATASOURCE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_DATASOURCE_BYTES + 32 * 1024 * 1024
MAX_DATASOURCE_FILES = 100000


def materialize_datasources(
    config, command, runtime_root, cancel_event=None, on_activity=None,
):
    """Download selected immutable versions and replace node target paths."""

    snapshots = command.get("datasource_snapshots") or []
    if not snapshots:
        return
    run_uuid = str(uuid.UUID(command["run_uuid"]))
    base = str(config.ai_gateway_url).rstrip("/")
    if base.endswith("/ai-gateway"):
        base = base[:-len("/ai-gateway")]
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
            snapshot_uuid = str(uuid.UUID(snapshot["snapshot_uuid"]))
            version_uuid = str(uuid.UUID(snapshot["version_uuid"]))
            url = f"{base}/runs/{run_uuid}/datasources/{snapshot_uuid}/"
            with tempfile.TemporaryFile() as archive:
                size = 0
                with httpx.Client(
                    timeout=config.request_timeout_s,
                    verify=create_config_ssl_context(config),
                    follow_redirects=False,
                ) as client:
                    with client.stream(
                        "GET", url,
                        headers={"Authorization": f"Bearer {config.token}"},
                    ) as response:
                        response.raise_for_status()
                        if response.headers.get(
                            "X-Datasource-Version"
                        ) != version_uuid:
                            raise ValueError("DATASOURCE_VERSION_MISMATCH")
                        for chunk in response.iter_bytes(1024 * 1024):
                            check_activity()
                            size += len(chunk)
                            if size > MAX_ARCHIVE_BYTES:
                                raise ValueError("DATASOURCE_ARCHIVE_TOO_LARGE")
                            archive.write(chunk)
                archive.seek(0)
                destination = staging / name
                destination.mkdir()
                _extract_archive(archive, destination, check_activity)
            directories.append({"name": name, "path": str(target / name)})
        check_activity()
        if target.is_symlink():
            raise ValueError("SESSION_WORKSPACE_PATH_INVALID")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging), str(target))
    command["target_dirs"] = directories
    command["workspace_path"] = str(target)


def _extract_archive(archive, destination, check_activity):
    """Extract regular files only, with traversal and expansion limits."""

    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > MAX_DATASOURCE_FILES:
            raise ValueError("DATASOURCE_ARCHIVE_TOO_LARGE")
        total = 0
        names = set()
        for entry in entries:
            check_activity()
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (not path.parts or path.is_absolute() or ".." in path.parts
                    or "\\" in entry.filename or path in names
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError("DATASOURCE_ARCHIVE_PATH_INVALID")
            names.add(path)
            total += entry.file_size
            if total > MAX_DATASOURCE_BYTES:
                raise ValueError("DATASOURCE_ARCHIVE_TOO_LARGE")
            output = destination / path
            if entry.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, output.open("xb") as stream:
                while chunk := source.read(1024 * 1024):
                    check_activity()
                    stream.write(chunk)
            output.chmod(0o700 if mode & 0o111 else 0o600)
