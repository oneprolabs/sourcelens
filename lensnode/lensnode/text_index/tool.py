"""A lightweight Agent tool which isolates optional indexing dependencies."""

import json
import subprocess
import sys
import time
from pathlib import Path

from langchain_core.tools import tool

from .config import MAX_RESULTS, IndexUnavailable


def query_worker(command, query, limit):
    """Send only trusted scope plus bounded query arguments to a child process."""

    payload = json.dumps(
        {
            "query": query,
            "limit": limit,
            "target_dirs": command.get("target_dirs") or [],
            "policy": (command.get("settings") or {}).get("retrieval_policy") or {},
        }
    )
    if len(payload) > 256 * 1024:
        raise IndexUnavailable("TEXT_INDEX_SCOPE_LIMIT")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "lensnode.text_index", "query"],
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
        )
        result = json.loads(completed.stdout)
        if completed.returncode or result.get("error"):
            reason = str(result.get("error", "TEXT_INDEX_WORKER_FAILED"))
            if not reason.startswith("TEXT_INDEX_") or not reason.replace("_", "").isalnum():
                reason = "TEXT_INDEX_WORKER_FAILED"
            raise IndexUnavailable(reason)
        return result
    except subprocess.TimeoutExpired:
        raise IndexUnavailable("TEXT_INDEX_QUERY_TIMEOUT") from None
    except (OSError, ValueError, AttributeError):
        raise IndexUnavailable("TEXT_INDEX_WORKER_FAILED") from None


def build_indexed_tool(command, literal_search, emit, source_recorder=None):
    """Add indexed text retrieval while retaining the literal tool contract."""

    @tool("search_indexed_workspace")
    def search_indexed_workspace(query: str, max_results: int = 8) -> str:
        """Search prepared text chunks within the selected workspace using full-text terms.

        Use for ranked text passages across documents and code, with keywords
        in the source language. This is lexical retrieval. Use CodeGraph for
        definitions, callers, dependencies and impact; use search_workspace
        for exact strings and regex. Read returned original paths to verify
        evidence. Missing or stale indexes fall back to workspace search.
        """

        started = time.monotonic()
        query = str(query).strip()
        if not query or len(query) > 2000:
            return json.dumps({"error": "TEXT_INDEX_QUERY_INVALID"})
        limit = min(MAX_RESULTS, max(1, int(max_results)))
        emit("tool.search_indexed_workspace.start", {"query": query, "max_results": limit})
        reason = ""
        try:
            result = query_worker(command, query, limit)
            if not result.get("matches"):
                raise IndexUnavailable("TEXT_INDEX_NO_MATCHES")
            if source_recorder is not None:
                source_recorder.record_search(query, result.get("matches") or [])
            for match in result.get("matches") or []:
                match["path"] = public_path(match["path"], command.get("target_dirs") or [])
        except IndexUnavailable as exc:
            reason = str(exc)
            result = json.loads(literal_search.invoke({"query": query, "max_results": limit}))
            result["index_fallback"] = reason
        emit(
            "tool.search_indexed_workspace.done",
            {
                "mode": result.get("mode"),
                "count": len(result.get("matches") or []),
                "fallback_reason": reason,
                "profile": result.get("profile", ""),
                "generations": sorted(
                    {row["generation"] for row in result.get("matches") or [] if "generation" in row}
                ),
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return json.dumps(result, ensure_ascii=False)

    return search_indexed_workspace


def public_path(value, target_dirs):
    """Use a selected mount name and relative source path for model-facing hits."""

    source = Path(value).resolve()
    for entry in target_dirs:
        root = Path(entry.get("path", "")).resolve()
        if source.is_relative_to(root):
            name = str(entry.get("name") or root.name)
            return f"{name}/{source.relative_to(root).as_posix()}"
    raise IndexUnavailable("TEXT_INDEX_SCOPE_INVALID")
