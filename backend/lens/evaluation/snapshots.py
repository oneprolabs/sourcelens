"""Freeze evaluation evidence before the source Run is retained away."""

from django.db.models import Count, Max

from lens.models import EvaluationResult, RunTraceEvent


def _trace_summary(run):
    """Build a bounded, JSON-safe trace summary for an evaluation result."""

    rows = list(
        RunTraceEvent.objects.filter(run=run)
        .order_by("sequence")
        .values("sequence", "event_type", "timestamp", "turn", "step", "payload")
    )
    events = [
        {
            **item,
            "timestamp": (item["timestamp"].isoformat() if item["timestamp"] else None),
        }
        for item in rows
    ]
    summary = {
        "event_count": len(events),
        "first_event_at": events[0]["timestamp"] if events else None,
        "last_event_at": events[-1]["timestamp"] if events else None,
        "event_types": sorted({item["event_type"] for item in events}),
        "events": events[:200],
    }
    return summary


def freeze_run_result(*, evaluation_run, case, run, scores=None, issues=None, judge_evidence=None):
    """Persist a complete evaluation result that survives source Run deletion."""

    output = run.output_message.content if run.output_message_id else ""
    metrics = RunTraceEvent.objects.filter(run=run).aggregate(
        trace_events=Count("pk"),
        last_sequence=Max("sequence"),
    )
    return EvaluationResult.objects.update_or_create(
        evaluation_run=evaluation_run,
        case=case,
        defaults={
            "source_run": run,
            "source_run_uuid": run.uuid,
            "output_snapshot": output or "",
            "citations_snapshot": run.citations or [],
            "trace_summary": _trace_summary(run),
            "execution_metrics": {
                "status": run.status,
                "outcome_reference": run.outcome or "",
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
        result.execution_metrics = result.execution_metrics or {
            "status": run.status,
            "outcome_reference": run.outcome or "",
        }
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
