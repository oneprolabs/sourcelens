import csv
import hashlib
import json
import math
import mimetypes
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from itertools import islice
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from langchain_core.tools import tool
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from .tls import create_config_ssl_context
from .workspace import (
    citation_path,
    glob_files,
    is_path_allowed,
    is_path_excluded,
    read_workspace_window,
    retrieval_path,
)
from .workspace import search_workspace as search_workspace_files
from .workspace import (
    target_scope,
)

# Tools that emit their own tool.<name>.start/.done events. The generic
# tool.<name>.invoke event is suppressed for these to avoid duplicate trace
# entries (their .start already carries the richer argument summary).
SELF_REPORTING_TOOLS = {
    "search_workspace",
    "read_workspace_file",
    "find_files",
    "git_log",
    "git_diff",
    "summarize_recent_changes",
    "call_skill_api",
    "validate_records",
    "analyze_structured_output",
    "inspect_saved_output",
    "run_skill_script",
    "run_skill_transform",
    "append_file",
}

_STRUCTURED_INPUT_MAX_BYTES = 50 * 1024 * 1024
_STRUCTURED_GROUP_MAX_ITEMS = 1000
_STRUCTURED_VALIDATION_MAX_ITEMS = 1_000_000
_STRUCTURED_REF_MIN_CHARS = 800
_SKILL_SCRIPT_MAX_OUTPUT_BYTES_PER_CALL = 50 * 1024 * 1024
_SKILL_SCRIPT_DEFAULT_MAX_OUTPUT_BYTES_PER_RUN = 200 * 1024 * 1024
_SKILL_SCRIPT_MAX_OUTPUT_BYTES_PER_RUN = 2 * 1024 * 1024 * 1024
_APPEND_FILE_CHUNK_MAX_BYTES = 24 * 1024
_APPEND_FILE_CHUNK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_APPEND_FILE_MANIFEST = ".sourcelens-append-chunks.json"
_SAVED_OUTPUT_LINE_MAX_CHARS = 500
_STRUCTURED_OPERATIONS = {
    "count",
    "group_count",
    "max",
    "min",
    "paginate",
    "project",
    "sample",
    "sort",
    "sum",
    "validate_records",
}


class _ReadWorkspaceFileArgs(BaseModel):
    """Args schema for read_workspace_file.

    Accepts both ``file_path`` (the name LLMs most often emit, matching the
    common read-file convention) and ``path``. The field has a default so
    LangChain still binds it when the value arrives via the alias, which it
    would otherwise drop for a required field.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(
        default="",
        validation_alias=AliasChoices("file_path", "path"),
        description="Workspace file path to read.",
    )
    offset: int = Field(default=1, description="1-based start line.")
    limit: int = Field(default=250, description="Max lines to read.")


class _GitLogArgs(BaseModel):
    """Args schema for git_log."""

    path: str = Field(default="", description="Workspace repository path.")
    max_count: int | str = Field(
        default=10,
        description="Max commits to return.",
    )


def build_agent_tools(command, resources=None, config=None, emit_event=None):
    """Build read-only tools scoped to the selected workspace dirs."""

    target_dirs = command.get("target_dirs") or []
    settings = command.get("settings") or {}
    retrieval_policy = settings.get("retrieval_policy") or {}

    def emit(name, detail=None):
        if emit_event is not None:
            emit_event(name, detail or {})

    @tool("search_workspace")
    def search_workspace(
        query: str,
        max_results: int = 50,
        regex: bool = False,
        glob: str = "",
        output_mode: str = "content",
        context_lines: int = 2,
        case_sensitive: bool = False,
    ) -> str:
        """Search selected workspace dirs, ripgrep-style.

        output_mode: "content" (default) returns matching lines
        {path, line, text, before, after}; "files" returns the files that
        match; "count" returns per-file match counts.

        query is keywords (fixed-string, case-folded) by default; set
        regex=True to pass a ripgrep regular expression. glob restricts the
        search by path/type (e.g. "**/*.md", "*.py"). Use keywords/terms as
        they appear in the files (translate names into the documents'
        language when needed). File size is not a constraint. In content
        mode, when nothing matches a 'files' listing of the scope is
        returned so you can read files directly with read_workspace_file.
        Converted documents are identified by their original source paths,
        never by internal .sourcelens sidecar paths.
        """

        emit(
            "tool.search_workspace.start",
            {
                "query": query,
                "max_results": max_results,
                "regex": regex,
                "glob": glob,
                "output_mode": output_mode,
                "summary": _search_summary(query, regex, glob, output_mode),
            },
        )
        started = time.monotonic()
        result = search_workspace_files(
            target_dirs,
            query,
            max_results=max_results,
            policy=retrieval_policy,
            regex=regex,
            glob=glob,
            output_mode=output_mode,
            context_lines=context_lines,
            case_sensitive=case_sensitive,
        )
        matches = result.get("matches") or []
        files = result.get("files") or []
        counts = result.get("counts") or []
        paths = list(dict.fromkeys(item["path"] for item in matches))
        emit(
            "tool.search_workspace.done",
            {
                "mode": result.get("mode"),
                "count": len(matches) or len(files) or len(counts),
                "paths": paths[:8],
                "summary": _search_done_summary(result, matches, files, counts, paths),
                "preview": _clip(matches[0]["text"], 140) if matches else "",
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return _json(result)

    @tool("read_workspace_file", args_schema=_ReadWorkspaceFileArgs)
    def read_workspace_file(path: str, offset: int = 1, limit: int = 250) -> str:
        """Read a window of a workspace file: limit lines from offset (1-based).

        Returns numbered lines plus has_more so you can page through any file
        by increasing offset; file size is not a constraint. Call
        search_workspace first to get the line numbers worth reading. An
        original converted-document path transparently reads its searchable
        text while retaining the original path for citation.
        """

        requested_path = str(citation_path(Path(path).resolve()))
        emit(
            "tool.read_workspace_file.start",
            {
                "path": requested_path,
                "offset": offset,
                "limit": limit,
                "summary": (
                    f"{_basename(requested_path)} · lines "
                    f"{offset}-{offset + limit - 1}"
                ),
            },
        )
        resolved = _resolve_allowed_path(path, target_dirs, retrieval_policy)
        if resolved is None:
            directory = _resolve_allowed_directory(
                path,
                target_dirs,
                retrieval_policy,
            )
            if directory is not None:
                candidates = _list_directory_files(
                    directory,
                    target_dirs,
                    retrieval_policy,
                )
                emit(
                    "tool.read_workspace_file.directory",
                    {
                        "path": str(directory),
                        "candidate_count": len(candidates),
                    },
                )
                return _json(
                    {
                        "error": "PATH_IS_DIRECTORY",
                        "path": str(directory),
                        "candidate_files": candidates,
                    }
                )
            emit("tool.read_workspace_file.denied", {"path": path})
            return _json({"error": "PATH_NOT_ALLOWED", "path": path})
        started = time.monotonic()
        window = read_workspace_window(
            str(resolved),
            offset=offset,
            limit=limit,
            policy=retrieval_policy,
        )
        visible_path = str(citation_path(resolved))
        window["path"] = visible_path
        emit(
            "tool.read_workspace_file.done",
            {
                "path": visible_path,
                "start": window.get("start_line"),
                "end": window.get("end_line"),
                "has_more": window.get("has_more"),
                "summary": (
                    f"{_basename(visible_path)} · lines "
                    f"{window.get('start_line')}-{window.get('end_line')}"
                    f"{' (+more)' if window.get('has_more') else ''}"
                ),
                "preview": _clip(window.get("content") or "", 200),
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return _json(window)

    @tool("find_files")
    def find_files(pattern: str, max_results: int = 50) -> str:
        """Find files by name/path glob across the workspace (newest first).

        Use when you know a filename or want to enumerate files of a type,
        e.g. pattern="**/*.md", "**/*install*", "src/**/*.py". Returns file
        paths sorted by modification time; read them with
        read_workspace_file. Converted content is returned under its original
        document path rather than an internal .sourcelens path.
        """

        emit(
            "tool.find_files.start",
            {
                "pattern": pattern,
                "max_results": max_results,
                "summary": pattern,
            },
        )
        started = time.monotonic()
        files = glob_files(
            target_dirs,
            pattern,
            max_results=max_results,
            policy=retrieval_policy,
        )
        note = None
        if not files and pattern and "**" not in pattern:
            # A non-recursive glob (e.g. "*") only matches the top level,
            # which is empty when the workspace root holds only
            # subdirectories. Retry recursively so a shallow pattern never
            # reads as "the workspace has no files".
            recursive = "**/" + pattern.lstrip("/")
            files = glob_files(
                target_dirs,
                recursive,
                max_results=max_results,
                policy=retrieval_policy,
            )
            if files:
                note = (
                    f"pattern '{pattern}' is non-recursive and matched "
                    f"nothing at the top level; showing recursive "
                    f"'{recursive}' results instead."
                )
        emit(
            "tool.find_files.done",
            {
                "count": len(files),
                "paths": files[:8],
                "summary": (
                    f"{len(files)} files · {_names(files)}" if files else "0 files"
                ),
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        payload = {"files": files}
        if note:
            payload["note"] = note
        return _json(payload)

    @tool("git_log", args_schema=_GitLogArgs)
    def git_log(path: str = "", max_count: int | str = 10) -> str:
        """Show recent git commits for a selected workspace repository."""

        root = _resolve_repo_path(path, target_dirs)
        if root is None:
            repos = _discover_git_repositories(path, target_dirs)
            if repos:
                emit(
                    "tool.git_log.repositories",
                    {
                        "path": path,
                        "count": len(repos),
                    },
                )
                return _json(
                    {
                        "error": "PATH_IS_NOT_REPOSITORY",
                        "repositories": repos,
                    }
                )
            emit("tool.git_log.denied", {"path": path})
            return _json({"error": "PATH_NOT_ALLOWED", "path": path})
        commit_count = min(_positive_int(max_count, default=10), 50)
        emit(
            "tool.git_log.start",
            {
                "path": str(root),
                "max_count": commit_count,
            },
        )
        result = _run_git(
            root,
            [
                "log",
                f"--max-count={commit_count}",
                "--date=iso",
                "--pretty=format:%h%x09%ad%x09%s",
            ],
        )
        emit("tool.git_log.done", {"path": str(root), "ok": result["ok"]})
        return _json(result)

    @tool("git_diff")
    def git_diff(path: str = "", ref: str = "HEAD~1..HEAD") -> str:
        """Show a read-only git diff for a selected workspace repository."""

        root = _resolve_repo_path(path, target_dirs)
        if root is None:
            repos = _discover_git_repositories(path, target_dirs)
            if repos:
                emit(
                    "tool.git_diff.repositories",
                    {
                        "path": path,
                        "count": len(repos),
                    },
                )
                return _json(
                    {
                        "error": "PATH_IS_NOT_REPOSITORY",
                        "repositories": repos,
                    }
                )
            emit("tool.git_diff.denied", {"path": path})
            return _json({"error": "PATH_NOT_ALLOWED", "path": path})
        emit("tool.git_diff.start", {"path": str(root), "ref": ref})
        result = _run_git(root, ["diff", "--stat", ref])
        if result["ok"]:
            detail = _run_git(root, ["diff", "--find-renames", ref])
            result["diff"] = detail.get("stdout", "")[:20000]
        emit("tool.git_diff.done", {"path": str(root), "ok": result["ok"]})
        return _json(result)

    @tool("summarize_recent_changes")
    def summarize_recent_changes(query: str, max_commits: int = 20) -> str:
        """Collect recent git evidence for repositories matching a query."""

        emit(
            "tool.summarize_recent_changes.start",
            {
                "query": query,
                "max_commits": max_commits,
            },
        )
        repositories = _matching_repositories(query, target_dirs)
        if not repositories:
            candidates = _discover_git_repositories("", target_dirs, limit=20)
            emit(
                "tool.summarize_recent_changes.no_match",
                {
                    "query": query,
                    "candidate_count": len(candidates),
                },
            )
            return _json(
                {
                    "error": "NO_MATCHING_REPOSITORY",
                    "query": query,
                    "candidate_repositories": candidates,
                    "instruction": (
                        "Choose a repository from candidate_repositories or "
                        "ask the user to clarify the project name."
                    ),
                }
            )
        summaries = []
        for repo in repositories[:3]:
            commit_count = min(
                _positive_int(max_commits, default=20),
                30,
            )
            log_result = _run_git(
                repo,
                [
                    "log",
                    f"--max-count={commit_count}",
                    "--date=iso",
                    "--pretty=format:%h%x09%ad%x09%s",
                ],
            )
            diff_ref = f"HEAD~{commit_count}..HEAD"
            diff_stat = _run_git(repo, ["diff", "--stat", diff_ref])
            diff_detail = _run_git(repo, ["diff", "--find-renames", diff_ref])
            summaries.append(
                {
                    "repository": str(repo),
                    "commits": log_result.get("stdout", ""),
                    "diff_ref": diff_ref,
                    "diff_stat": diff_stat.get("stdout", ""),
                    "diff": diff_detail.get("stdout", "")[:20000],
                }
            )
        emit(
            "tool.summarize_recent_changes.done",
            {
                "query": query,
                "repository_count": len(summaries),
                "repositories": [item["repository"] for item in summaries],
            },
        )
        return _json({"repositories": summaries})

    tools = [
        search_workspace,
        read_workspace_file,
        find_files,
        summarize_recent_changes,
        git_log,
        git_diff,
    ]
    if resources is not None and config is not None:
        tools.append(_build_append_file_tool(resources, emit_event))
        tools.append(
            _build_save_deliverable_tool(command, resources, config, emit_event)
        )
    return tools


class _RunSkillScriptArgs(BaseModel):
    """Args schema for running an executable bundled with a Skill."""

    skill: str = Field(description="Loaded Skill package name.")
    script: str = Field(
        description=(
            "Path of the executable relative to the Skill root, for example "
            "scripts/report.sh, scripts/tool.py, or bin/linux-amd64/glab."
        )
    )
    args: list[str] = Field(default_factory=list, description="Arguments.")
    stdin: str = Field(default="", description="Optional standard input.")


class _AnalyzeStructuredOutputArgs(BaseModel):
    """Args schema for bounded analysis of a saved JSON tool result."""

    ref: str = Field(
        min_length=1,
        max_length=512,
        description="Input reference below /large_tool_results/.",
    )
    operation: str = Field(
        description=(
            "One of count, project, group_count, sum, min, max, sort, "
            "sample, paginate, or validate_records."
        )
    )
    path: str = Field(
        default="",
        max_length=512,
        description="Optional dotted path to the target JSON value.",
    )
    field: str = Field(
        default="",
        max_length=512,
        description="Value or sort field for sum/min/max/sort.",
    )
    group_by: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="One to three dotted fields for group_count.",
    )
    fields: list[str] = Field(
        default_factory=list,
        max_length=32,
        description=(
            "Optional dotted fields returned by project, sort, sample, "
            "or paginate; required fields for validate_records."
        ),
    )
    expected_count: int | None = Field(
        default=None,
        ge=0,
        le=1000000,
        description="Expected item count for validate_records.",
    )
    unique_by: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="One to three unique-key fields for validate_records.",
    )
    offset: int = Field(default=0, ge=0, le=1000000)
    limit: int = Field(default=100, ge=1, le=1000)
    descending: bool = Field(default=False)


class _ValidateRecordsArgs(BaseModel):
    """Args schema for completeness validation of saved JSON records."""

    ref: str = Field(
        min_length=1,
        max_length=512,
        description="Input reference below /large_tool_results/.",
    )
    path: str = Field(
        default="",
        max_length=512,
        description=("Optional record-list path. Leave empty for {total, items}."),
    )
    expected_count: int | None = Field(
        default=None,
        ge=0,
        le=1000000,
        description=("Expected record count. Leave empty to use wrapper total."),
    )
    unique_by: list[str] = Field(
        default_factory=list,
        min_length=1,
        max_length=3,
        description="One to three fields that uniquely identify a record.",
    )
    fields: list[str] = Field(
        default_factory=list,
        max_length=32,
        description="Required fields that must be present on every record.",
    )


class _InspectSavedOutputArgs(BaseModel):
    """Args schema for bounded inspection of a saved tool result."""

    ref: str = Field(
        min_length=1,
        max_length=512,
        description="Input reference below /large_tool_results/.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=1000000,
        description="Zero-based line offset.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Number of lines to return, from 1 to 100.",
    )


class _RunSkillTransformArgs(BaseModel):
    """Args schema for running a declared Skill Transform."""

    skill: str = Field(description="Loaded Skill package name.")
    transform: str = Field(description="Transform name declared in sourcelens.json.")
    stdin_ref: str = Field(
        min_length=1,
        max_length=512,
        description="JSON input reference below /large_tool_results/.",
    )
    args: list[str] = Field(
        default_factory=list,
        max_length=64,
        description="Optional bounded arguments for the declared transform.",
    )


class _CallSkillApiArgs(BaseModel):
    """Args schema for an HTTP request configured by a loaded Skill."""

    skill: str = Field(description="Loaded Skill package name.")
    base_url_env: str = Field(
        description="Environment variable containing the API base URL."
    )
    method: str = Field(default="GET", description="HTTP method.")
    path: str = Field(default="", description="Path relative to the base URL.")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description=("Headers with {{ENV_NAME}} or {{session.name}} references."),
    )
    query: dict[str, object] = Field(
        default_factory=dict,
        description="Query parameters with environment/session references.",
    )
    json_body: object | None = Field(
        default=None,
        description="JSON body with environment/session references.",
    )
    capture: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Session value names mapped to dotted JSON response paths. "
            "Captured values are stored for later calls and redacted."
        ),
    )


def _build_save_deliverable_tool(command, resources, config, emit_event):
    """Build the save_deliverable tool bound to this run.

    Uploads a file the agent produced to the control plane at produce
    time (same token/URL family as the AI gateway), so it reaches the
    user as a download and survives the run's scratch cleanup. The
    control plane never reads the node's volume — the node pushes it.
    """

    def emit(name, detail=None):
        if emit_event is not None:
            emit_event(name, detail or {})

    @tool("save_deliverable")
    def save_deliverable(path: str) -> str:
        """Deliver a file you produced to the user for download.

        Write the finished artifact first (e.g. with write_file), then
        call this with its path. Only files passed here reach the user;
        the private scratch directory is discarded when the run ends, so
        it is the source for delivery, not a delivery target itself. Relative
        paths resolve inside scratch. Absolute paths are virtual filesystem
        paths and are also resolved inside scratch; host filesystem paths are
        never accepted.
        """

        emit("tool.save_deliverable.start", {"path": path, "summary": path})
        root = resources.root.resolve()
        requested_path = Path(path)
        resolved = None
        allowed = False

        if requested_path.is_absolute():
            # Deep Agents' virtual filesystem presents scratch as '/'.
            # Map an absolute virtual path such as '/tmp/report.html' into
            # the run's scratch root instead of reading the host filesystem.
            if ".." not in requested_path.parts:
                virtual = (root / str(requested_path).lstrip("/")).resolve()
                try:
                    virtual.relative_to(root)
                    resolved = virtual
                    allowed = True
                except ValueError:
                    pass
        else:
            resolved = (root / requested_path).resolve()
            try:
                resolved.relative_to(root)
                allowed = True
            except ValueError:
                pass

        if not allowed:
            emit("tool.save_deliverable.denied", {"path": path})
            return _json({"ok": False, "error": "PATH_NOT_ALLOWED"})
        if resolved is None or not resolved.is_file():
            emit(
                "tool.save_deliverable.done",
                {"path": path, "summary": "not found"},
            )
            return _json(
                {
                    "ok": False,
                    "error": "FILE_NOT_FOUND",
                    "message": (
                        "No file at that path. Write it first, then call "
                        "save_deliverable(path)."
                    ),
                }
            )
        byte_size = resolved.stat().st_size
        if byte_size > config.deliverable_max_bytes:
            emit(
                "tool.save_deliverable.failed",
                {"path": path, "error": "TOO_LARGE"},
            )
            return _json(
                {
                    "ok": False,
                    "error": "FILE_TOO_LARGE",
                    "message": (
                        f"File is {byte_size} bytes, over the "
                        f"{config.deliverable_max_bytes}-byte limit."
                    ),
                }
            )
        data = resolved.read_bytes()
        filename = resolved.name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            with httpx.Client(
                timeout=config.request_timeout_s,
                verify=create_config_ssl_context(config),
            ) as client:
                response = client.post(
                    config.deliverable_upload_url,
                    headers={"Authorization": f"Bearer {config.token}"},
                    data={
                        "run_uuid": command.get("run_uuid") or "",
                        "filename": filename,
                        "content_type": content_type,
                    },
                    files={"file": (filename, data, content_type)},
                )
                response.raise_for_status()
        except Exception as exc:
            emit(
                "tool.save_deliverable.failed",
                {"path": path, "error": str(exc)},
            )
            return _json({"ok": False, "error": "DELIVERY_FAILED", "message": str(exc)})
        emit(
            "tool.save_deliverable.done",
            {
                "filename": filename,
                "byte_size": len(data),
                "summary": f"{filename} ({len(data)} bytes)",
            },
        )
        emit(
            "workflow.artifact.created",
            {
                "event_type": "artifact.created",
                "visibility": "user",
                "payload": {
                    "filename": filename,
                    "byte_size": len(data),
                    "content_type": content_type,
                },
            },
        )
        return _json(
            {
                "ok": True,
                "filename": filename,
                "byte_size": len(data),
                "message": (f"Delivered '{filename}' to the user for download."),
            }
        )

    return save_deliverable


def _build_append_file_tool(resources, emit_event):
    """Build an idempotent, bounded append tool for long deliverables."""

    root = resources.root.resolve()
    manifest_path = root / _APPEND_FILE_MANIFEST
    state_lock = threading.Lock()

    def emit(name, detail=None):
        if emit_event is not None:
            emit_event(name, detail or {})

    def load_manifest():
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        chunks = data.get("chunks") if isinstance(data, dict) else None
        return chunks if isinstance(chunks, dict) else None

    def save_manifest(chunks):
        temporary = manifest_path.with_name(
            f"{manifest_path.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps({"chunks": chunks}, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, manifest_path)

    @tool("append_file")
    def append_file(path: str, chunk_id: str, content: str) -> str:
        """Append one bounded, idempotent text chunk to a scratch file.

        Use this for every chunk of a long new document. Keep content at or
        below 24 KiB. Use the same path and a unique ordered chunk_id such as
        translation-001, translation-002. Retrying a chunk with the same id
        and content is safe; changing content for an existing id is rejected.
        Call save_deliverable only after all chunks have been appended.
        """

        relative = str(path or "").lstrip("/")
        resolved = (root / relative).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return _json({"ok": False, "error": "PATH_NOT_ALLOWED"})
        if (
            not relative
            or resolved == manifest_path
            or not _APPEND_FILE_CHUNK_ID_PATTERN.fullmatch(str(chunk_id))
        ):
            return _json({"ok": False, "error": "INVALID_CHUNK"})

        content_bytes = len(content.encode("utf-8"))
        if content_bytes > _APPEND_FILE_CHUNK_MAX_BYTES:
            return _json(
                {
                    "ok": False,
                    "error": "CHUNK_TOO_LARGE",
                    "max_bytes": _APPEND_FILE_CHUNK_MAX_BYTES,
                }
            )

        relative_path = resolved.relative_to(root).as_posix()
        manifest_key = f"{relative_path}:{chunk_id}"
        content_data = content.encode("utf-8")
        content_sha256 = hashlib.sha256(content_data).hexdigest()
        emit(
            "tool.append_file.start",
            {
                "path": relative_path,
                "chunk_id": chunk_id,
                "byte_size": content_bytes,
            },
        )
        with state_lock:
            chunks = load_manifest()
            if chunks is None:
                return _json({"ok": False, "error": "CHUNK_STATE_INVALID"})
            existing = chunks.get(manifest_key)
            if existing is not None:
                if existing == content_sha256:
                    emit(
                        "tool.append_file.done",
                        {
                            "path": relative_path,
                            "chunk_id": chunk_id,
                            "duplicate": True,
                        },
                    )
                    return _json({"ok": True, "duplicate": True})
                if not isinstance(existing, dict):
                    return _json({"ok": False, "error": "CHUNK_CONFLICT"})
                if existing.get("sha256") != content_sha256:
                    return _json({"ok": False, "error": "CHUNK_CONFLICT"})
                if existing.get("state") == "completed":
                    return _json({"ok": True, "duplicate": True})
                if existing.get("state") != "pending":
                    return _json({"ok": False, "error": "CHUNK_STATE_INVALID"})
                offset = existing.get("offset")
                byte_size = existing.get("byte_size")
                if not isinstance(offset, int) or byte_size != content_bytes:
                    return _json({"ok": False, "error": "CHUNK_STATE_INVALID"})
                try:
                    if resolved.exists():
                        with resolved.open("rb") as output:
                            output.seek(offset)
                            written = output.read(content_bytes)
                    else:
                        written = b""
                    if written == content_data:
                        existing["state"] = "completed"
                        save_manifest(chunks)
                        return _json({"ok": True, "duplicate": True})
                    current_size = resolved.stat().st_size if resolved.exists() else 0
                    if current_size != offset:
                        return _json(
                            {
                                "ok": False,
                                "error": "CHUNK_RECOVERY_REQUIRED",
                            }
                        )
                except OSError:
                    return _json({"ok": False, "error": "APPEND_FAILED"})
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                if existing is None:
                    chunks[manifest_key] = {
                        "byte_size": content_bytes,
                        "offset": (resolved.stat().st_size if resolved.exists() else 0),
                        "sha256": content_sha256,
                        "state": "pending",
                    }
                    save_manifest(chunks)
                with resolved.open("ab") as output:
                    output.write(content_data)
                chunks[manifest_key]["state"] = "completed"
                save_manifest(chunks)
            except OSError:
                return _json({"ok": False, "error": "APPEND_FAILED"})
        emit(
            "tool.append_file.done",
            {
                "path": relative_path,
                "chunk_id": chunk_id,
                "duplicate": False,
            },
        )
        return _json({"ok": True, "duplicate": False})

    append_file.metadata = {
        "operation": "write",
        "idempotent": True,
    }
    return append_file


def _build_skill_api_tool(resources, timeout_s=60, emit_event=None):
    """Build a secret-safe HTTP client for loaded manual Skills."""

    session_values = {}

    def emit(name, detail=None):
        if emit_event is not None:
            emit_event(name, detail or {})

    @tool("call_skill_api", args_schema=_CallSkillApiArgs)
    def call_skill_api(
        skill: str,
        base_url_env: str,
        method: str = "GET",
        path: str = "",
        headers: dict[str, str] | None = None,
        query: dict[str, object] | None = None,
        json_body: object | None = None,
        capture: dict[str, str] | None = None,
    ) -> str:
        """Call an HTTP API using one loaded Skill's bound environment.

        Use this when a loaded Skill describes an HTTP integration. Supply
        only environment-variable references, never resolved secrets.
        References use ``{{ENV_NAME}}``. Values captured from an earlier
        response use ``{{session.name}}`` and exist only for this run.
        ``base_url_env`` must name a bound environment variable. ``path``
        must be relative to that base URL. Use ``capture`` to retain tokens
        without exposing them, for example ``{"token": "data.access"}``.
        """

        started = time.monotonic()
        skill_dir = _resolve_skill_dir(resources.root / "skills", skill)
        if skill_dir is None:
            emit("tool.call_skill_api.denied", {"skill": skill})
            return _json({"ok": False, "error": "SKILL_NOT_LOADED"})
        environment = resources.skill_environments.get(skill_dir.name, {})
        base_url = environment.get(str(base_url_env or "").strip())
        if not base_url:
            return _json({"ok": False, "error": "ENVIRONMENT_NOT_BOUND"})
        parsed_base = urlparse(base_url)
        if (
            parsed_base.scheme not in {"http", "https"}
            or not parsed_base.netloc
            or parsed_base.username
            or parsed_base.password
        ):
            return _json({"ok": False, "error": "INVALID_BASE_URL"})
        parsed_path = urlparse(str(path or ""))
        if (
            parsed_path.scheme
            or parsed_path.netloc
            or parsed_path.params
            or parsed_path.query
            or parsed_path.fragment
        ):
            return _json({"ok": False, "error": "PATH_MUST_BE_RELATIVE"})
        request_method = str(method or "GET").upper()
        if request_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return _json({"ok": False, "error": "METHOD_NOT_ALLOWED"})
        request_path = _normalized_skill_api_path(parsed_path.path)
        if request_path is None:
            return _json({"ok": False, "error": "PATH_MUST_BE_RELATIVE"})
        if not _skill_api_request_allowed(
            resources.skill_api_policies.get(skill_dir.name, {}),
            base_url_env,
            request_method,
            request_path,
        ):
            return _json({"ok": False, "error": "API_ROUTE_NOT_ALLOWED"})

        skill_session = session_values.setdefault(skill_dir.name, {})
        try:
            resolved_headers = _resolve_skill_api_references(
                headers or {}, environment, skill_session
            )
            resolved_query = _resolve_skill_api_references(
                query or {}, environment, skill_session
            )
            resolved_body = _resolve_skill_api_references(
                json_body, environment, skill_session
            )
        except ValueError as exc:
            return _json({"ok": False, "error": str(exc)})

        url = urljoin(base_url.rstrip("/") + "/", request_path.lstrip("/"))
        emit(
            "tool.call_skill_api.start",
            {
                "skill": skill_dir.name,
                "method": request_method,
                "path": parsed_path.path,
                "summary": f"{request_method} {parsed_path.path or '/'}",
            },
        )
        try:
            with httpx.Client(
                timeout=timeout_s,
                follow_redirects=False,
            ) as client:
                with client.stream(
                    request_method,
                    url,
                    headers=resolved_headers,
                    params=resolved_query,
                    json=(resolved_body if json_body is not None else None),
                ) as response:
                    response_status_code = response.status_code
                    response_is_success = response.is_success
                    payload = _skill_api_response_payload(response)
            captured_names = []
            for name, response_path in (capture or {}).items():
                normalized_name = str(name or "").strip()
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", normalized_name):
                    return _json({"ok": False, "error": "INVALID_CAPTURE_NAME"})
                captured_value = _json_path_value(payload, response_path)
                if captured_value in (None, ""):
                    return _json(
                        {
                            "ok": False,
                            "error": "CAPTURE_VALUE_MISSING",
                            "capture": normalized_name,
                        }
                    )
                skill_session[normalized_name] = captured_value
                captured_names.append(normalized_name)
            output = {
                "ok": response_is_success,
                "status_code": response_status_code,
                "response": _redact_skill_api_payload(
                    payload,
                    [*environment.values(), *skill_session.values()],
                ),
            }
            if captured_names:
                output["captured"] = captured_names
        except httpx.HTTPError:
            output = {"ok": False, "error": "HTTP_REQUEST_FAILED"}
        emit(
            "tool.call_skill_api.done",
            {
                "skill": skill_dir.name,
                "method": request_method,
                "path": parsed_path.path,
                "ok": output.get("ok", False),
                "status_code": output.get("status_code"),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "summary": (
                    f"{request_method} {parsed_path.path or '/'} · "
                    f"{output.get('status_code') or output.get('error')}"
                ),
            },
        )
        return _json(output)

    return call_skill_api


def build_general_chat_tools(
    command,
    resources,
    config=None,
    emit_event=None,
    runtime_evidence=None,
    on_runtime_evidence=None,
    http_client=None,
):
    """Build tools for General Chat without workspace retrieval tools."""

    if runtime_evidence is None:
        runtime_evidence = {}

    settings = command.get("settings") or {}
    tool_policy = settings.get("tool_policy") or {}
    timeout_s = min(
        _positive_int(
            tool_policy.get("skill_script_timeout_s"),
            default=60,
        ),
        300,
    )
    stdout_preview_chars = tool_policy.get("skill_script_stdout_preview_chars")
    if stdout_preview_chars is None:
        stdout_preview_chars = tool_policy.get("skill_script_stdout_limit")
    stdout_limit = min(
        _positive_int(stdout_preview_chars, 4000),
        100000,
    )
    stderr_preview_chars = tool_policy.get("skill_script_stderr_preview_chars")
    if stderr_preview_chars is None:
        stderr_preview_chars = tool_policy.get("skill_script_stderr_limit")
    stderr_limit = min(
        _positive_int(stderr_preview_chars, 4000),
        50000,
    )
    script_calls = {"count": 0}
    script_output_bytes = {"count": 0}
    script_max_output_bytes_per_call = min(
        _positive_int(
            tool_policy.get("skill_script_max_output_bytes_per_call"),
            default=_SKILL_SCRIPT_MAX_OUTPUT_BYTES_PER_CALL,
        ),
        _SKILL_SCRIPT_MAX_OUTPUT_BYTES_PER_CALL,
    )
    script_max_output_bytes_per_run = min(
        _positive_int(
            tool_policy.get("skill_script_max_output_bytes_per_run"),
            default=_SKILL_SCRIPT_DEFAULT_MAX_OUTPUT_BYTES_PER_RUN,
        ),
        _SKILL_SCRIPT_MAX_OUTPUT_BYTES_PER_RUN,
    )
    transform_stdout_limit = min(
        _positive_int(tool_policy.get("skill_transform_stdout_limit"), 8000),
        50000,
    )
    structured_analysis_output_limit = min(
        _positive_int(
            tool_policy.get("structured_analysis_output_limit"),
            default=20000,
        ),
        100000,
    )
    structured_analysis_calls = {"count": 0}
    structured_validation_calls = {"count": 0}
    saved_output_inspection_calls = {"count": 0}
    transform_calls = {"count": 0}
    skills_root = resources.root / "skills"

    def emit(name, detail=None):
        if emit_event is not None:
            emit_event(name, detail or {})

    @tool(
        "analyze_structured_output",
        args_schema=_AnalyzeStructuredOutputArgs,
    )
    def analyze_structured_output(
        ref: str,
        operation: str,
        path: str = "",
        field: str = "",
        group_by: list[str] | None = None,
        fields: list[str] | None = None,
        expected_count: int | None = None,
        unique_by: list[str] | None = None,
        offset: int = 0,
        limit: int = 100,
        descending: bool = False,
    ) -> str:
        """Analyze a saved JSON result with fixed, bounded operations.

        Use this instead of repeatedly reading or grepping a large JSON
        result. ``ref`` must be a file returned as a tool ``stdout_ref``
        below ``/large_tool_results/``. No Python, SQL, shell expression, or
        arbitrary code is accepted. Results are bounded and large analysis
        output is preserved by reference.
        """

        started = time.monotonic()
        invocation_id = uuid.uuid4().hex
        normalized_operation = str(operation or "").strip().lower()
        is_validation = normalized_operation == "validate_records"
        call_counter = (
            structured_validation_calls if is_validation else structured_analysis_calls
        )
        call_counter["count"] += 1
        call_count = call_counter["count"]

        request_detail = {
            "invocation_id": invocation_id,
            "input_ref": str(ref or "")[:512],
            "operation": normalized_operation,
            "path": str(path or "")[:512],
            "field": str(field or "")[:512],
            "group_by": [str(item)[:512] for item in (group_by or [])],
            "fields": [str(item)[:512] for item in (fields or [])],
            "expected_count": expected_count,
            "unique_by": [str(item)[:512] for item in (unique_by or [])],
            "offset": offset,
            "limit": limit,
            "descending": bool(descending),
            "call_count": call_count,
            "summary": f"{normalized_operation} · {_basename(ref)}",
        }
        emit("tool.analyze_structured_output.start", request_detail)

        input_path = _resolve_large_tool_result_ref(resources.root, ref)
        if input_path is None:
            return _structured_analysis_failure(
                emit,
                started,
                invocation_id,
                normalized_operation,
                ref,
                "INPUT_REF_NOT_ALLOWED",
            )
        try:
            raw = input_path.read_bytes()
        except OSError:
            return _structured_analysis_failure(
                emit,
                started,
                invocation_id,
                normalized_operation,
                ref,
                "INPUT_READ_FAILED",
            )
        if len(raw) > _STRUCTURED_INPUT_MAX_BYTES:
            return _structured_analysis_failure(
                emit,
                started,
                invocation_id,
                normalized_operation,
                ref,
                "INPUT_TOO_LARGE",
                raw=raw,
            )
        try:
            payload = json.loads(raw, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _structured_analysis_failure(
                emit,
                started,
                invocation_id,
                normalized_operation,
                ref,
                "INVALID_JSON",
                raw=raw,
            )
        try:
            result = _apply_structured_operation(
                payload,
                normalized_operation,
                path=path,
                field=field,
                group_by=group_by or [],
                fields=fields or [],
                expected_count=expected_count,
                unique_by=unique_by or [],
                offset=offset,
                limit=limit,
                descending=descending,
            )
        except ValueError as exc:
            return _structured_analysis_failure(
                emit,
                started,
                invocation_id,
                normalized_operation,
                ref,
                str(exc),
                raw=raw,
            )

        if normalized_operation == "validate_records":
            runtime_evidence["record_validation"] = result
            if on_runtime_evidence is not None:
                on_runtime_evidence(runtime_evidence)

        duration_ms = int((time.monotonic() - started) * 1000)
        output = _persist_json_analysis_output(
            resources.root,
            invocation_id,
            result,
            structured_analysis_output_limit,
        )
        result_count = len(result) if isinstance(result, (dict, list)) else 1
        input_sha256 = hashlib.sha256(raw).hexdigest()
        detail = {
            "invocation_id": invocation_id,
            "input_ref": str(ref),
            "input_bytes": len(raw),
            "input_sha256": input_sha256,
            "operation": normalized_operation,
            "duration_ms": duration_ms,
            "result_count": result_count,
            "output_bytes": output["output_bytes"],
            "output_sha256": output["output_sha256"],
            "output_truncated": output["output_truncated"],
            "output_ref": output["output_ref"],
            "call_count": call_count,
            "summary": (
                f"{normalized_operation} · {result_count} result"
                f"{'s' if result_count != 1 else ''} · {duration_ms}ms"
            ),
        }
        emit("tool.analyze_structured_output.done", detail)
        return _json(
            {
                "ok": True,
                "invocation_id": invocation_id,
                "operation": normalized_operation,
                "input_ref": str(ref),
                "input_bytes": len(raw),
                "input_sha256": input_sha256,
                "duration_ms": duration_ms,
                "result_count": result_count,
                **output,
            }
        )

    @tool("validate_records", args_schema=_ValidateRecordsArgs)
    def validate_records(
        ref: str,
        path: str = "",
        expected_count: int | None = None,
        unique_by: list[str] | None = None,
        fields: list[str] | None = None,
    ) -> str:
        """Validate a complete bulk JSON result before other analysis.

        Call this immediately after a bulk Artifact returns a JSON
        ``stdout_ref``. It checks total count, duplicate or missing unique
        keys, and required fields in one bounded pass. For a common
        ``{total, items}`` result, leave ``path`` empty to validate
        ``items``. Pass ``expected_count`` only when ``items`` is known to
        contain the complete result rather than one page.
        """

        return analyze_structured_output.invoke(
            {
                "ref": ref,
                "operation": "validate_records",
                "path": path,
                "expected_count": expected_count,
                "unique_by": unique_by or [],
                "fields": fields or [],
            }
        )

    @tool("inspect_saved_output", args_schema=_InspectSavedOutputArgs)
    def inspect_saved_output(
        ref: str,
        offset: int = 0,
        limit: int = 50,
    ) -> str:
        """Inspect a saved JSON, CSV, or text result through a small window.

        Use this for non-JSON ``stdout_ref`` values instead of ``read_file``
        or ``grep``. The tool reports a typed synopsis and returns at most
        100 lines with long lines capped. For JSON aggregation, prefer
        ``analyze_structured_output``.
        """

        started = time.monotonic()
        invocation_id = uuid.uuid4().hex
        saved_output_inspection_calls["count"] += 1
        call_count = saved_output_inspection_calls["count"]

        emit(
            "tool.inspect_saved_output.start",
            {
                "invocation_id": invocation_id,
                "input_ref": str(ref or "")[:512],
                "offset": offset,
                "limit": limit,
                "call_count": call_count,
                "summary": f"inspect · {_basename(ref)}",
            },
        )
        input_path = _resolve_large_tool_result_ref(resources.root, ref)
        if input_path is None:
            emit(
                "tool.inspect_saved_output.failed",
                {
                    "invocation_id": invocation_id,
                    "input_ref": str(ref or "")[:512],
                    "error": "INPUT_REF_NOT_ALLOWED",
                    "summary": "inspect · input denied",
                },
            )
            return _json({"ok": False, "error": "INPUT_REF_NOT_ALLOWED"})
        try:
            raw = input_path.read_bytes()
        except OSError:
            emit(
                "tool.inspect_saved_output.failed",
                {
                    "invocation_id": invocation_id,
                    "input_ref": str(ref),
                    "error": "INPUT_READ_FAILED",
                    "summary": "inspect · read failed",
                },
            )
            return _json({"ok": False, "error": "INPUT_READ_FAILED"})
        if len(raw) > _STRUCTURED_INPUT_MAX_BYTES:
            emit(
                "tool.inspect_saved_output.failed",
                {
                    "invocation_id": invocation_id,
                    "input_ref": str(ref),
                    "input_bytes": len(raw),
                    "error": "INPUT_TOO_LARGE",
                    "summary": "inspect · input too large",
                },
            )
            return _json({"ok": False, "error": "INPUT_TOO_LARGE"})

        text = _decode_output(raw)
        output_format, synopsis = _saved_output_synopsis(raw, text)
        selected = list(
            islice(
                _iter_output_lines(text),
                offset,
                offset + limit + 1,
            )
        )
        has_more = len(selected) > limit
        selected = selected[:limit]
        lines = [
            {
                "number": offset + index + 1,
                "text": _truncate_output(
                    value,
                    _SAVED_OUTPUT_LINE_MAX_CHARS,
                ),
            }
            for index, value in enumerate(selected)
        ]
        duration_ms = int((time.monotonic() - started) * 1000)
        payload = {
            "ok": True,
            "invocation_id": invocation_id,
            "input_ref": str(ref),
            "input_bytes": len(raw),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "format": output_format,
            "synopsis": synopsis,
            "offset": offset,
            "limit": limit,
            "returned_lines": len(lines),
            "has_more": has_more,
            "lines": lines,
            "duration_ms": duration_ms,
        }
        emit(
            "tool.inspect_saved_output.done",
            {
                "invocation_id": invocation_id,
                "input_ref": str(ref),
                "input_bytes": len(raw),
                "format": output_format,
                "offset": offset,
                "returned_lines": len(lines),
                "has_more": payload["has_more"],
                "duration_ms": duration_ms,
                "summary": (
                    f"inspect {output_format} · {len(lines)} lines · "
                    f"{duration_ms}ms"
                ),
            },
        )
        return _json(payload)

    @tool("run_skill_transform", args_schema=_RunSkillTransformArgs)
    def run_skill_transform(
        skill: str,
        transform: str,
        stdin_ref: str,
        args: list[str] | None = None,
    ) -> str:
        """Run a predeclared JSON Transform from a loaded Skill.

        The Transform name and Python entrypoint must be declared in the
        uploaded Skill's ``sourcelens.json``. Input is read only from a saved
        ``/large_tool_results/`` reference. The model cannot provide code or
        an executable path. Only environment variables explicitly listed by
        the Transform declaration are injected.
        """

        started = time.monotonic()
        invocation_id = uuid.uuid4().hex
        args = [str(item) for item in (args or [])]
        transform_name = str(transform or "").strip()
        skill_dir = _resolve_skill_dir(skills_root, skill)
        if skill_dir is None:
            emit(
                "tool.run_skill_transform.denied",
                {
                    "skill": str(skill or "")[:180],
                    "transform": transform_name[:64],
                    "error": "SKILL_NOT_LOADED",
                    "invocation_id": invocation_id,
                },
            )
            return _json({"ok": False, "error": "SKILL_NOT_LOADED"})
        definition, script_path, error = _resolve_skill_transform(
            skill_dir,
            resources.skill_transforms.get(skill_dir.name, {}),
            transform_name,
        )
        if definition is None or script_path is None:
            emit(
                "tool.run_skill_transform.denied",
                {
                    "skill": skill_dir.name,
                    "transform": transform_name,
                    "error": error,
                    "invocation_id": invocation_id,
                },
            )
            return _json({"ok": False, "error": error})
        if len(args) > 64 or any(len(item) > 512 for item in args):
            emit(
                "tool.run_skill_transform.denied",
                {
                    "skill": skill_dir.name,
                    "transform": transform_name,
                    "error": "TRANSFORM_ARGUMENTS_TOO_LARGE",
                    "invocation_id": invocation_id,
                },
            )
            return _json({"ok": False, "error": "TRANSFORM_ARGUMENTS_TOO_LARGE"})

        transform_calls["count"] += 1
        call_count = transform_calls["count"]

        input_path = _resolve_large_tool_result_ref(
            resources.root,
            stdin_ref,
        )
        if input_path is None:
            emit(
                "tool.run_skill_transform.denied",
                {
                    "skill": skill_dir.name,
                    "transform": transform_name,
                    "entrypoint": definition["entrypoint"],
                    "input_ref": str(stdin_ref or "")[:512],
                    "error": "INPUT_REF_NOT_ALLOWED",
                    "invocation_id": invocation_id,
                },
            )
            return _json({"ok": False, "error": "INPUT_REF_NOT_ALLOWED"})
        try:
            raw = input_path.read_bytes()
        except OSError:
            emit(
                "tool.run_skill_transform.failed",
                {
                    "skill": skill_dir.name,
                    "transform": transform_name,
                    "input_ref": str(stdin_ref),
                    "error": "INPUT_READ_FAILED",
                    "invocation_id": invocation_id,
                },
            )
            return _json({"ok": False, "error": "INPUT_READ_FAILED"})
        if len(raw) > _STRUCTURED_INPUT_MAX_BYTES:
            return _transform_input_failure(
                emit,
                started,
                invocation_id,
                skill_dir.name,
                transform_name,
                definition["entrypoint"],
                stdin_ref,
                raw,
                "INPUT_TOO_LARGE",
            )
        try:
            json.loads(raw, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _transform_input_failure(
                emit,
                started,
                invocation_id,
                skill_dir.name,
                transform_name,
                definition["entrypoint"],
                stdin_ref,
                raw,
                "INVALID_JSON",
            )

        input_sha256 = hashlib.sha256(raw).hexdigest()
        selected_environment = {
            name: value
            for name, value in resources.skill_environments.get(
                skill_dir.name,
                {},
            ).items()
            if name in definition["environment"]
        }
        display_name = f"{skill_dir.name}/{transform_name}"
        emit(
            "tool.run_skill_transform.start",
            {
                "skill": skill_dir.name,
                "transform": transform_name,
                "entrypoint": definition["entrypoint"],
                "args_redacted": _redact_command_args(args),
                "arg_count": len(args),
                "input_ref": str(stdin_ref),
                "input_bytes": len(raw),
                "input_sha256": input_sha256,
                "environment_names": sorted(selected_environment),
                "call_count": call_count,
                "invocation_id": invocation_id,
                "summary": display_name,
            },
        )
        try:
            completed = subprocess.run(
                [sys.executable, str(script_path), *args],
                cwd=str(skill_dir),
                env=_skill_script_environment(selected_environment),
                input=raw,
                capture_output=True,
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout = _persist_large_tool_output(
                resources.root,
                invocation_id,
                "stdout",
                exc.stdout,
                transform_stdout_limit,
            )
            stderr = _persist_large_tool_output(
                resources.root,
                invocation_id,
                "stderr",
                exc.stderr,
                stderr_limit,
            )
            emit(
                "tool.run_skill_transform.timeout",
                {
                    "skill": skill_dir.name,
                    "transform": transform_name,
                    "entrypoint": definition["entrypoint"],
                    "input_ref": str(stdin_ref),
                    "input_bytes": len(raw),
                    "input_sha256": input_sha256,
                    "duration_ms": duration_ms,
                    "timeout_s": timeout_s,
                    "invocation_id": invocation_id,
                    "stdout_bytes": stdout["stdout_bytes"],
                    "stdout_ref": stdout["stdout_ref"],
                    "stdout_truncated": stdout["stdout_truncated"],
                    "stderr_bytes": stderr["stderr_bytes"],
                    "stderr_ref": stderr["stderr_ref"],
                    "stderr_truncated": stderr["stderr_truncated"],
                    "summary": f"{display_name} · timeout",
                },
            )
            return _json(
                {
                    "ok": False,
                    "error": "TRANSFORM_TIMEOUT",
                    "duration_ms": duration_ms,
                    "invocation_id": invocation_id,
                    "input_ref": str(stdin_ref),
                    "input_bytes": len(raw),
                    "input_sha256": input_sha256,
                    "timeout_s": timeout_s,
                    **stdout,
                    **stderr,
                }
            )
        except OSError:
            duration_ms = int((time.monotonic() - started) * 1000)
            emit(
                "tool.run_skill_transform.failed",
                {
                    "skill": skill_dir.name,
                    "transform": transform_name,
                    "entrypoint": definition["entrypoint"],
                    "input_ref": str(stdin_ref),
                    "input_bytes": len(raw),
                    "input_sha256": input_sha256,
                    "duration_ms": duration_ms,
                    "error": "TRANSFORM_EXECUTION_FAILED",
                    "invocation_id": invocation_id,
                    "summary": f"{display_name} · failed",
                },
            )
            return _json({"ok": False, "error": "TRANSFORM_EXECUTION_FAILED"})

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = _persist_large_tool_output(
            resources.root,
            invocation_id,
            "stdout",
            completed.stdout,
            transform_stdout_limit,
        )
        stderr = _persist_large_tool_output(
            resources.root,
            invocation_id,
            "stderr",
            completed.stderr,
            stderr_limit,
        )
        detail = {
            "skill": skill_dir.name,
            "transform": transform_name,
            "entrypoint": definition["entrypoint"],
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "duration_ms": duration_ms,
            "invocation_id": invocation_id,
            "input_ref": str(stdin_ref),
            "input_bytes": len(raw),
            "input_sha256": input_sha256,
            "stdout_bytes": stdout["stdout_bytes"],
            "stdout_sha256": stdout["stdout_sha256"],
            "stdout_ref": stdout["stdout_ref"],
            "stdout_truncated": stdout["stdout_truncated"],
            "stderr_bytes": stderr["stderr_bytes"],
            "stderr_sha256": stderr["stderr_sha256"],
            "stderr_ref": stderr["stderr_ref"],
            "stderr_truncated": stderr["stderr_truncated"],
            "call_count": call_count,
            "summary": f"{display_name} · rc={completed.returncode}",
        }
        emit("tool.run_skill_transform.done", detail)
        payload = {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "duration_ms": duration_ms,
            "skill": skill_dir.name,
            "transform": transform_name,
            "entrypoint": definition["entrypoint"],
            "invocation_id": invocation_id,
            "input_ref": str(stdin_ref),
            "input_bytes": len(raw),
            "input_sha256": input_sha256,
            **stdout,
            **stderr,
        }
        if payload["stdout_truncated"]:
            if payload["stdout_format"] == "json":
                payload["instruction"] = (
                    "Use analyze_structured_output on stdout_ref. Do not "
                    "rerun the Transform to recover its full output."
                )
            else:
                payload["instruction"] = (
                    "Read stdout_ref with inspect_saved_output for a "
                    "bounded view. Do not rerun the Transform to recover "
                    "its full output."
                )
        return _json(payload)

    @tool("run_skill_script", args_schema=_RunSkillScriptArgs)
    def run_skill_script(
        skill: str,
        script: str,
        args: list[str] | None = None,
        stdin: str = "",
    ) -> str:
        """Run a bundled executable file inside a loaded Skill.

        Use this only when the Skill instructions tell you to run a bundled
        script or binary. The path must be relative to that Skill's root
        directory, for example "scripts/report.sh", "scripts/tool.py" or
        "bin/linux-amd64/glab". Prefer one call that batches the work over
        many per-record calls. Per-run call and raw-output byte budgets
        apply. Analyze saved stdout_ref results instead of rerunning a
        script whose output was externalized.
        """

        started = time.monotonic()
        invocation_id = uuid.uuid4().hex
        args = [str(item) for item in (args or [])]
        skill_dir = _resolve_skill_dir(skills_root, skill)
        if skill_dir is None:
            emit("tool.run_skill_script.denied", {"skill": skill})
            return _json({"ok": False, "error": "SKILL_NOT_LOADED"})
        script_path = _resolve_skill_script(skill_dir, script)
        if script_path is None:
            emit(
                "tool.run_skill_script.denied",
                {"skill": skill, "script": script},
            )
            return _json({"ok": False, "error": "SCRIPT_NOT_ALLOWED"})
        command_args = _skill_script_command(script_path)
        if command_args is None:
            emit(
                "tool.run_skill_script.denied",
                {"skill": skill, "script": str(script_path)},
            )
            return _json({"ok": False, "error": "SCRIPT_NOT_EXECUTABLE"})
        script_calls["count"] += 1
        remaining_run_output_bytes = (
            script_max_output_bytes_per_run - script_output_bytes["count"]
        )
        if remaining_run_output_bytes <= 0:
            detail = {
                "skill": skill,
                "script": script,
                "quota_scope": "per_run",
                "output_bytes_this_call": 0,
                "output_bytes_this_run": script_output_bytes["count"],
                "max_output_bytes_per_call": (script_max_output_bytes_per_call),
                "max_output_bytes_per_run": script_max_output_bytes_per_run,
            }
            emit("tool.run_skill_script.output_quota_exceeded", detail)
            return _json(
                {
                    "ok": False,
                    "error": "SKILL_SCRIPT_OUTPUT_QUOTA_EXCEEDED",
                    "tool": "run_skill_script",
                    **detail,
                    "instruction": (
                        "The raw script output byte quota is exhausted. "
                        "Stop running scripts and analyze the stdout_ref "
                        "results already saved."
                    ),
                }
            )
        display_name = f"{skill}/{script}"
        emit(
            "tool.run_skill_script.start",
            {
                "skill": skill,
                "script": script,
                "arg_count": len(args),
                "args_redacted": _redact_command_args(args),
                "summary": display_name,
            },
        )
        output_quota_scope = (
            "per_call"
            if script_max_output_bytes_per_call <= remaining_run_output_bytes
            else "per_run"
        )
        output_byte_limit = min(
            script_max_output_bytes_per_call,
            remaining_run_output_bytes,
        )
        try:
            completed = _run_bounded_skill_script(
                [*command_args, *args],
                cwd=str(skill_dir),
                env=_skill_script_environment(
                    resources.skill_environments.get(skill_dir.name, {})
                ),
                stdin=stdin.encode("utf-8"),
                timeout_s=timeout_s,
                max_output_bytes=output_byte_limit,
                output_root=resources.root,
                invocation_id=invocation_id,
            )
        except OSError as exc:
            emit(
                "tool.run_skill_script.failed",
                {"skill": skill, "script": script, "error": str(exc)},
            )
            return _json({"ok": False, "error": str(exc)})
        duration_ms = int((time.monotonic() - started) * 1000)
        script_output_bytes["count"] += completed["output_bytes"]
        stdout = _persist_large_tool_output(
            resources.root,
            invocation_id,
            "stdout",
            completed["stdout"],
            stdout_limit,
            persisted_path=completed["stdout_path"],
            force_ref=completed["output_quota_exceeded"],
        )
        stderr = _persist_large_tool_output(
            resources.root,
            invocation_id,
            "stderr",
            completed["stderr"],
            stderr_limit,
            persisted_path=completed["stderr_path"],
            force_ref=completed["output_quota_exceeded"],
        )
        if completed["output_quota_exceeded"]:
            detail = {
                "skill": skill,
                "script": script,
                "quota_scope": output_quota_scope,
                "output_bytes_this_call": completed["output_bytes"],
                "output_bytes_this_run": script_output_bytes["count"],
                "max_output_bytes_per_call": (script_max_output_bytes_per_call),
                "max_output_bytes_per_run": script_max_output_bytes_per_run,
            }
            emit("tool.run_skill_script.output_quota_exceeded", detail)
            return _json(
                {
                    "ok": False,
                    "error": "SKILL_SCRIPT_OUTPUT_QUOTA_EXCEEDED",
                    "tool": "run_skill_script",
                    "duration_ms": duration_ms,
                    **detail,
                    **stdout,
                    **stderr,
                    "instruction": (
                        "The script was stopped after reaching its raw "
                        "output byte quota. Analyze the captured stdout_ref "
                        "or stderr_ref instead of rerunning it."
                    ),
                }
            )
        if completed["timed_out"]:
            emit(
                "tool.run_skill_script.timeout",
                {
                    "skill": skill,
                    "script": script,
                    "timeout_s": timeout_s,
                },
            )
            return _json(
                {
                    "ok": False,
                    "error": "SCRIPT_TIMEOUT",
                    "timeout_s": timeout_s,
                    "output_bytes_this_call": completed["output_bytes"],
                    "output_bytes_this_run": script_output_bytes["count"],
                    **stdout,
                    **stderr,
                }
            )
        payload = {
            "ok": completed["returncode"] == 0,
            "returncode": completed["returncode"],
            "duration_ms": duration_ms,
            "script": str(script_path.relative_to(skill_dir)),
            "output_bytes_this_call": completed["output_bytes"],
            "output_bytes_this_run": script_output_bytes["count"],
            **stdout,
            **stderr,
        }
        if payload["stdout_truncated"]:
            if payload["stdout_format"] == "json":
                payload["instruction"] = (
                    "Use analyze_structured_output on stdout_ref. Do not "
                    "read_file or grep it and do not rerun the Script to "
                    "recover its full output."
                )
            else:
                payload["instruction"] = (
                    "Read stdout_ref with inspect_saved_output for a "
                    "bounded view. Do not read_file or grep it and do not "
                    "rerun the Script to recover its full output."
                )
        emit(
            "tool.run_skill_script.done",
            {
                "skill": skill,
                "script": script,
                "ok": payload["ok"],
                "returncode": completed["returncode"],
                "duration_ms": duration_ms,
                "stdout_bytes": payload["stdout_bytes"],
                "stdout_ref": payload["stdout_ref"],
                "stdout_truncated": payload["stdout_truncated"],
                "call_count": script_calls["count"],
                "output_bytes_this_run": script_output_bytes["count"],
                "summary": (f"{display_name} · rc={completed['returncode']}"),
            },
        )
        return _json(payload)

    tools = [
        _build_skill_api_tool(
            resources,
            timeout_s=min(
                _positive_int(
                    tool_policy.get("skill_http_timeout_s"),
                    default=60,
                ),
                300,
            ),
            emit_event=emit_event,
        ),
        validate_records,
        analyze_structured_output,
        inspect_saved_output,
        run_skill_transform,
        run_skill_script,
    ]
    if config is not None:
        tools.append(
            _build_save_deliverable_tool(command, resources, config, emit_event)
        )
    return tools


def _resolve_large_tool_result_ref(root, value):
    """Resolve one regular non-symlink large-result file by virtual ref."""

    ref = str(value or "").replace("\\", "/")
    prefix = "/large_tool_results/"
    if not ref.startswith(prefix):
        return None
    relative_text = ref[len(prefix) :]
    if (
        not relative_text
        or relative_text.startswith("/")
        or any(part in {"", ".", ".."} for part in relative_text.split("/"))
    ):
        return None
    output_root = Path(root) / "large_tool_results"
    candidate = output_root.joinpath(*relative_text.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(output_root.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError):
        return None
    if _path_contains_symlink(output_root, candidate):
        return None
    try:
        if not stat.S_ISREG(candidate.lstat().st_mode):
            return None
    except OSError:
        return None
    return candidate


def _reject_json_constant(value):
    """Reject non-standard JSON numeric constants."""

    raise ValueError(f"Invalid JSON constant: {value}")


def _structured_analysis_failure(
    emit,
    started,
    invocation_id,
    operation,
    input_ref,
    error,
    raw=None,
):
    """Emit and return a stable structured-analysis failure."""

    duration_ms = int((time.monotonic() - started) * 1000)
    detail = {
        "invocation_id": invocation_id,
        "input_ref": str(input_ref or "")[:512],
        "operation": operation,
        "error": error,
        "duration_ms": duration_ms,
        "summary": f"{operation or 'analysis'} · {error}",
    }
    payload = {
        "ok": False,
        "error": error,
        "invocation_id": invocation_id,
        "input_ref": str(input_ref or "")[:512],
        "operation": operation,
        "duration_ms": duration_ms,
    }
    if raw is not None:
        input_sha256 = hashlib.sha256(raw).hexdigest()
        detail.update({"input_bytes": len(raw), "input_sha256": input_sha256})
        payload.update({"input_bytes": len(raw), "input_sha256": input_sha256})
    emit("tool.analyze_structured_output.failed", detail)
    return _json(payload)


def _structured_path_value(payload, path):
    """Read a required dotted path, allowing an empty path for the root."""

    if not str(path or "").strip():
        return payload
    sentinel = object()
    value = payload
    for part in str(path).split("."):
        if not part:
            raise ValueError("PATH_NOT_FOUND")
        next_value = sentinel
        if isinstance(value, dict):
            next_value = value.get(part, sentinel)
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            if index < len(value):
                next_value = value[index]
        if next_value is sentinel:
            raise ValueError("PATH_NOT_FOUND")
        value = next_value
    return value


def _structured_record_value(record, field):
    """Read a field from one structured record."""

    if not str(field or "").strip():
        return record
    return _structured_path_value(record, field)


def _structured_numeric_values(target, field):
    """Return finite numeric values from a list target."""

    if not isinstance(target, list):
        raise ValueError("OPERATION_INPUT_INVALID")
    values = []
    for item in target:
        try:
            value = _structured_record_value(item, field)
        except ValueError as exc:
            raise ValueError("FIELD_NOT_FOUND") from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("FIELD_NOT_NUMERIC")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("FIELD_NOT_NUMERIC")
        values.append(value)
    if not values:
        raise ValueError("OPERATION_INPUT_EMPTY")
    return values


def _structured_sort_key(value):
    """Return a deterministic key for JSON-compatible sort values."""

    if isinstance(value, bool):
        return "bool", int(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("FIELD_NOT_SORTABLE")
        return "number", value
    if isinstance(value, str):
        return "string", value
    try:
        return "json", json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("FIELD_NOT_SORTABLE") from exc


def _validate_structured_records(
    target,
    *,
    expected_count,
    unique_by,
    required_fields,
):
    """Return bounded completeness statistics for a record collection."""

    if not isinstance(target, list):
        raise ValueError("OPERATION_INPUT_INVALID")
    if len(target) > _STRUCTURED_VALIDATION_MAX_ITEMS:
        raise ValueError("VALIDATION_ITEM_LIMIT_EXCEEDED")
    for names in (unique_by, required_fields):
        if any(not str(item).strip() for item in names) or len(set(names)) != len(
            names
        ):
            raise ValueError("OPERATION_INPUT_INVALID")

    missing_required = {field: 0 for field in required_fields}
    seen_keys = set()
    duplicate_count = 0
    missing_unique_key_count = 0
    for item in target:
        for item_field in required_fields:
            try:
                value = _structured_record_value(item, item_field)
            except ValueError:
                value = None
            if value is None or (isinstance(value, str) and not value.strip()):
                missing_required[item_field] += 1

        if not unique_by:
            continue
        key_values = []
        for item_field in unique_by:
            try:
                value = _structured_record_value(item, item_field)
            except ValueError:
                value = None
            if value is None or (isinstance(value, str) and not value.strip()):
                key_values = []
                break
            key_values.append(value)
        if not key_values:
            missing_unique_key_count += 1
            continue
        key = json.dumps(
            key_values,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        if key in seen_keys:
            duplicate_count += 1
        else:
            seen_keys.add(key)

    total_count = len(target)
    count_matches = None if expected_count is None else total_count == expected_count
    valid = (
        count_matches is not False
        and duplicate_count == 0
        and missing_unique_key_count == 0
        and not any(missing_required.values())
    )
    return {
        "valid": valid,
        "total_count": total_count,
        "expected_count": expected_count,
        "count_matches": count_matches,
        "unique_by": unique_by,
        "duplicate_count": duplicate_count,
        "missing_unique_key_count": missing_unique_key_count,
        "missing_required": missing_required,
    }


def _apply_structured_operation(
    payload,
    operation,
    *,
    path,
    field,
    group_by,
    fields,
    expected_count,
    unique_by,
    offset,
    limit,
    descending,
):
    """Apply one allowlisted JSON operation and return its bounded result."""

    if operation not in _STRUCTURED_OPERATIONS:
        raise ValueError("OPERATION_NOT_ALLOWED")
    target = _structured_path_value(payload, path)
    if operation == "count":
        if not isinstance(target, (dict, list)):
            raise ValueError("OPERATION_INPUT_INVALID")
        return len(target)
    if operation == "validate_records":
        if isinstance(payload, dict):
            if not path and isinstance(payload.get("items"), list):
                target = payload["items"]
        return _validate_structured_records(
            target,
            expected_count=expected_count,
            unique_by=unique_by,
            required_fields=fields,
        )
    if operation == "project":
        if not isinstance(target, list) or not fields:
            raise ValueError("OPERATION_INPUT_INVALID")
        return _project_structured_items(
            target[offset : offset + limit],
            fields,
        )
    if operation == "group_count":
        if (
            not isinstance(target, list)
            or not group_by
            or any(not str(item).strip() for item in group_by)
            or len(set(group_by)) != len(group_by)
        ):
            raise ValueError("OPERATION_INPUT_INVALID")
        groups = {}
        for item in target:
            values = []
            for item_field in group_by:
                try:
                    values.append(_structured_record_value(item, item_field))
                except ValueError as exc:
                    raise ValueError("FIELD_NOT_FOUND") from exc
            try:
                key = json.dumps(
                    values,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("FIELD_NOT_GROUPABLE") from exc
            if key not in groups:
                if len(groups) >= _STRUCTURED_GROUP_MAX_ITEMS:
                    raise ValueError("GROUP_LIMIT_EXCEEDED")
                groups[key] = {
                    "group": dict(zip(group_by, values, strict=True)),
                    "count": 0,
                }
            groups[key]["count"] += 1
        return sorted(
            groups.values(),
            key=lambda item: (
                -item["count"],
                json.dumps(
                    item["group"],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )[:limit]
    if operation in {"sum", "min", "max"}:
        values = _structured_numeric_values(target, field)
        if operation == "sum":
            return sum(values)
        return min(values) if operation == "min" else max(values)
    if operation == "sort":
        if not isinstance(target, list) or not str(field).strip():
            raise ValueError("OPERATION_INPUT_INVALID")
        sortable = []
        missing = []
        for item in target:
            try:
                value = _structured_record_value(item, field)
            except ValueError:
                missing.append(item)
                continue
            sortable.append((_structured_sort_key(value), item))
        sortable.sort(key=lambda item: item[0], reverse=descending)
        ordered = [item for _key, item in sortable] + missing
        return _project_structured_items(
            ordered[offset : offset + limit],
            fields,
        )
    if operation == "paginate":
        if not isinstance(target, list):
            raise ValueError("OPERATION_INPUT_INVALID")
        return _project_structured_items(
            target[offset : offset + limit],
            fields,
        )
    if not isinstance(target, list):
        raise ValueError("OPERATION_INPUT_INVALID")
    items = target[offset:]
    if len(items) <= limit:
        sampled = items
    else:
        sampled = [items[index * len(items) // limit] for index in range(limit)]
    return _project_structured_items(sampled, fields)


def _project_structured_items(items, fields):
    """Return collection items with an optional bounded field projection."""

    if not fields:
        return items
    if any(not str(item).strip() for item in fields) or len(set(fields)) != len(fields):
        raise ValueError("OPERATION_INPUT_INVALID")
    projected = []
    for item in items:
        row = {}
        for item_field in fields:
            try:
                row[item_field] = _structured_record_value(
                    item,
                    item_field,
                )
            except ValueError as exc:
                raise ValueError("FIELD_NOT_FOUND") from exc
        projected.append(row)
    return projected


def _persist_json_analysis_output(root, invocation_id, result, limit):
    """Return output metadata and preserve an oversized JSON result."""

    try:
        raw = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("OUTPUT_NOT_JSON") from exc
    truncated = len(raw) > limit
    output_ref = None
    if truncated:
        output_dir = Path(root) / "large_tool_results"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"analysis_{invocation_id}.json"
        output_path = output_dir / filename
        output_path.write_bytes(raw)
        output_ref = f"/large_tool_results/{filename}"
    return {
        "result": None if truncated else result,
        "output_bytes": len(raw),
        "output_sha256": hashlib.sha256(raw).hexdigest(),
        "output_truncated": truncated,
        "output_ref": output_ref,
        "instruction": (
            "Read output_ref only if another bounded operation is needed."
            if truncated
            else ""
        ),
    }


def _resolve_skill_transform(skill_dir, transforms, name):
    """Resolve one declared Python Transform and its verified entrypoint."""

    if not isinstance(transforms, dict):
        return None, None, "TRANSFORM_NOT_DECLARED"
    definition = transforms.get(str(name or "").strip())
    if not isinstance(definition, dict):
        return None, None, "TRANSFORM_NOT_DECLARED"
    entrypoint = str(definition.get("entrypoint") or "").replace("\\", "/")
    parts = entrypoint.split("/")
    if (
        len(parts) < 2
        or parts[0] != "scripts"
        or any(part in {"", ".", ".."} for part in parts)
        or not entrypoint.lower().endswith(".py")
        or definition.get("input_format") != "json"
        or not isinstance(definition.get("environment"), list)
    ):
        return None, None, "TRANSFORM_DECLARATION_INVALID"
    script_path = skill_dir.joinpath(*parts)
    scripts_root = skill_dir / "scripts"
    try:
        resolved = script_path.resolve(strict=True)
        resolved.relative_to(scripts_root.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError):
        return None, None, "TRANSFORM_ENTRYPOINT_INVALID"
    if _path_contains_symlink(skill_dir, script_path):
        return None, None, "TRANSFORM_ENTRYPOINT_INVALID"
    try:
        if not stat.S_ISREG(script_path.lstat().st_mode):
            return None, None, "TRANSFORM_ENTRYPOINT_INVALID"
    except OSError:
        return None, None, "TRANSFORM_ENTRYPOINT_INVALID"
    expected_hash = str(definition.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        return None, None, "TRANSFORM_DECLARATION_INVALID"
    try:
        actual_hash = hashlib.sha256(script_path.read_bytes()).hexdigest()
    except OSError:
        return None, None, "TRANSFORM_ENTRYPOINT_INVALID"
    if actual_hash != expected_hash:
        return None, None, "TRANSFORM_HASH_MISMATCH"
    return definition, script_path, None


def _transform_input_failure(
    emit,
    started,
    invocation_id,
    skill,
    transform,
    entrypoint,
    input_ref,
    raw,
    error,
):
    """Emit and return a stable Transform input failure."""

    duration_ms = int((time.monotonic() - started) * 1000)
    input_sha256 = hashlib.sha256(raw).hexdigest()
    detail = {
        "skill": skill,
        "transform": transform,
        "entrypoint": entrypoint,
        "input_ref": str(input_ref or "")[:512],
        "input_bytes": len(raw),
        "input_sha256": input_sha256,
        "error": error,
        "duration_ms": duration_ms,
        "invocation_id": invocation_id,
        "summary": f"{skill}/{transform} · {error}",
    }
    emit("tool.run_skill_transform.failed", detail)
    return _json(
        {
            "ok": False,
            "error": error,
            "invocation_id": invocation_id,
            "input_ref": str(input_ref or "")[:512],
            "input_bytes": len(raw),
            "input_sha256": input_sha256,
            "duration_ms": duration_ms,
        }
    )


def _resolve_skill_dir(skills_root, value):
    """Resolve a loaded Skill directory by package name."""

    name = _safe_resource_name(value)
    if not name:
        return None
    candidate = (skills_root / name).resolve()
    try:
        candidate.relative_to(skills_root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_dir() else None


def _resolve_skill_script(skill_dir, script):
    """Resolve an executable file anywhere under a Skill root directory."""

    relative = _safe_relative_path(script)
    if relative is None:
        return None
    candidate = (skill_dir / relative).resolve()
    try:
        candidate.relative_to(skill_dir)
    except ValueError:
        return None
    if not candidate.is_file() or candidate.is_symlink():
        return None
    return candidate


def _path_contains_symlink(root, path):
    """Return whether a path below root contains a symbolic link."""

    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _skill_script_command(script_path):
    """Return the command used to execute a Skill script."""

    try:
        first_line = script_path.read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines()[0]
    except IndexError:
        first_line = ""
    if first_line.startswith("#!"):
        command = shlex.split(first_line[2:].strip())
        return [*command, str(script_path)] if command else None
    suffix = script_path.suffix.lower()
    if suffix == ".py":
        return [sys.executable, str(script_path)]
    if suffix == ".sh":
        return ["bash", str(script_path)]
    if os.access(script_path, os.X_OK):
        return [str(script_path)]
    return None


def _skill_script_environment(environment):
    """Build an isolated subprocess environment for one loaded Skill."""

    allowed = {
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
    process_environment = {
        key: value
        for key, value in os.environ.items()
        if key in allowed or key.startswith("LC_")
    }
    process_environment.update(environment or {})
    return process_environment


def _decode_output(value):
    """Return subprocess output as text."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _truncate_output(value, limit):
    """Decode and truncate subprocess output without changing whitespace."""

    text = _decode_output(value)
    return text[:limit] + "…" if len(text) > limit else text


def _terminate_skill_process(process):
    """Terminate a script process and its process group when available."""

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        process.wait()
        return

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=0.2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        pass
    process.wait()


def _run_bounded_skill_script(
    command,
    *,
    cwd,
    env,
    stdin,
    timeout_s,
    max_output_bytes,
    output_root,
    invocation_id,
):
    """Run a Skill executable with streaming, byte-bounded output capture."""

    output_dir = Path(output_root) / "large_tool_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / f"artifact_{invocation_id}.stdout.txt"
    stderr_path = output_dir / f"artifact_{invocation_id}.stderr.txt"
    process = None
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        captured = {"bytes": 0}
        captured_lock = threading.Lock()
        capture_failed = threading.Event()
        capture_errors = []
        output_quota_exceeded = threading.Event()

        def drain(source, target):
            try:
                while True:
                    chunk = source.read1(64 * 1024)
                    if not chunk:
                        return
                    with captured_lock:
                        remaining = max_output_bytes - captured["bytes"]
                        kept = chunk[: max(0, remaining)]
                        captured["bytes"] += len(kept)
                        if len(kept) < len(chunk):
                            output_quota_exceeded.set()
                    if kept:
                        target.write(kept)
            except OSError as exc:
                with captured_lock:
                    capture_errors.append(exc)
                capture_failed.set()

        def feed_stdin():
            try:
                if stdin:
                    process.stdin.write(stdin)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        with (
            stdout_path.open("wb") as stdout_file,
            stderr_path.open("wb") as stderr_file,
        ):
            stdout_thread = threading.Thread(
                target=drain,
                args=(process.stdout, stdout_file),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=drain,
                args=(process.stderr, stderr_file),
                daemon=True,
            )
            stdin_thread = threading.Thread(target=feed_stdin, daemon=True)
            stdout_thread.start()
            stderr_thread.start()
            stdin_thread.start()

            deadline = time.monotonic() + timeout_s
            termination_reason = None
            while True:
                if capture_failed.is_set():
                    termination_reason = "capture_error"
                    _terminate_skill_process(process)
                    break
                if output_quota_exceeded.is_set():
                    termination_reason = "output_quota"
                    _terminate_skill_process(process)
                    break
                if time.monotonic() >= deadline:
                    termination_reason = "timeout"
                    _terminate_skill_process(process)
                    break
                if (
                    process.poll() is not None
                    and not stdout_thread.is_alive()
                    and not stderr_thread.is_alive()
                    and not stdin_thread.is_alive()
                ):
                    break
                output_quota_exceeded.wait(0.01)

            stdout_thread.join()
            stderr_thread.join()
            stdin_thread.join()
            if capture_errors:
                raise capture_errors[0]
            if termination_reason is None and output_quota_exceeded.is_set():
                termination_reason = "output_quota"

        return {
            "returncode": process.returncode,
            "stdout": stdout_path.read_bytes(),
            "stderr": stderr_path.read_bytes(),
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "output_bytes": captured["bytes"],
            "output_quota_exceeded": (termination_reason == "output_quota"),
            "timed_out": termination_reason == "timeout",
        }
    except OSError:
        if process is not None:
            _terminate_skill_process(process)
        stdout_path.unlink(missing_ok=True)
        stderr_path.unlink(missing_ok=True)
        raise


def _persist_large_tool_output(
    root,
    invocation_id,
    stream,
    value,
    limit,
    *,
    persisted_path=None,
    force_ref=False,
):
    """Return bounded output metadata and preserve a complete large value."""

    text = _decode_output(value)
    raw = value if isinstance(value, bytes) else text.encode("utf-8")
    output_format, synopsis = _saved_output_synopsis(raw, text)
    structured_ref = (
        output_format in ("json", "csv") and len(text) > _STRUCTURED_REF_MIN_CHARS
    )
    truncated = len(text) > limit or (force_ref and bool(raw)) or structured_ref
    output_ref = None
    if truncated:
        output_path = Path(persisted_path) if persisted_path else None
        if output_path is None:
            output_dir = Path(root) / "large_tool_results"
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = f"artifact_{invocation_id}.{stream}.txt"
            output_path = output_dir / filename
        output_path.write_text(text, encoding="utf-8")
        output_ref = f"/large_tool_results/{output_path.name}"
    elif persisted_path:
        Path(persisted_path).unlink(missing_ok=True)
    return {
        stream: _truncate_output(text, limit),
        f"{stream}_bytes": len(raw),
        f"{stream}_chars": len(text),
        f"{stream}_sha256": hashlib.sha256(raw).hexdigest(),
        f"{stream}_truncated": truncated,
        f"{stream}_ref": output_ref,
        f"{stream}_format": output_format,
        f"{stream}_synopsis": synopsis,
    }


def _saved_output_synopsis(raw, text):
    """Return a typed, bounded synopsis for saved subprocess output."""

    base = {
        "line_count": _output_line_count(text),
        "char_count": len(text),
    }
    if b"\x00" in raw:
        return "binary", {**base, "byte_count": len(raw)}

    start = 0
    end = len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    json_candidate = start < end and text[start] in '{["-0123456789tfn'
    if json_candidate and end - start <= 1024 * 1024:
        try:
            payload = json.loads(
                text[start:end],
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError):
            payload = None
        else:
            synopsis = {
                **base,
                "top_level_type": type(payload).__name__,
            }
            if isinstance(payload, dict):
                synopsis["keys"] = [str(key) for key in list(payload)[:32]]
                synopsis["key_count"] = len(payload)
            elif isinstance(payload, list):
                synopsis["item_count"] = len(payload)
            return "json", synopsis
    elif json_candidate:
        pairs = {"{": "}", "[": "]", '"': '"'}
        if pairs.get(text[start]) == text[end - 1]:
            top_level = {
                "{": "dict",
                "[": "list",
                '"': "str",
            }[text[start]]
            return "json", {
                **base,
                "top_level_type": top_level,
                "validated": False,
            }

    csv_synopsis = _csv_output_synopsis(text, base["line_count"])
    if csv_synopsis is not None:
        return "csv", {**base, **csv_synopsis}
    return "text", base


def _csv_output_synopsis(text, line_count):
    """Return CSV shape metadata when text has a consistent table shape."""

    if line_count < 2:
        return None
    sample = text[:8192]
    sample_lines = sample.splitlines()
    if len(sample_lines) < 2:
        return None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        return None

    reader = csv.reader(sample_lines, dialect)
    try:
        first = next(reader)
    except StopIteration:
        return None
    width = len(first)
    if width < 2:
        return None
    sampled_rows = 1
    consistent_rows = 1
    for row in reader:
        sampled_rows += 1
        if len(row) == width:
            consistent_rows += 1
    if consistent_rows / sampled_rows < 0.9:
        return None
    columns = (
        [str(value)[:200] for value in first]
        if has_header
        else [f"column_{index}" for index in range(1, width + 1)]
    )
    return {
        "delimiter": dialect.delimiter,
        "has_header": has_header,
        "columns": columns,
        "column_count": width,
        "row_count": line_count - (1 if has_header else 0),
    }


def _output_line_count(text):
    """Count physical text lines without building a line list."""

    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _iter_output_lines(text):
    """Yield physical lines without materializing the complete output."""

    start = 0
    while start < len(text):
        newline = text.find("\n", start)
        if newline < 0:
            yield text[start:].removesuffix("\r")
            break
        yield text[start:newline].removesuffix("\r")
        start = newline + 1


def _redact_command_args(args):
    """Return bounded CLI arguments with likely secret values removed."""

    sensitive_fragments = {
        "api-key",
        "apikey",
        "authorization",
        "cookie",
        "email",
        "passwd",
        "password",
        "secret",
        "token",
        "username",
    }
    output = []
    redact_next = False
    for item in args[:64]:
        value = str(item)
        if redact_next:
            output.append("[REDACTED]")
            redact_next = False
            continue
        key, separator, _argument_value = value.partition("=")
        normalized = key.lstrip("-").lower().replace("_", "-")
        sensitive = any(fragment in normalized for fragment in sensitive_fragments)
        if value.startswith("-") and sensitive:
            if separator:
                output.append(f"{key}=[REDACTED]")
            else:
                output.append(value[:160])
                redact_next = True
            continue
        output.append(value[:160])
    return output


def _safe_relative_path(value):
    """Return a safe relative Path or None."""

    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith("/"):
        return None
    path = Path(text)
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _safe_resource_name(value):
    """Normalize a Skill resource name."""

    text = str(value or "").strip().lower()
    output = []
    for char in text:
        if char.isalnum() or char in {"-", "_"}:
            output.append(char)
        elif char.isspace():
            output.append("-")
    return "".join(output).strip("-_")


def _resolve_allowed_path(path, target_dirs, policy=None):
    """Resolve a file path and ensure it is under selected dirs."""

    candidate = Path(path).resolve()
    for item in target_dirs:
        root = Path(item.get("path", "")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        scope = target_scope(item)
        resolved = retrieval_path(candidate)
        if is_path_allowed(root, resolved, scope, policy or {}):
            return resolved
    return None


def _resolve_allowed_directory(path, target_dirs, policy=None):
    """Resolve a directory path and ensure it is under selected dirs."""

    candidate = Path(path).resolve()
    for item in target_dirs:
        root = Path(item.get("path", "")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        scope = target_scope(item)
        if candidate.is_dir() and not is_path_excluded(
            root,
            candidate,
            scope,
            policy or {},
        ):
            return candidate
    return None


def _resolve_repo_path(path, target_dirs):
    """Resolve a repo path under selected dirs."""

    candidates = []
    if path:
        requested = Path(path)
        if requested.is_absolute():
            candidates.append(requested.resolve())
        else:
            candidates.extend(
                (Path(item.get("path", "")).resolve() / requested).resolve()
                for item in target_dirs
            )
    candidates.extend(Path(item.get("path", "")).resolve() for item in target_dirs)
    for candidate in candidates:
        for root_item in target_dirs:
            root = Path(root_item.get("path", "")).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.exists() and (candidate / ".git").exists():
                return candidate
    return None


def _discover_git_repositories(path, target_dirs, limit=20):
    """Discover candidate Git repositories under an allowed directory."""

    directory = _resolve_allowed_directory(path, target_dirs)
    if directory is None and not path:
        first = next(iter(target_dirs or []), {})
        directory = _resolve_allowed_directory(first.get("path", ""), target_dirs)
    if directory is None:
        return []

    return [str(repo) for repo in _iter_git_repositories(directory, limit)]


def _matching_repositories(query, target_dirs):
    """Return repositories whose directory names match query tokens."""

    tokens = _query_tokens(query)
    repositories = []
    seen = set()
    for item in target_dirs:
        root = Path(item.get("path", "")).resolve()
        if not root.is_dir():
            continue
        for repo in _iter_git_repositories(root, limit=20):
            if repo in seen:
                continue
            seen.add(repo)
            if repo == root:
                repositories.append(repo)
                continue
            try:
                identity = repo.relative_to(root).as_posix()
            except ValueError:
                identity = repo.name
            name_tokens = set(_name_tokens(identity))
            if any(token in name_tokens for token in tokens):
                repositories.append(repo)
    if repositories:
        return repositories
    return []


def _iter_git_repositories(directory, limit):
    """Yield Git repositories recursively without scanning their contents."""

    repositories = []
    for current, dirnames, _filenames in os.walk(directory):
        current = Path(current)
        if (current / ".git").exists():
            repositories.append(current)
            dirnames[:] = []
            if len(repositories) >= limit:
                break
            continue
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and not (current / name).is_symlink()
        )
    return repositories


def _query_tokens(query):
    """Return meaningful lowercase query tokens."""

    tokens = []
    current = []
    for char in str(query or "").lower():
        if char.isalnum() or char in {"-", "_"}:
            current.append(char)
            continue
        if current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    ignored = {
        "recent",
        "changes",
        "features",
        "bug",
        "fix",
        "fixes",
        "new",
        "project",
    }
    return [token for token in tokens if len(token) >= 3 and token not in ignored]


def _name_tokens(name):
    """Return lowercase tokens from a repository directory name."""

    tokens = []
    current = []
    for char in str(name or "").lower():
        if char.isalnum():
            current.append(char)
            continue
        if current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _list_directory_files(directory, target_dirs, policy=None, limit=50):
    """Return a bounded, recursive list of candidate files in a directory."""

    context = None
    for item in target_dirs:
        root = Path(item.get("path", "")).resolve()
        try:
            directory.relative_to(root)
        except ValueError:
            continue
        context = (root, target_scope(item))
        break
    if context is None:
        return []

    root, scope = context
    policy = policy or {}
    files = []
    for current, subdirs, filenames in os.walk(directory):
        current_path = Path(current)
        subdirs[:] = sorted(
            name
            for name in subdirs
            if not is_path_excluded(
                root,
                current_path / name,
                scope,
                policy,
            )
        )
        for name in sorted(filenames):
            if len(files) >= limit:
                return files
            path = current_path / name
            if not is_path_allowed(root, path, scope, policy):
                continue
            visible_path = citation_path(path)
            if visible_path != path and not is_path_allowed(
                root,
                visible_path,
                scope,
                policy,
            ):
                continue
            visible = str(visible_path)
            if visible not in files:
                files.append(visible)
    return files


def _run_git(root, args):
    """Run a read-only git command in a repository."""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "error": str(exc),
        }
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout[:20000],
        "stderr": completed.stderr[:4000],
    }


_SKILL_API_REFERENCE_RE = re.compile(
    r"{{\s*(?:(session)\.)?([A-Za-z_][A-Za-z0-9_]*)\s*}}"
)
_SENSITIVE_RESPONSE_KEY_RE = re.compile(
    r"(?:^|_)(?:access|refresh|token|secret|password|passwd|authorization|"
    r"cookie|api_key|private_key)(?:$|_)",
    re.IGNORECASE,
)
_SKILL_API_MAX_RESPONSE_BYTES = 100000


def _resolve_skill_api_references(value, environment, session):
    """Resolve secret references without exposing their values to the model."""

    if isinstance(value, dict):
        return {
            key: _resolve_skill_api_references(item, environment, session)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_skill_api_references(item, environment, session) for item in value
        ]
    if not isinstance(value, str):
        return value

    exact = _SKILL_API_REFERENCE_RE.fullmatch(value)
    if exact:
        source, name = exact.groups()
        values = session if source == "session" else environment
        if name not in values:
            error = (
                "SESSION_REFERENCE_MISSING"
                if source == "session"
                else "ENVIRONMENT_REFERENCE_MISSING"
            )
            raise ValueError(error)
        return values[name]

    def replace(match):
        source, name = match.groups()
        values = session if source == "session" else environment
        if name not in values:
            error = (
                "SESSION_REFERENCE_MISSING"
                if source == "session"
                else "ENVIRONMENT_REFERENCE_MISSING"
            )
            raise ValueError(error)
        return str(values[name])

    return _SKILL_API_REFERENCE_RE.sub(replace, value)


def _skill_api_response_payload(response):
    """Return a bounded JSON-compatible response payload."""

    content_length = response.headers.get("content-length")
    try:
        if content_length and int(content_length) > _SKILL_API_MAX_RESPONSE_BYTES:
            return {"detail": "Response body exceeded the 100 KB limit."}
    except ValueError:
        pass

    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > _SKILL_API_MAX_RESPONSE_BYTES:
            return {"detail": "Response body exceeded the 100 KB limit."}
        body.extend(chunk)
    text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text[:20000]


def _normalized_skill_api_path(value):
    """Return a decoded absolute path without traversal segments."""

    decoded = str(value or "")
    for _ in range(5):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    if unquote(decoded) != decoded:
        return None
    decoded = decoded.replace("\\", "/")
    parts = decoded.split("/")
    if any(part in {".", ".."} for part in parts):
        return None
    return "/" + decoded.lstrip("/")


def _skill_api_request_allowed(
    policy,
    base_url_env,
    request_method,
    request_path,
):
    """Return whether a trusted runtime policy permits one API request."""

    if not isinstance(policy, dict):
        return False
    if policy.get("base_url_env") != str(base_url_env or "").strip():
        return False
    for route in policy.get("routes") or []:
        if not isinstance(route, dict):
            continue
        methods = {
            str(method).upper()
            for method in route.get("methods") or []
            if str(method).upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        }
        if request_method not in methods:
            continue
        exact_path = _normalized_skill_api_path(route.get("path"))
        if route.get("path") and exact_path == request_path:
            return True
        prefix = route.get("path_prefix")
        normalized_prefix = _normalized_skill_api_path(prefix)
        if (
            prefix
            and normalized_prefix
            and request_path.startswith(normalized_prefix.rstrip("/") + "/")
        ):
            return True
    return False


def _json_path_value(payload, path):
    """Read a dotted dict/list path from a JSON-compatible payload."""

    value = payload
    for part in str(path or "").split("."):
        if not part:
            return None
        if isinstance(value, dict):
            if part not in value:
                return None
            value = value[part]
            continue
        if isinstance(value, list) and part.isdigit():
            index = int(part)
            if index >= len(value):
                return None
            value = value[index]
            continue
        return None
    return value


def _redact_skill_api_payload(payload, secret_values=()):
    """Redact credentials and tokens from an external API response."""

    secrets = [str(value) for value in secret_values if value not in (None, "")]
    if isinstance(payload, dict):
        return {
            key: (
                "***"
                if _SENSITIVE_RESPONSE_KEY_RE.search(_normalized_sensitive_key(key))
                else _redact_skill_api_payload(value, secrets)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_redact_skill_api_payload(item, secrets) for item in payload]
    if isinstance(payload, str):
        if payload in secrets:
            return "***"
        redacted = payload
        for secret in secrets:
            if len(secret) >= 4:
                pattern = (
                    rf"(?<![A-Za-z0-9._~+/-]){re.escape(secret)}"
                    r"(?![A-Za-z0-9._~+/-])"
                )
                redacted = re.sub(pattern, "***", redacted)
        return redacted
    return payload


def _normalized_sensitive_key(value):
    """Normalize camelCase and separators before sensitive-key matching."""

    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def _positive_int(value, default):
    """Return a positive integer setting value."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _basename(path):
    """Return a path's file name for compact event summaries."""

    return Path(str(path)).name or str(path)


def _clip(text, limit):
    """Collapse whitespace and clip to `limit` chars for a bounded preview."""

    text = " ".join(str(text or "").split())
    return text[:limit] + "…" if len(text) > limit else text


def _names(paths, limit=3):
    """Return up to `limit` comma-joined basenames, with a +N overflow tag."""

    items = paths or []
    names = [Path(str(item)).name for item in items[:limit]]
    text = ", ".join(names)
    extra = len(items) - len(names)
    if extra > 0:
        text += f" +{extra}"
    return text


def _search_summary(query, regex, glob, output_mode):
    """Build a compact summary of a search request for the trace."""

    parts = [f"/{query}/" if regex else f'"{query}"']
    if glob:
        parts.append(f"in {glob}")
    if output_mode and output_mode != "content":
        parts.append(f"({output_mode})")
    return " ".join(parts)


def _search_done_summary(result, matches, files, counts, paths):
    """Build a compact summary of a search result for the trace."""

    mode = result.get("mode")
    if mode == "files":
        return f"{len(files)} files · {_names(files)}" if files else "0 files"
    if mode == "count":
        return f"{len(counts)} files"
    if matches:
        return f"{len(matches)} matches in {len(paths)} files · {_names(paths)}"
    if files:
        return f"no matches · listing {len(files)} files"
    return "no matches"


def _json(payload):
    """Serialize tool output as JSON."""

    return json.dumps(payload, ensure_ascii=False)
