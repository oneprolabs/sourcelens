"""Freeze evaluation evidence before the source Run is retained away."""

from django.conf import settings

from lens.evaluation.evaluators.trace import load_trace_events
from lens.evaluation.scoring import EVALUATION_SCORING_VERSION
from lens.models import EvaluationResult

TRACE_SUMMARY_LIMIT = 200

_COMMON_PAYLOAD_KEYS = ("status", "error", "error_code", "reason")
_TRACE_PAYLOAD_KEYS = {
    "model.started": ("model",),
    "model.completed": ("model", "usage", "ttft_ms", "duration_ms", "finish_reason"),
    "model.failed": ("model", "error", "error_code", "duration_ms"),
    "tool.started": ("tool", "name", "call_id"),
    "tool.completed": ("tool", "name", "status", "duration_ms"),
    "tool.failed": ("tool", "name", "error", "error_code", "duration_ms"),
    "route.selected": ("mode", "evidence_requirement", "assistant_uuid"),
    "deepagents.evidence.verified": ("action", "reason"),
    "step.event": (
        "name",
        "action",
        "reason",
        "status",
        "tool",
        "call_id",
        "duration_ms",
        "error",
        "error_code",
    ),
}


def _compact_payload(event_type, payload):
    """Return only the payload fields evaluation reports actually read."""

    if not isinstance(payload, dict):
        return {}
    keys = _TRACE_PAYLOAD_KEYS.get(event_type, _COMMON_PAYLOAD_KEYS)
    return {key: payload[key] for key in keys if key in payload}


def trace_summary_from_events(events):
    """Build a bounded, JSON-safe trace summary from already loaded events."""

    rows = []
    for event in events:
        timestamp = getattr(event, "timestamp", None)
        event_type = getattr(event, "event_type", "") or ""
        rows.append(
            {
                "sequence": getattr(event, "sequence", None),
                "event_type": event_type,
                "timestamp": timestamp.isoformat() if timestamp else None,
                "turn": getattr(event, "turn", None),
                "step": getattr(event, "step", None),
                "payload": _compact_payload(event_type, getattr(event, "payload", None)),
            }
        )
    return {
        "event_count": len(rows),
        "last_sequence": rows[-1]["sequence"] if rows else None,
        "first_event_at": rows[0]["timestamp"] if rows else None,
        "last_event_at": rows[-1]["timestamp"] if rows else None,
        "event_types": sorted({row["event_type"] for row in rows}),
        "events": rows[:TRACE_SUMMARY_LIMIT],
        "truncated": len(rows) > TRACE_SUMMARY_LIMIT,
    }


def _version_metrics(run):
    """Return the code versions that produced one frozen case Run."""

    execution = getattr(run, "execution", None)
    runtime_snapshot = (execution.runtime_snapshot if execution else {}) or {}
    lensnode_version = runtime_snapshot.get("lensnode_agent_version") or ""
    if not lensnode_version:
        lensnode = getattr(run, "lensnode", None)
        if lensnode is not None:
            lensnode_version = lensnode.agent_version or ""
    return {
        "platform_version": getattr(settings, "APP_VERSION", "") or "",
        "lensnode_agent_version": lensnode_version,
        "scoring_version": EVALUATION_SCORING_VERSION,
    }


def _trace_summary(run):
    """Build a bounded, JSON-safe trace summary for an evaluation result."""

    return trace_summary_from_events(load_trace_events(run))


def freeze_run_result(
    *,
    evaluation_run,
    case,
    run,
    scores=None,
    issues=None,
    judge_evidence=None,
    trace_summary=None,
):
    """Persist a complete evaluation result that survives source Run deletion."""

    output = run.output_message.content if run.output_message_id else ""
    summary = trace_summary if isinstance(trace_summary, dict) else _trace_summary(run)
    metrics = {
        "trace_events": summary.get("event_count", 0),
        "last_sequence": summary.get("last_sequence"),
    }
    return EvaluationResult.objects.update_or_create(
        evaluation_run=evaluation_run,
        case=case,
        defaults={
            "source_run": run,
            "source_run_uuid": run.uuid,
            "output_snapshot": output or "",
            "citations_snapshot": run.citations or [],
            "trace_summary": summary,
            "execution_metrics": {
                "status": run.status,
                "outcome_reference": run.outcome or "",
                **_version_metrics(run),
                **metrics,
            },
            "scores": scores or {},
            "issues": issues or [],
            "judge_evidence": judge_evidence or {},
        },
    )[0]


def freeze_evaluation_results_for_runs(run_ids):
    """Backfill missing snapshots before retention deletes source Runs."""

    if not run_ids:
        return 0
    results = EvaluationResult.objects.filter(
        source_run_id__in=run_ids,
    ).select_related("source_run", "source_run__output_message")
    frozen = 0
    for result in results:
        run = result.source_run
        if run is None:
            continue
        result.output_snapshot = result.output_snapshot or (run.output_message.content if run.output_message_id else "")
        result.citations_snapshot = result.citations_snapshot or run.citations or []
        result.trace_summary = result.trace_summary or _trace_summary(run)
        execution_metrics = dict(result.execution_metrics or {})
        execution_metrics.setdefault("status", run.status)
        execution_metrics.setdefault("outcome_reference", run.outcome or "")
        for key, value in _version_metrics(run).items():
            execution_metrics.setdefault(key, value)
        result.execution_metrics = execution_metrics
        result.source_run_uuid = result.source_run_uuid or run.uuid
        result.save(
            update_fields=[
                "output_snapshot",
                "citations_snapshot",
                "trace_summary",
                "execution_metrics",
                "source_run_uuid",
                "updated_at",
            ]
        )
        frozen += 1
    return frozen
