"""Durable tool evidence using the existing extension and completion lifecycle."""

import hashlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from .plugin_agent import AgentExtension
from .plugin_package_loader import PluginPackageLoadError, load_runtime_contract

MAX_EVIDENCE_PAGE_CHARS = 24000


def _json(value):
    """Encode stable tool snapshots without arbitrary object serialization."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ToolEvidenceStore:
    """Persist immutable source results outside the model-writable filesystem."""

    def __init__(self, root):
        self.root = Path(root)
        if any(path.is_symlink() for path in (self.root, *self.root.parents)):
            raise ValueError("Invalid tool evidence root")
        self.lock = threading.RLock()
        self.request_locks = {}
        self.entries = {}
        for path in self.root.glob("*.json"):
            if path.is_symlink():
                raise ValueError("Invalid tool evidence file")
            entry = json.loads(path.read_text(encoding="utf-8"))
            self.entries[path.stem] = entry

    @staticmethod
    def _key(provider, connection, version, tool, arguments):
        identity = _json([provider, connection, version, tool, arguments])
        return hashlib.sha256(identity.encode()).hexdigest()

    def cached(self, provider, connection, version, tool, arguments):
        """Reuse only an exact source snapshot within this Run."""

        key = self._key(provider, connection, version, tool, arguments)
        with self.lock:
            entry = self.entries.get(key)
            return _json(entry["result"]) if entry else None

    def has_evidence(self):
        """Distinguish collected evidence from an unused source capability."""

        with self.lock:
            return bool(self.entries)

    def request_lock(self, provider, connection, version, tool, arguments):
        """Serialize identical concurrent source requests within this Run."""

        key = self._key(provider, connection, version, tool, arguments)
        with self.lock:
            return self.request_locks.setdefault(key, threading.Lock())

    def record(self, provider, connection, version, tool, arguments, result):
        """Persist projected evidence atomically before model compaction."""

        if not isinstance(result, dict) or result.get("ok") is not True:
            return
        key = self._key(provider, connection, version, tool, arguments)
        with self.lock:
            if any(path.is_symlink() for path in (self.root, *self.root.parents)):
                raise ValueError("Invalid tool evidence root")
            entry = json.loads(
                _json(
                    {
                        "provider": provider,
                        "connection": connection,
                        "version": version,
                        "tool": tool,
                        "arguments": arguments,
                        "result": result,
                        "sequence": max((item["sequence"] for item in self.entries.values()), default=0) + 1,
                    }
                )
            )
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.root / f"{key}.json"
            temporary = self.root / f"{key}.{uuid.uuid4().hex}.tmp"
            with temporary.open("x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(_json(entry))
            os.replace(temporary, path)
            self.entries[key] = entry

    def read_progress(self, evidence, page=None, *, persist=True):
        """Persist consumed ranges for one exact immutable evidence snapshot."""

        digest = hashlib.sha256(_json(evidence).encode()).hexdigest()
        total = len(evidence["records"])
        folder = self.root / "review" / digest
        path = folder / "progress.json"
        with self.lock:
            if any(item.is_symlink() for item in (self.root, *self.root.parents, folder.parent, folder, path)):
                raise ValueError("Invalid tool evidence review state")
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if state.get("snapshot_sha256") != digest:
                state = {"snapshot_sha256": digest, "ranges": [], "read": False}
            if page is not None:
                state["read"] = True
                ranges = sorted([*state["ranges"], list(page)])
                merged = []
                for start, end in ranges:
                    if merged and start <= merged[-1][1]:
                        merged[-1][1] = max(merged[-1][1], end)
                    else:
                        merged.append([start, end])
                state["ranges"] = merged
                if persist:
                    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                    temporary = folder / f"{uuid.uuid4().hex}.tmp"
                    with temporary.open("x", encoding="utf-8") as stream:
                        os.chmod(temporary, 0o600)
                        stream.write(_json(state))
                    os.replace(temporary, path)
            cursor = 0
            for start, end in state["ranges"]:
                if start > cursor:
                    break
                cursor = max(cursor, end)
            complete = state["read"] and cursor >= total
            return {
                "complete": complete,
                "total_records": total,
                "next_offset": None if complete else cursor,
                "next_unread_end": (
                    None if complete else next((start for start, end in state["ranges"] if start > cursor), total)
                ),
                "snapshot_sha256": digest,
            }

    def material(self):
        """Combine source-declared records while retaining every snapshot's identity."""

        records = []
        sources = []
        with self.lock:
            for key, entry in sorted(self.entries.items(), key=lambda item: item[1]["sequence"]):
                evidence = entry["result"]["_evidence"]
                records.extend(
                    {
                        **(record if isinstance(record, dict) else {"value": record}),
                        "evidence_id": f"{key[:24]}:{index}",
                    }
                    for index, record in enumerate(evidence["records"])
                )
                sources.append(
                    {
                        "snapshot_id": key,
                        **{field: entry[field] for field in ("provider", "connection", "version", "tool", "arguments")},
                        "coverage": {field: value for field, value in evidence.items() if field != "records"},
                    }
                )
            return json.loads(
                _json(
                    {
                        "records": records,
                        "sources": sources,
                        "complete": bool(sources)
                        and all(source["coverage"].get("complete") is True for source in sources),
                    }
                )
            )


def build_tool_evidence_reader(store):
    """Expose bounded pages of immutable, source-declared facts."""

    from langchain.tools import tool

    @tool
    def read_tool_evidence(offset: int | None = None, limit: int = 50, record_ids: list[str] | None = None) -> str:
        """Read saved source facts without issuing another remote request.

        Omit offset to continue from the first unread range. Follow next_page
        sequentially until read_progress.complete. Counts cover the full saved
        dataset; records contains only this page. Sources retain their own
        authorization binding, request arguments and coverage gaps. After
        compaction, use record_ids to recheck specific evidence_id values
        instead of rereading every page. Targeted reads preserve read progress.
        """

        with store.lock:
            if not store.has_evidence():
                return _json({"ok": False, "error": "TOOL_EVIDENCE_REQUIRED"})
            material = store.material()
            progress = store.read_progress(material)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
                return _json({"ok": False, "error": "TOOL_EVIDENCE_PAGE_INVALID"})
            if record_ids is not None:
                if offset is not None or not record_ids or len(record_ids) > 100:
                    return _json({"ok": False, "error": "TOOL_EVIDENCE_IDS_INVALID"})
                requested = set(record_ids)
                selected = [
                    (index, record)
                    for index, record in enumerate(material["records"])
                    if record["evidence_id"] in requested
                ]
                missing = sorted(requested - {record["evidence_id"] for _, record in selected})
                if missing:
                    return _json({"ok": False, "error": "TOOL_EVIDENCE_IDS_UNKNOWN", "missing": missing})
                payload = {"ok": True, "records": [record for _, record in selected], "read_progress": progress}
                if len(_json(payload)) > MAX_EVIDENCE_PAGE_CHARS:
                    return _json({"ok": False, "error": "TOOL_EVIDENCE_PAGE_TOO_LARGE"})
                for index, _ in selected:
                    progress = store.read_progress(material, (index, index + 1))
                payload["read_progress"] = progress
                return _json(payload)
            start = progress["next_offset"] if not progress["complete"] else len(material["records"])
            start = start if offset is None else offset
            if not isinstance(start, int) or isinstance(start, bool) or not 0 <= start <= len(material["records"]):
                return _json({"ok": False, "error": "TOOL_EVIDENCE_PAGE_INVALID"})
            end = min(start + limit, len(material["records"]))
            if progress["next_offset"] == start:
                end = min(end, progress["next_unread_end"] or end)
            lower, upper = start + 1, end
            best = None
            while True:
                progress = store.read_progress(material, (start, end), persist=False)
                payload = {
                    "ok": True,
                    "records": material["records"][start:end],
                    "total_records": len(material["records"]),
                    "sources": material["sources"],
                    "_evidence": {"complete": material["complete"]},
                    "read_progress": progress,
                    "next_offset": progress["next_offset"],
                    "next_page": (
                        {"tool": "read_tool_evidence", "arguments": {"offset": progress["next_offset"], "limit": limit}}
                        if not progress["complete"]
                        else None
                    ),
                }
                if start > 0 or len(_json(payload)) > MAX_EVIDENCE_PAGE_CHARS:
                    payload["sources"] = [
                        {
                            "snapshot_id": source["snapshot_id"],
                            "tool": source["tool"],
                            "complete": source["coverage"].get("complete") is True,
                        }
                        for source in material["sources"]
                    ]
                    payload["source_details_omitted"] = True
                if len(_json(payload)) <= MAX_EVIDENCE_PAGE_CHARS:
                    best = (end, payload)
                    lower = end + 1
                else:
                    upper = end - 1
                if lower > upper:
                    if best is None:
                        return _json({"ok": False, "error": "TOOL_EVIDENCE_PAGE_TOO_LARGE"})
                    store.read_progress(material, (start, best[0]))
                    return _json(best[1])
                end = (lower + upper) // 2

    read_tool_evidence.metadata = {"bounded_inline_result": True}
    return read_tool_evidence


class ToolEvidenceExtension(AgentExtension):
    """Reuse the extension lifecycle for source evidence, without task or output policy."""

    refresh_evidence = True
    guidance = (
        "Source tools may return evidence_saved receipts. Read their saved facts using read_tool_evidence; "
        "follow next_page until read_progress.complete. A receipt is not the underlying source data. "
        "Coverage belongs to each source snapshot. Preserve evidence_id references and established findings "
        "in working notes. After compaction or correction, recheck missing facts with record_ids instead of "
        "restarting all pages. An earlier read does not prove that a missing fact survived compaction."
    )

    def __init__(self, context):
        self.context = context
        self.bindings = {
            (item.plugin_key, item.connection_uuid, item.plugin_version, item.key)
            for item in context.bound_tools
            if item.metadata.get("durable_evidence") is True
        }
        legacy = context.state_root.parent / "activity-report"
        if legacy.is_symlink() or context.state_root.is_symlink():
            raise ValueError("Invalid legacy tool evidence path")
        if legacy.exists() and not context.state_root.exists():
            shutil.move(str(legacy), str(context.state_root))
        self.store = ToolEvidenceStore(context.state_root)
        self.tools = (build_tool_evidence_reader(self.store),)
        for key, entry in list(self.store.entries.items()):
            identity = tuple(entry[field] for field in ("provider", "connection", "version", "tool"))
            if identity not in self.bindings:
                del self.store.entries[key]
                continue
            evidence = entry["result"].get("_evidence", {})
            if not isinstance(evidence.get("records"), list):
                contract = load_runtime_contract(entry["provider"], entry["version"])
                result = {
                    **entry["result"],
                    "_evidence": contract.project_tool_evidence(entry["tool"], entry["result"], entry["connection"]),
                }
                self.store.record(*identity, entry["arguments"], result)

    def wrap_executor(self, binding, definition, executor):
        """Reuse only exact snapshots admitted by this Run's frozen tool bindings."""

        identity = (binding["plugin_key"], binding["connection_uuid"], binding["plugin_version"], definition["key"])
        if identity not in self.bindings or definition.get("side_effect", "none") != "none":
            return executor
        contract = load_runtime_contract(identity[0], identity[2])
        if contract.project_tool_evidence is None:
            return executor

        def invoke(tool_key, arguments, runtime):
            """Persist successful source evidence before returning a bounded receipt."""

            if tool_key != identity[-1] or not getattr(runtime, "tool_call_id", None):
                return executor(tool_key, arguments, runtime)
            with self.store.request_lock(*identity, arguments):
                cached = self.store.cached(*identity, arguments)
                result = json.loads(cached if cached is not None else executor(tool_key, arguments, runtime))
                if result.get("ok") is not True:
                    return _json(result)
                evidence = (
                    result.get("_evidence")
                    if cached is not None
                    else contract.project_tool_evidence(tool_key, result, identity[1])
                )
                if (
                    not isinstance(evidence, dict)
                    or not isinstance(evidence.get("records"), list)
                    or not isinstance(evidence.get("complete"), bool)
                ):
                    raise PluginPackageLoadError("plugin evidence projection is invalid")
                if isinstance(result.get("_evidence"), dict) and result["_evidence"].get("complete") is False:
                    evidence = {**evidence, "complete": False}
                if cached is None:
                    result = {**result, "_evidence": evidence}
                    self.store.record(*identity, arguments, result)
                event = "tool.plugin.cache_hit" if cached is not None else "tool.evidence.saved"
                if self.context.emit_event:
                    self.context.emit_event(
                        event,
                        {
                            "plugin": identity[0],
                            "plugin_version": identity[2],
                            "tool": tool_key,
                            "call_id": str(getattr(runtime, "tool_call_id", "") or ""),
                            "runtime_identity": getattr(executor, "_lens_runtime_identity", None),
                        },
                    )
                return _json(
                    {
                        "ok": True,
                        "evidence_saved": True,
                        "tool": tool_key,
                        "snapshot_id": self.store._key(*identity, arguments),
                        "record_count": len(evidence["records"]),
                        "_evidence": {"complete": evidence.get("complete") is True},
                        "next_page": {"tool": "read_tool_evidence", "arguments": {}},
                    }
                )

        return invoke

    def evidence(self):
        """Supply complete saved facts to the existing answer-grounding gate."""

        if not self.store.has_evidence():
            return {}
        material = self.store.material()
        progress = self.store.read_progress(material)
        return {
            "plugin_evidence": {"tool-results": {**material, "read_progress": progress}},
            "review_replacements": {
                name: "tool-results"
                for name in {"read_tool_evidence", *(source["tool"] for source in material["sources"])}
            },
            "evidence_completeness": "complete" if material["complete"] else "incomplete",
            "review_completeness": "complete" if progress["complete"] else "incomplete",
        }

    def completion_checks(self, answer):
        """Require consumed source pages without interpreting the proposed answer."""

        if not self.store.has_evidence():
            return []
        progress = self.store.read_progress(self.store.material())
        if progress["complete"]:
            return []
        return [
            {
                "code": "tool_evidence_unread",
                "message": "Source evidence has unread records.",
                "recovery": "Call read_tool_evidence() and follow next_page until read_progress.complete, then "
                "update affected output files and retry delivery.",
            }
        ]
