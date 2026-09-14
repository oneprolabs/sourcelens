"""Validated Session and Run workspace paths and lifecycle operations."""

import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path


UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _identifier(value, label):
    """Validate one externally supplied workspace identifier."""

    identifier = str(value or "").strip()
    if not UUID_PATTERN.fullmatch(identifier):
        raise ValueError(f"Invalid {label} identifier")
    return identifier


def sessions_root(config):
    """Return the node-local root containing all Session workspaces."""

    base = Path(
        getattr(config, "runtime_path", None) or config.workspace_path
    )
    root = base / "sessions"
    if root.is_symlink():
        raise ValueError("Session root cannot be a symlink")
    return root.resolve()


def session_root(config, session_uuid):
    """Return a validated Session root."""

    root = sessions_root(config)
    path = root / _identifier(session_uuid, "Session")
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("Session workspace path is invalid")
    return path


def run_root(config, session_uuid, run_uuid):
    """Return a validated per-Run directory below its Session."""

    session = session_root(config, session_uuid)
    runs = session / "runs"
    path = runs / _identifier(run_uuid, "Run")
    if runs.is_symlink() or path.is_symlink():
        raise ValueError("Run workspace path is invalid")
    if path.resolve().parent != runs:
        raise ValueError("Run workspace path is invalid")
    return path


@contextmanager
def session_lock(config, session_uuid, *, create=True):
    """Serialize Session setup, execution admission, and cleanup."""

    root = session_root(config, session_uuid)
    locks = root.parent.parent / ".session-locks"
    if locks.is_symlink():
        raise ValueError("Session lock directory is invalid")
    locks.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = locks / f"{root.name}.lock"
    descriptor = os.open(
        lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
    )
    with os.fdopen(descriptor, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("SESSION_WORKSPACE_BUSY") from exc
        try:
            root = session_root(config, session_uuid)
            if create:
                root.mkdir(parents=True, mode=0o700, exist_ok=True)
            yield root
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _remove_tree_without_following_links(path):
    """Remove a path while treating every symlink as a leaf."""

    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_symlink():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            _remove_tree_without_following_links(child)
        else:
            child.unlink(missing_ok=True)
    path.rmdir()


def cleanup_run(config, session_uuid, run_uuid):
    """Remove disposable Run files while preserving retained resources."""

    run = run_root(config, session_uuid, run_uuid)
    if not run.exists():
        return False
    for name in ("tmp", "conversation-artifacts", "subject-documents"):
        _remove_tree_without_following_links(run / name)
    return True


def cleanup_session(config, session_uuid):
    """Remove one Session workspace without following datasource links."""

    with session_lock(config, session_uuid, create=False) as root:
        if root.exists():
            _remove_tree_without_following_links(root)
        return True
