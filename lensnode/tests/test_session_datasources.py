import os
import uuid

import pytest

from lensnode.session_datasources import (
    default_datasource_target,
    local_datasource_target,
    materialize_datasources,
)


def _command(datasource_uuid):
    return {
        "run_uuid": str(uuid.uuid4()),
        "datasource_snapshots": [
            {
                "datasource_uuid": str(datasource_uuid),
                "mount_name": "source",
            }
        ],
    }


def test_materialize_datasource_uses_local_uuid_directory(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    workspace.mkdir()
    runtime.mkdir()
    datasource_uuid = uuid.uuid4()
    source = workspace / "datasources" / f"{datasource_uuid}-abc123"
    source.mkdir(parents=True)

    command = _command(datasource_uuid)
    materialize_datasources(
        type("Config", (), {"workspace_path": str(workspace)})(),
        command,
        runtime,
    )

    link = workspace / "sessions" / command["run_uuid"] / "sources" / "source"
    assert link.is_symlink()
    assert link.resolve() == source.resolve()


def test_materialize_datasource_rejects_missing_local_directory(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    workspace.mkdir()
    runtime.mkdir()

    with pytest.raises(RuntimeError, match="DATASOURCE_TARGET_UNAVAILABLE"):
        materialize_datasources(
            type("Config", (), {"workspace_path": str(workspace)})(),
            _command(uuid.uuid4()),
            runtime,
        )


def test_local_datasource_target_prefers_newest_directory(tmp_path):
    datasource_uuid = uuid.uuid4()
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "datasources").mkdir()
    older = root / "datasources" / f"{datasource_uuid}-one"
    newer = root / "datasources" / f"{datasource_uuid}-two"
    older.mkdir()
    newer.mkdir()
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    assert local_datasource_target(datasource_uuid, root) == newer.resolve()
    assert default_datasource_target(datasource_uuid, root) == (
        root / "datasources" / str(datasource_uuid)
    )
