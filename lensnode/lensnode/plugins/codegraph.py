"""Built-in CodeGraph MCP plugin.

Contributes a stdio MCP server that exposes a CodeGraph knowledge graph
of the lensnode workspace to the agent, indexing the workspace once on
first use.
"""

import fcntl
import json
import os
import shutil
import subprocess
from pathlib import Path

from ..mcp_tools import MCPToolFirstMiddleware
from . import AgentRuntimeContribution, LensNodePlugin

CODEGRAPH_SERVER_NAME = "codegraph"
CODEGRAPH_INDEX_DIR = ".codegraph"
CODEGRAPH_INIT_LOCK = "codegraph-init.lock"
CODEGRAPH_CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}


class CodeGraphPlugin(LensNodePlugin):
    """Contribute the CodeGraph MCP server for the current workspace."""

    name = CODEGRAPH_SERVER_NAME

    def enabled(self, config):
        """Require the toggle, an allowlisted binary on PATH."""

        command = str(getattr(config, "codegraph_command", "") or "")
        allowlist = getattr(config, "mcp_stdio_allowlist", ()) or ()
        return bool(
            getattr(config, "mcp_enable_codegraph", False)
            and command
            and Path(command).name in allowlist
            and shutil.which(command)
        )

    def contribute_mcp_servers(
        self,
        config,
        emit_event=None,
        command=None,
    ):
        """Return the CodeGraph stdio server once the index is usable."""

        if command is not None and command.get("task") != "code_analysis":
            return []
        workspace = _codegraph_workspace(config, command)
        if workspace is None:
            return []
        if not _ensure_codegraph_index(
            config,
            workspace,
            emit_event=emit_event,
        ):
            return []
        return [
            {
                "name": CODEGRAPH_SERVER_NAME,
                "transport": "stdio",
                "endpoint": "",
                "config": {
                    "command": config.codegraph_command,
                    "args": ["serve", "--mcp", "--path", str(workspace)],
                },
                "load_config": {},
            }
        ]
    def contribute_agent_runtime(self, config, command, mcp_tools):
        """Prioritize CodeGraph when its MCP tools are available."""

        del config
        if (command or {}).get("task") != "code_analysis":
            return None
        if not any(
            str(getattr(tool, "name", "")).startswith("mcp__codegraph__")
            for tool in mcp_tools
        ):
            return None
        middleware = MCPToolFirstMiddleware("mcp__codegraph__")
        return AgentRuntimeContribution(
            prompt_guidance=(
                "CodeGraph is available through the MCP tool family "
                "mcp__codegraph__. For structural code questions — where "
                "a symbol or function is defined, what calls what, how a "
                "module reaches another, or what would break if something "
                "changed — MUST call the CodeGraph MCP tool before any "
                "workspace search or file-reading tool. Use workspace "
                "search only for literal text such as exact traceback "
                "lines, comments, log messages, or regex patterns, and "
                "after CodeGraph has identified the relevant files. If a "
                "CodeGraph call is empty, unavailable, or fails, immediately "
                "fall back to search_workspace with keywords from the "
                "question, symbol names, and the failed query, then read "
                "the relevant source with read_workspace_file."
            ),
            middleware=(middleware,),
            subagent_middleware=(middleware,),
            always_visible_tool_prefixes=("mcp__codegraph__",),
        )


def _codegraph_workspace(config, command):
    """Return the current Run's Session root for CodeGraph indexing."""

    workspace_root = Path(config.workspace_path).resolve()
    target_dirs = (command or {}).get("target_dirs") or []
    candidate = target_dirs[0].get("path") if target_dirs else ""
    if not candidate:
        return workspace_root
    workspace = Path(candidate).resolve()
    sessions_root = workspace_root / "sessions"
    try:
        workspace.relative_to(sessions_root)
    except ValueError:
        return None
    return workspace


def _ensure_codegraph_index(config, workspace, emit_event=None):
    """Build the workspace CodeGraph index once, guarded by a lock.

    Creates the index on first use; afterwards refreshes it only when the
    source worktree has pending changes or the tooling version changed.
    """

    index_dir = workspace / CODEGRAPH_INDEX_DIR
    if not index_dir.is_dir() and not _has_indexable_code(workspace):
        return False
    lock_dir = workspace / ".sourcelens"
    lock_path = lock_dir / CODEGRAPH_INIT_LOCK
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
        timeout_s = int(getattr(config, "codegraph_init_timeout_s", 300))
        with open(lock_path, "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                if index_dir.is_dir():
                    return _refresh_codegraph_index(
                        config, workspace, emit_event=emit_event
                    )
                subprocess.run(
                    [
                        str(config.codegraph_command),
                        "init",
                        str(workspace),
                    ],
                    timeout=timeout_s,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        if emit_event is not None:
            emit_event(
                "codegraph.init.failed",
                {"reason": type(exc).__name__},
            )
        return False
    return index_dir.is_dir()


def _refresh_codegraph_index(config, workspace, emit_event=None):
    """Refresh an existing index only when the worktree or tooling changed.

    Must be called under the same lock that guards init, so a refresh never
    races an in-progress build or another run's sync.
    """

    status = _codegraph_status(config, workspace)
    if status is None:
        return True
    if status.get("reindex") or status.get("state") != "complete":
        return _codegraph_rebuild(config, workspace, emit_event=emit_event)
    if status.get("pending"):
        return _codegraph_sync(config, workspace, emit_event=emit_event)
    return True


def _codegraph_status(config, workspace):
    """Return (pending, reindex, state) from `codegraph status --json`.

    Returns None when the index is not initialized or status cannot be
    parsed, in which case the caller keeps the existing index untouched.
    """

    try:
        result = subprocess.run(
            [
                str(config.codegraph_command),
                "status",
                str(workspace),
                "--json",
            ],
            timeout=int(getattr(config, "codegraph_init_timeout_s", 300)),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not payload.get("initialized"):
        return None
    pending = payload.get("pendingChanges") or {}
    has_pending = bool(
        pending.get("added") or pending.get("modified") or pending.get("removed")
    )
    index_info = payload.get("index") or {}
    return {
        "pending": has_pending,
        "reindex": bool(index_info.get("reindexRecommended")),
        "state": index_info.get("state"),
    }


def _codegraph_sync(config, workspace, emit_event=None):
    """Incrementally update the index for changed source files."""

    try:
        subprocess.run(
            [
                str(config.codegraph_command),
                "sync",
                str(workspace),
                "--quiet",
            ],
            timeout=int(getattr(config, "codegraph_init_timeout_s", 300)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if emit_event is not None:
            emit_event(
                "codegraph.sync.failed",
                {"reason": type(exc).__name__},
            )
        return False
    return True


def _codegraph_rebuild(config, workspace, emit_event=None):
    """Rebuild the index when the tooling or extraction format changed."""

    try:
        subprocess.run(
            [
                str(config.codegraph_command),
                "index",
                str(workspace),
                "--quiet",
            ],
            timeout=int(getattr(config, "codegraph_init_timeout_s", 300)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if emit_event is not None:
            emit_event(
                "codegraph.rebuild.failed",
                {"reason": type(exc).__name__},
            )
        return False
    return True


def _has_indexable_code(workspace):
    """Return whether the workspace has source files CodeGraph indexes."""

    if not workspace.is_dir():
        return False
    try:
        seen_dirs = set()
        boundary = _symlink_boundary(workspace)
        for current_root, dirnames, filenames in os.walk(
            workspace, followlinks=True
        ):
            real_root = os.path.realpath(current_root)
            if real_root in seen_dirs:
                dirnames[:] = []
                continue
            seen_dirs.add(real_root)
            dirnames[:] = [
                name
                for name in dirnames
                if _symlink_target_allowed(
                    Path(current_root) / name, boundary
                )
                if os.path.realpath(os.path.join(current_root, name))
                not in seen_dirs
            ]
            if any(
                Path(name).suffix.lower() in CODEGRAPH_CODE_EXTENSIONS
                and _symlink_target_allowed(
                    Path(current_root) / name, boundary
                )
                for name in filenames
            ):
                return True
    except OSError:
        return False
    return False


def _symlink_boundary(workspace):
    """Return the trusted workspace boundary for datasource links."""

    parts = workspace.resolve().parts
    if "sessions" in parts:
        workspace_root = Path(*parts[: parts.index("sessions")])
        return workspace_root / "datasources"
    return workspace.resolve()


def _symlink_target_allowed(path, boundary):
    """Allow Session datasource links but reject unrelated directories."""

    if not path.is_symlink():
        return True
    try:
        path.resolve().relative_to(boundary)
    except (OSError, ValueError):
        return False
    return True
