"""Build semantic execution steps from immutable trajectory events.

The runtime publishes low-level, append-only events. This module folds those
events into the user-visible *Step* model the admin trace UI renders:

* model / tool / subtool calls are grouped by call id into one step each,
* a small whitelist of standalone events (decision gates, artifacts, plans,
  compaction, messages) becomes a step of its own,
* every other internal event (snapshots, checkpoints, phases, budgets, ...)
  is ignored so the timeline stays factual instead of noisy.

Payloads are projected through a per-type allowlist and bounded, so neither
the REST response nor the SSE stream carries full prompts, tool schemas, or
raw results. Full raw events stay available through the trajectory endpoint
filtered by ``call_id``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

STEP_TYPES = (
    "model",
    "retrieval",
    "decision",
    "tool",
    "artifact",
    "message",
    "reasoning",
    "system",
)

_RETRIEVAL_TOKENS = (
    "search",
    "retriev",
    "query",
    "evidence",
    "find_files",
    "read_workspace",
    "codegraph",
    "grep",
    "explore",
    "knowledge",
    "document",
)

_ARTIFACT_TOKENS = (
    "artifact",
    "deliverable",
    "save_",
    "write_file",
    "append_file",
    "report",
    "export",
)

# Standalone events (no call) that carry a user-visible step.
_STANDALONE_STEP_TYPES = {
    "deepagents.decision.gate.start": "decision",
    "deepagents.decision.gate.done": "decision",
    "route.selected": "decision",
    "artifact.created": "artifact",
    "workflow.artifact.created": "artifact",
    "assistant.message": "message",
    "assistant.reasoning": "reasoning",
    "context.message": "message",
    "deepagents.plan.ready": "reasoning",
    "deepagents.plan.required": "reasoning",
    "plan.updated": "reasoning",
    "workflow.plan.updated": "reasoning",
    "deepagents.summarization.compacted": "reasoning",
    "compaction.completed": "reasoning",
    "compaction.event": "reasoning",
    "request.failed": "system",
    "execution.failed": "system",
    "verification.failed": "system",
}

# Payload keys projected into step details, per semantic type.
_DETAIL_KEYS = {
    "model": (
        "model_ref",
        "is_subagent",
        "finish_reason",
        "duration_ms",
        "ttft_ms",
        "error_type",
        "error",
    ),
    "retrieval": (
        "name",
        "query",
        "strategy",
        "total_results",
        "selected_results",
        "sources",
        "documents",
        "metrics",
        "duration_ms",
        "error_type",
        "error",
    ),
    "decision": (
        "gate",
        "source",
        "provider",
        "decision",
        "score",
        "value",
        "threshold",
        "margin",
        "verdict",
        "result",
        "fallback_reason",
        "options",
        "duration_ms",
    ),
    "tool": (
        "name",
        "tool",
        "arguments",
        "result",
        "error_type",
        "error",
        "duration_ms",
        "request_id",
    ),
    "artifact": (
        "name",
        "path",
        "format",
        "citations",
        "output_summary",
        "duration_ms",
    ),
    "message": ("role", "finish_reason"),
    "reasoning": ("reason", "summary"),
    "system": ("reason", "error_type", "error", "duration_ms", "outcome"),
}
_DETAIL_KEYS["subtool"] = _DETAIL_KEYS["tool"]

_PREVIEW_SOURCE_KEYS = ("content", "output_summary", "summary", "reason")
_PREVIEW_LIMIT = 600
_TEXT_LIMIT = 1200
_LIST_LIMIT = 20
_DETAILS_LIMIT = 20000


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _timestamp(event: dict[str, Any]) -> datetime | None:
    value = event.get("timestamp")
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or "")


def _effective_call_id(event: dict[str, Any]) -> str:
    """Return the event's call id, falling back to the payload one.

    Decision gates record their identity inside the payload rather than on the
    event row, so both start and done frames must group under the same id.
    """

    payload = _payload(event)
    return str(event.get("call_id") or payload.get("call_id") or "")


def _event_name(event: dict[str, Any]) -> str:
    payload = _payload(event)
    for key in ("name", "model_ref", "gate", "title", "step_type", "kind"):
        value = payload.get(key)
        if value:
            return str(value)
    return _event_type(event) or "Event"


def _short(value: Any) -> str:
    try:
        return str(value)[:_TEXT_LIMIT]
    except Exception:
        return f"<{type(value).__name__}>"


def _bounded(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return _short(value)
    if isinstance(value, str):
        return value[:_TEXT_LIMIT]
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, depth + 1)
            for key, item in list(value.items())[:_LIST_LIMIT]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth + 1) for item in list(value)[:_LIST_LIMIT]]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _short(value)


def _preview(payload: dict[str, Any]) -> str:
    for key in _PREVIEW_SOURCE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_PREVIEW_LIMIT]
    return ""


def _classify_tool(name: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in _RETRIEVAL_TOKENS):
        return "retrieval"
    if any(token in lowered for token in _ARTIFACT_TOKENS):
        return "artifact"
    return "tool"


def _step_type(group: list[dict[str, Any]]) -> str:
    event_type = _event_type(group[0])
    mapped = _STANDALONE_STEP_TYPES.get(event_type)
    if mapped:
        return mapped
    category = event_type.split(".", 1)[0]
    if category == "model":
        return "model"
    if category in {"tool", "subtool"}:
        return _classify_tool(_event_name(group[0]))
    if category in STEP_TYPES:
        return category
    return "system"


def _status(events: list[dict[str, Any]]) -> str:
    suffix = _event_type(events[-1]).rsplit(".", 1)[-1]
    if suffix in {"failed", "error"}:
        return "failed"
    if suffix in {"cancelled", "canceled", "interrupted", "stopped"}:
        return "cancelled"
    if suffix in {"completed", "complete", "done", "success"}:
        return "completed"
    if suffix in {"started", "start", "running"}:
        return "running"
    return "completed"


def _normalize_usage(usage: dict[str, Any]) -> dict[str, int]:
    def _int(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            try:
                if value is not None:
                    return max(int(value), 0)
            except (TypeError, ValueError):
                continue
        return 0

    input_tokens = _int("input_tokens", "prompt_tokens")
    output_tokens = _int("output_tokens", "completion_tokens")
    total_tokens = _int("total_tokens") or input_tokens + output_tokens
    reasoning_tokens = _int("reasoning_tokens", "reasoning_output_tokens")
    return {
        "input": input_tokens,
        "output": output_tokens,
        "total": total_tokens,
        "reasoning": reasoning_tokens,
    }


def _tokens(events: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"input": 0, "output": 0, "total": 0, "reasoning": 0}
    for event in events:
        usage = _payload(event).get("usage")
        if not isinstance(usage, dict):
            continue
        normalized = _normalize_usage(usage)
        for key in totals:
            totals[key] += normalized[key]
    return totals


def _duration_ms(
    events: list[dict[str, Any]],
    start: datetime | None,
    end: datetime | None,
) -> int:
    elapsed = 0
    if start and end:
        elapsed = max(int((end - start).total_seconds() * 1000), 0)
    for event in events:
        value = _payload(event).get("duration_ms")
        try:
            elapsed = max(elapsed, int(value or 0))
        except (TypeError, ValueError):
            continue
    return elapsed


def _merge_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for event in events:
        merged.update(_payload(event))
    return merged


def _details(step_type: str, merged: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for key in _DETAIL_KEYS.get(step_type, ()):
        value = merged.get(key)
        if value not in (None, "", {}, []):
            details[key] = _bounded(value)
    if step_type == "model":
        usage = merged.get("usage")
        if isinstance(usage, dict):
            details["usage"] = _normalize_usage(usage)
        messages = merged.get("messages")
        if isinstance(messages, list):
            details["message_count"] = len(messages)
        tool_calls = merged.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            details["tool_count"] = len(tool_calls)
    preview = _preview(merged)
    if preview and step_type in {"model", "message", "reasoning"}:
        details["preview"] = preview
    try:
        encoded = json.dumps(details, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"error": "unserializable"}
    if len(encoded) > _DETAILS_LIMIT:
        return {"truncated": True, "size": len(encoded)}
    return details


def _decision_value(details: dict[str, Any]) -> Any:
    for key in ("value", "score", "result", "decision"):
        if details.get(key) not in (None, ""):
            return details[key]
    return None


def _summary(
    step_type: str,
    group: list[dict[str, Any]],
    title: str,
    details: dict[str, Any],
) -> str:
    status = _status(group)
    if status == "failed":
        error = details.get("error") or details.get("error_type")
        return f"{title} failed" + (f": {error}" if error else "")
    if status == "cancelled":
        return f"{title} cancelled"
    if step_type == "model":
        return details.get("preview") or f"{title} · {status}"
    if step_type == "retrieval":
        count = details.get("total_results") or details.get("selected_results")
        metrics = details.get("metrics")
        if count is None and isinstance(metrics, dict):
            count = metrics.get("result_count")
        if count is not None:
            return f"{title} · {count} results"
        return f"{title} completed"
    if step_type == "decision":
        gate = details.get("gate") or title
        value = _decision_value(details)
        if value is not None:
            return f"{gate} → {value}"
        return str(details.get("verdict") or f"{gate} completed")
    if step_type == "tool":
        result = details.get("result")
        if isinstance(result, dict):
            result = result.get("result_count") or result.get("status")
        if result not in (None, ""):
            return f"{title} → {result}"
        return f"{title} completed"
    if step_type == "artifact":
        return str(details.get("output_summary") or f"{title} created")
    if step_type in {"message", "reasoning"}:
        return details.get("preview") or f"{title} · {status}"
    return str(details.get("reason") or f"{title} · {status}")[:240]


def build_trace_steps(
    events: list[dict[str, Any]],
    run_start: datetime | None = None,
) -> dict[str, Any]:
    """Return semantic steps, causal edges, and hidden-event count."""

    ordered = sorted(
        events,
        key=lambda item: (
            item.get("sequence") is None,
            item.get("sequence") or 0,
        ),
    )
    timestamps = [value for value in (_timestamp(event) for event in ordered) if value]
    origin = run_start or (min(timestamps) if timestamps else None)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for event in ordered:
        call_id = _effective_call_id(event)
        if call_id:
            key = f"{event.get('trace_run_uuid') or 'root'}:{call_id}"
        elif _event_type(event) in _STANDALONE_STEP_TYPES:
            key = f"{event.get('trace_run_uuid') or 'root'}:event:{event.get('event_id') or event.get('sequence')}"
        else:
            continue
        if key not in groups:
            order.append(key)
        groups[key].append(event)

    steps: list[dict[str, Any]] = []
    hidden = 0
    for key in order:
        group = groups[key]
        times = [value for value in (_timestamp(event) for event in group) if value]
        if not times:
            continue
        start, end = min(times), max(times)
        step_type = _step_type(group)
        title = _event_name(group[0])
        merged = _merge_payload(group)
        details = _details(step_type, merged)
        duration = _duration_ms(group, start, end)
        start_ms = (
            max(int((start - origin).total_seconds() * 1000), 0)
            if origin
            else 0
        )
        first = group[0]
        steps.append(
            {
                "id": f"step_{len(steps) + 1}",
                "type": step_type,
                "label": step_type.title(),
                "title": title,
                "summary": _summary(step_type, group, title, details)[:240],
                "start_ms": start_ms,
                "end_ms": start_ms + duration,
                "duration_ms": duration,
                "status": _status(group),
                "tokens": _tokens(group),
                "details": details,
                "call_id": first.get("call_id") or merged.get("call_id"),
                "parent_call_id": first.get("parent_call_id"),
                "run_role": first.get("trace_run_role") or "parent",
                "assistant_name": first.get("assistant_name"),
                "event_ids": [
                    event.get("event_id") for event in group if event.get("event_id")
                ],
            }
        )

    hidden = max(len(ordered) - sum(len(group) for group in groups.values()), 0)

    steps.sort(key=lambda item: (item["start_ms"], item["id"]))
    for index, step in enumerate(steps, start=1):
        step["id"] = f"step_{index}"

    id_by_call = {
        str(step["call_id"]): step["id"] for step in steps if step.get("call_id")
    }
    edges: list[dict[str, Any]] = [
        {"from": previous["id"], "to": current["id"], "kind": "flow"}
        for previous, current in zip(steps, steps[1:])
    ]
    for step in steps:
        parent_id = id_by_call.get(str(step.get("parent_call_id") or ""))
        if parent_id and parent_id != step["id"]:
            edges.append({"from": parent_id, "to": step["id"], "kind": "child"})

    return {"steps": steps, "edges": edges, "hidden_event_count": hidden}
