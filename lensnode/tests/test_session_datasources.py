import os
import uuid
from pathlib import Path
from types import SimpleNamespace

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
    (source / "README.md").write_text("ready", encoding="utf-8")

    command = _command(datasource_uuid)
    materialize_datasources(
        type("Config", (), {"workspace_path": str(workspace)})(),
        command,
        runtime,
    )

    link = workspace / "sessions" / command["run_uuid"] / "sources" / "source"
    assert link.is_symlink()
    assert link.resolve() == source.resolve()
    assert command["target_dirs"] == [{
        "name": "source",
        "path": str(link),
    }]


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


def test_relative_link_survives_staging_and_workspace_relocation(tmp_path):
    """Resolve links from the final Session directory, not staging."""

    workspace = tmp_path / "workspace"
    datasource_uuid = uuid.uuid4()
    source = workspace / "datasources" / f"{datasource_uuid}-abc123"
    source.mkdir(parents=True)
    (source / "README.md").write_text("ready", encoding="utf-8")
    command = _command(datasource_uuid)
    command["session_uuid"] = str(uuid.uuid4())
    runtime = (
        workspace / "sessions" / command["session_uuid"]
        / "runs" / command["run_uuid"]
    )
    runtime.mkdir(parents=True)
    config = SimpleNamespace(
        workspace_path=str(workspace), runtime_path=str(workspace),
    )
    link = runtime.parent.parent / "sources" / "source"
    link.parent.mkdir()
    link.symlink_to(source, target_is_directory=True)

    for _ in range(2):
        materialize_datasources(config, command, runtime)
        assert link.readlink() == Path("../../../datasources") / source.name
        assert link.resolve(strict=True) == source.resolve()
        assert (link / "README.md").read_text(encoding="utf-8") == "ready"
        assert command["target_dirs"] == [{"name": "source", "path": str(link)}]

    moved = tmp_path / "relocated-workspace"
    relative_link = link.relative_to(workspace)
    workspace.rename(moved)
    assert (moved / relative_link / "README.md").read_text() == "ready"


def test_local_datasource_target_prefers_newest_directory(tmp_path):
    datasource_uuid = uuid.uuid4()
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "datasources").mkdir()
    older = root / "datasources" / f"{datasource_uuid}-one"
    newer = root / "datasources" / f"{datasource_uuid}-two"
    older.mkdir()
    newer.mkdir()
    (older / "old.txt").write_text("old", encoding="utf-8")
    (newer / "new.txt").write_text("new", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    assert local_datasource_target(datasource_uuid, root) == newer.resolve()
    assert default_datasource_target(datasource_uuid, root) == (
        root / "datasources" / str(datasource_uuid)
    )


def test_local_datasource_target_rejects_empty_directories(tmp_path):
    datasource_uuid = uuid.uuid4()
    root = tmp_path / "workspace"
    (root / "datasources" / f"{datasource_uuid}-empty").mkdir(parents=True)

    assert local_datasource_target(datasource_uuid, root) is None
