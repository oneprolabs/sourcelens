"""Bounded trace counters shared by deterministic evaluators."""

from collections import Counter

from lens.models import RunTraceEvent


def _field(event, name, default=None):
    """Read one trace field from either a model instance or a mapping."""

    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def load_trace_events(run):
    """Return the persisted trace events for one run in sequence order."""

    return list(
        RunTraceEvent.objects.filter(run=run)
        .order_by("sequence")
        .only("event_type", "payload", "call_id", "turn", "sequence", "step", "timestamp")
    )


def _int_value(value):
    """Return a non-negative int for numeric payload values."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


def summarize_trace(events):
    """Return deterministic counters, usage totals and latency samples."""

    counts = Counter()
    tool_started = set()
    tool_completed = set()
    tool_failed = set()
    verified_actions = []
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    cached_tokens = 0
    ttft_samples = []
    model_duration_ms = 0
    for index, event in enumerate(events):
        event_type = str(_field(event, "event_type", "") or "")
        payload = _field(event, "payload", {}) or {}
        call_id = str(_field(event, "call_id", "") or "") or f"#{index}"
        counts[event_type] += 1
        if event_type == "tool.started":
            tool_started.add(call_id)
        elif event_type == "tool.completed":
            tool_completed.add(call_id)
        elif event_type == "tool.failed":
            tool_failed.add(call_id)
        elif event_type == "model.completed":
            usage = payload.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            prompt_tokens += _int_value(usage.get("prompt_tokens"))
            completion_tokens += _int_value(usage.get("completion_tokens"))
            total_tokens += _int_value(usage.get("total_tokens"))
            cached_tokens += _int_value(usage.get("cached_tokens"))
            ttft = payload.get("ttft_ms")
            if isinstance(ttft, (int, float)) and not isinstance(ttft, bool):
                ttft_samples.append(float(ttft))
            model_duration_ms += _int_value(payload.get("duration_ms"))
        elif event_type == "deepagents.evidence.verified":
            action = str(payload.get("action") or "")
            if action:
                verified_actions.append(action)
        elif event_type == "step.event":
            # LensNode forwards runtime events as ``step.event`` frames whose
            # payload carries the original event name.
            if payload.get("name") == "deepagents.evidence.verified":
                action = str(payload.get("action") or "")
                if action:
                    verified_actions.append(action)
    return {
        "event_counts": dict(counts),
        "tool_started": len(tool_started),
        "tools_completed": len(tool_completed),
        "tools_failed": len(tool_failed),
        "model_calls": counts.get("model.started", 0),
        "model_completed": counts.get("model.completed", 0),
        "model_failed": counts.get("model.failed", 0),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "ttft_ms_avg": (round(sum(ttft_samples) / len(ttft_samples), 1) if ttft_samples else None),
        "model_duration_ms": model_duration_ms,
        "evidence_verifications": verified_actions,
    }
