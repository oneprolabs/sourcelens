"""Deterministic evaluation outcome derivation from persisted Run evidence."""

from collections.abc import Iterable

from lens.models import Run

_EXECUTION_FAILURE_REASONS = {
    "capability_unavailable",
    "execution_failed",
    "model_error",
    "timeout",
}


def _trace_events(run) -> list:
    """Return trace events without requiring callers to know the storage shape."""

    events = getattr(run, "trace_events", None)
    if events is None:
        return []
    if hasattr(events, "all"):
        return list(events.all().only("event_type", "payload", "sequence"))
    return list(events)


def _latest_route(events: Iterable) -> dict:
    """Return the latest route selection payload, if one was recorded."""

    routes = [event.payload or {} for event in events if getattr(event, "event_type", "") == "route.selected"]
    return routes[-1] if routes else {}


def derive_run_quality(run) -> dict:
    """Derive a quality verdict without treating ``Run.outcome`` as ground truth.

    ``evidence_unavailable`` is inconclusive when the selected route explicitly
    required no evidence. This preserves valid direct answers whose execution
    layer reports a partial outcome because a route invariant was applied.
    """

    if run.status != Run.Status.DONE:
        return {
            "passed": None,
            "status": "inconclusive",
            "reason": "run_not_done",
        }

    events = _trace_events(run)
    termination = run.termination_detail or {}
    reason = str(termination.get("reason") or "")
    if reason in _EXECUTION_FAILURE_REASONS:
        return {"passed": False, "status": "failed", "reason": reason}

    route = _latest_route(events)
    evidence_requirement = route.get("evidence_requirement")
    if reason == "evidence_unavailable":
        if evidence_requirement in {None, "none"}:
            return {
                "passed": None,
                "status": "inconclusive",
                "reason": "evidence_unavailable_for_no_evidence_route",
            }
        return {"passed": False, "status": "failed", "reason": reason}

    if reason in {"partial", "step_limit", "blocked"} or run.outcome in {"partial", "step_limit", "blocked"}:
        return {"passed": False, "status": "failed", "reason": reason or run.outcome}

    return {"passed": True, "status": "passed", "reason": "terminal_trace_complete"}
