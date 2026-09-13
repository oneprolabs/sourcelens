"""Transfer frozen datasource versions to remote execution nodes."""

import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings


MAX_DATASOURCE_BYTES = 1024 * 1024 * 1024
MAX_DATASOURCE_FILES = 100000


def run_datasource_snapshots(run):
    """Describe selected versions without exposing control-plane paths."""

    result = []
    for row in run.session.datasource_snapshots.select_related(
        "version", "datasource"
    ):
        if row.version_id is None or row.version.status != "ready":
            raise ValueError("DATASOURCE_VERSION_NOT_READY")
        result.append({
            "snapshot_uuid": str(row.uuid),
            "version_uuid": str(row.version.uuid),
            "datasource_uuid": str(row.datasource.uuid),
            "mount_name": row.mount_name,
            "target_path": row.datasource.target_path,
        })
    return result


def datasource_version_archive(version):
    """Package one version with bounded size and no escaping links."""

    media = Path(settings.MEDIA_ROOT).resolve()
    key = PurePosixPath(version.storage_key)
    if key.is_absolute() or ".." in key.parts or not key.parts:
        raise ValueError("DATASOURCE_STORAGE_PATH_INVALID")
    source = media / key
    root = source.resolve()
    if source.is_symlink() or not root.is_relative_to(media):
        raise ValueError("DATASOURCE_STORAGE_PATH_INVALID")
    if root == media or not root.is_dir():
        raise ValueError("DATASOURCE_TARGET_UNAVAILABLE")
    archive = tempfile.TemporaryFile()
    count = total = 0
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as bundle:
            for directory, dirs, files in os.walk(root, followlinks=False):
                for name in dirs + files:
                    path = Path(directory) / name
                    if path.is_symlink():
                        raise ValueError("DATASOURCE_ARCHIVE_LINK_UNSUPPORTED")
                    if path.is_dir():
                        continue
                    if not path.is_file():
                        raise ValueError("DATASOURCE_ARCHIVE_FILE_INVALID")
                    count += 1
                    total += path.stat().st_size
                    if (count > MAX_DATASOURCE_FILES
                            or total > MAX_DATASOURCE_BYTES):
                        raise ValueError("DATASOURCE_ARCHIVE_LIMIT_EXCEEDED")
                    bundle.write(path, path.relative_to(root).as_posix())
        archive.seek(0)
        return archive
    except BaseException:
        archive.close()
        raise
