"""Verify session ownership, cleanup boundaries, and execution exclusion."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from lensnode.session_workspace import (
    cleanup_run,
    cleanup_session,
    run_root,
    session_lock,
    session_root,
)


@pytest.fixture
def config(tmp_path):
    """Keep datasource storage separate from session runtime storage."""
    return SimpleNamespace(
        workspace_path=str(tmp_path / "workspace"),
        runtime_path=str(tmp_path / "runtime"),
    )


def test_runs_share_session_root_but_have_distinct_directories(config):
    """Successive runs reuse a session without sharing run-owned files."""
    session_uuid = str(uuid4())
    first_run = str(uuid4())
    second_run = str(uuid4())
    expected = Path(config.runtime_path) / "sessions" / session_uuid

    assert session_root(config, session_uuid) == expected
    assert run_root(config, session_uuid, first_run) == (
        expected / "runs" / first_run
    )
    assert run_root(config, session_uuid, second_run) == (
        expected / "runs" / second_run
    )
    assert not (Path(config.workspace_path) / "sessions").exists()


@pytest.mark.parametrize("runtime_path", [None, ""])
def test_empty_runtime_path_falls_back_to_workspace(config, runtime_path):
    """Legacy configurations retain a usable workspace root."""
    config.runtime_path = runtime_path
    session_uuid = str(uuid4())

    assert session_root(config, session_uuid) == (
        Path(config.workspace_path) / "sessions" / session_uuid
    )


def test_missing_runtime_path_falls_back_to_workspace(config):
    """Configurations predating runtime_path remain supported."""
    del config.runtime_path
    session_uuid = str(uuid4())

    assert session_root(config, session_uuid) == (
        Path(config.workspace_path) / "sessions" / session_uuid
    )


@pytest.mark.parametrize(
    "identifier", ["", "../escape", "/tmp/escape", "a/b", "a\\b"]
)
def test_session_identifier_cannot_escape_runtime(config, identifier):
    """Untrusted session identifiers cannot become arbitrary paths."""
    with pytest.raises((ValueError, RuntimeError)):
        session_root(config, identifier)


@pytest.mark.parametrize(
    "identifier", ["", "../escape", "/tmp/escape", "a/b", "a\\b"]
)
def test_run_identifier_cannot_escape_session(config, identifier):
    """Run identifiers must not bypass their owning session."""
    with pytest.raises((ValueError, RuntimeError)):
        run_root(config, str(uuid4()), identifier)


@pytest.mark.parametrize("component", ["sessions", "session", "runs"])
def test_roots_reject_symlinked_ancestors(config, tmp_path, component):
    """Existing intermediate links cannot redirect run storage."""
    session_uuid = str(uuid4())
    outside = tmp_path / "outside"
    outside.mkdir()
    root = Path(config.runtime_path) / "sessions"
    if component != "sessions":
        root /= session_uuid
    if component == "runs":
        root /= "runs"
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises((ValueError, RuntimeError)):
        run_root(config, session_uuid, str(uuid4()))

    assert list(outside.iterdir()) == []


def test_cleanup_session_does_not_follow_datasource_links(config):
    """Reclaiming one session preserves shared sources and other sessions."""
    session_uuid = str(uuid4())
    root = session_root(config, session_uuid)
    sources = root / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    shared = Path(config.workspace_path) / "datasources" / str(uuid4())
    shared.mkdir(parents=True)
    original = shared / "source.txt"
    original.write_text("shared source", encoding="utf-8")
    (sources / "source").symlink_to(shared, target_is_directory=True)
    sibling = session_root(config, str(uuid4()))
    sibling.mkdir(parents=True, exist_ok=True)
    retained = sibling / "result.json"
    retained.write_text("other session", encoding="utf-8")

    cleanup_session(config, session_uuid)
    cleanup_session(config, session_uuid)

    assert not root.exists()
    assert original.read_text(encoding="utf-8") == "shared source"
    assert retained.read_text(encoding="utf-8") == "other session"


def test_cleanup_preserves_lock_inode_for_recreated_session(config):
    """Deleting and recreating a Session cannot split its mutex."""

    session_uuid = str(uuid4())
    with session_lock(config, session_uuid):
        pass
    locks = Path(config.runtime_path) / ".session-locks"
    lock = locks / f"{session_uuid}.lock"
    inode = lock.stat().st_ino
    assert cleanup_session(config, session_uuid)
    assert cleanup_session(config, session_uuid)
    assert not session_root(config, session_uuid).exists()
    with session_lock(config, session_uuid):
        assert lock.stat().st_ino == inode
        with pytest.raises(RuntimeError, match="SESSION_WORKSPACE_BUSY"):
            cleanup_session(config, session_uuid)


def test_cleanup_session_rejects_symlinked_session(config, tmp_path):
    """A replaced session root must never redirect recursive deletion."""
    session_uuid = str(uuid4())
    outside = tmp_path / "outside"
    outside.mkdir()
    retained = outside / "keep.txt"
    retained.write_text("keep", encoding="utf-8")
    root = Path(config.runtime_path) / "sessions" / session_uuid
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises((ValueError, RuntimeError)):
        cleanup_session(config, session_uuid)

    assert retained.read_text(encoding="utf-8") == "keep"


def test_cleanup_run_preserves_results_checkpoints_and_other_runs(config):
    """Run completion removes temporary files without erasing continuity."""
    session_uuid = str(uuid4())
    run_uuid = str(uuid4())
    root = run_root(config, session_uuid, run_uuid)
    temporary = root / "tmp" / "nested"
    temporary.mkdir(parents=True, exist_ok=True)
    (temporary / "scratch.txt").write_text("scratch", encoding="utf-8")
    retained_paths = [
        root / "result.json",
        root / "outputs" / "report.md",
        root / "checkpoint" / "state.sqlite",
        run_root(config, session_uuid, str(uuid4())) / "tmp" / "active.txt",
        session_root(config, session_uuid) / "skills" / "guide.md",
    ]
    for path in retained_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("retained", encoding="utf-8")

    cleanup_run(config, session_uuid, run_uuid)
    cleanup_run(config, session_uuid, run_uuid)

    assert not (temporary / "scratch.txt").exists()
    for path in retained_paths:
        assert path.read_text(encoding="utf-8") == "retained"


def test_cleanup_run_does_not_follow_links_in_tmp(config, tmp_path):
    """Temporary links are disposable; their external targets are not."""
    session_uuid = str(uuid4())
    run_uuid = str(uuid4())
    temporary = run_root(config, session_uuid, run_uuid) / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    retained = outside / "keep.txt"
    retained.write_text("keep", encoding="utf-8")
    (temporary / "external").symlink_to(outside, target_is_directory=True)

    cleanup_run(config, session_uuid, run_uuid)

    assert retained.read_text(encoding="utf-8") == "keep"
    assert not (temporary / "external").is_symlink()


def test_session_lock_is_exclusive_and_released_on_error(config):
    """Concurrent admission fails promptly and exceptions release ownership."""
    session_uuid = str(uuid4())
    with pytest.raises(LookupError, match="execution failed"):
        with session_lock(config, session_uuid):
            with pytest.raises(RuntimeError, match="SESSION_WORKSPACE_BUSY"):
                with session_lock(config, session_uuid):
                    pytest.fail("A second execution acquired the same session")
            raise LookupError("execution failed")

    with session_lock(config, session_uuid):
        pass


def test_different_sessions_can_hold_locks_at_the_same_time(config):
    """Per-session serialization does not block unrelated conversations."""
    with session_lock(config, str(uuid4())):
        with session_lock(config, str(uuid4())):
            pass


@pytest.mark.parametrize("operation", ["lock", "cleanup"])
def test_session_lock_blocks_other_processes(config, operation):
    """Execution and collection honor the same lock across processes."""
    session_uuid = str(uuid4())
    script = """
import sys
from types import SimpleNamespace
from lensnode.session_workspace import cleanup_session, session_lock

config = SimpleNamespace(workspace_path=sys.argv[1], runtime_path=sys.argv[2])
try:
    if sys.argv[4] == "cleanup":
        cleanup_session(config, sys.argv[3])
    else:
        with session_lock(config, sys.argv[3]):
            pass
except RuntimeError as exc:
    if "SESSION_WORKSPACE_BUSY" not in str(exc):
        raise
else:
    raise AssertionError("A second process bypassed session ownership")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    with session_lock(config, session_uuid):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                config.workspace_path,
                config.runtime_path,
                session_uuid,
                operation,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

    assert result.returncode == 0, result.stdout + result.stderr
