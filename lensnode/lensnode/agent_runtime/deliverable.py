"""Host-owned final output staging and append-write deduplication."""

import hashlib
import json
import os
from pathlib import Path
import uuid

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from .evidence_material import DEFAULT_REVIEW_STATE_CHARS

_APPEND_FILE_MANIFEST = ".sourcelens-append-chunks.json"
DELIVERABLE_DIRECTORY = "deliverables"
MAX_DELIVERABLE_FILES = 64


class AutomaticDeliverables:
    """Stage only final output files, independently of model tool selection."""

    def __init__(self, command, resources, config, emit_event=None, *, evidence=None):
        from ..agent_tools import _build_deliverable_stager

        self.root = resources.root.resolve()
        self.directory = self.root / DELIVERABLE_DIRECTORY
        self.command = command
        self.evidence = evidence if evidence is not None else {}
        self.manifest = self.evidence.setdefault("_deliverables", command.setdefault("_deliverables", {}))
        command["_deliverables"] = self.manifest
        self.fingerprints = self.evidence.setdefault("_deliverable_fingerprints", {})
        self.rejected_fingerprints = self.evidence.setdefault("_rejected_deliverable_fingerprints", {})
        self.config = config
        self.stage = _build_deliverable_stager(command, resources, config, emit_event)
        if not self.directory.exists() and not self.directory.is_symlink():
            self.directory.mkdir(exist_ok=True)

    @staticmethod
    def _issue(message):
        """Return a bounded, model-readable delivery failure."""

        return {"code": "artifact_delivery_failed", "message": message,
                "recovery": message + " Correct the files in /deliverables/ and submit the final answer again."}

    def _files(self):
        """Discover regular outputs without traversing links or private scratch."""

        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("The /deliverables/ output directory must be a regular directory.")
        files = []

        def raise_error(error):
            raise error

        for directory, directories, filenames in os.walk(self.directory, onerror=raise_error, followlinks=False):
            for name in sorted(directories + filenames):
                path = Path(directory) / name
                if path.is_symlink():
                    raise ValueError(
                        "Remove symbolic links from /deliverables/; only generated regular files are accepted."
                    )
            for name in sorted(filenames):
                path = Path(directory) / name
                if not path.is_file():
                    raise ValueError("Only regular files are accepted in /deliverables/.")
                if path.stat().st_size > self.config.deliverable_max_bytes:
                    raise ValueError("An output exceeds the attachment size limit. Reduce its size.")
                files.append(path)
                if len(files) > MAX_DELIVERABLE_FILES:
                    raise ValueError(f"Keep at most {MAX_DELIVERABLE_FILES} final files in /deliverables/.")
        return sorted(files)

    def collect(self):
        """Refresh candidates from current bytes and return any failed checks."""

        from ..agent_tools import _deliverable_content_type, _deliverable_validation_issues

        self.evidence["answer_artifacts"] = []
        try:
            files = self._files()
            current = {path.relative_to(self.root).as_posix() for path in files}
            for path in list(self.manifest):
                if path not in current:
                    self.manifest.pop(path, None)
                    self.fingerprints.pop(path, None)
            issues = []
            for path in files:
                relative = path.relative_to(self.root).as_posix()
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                content_type = _deliverable_content_type(path.name)
                if content_type.startswith("text/") or content_type in {"application/json", "application/xml"}:
                    text = data.decode("utf-8", errors="replace")
                    self.evidence["answer_artifacts"].append({
                        "path": relative, "sha256": digest, "content_type": content_type,
                        "content": text[:DEFAULT_REVIEW_STATE_CHARS],
                        "truncated": len(text) > DEFAULT_REVIEW_STATE_CHARS,
                    })
                if self.rejected_fingerprints.get(relative) == digest:
                    issues.append(self._issue(
                        f"The rejected draft {relative} is unchanged. Correct the affected sections in place "
                        "or remove the file if it is not intended for the final answer."
                    ))
                    continue
                if self.fingerprints.get(relative) == digest and self.manifest.get(relative):
                    issues.extend(_deliverable_validation_issues(self.command, path.read_bytes(), path.name))
                    continue
                result = json.loads(self.stage(relative))
                if result.get("ok"):
                    self.fingerprints[relative] = digest
                    self.rejected_fingerprints.pop(relative, None)
                else:
                    self.fingerprints.pop(relative, None)
                    checks = result.get("checks") or [self._issue(
                        f"Automatic attachment staging failed for {relative}: {result.get('error', 'DELIVERY_FAILED')}."
                    )]
                    issues.extend(checks)
            if issues:
                self.manifest.clear()
            return issues
        except (OSError, ValueError) as exc:
            self.manifest.clear()
            return [self._issue(str(exc) if isinstance(exc, ValueError) else "Could not read the output directory.")]

    def invalidate(self):
        """Withdraw candidate uploads while keeping drafts available for local edits."""

        self.rejected_fingerprints.update(self.fingerprints)
        self.manifest.clear()
        self.fingerprints.clear()
        self.evidence["answer_artifacts"] = []

    def discard(self):
        """Quarantine a rejected batch so unchanged drafts cannot be republished."""

        self.manifest.clear()
        self.fingerprints.clear()
        self.rejected_fingerprints.clear()
        self.evidence["answer_artifacts"] = []
        if self.directory.is_symlink():
            self.directory.unlink()
        elif self.directory.exists():
            self.directory.rename(self.root / f".rejected-deliverables-{uuid.uuid4().hex}")
        self.directory.mkdir(exist_ok=True)
        append_manifest = self.root / _APPEND_FILE_MANIFEST
        if append_manifest.is_file():
            manifest = json.loads(append_manifest.read_text(encoding="utf-8"))
            chunks = manifest.get("chunks", {})
            manifest["chunks"] = {key: value for key, value in chunks.items()
                                  if not key.startswith(f"{DELIVERABLE_DIRECTORY}/")}
            temporary = append_manifest.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(manifest), encoding="utf-8")
            temporary.replace(append_manifest)


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
