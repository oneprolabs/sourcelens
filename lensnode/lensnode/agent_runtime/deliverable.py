"""Middleware protecting append-based deliverables from redundant rewrites."""

import json
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


_APPEND_FILE_MANIFEST = ".sourcelens-append-chunks.json"


class AppendWriteDeduplicationMiddleware(AgentMiddleware):
    """Skip an identical write after a file was completed with append_file."""

    def __init__(self, root, emit_event=None):
        self.root = Path(root).resolve()
        self.emit_event = emit_event

    def _completed_append(self, file_path):
        """Return whether append_file completed at least one chunk for a path."""

        try:
            relative = self._relative_path(file_path)
            manifest = json.loads(
                (self.root / _APPEND_FILE_MANIFEST).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return False
        chunks = manifest.get("chunks") if isinstance(manifest, dict) else None
        if not isinstance(chunks, dict):
            return False
        prefix = f"{relative}:"
        return any(
            key.startswith(prefix)
            and isinstance(value, dict)
            and value.get("state") == "completed"
            for key, value in chunks.items()
        )

    def _relative_path(self, file_path):
        """Normalize a virtual scratch path and reject escapes."""

        candidate = (self.root / str(file_path or "").lstrip("/")).resolve()
        return candidate.relative_to(self.root).as_posix()

    def _deduplicate(self, request):
        """Return a successful tool result when the requested write is a no-op."""

        tool_call = getattr(request, "tool_call", None) or {}
        if tool_call.get("name") != "write_file":
            return None
        args = tool_call.get("args") or {}
        file_path = args.get("file_path")
        content = args.get("content")
        if not isinstance(file_path, str) or not isinstance(content, str):
            return None
        try:
            relative = self._relative_path(file_path)
            resolved = self.root / relative
            if not self._completed_append(file_path):
                return None
            if not resolved.is_file() or resolved.read_text(encoding="utf-8") != content:
                return None
        except (OSError, UnicodeError, ValueError):
            return None
        if self.emit_event is not None:
            self.emit_event(
                "deepagents.agent.write_deduplicated",
                {"path": relative, "reason": "append_content_unchanged"},
            )
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": True,
                    "deduplicated": True,
                    "message": "Skipped an identical write after append_file.",
                }
            ),
            name="write_file",
            tool_call_id=tool_call.get("id") or "write-file-deduplicated",
            status="success",
        )

    def wrap_tool_call(self, request, handler):
        """Deduplicate an identical synchronous write."""

        return self._deduplicate(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        """Deduplicate an identical asynchronous write."""

        return self._deduplicate(request) or await handler(request)
