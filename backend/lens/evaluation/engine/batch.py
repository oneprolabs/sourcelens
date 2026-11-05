"""Batch orchestration for offline Agent Evaluation runs."""

import logging
import uuid
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from lens.evaluation.engine.runner import (
    evaluation_node_capacity,
    select_evaluation_lensnode,
)
from lens.evaluation.models import EvaluationResult, EvaluationRun
from lens.evaluation.scoring import (
    aggregate_results,
    evaluate_case_result,
    metrics_snapshot,
    resolve_metric_configs,
)
from lens.evaluation.snapshots import freeze_run_result
from lens.models import (
    EvaluationMetric,
    LensNode,
    Run,
    RunExecution,
    Session,
)

logger = logging.getLogger(__name__)

IDEMPOTENCY_PREFIX = "evaluation"
TERMINAL_RUN_STATUSES = {
    Run.Status.DONE,
    Run.Status.FAILED,
    Run.Status.CANCELLED,
    Run.Status.AWAITING_USER_INPUT,
}


def case_run_key(evaluation_run, case):
    """Return the idempotency key for one case inside one batch."""

    return f"{IDEMPOTENCY_PREFIX}:{evaluation_run.uuid}:{case.uuid}"


def _assistant_snapshot(assistant):
    """Return the frozen assistant configuration used by the batch."""

    return {
        "uuid": str(assistant.uuid),
        "name": assistant.name,
        "slug": assistant.slug,
        "capability": assistant.capability,
        "routing_mode": assistant.routing_mode,
        "deep_thinking": assistant.deep_thinking,
        "agent_rounds": assistant.agent_rounds,
        "token_budget_profile": assistant.token_budget_profile,
        "agent_model_ref": (str(assistant.agent_model_ref) if assistant.agent_model_ref else None),
        "preprocess_model_ref": (str(assistant.preprocess_model_ref) if assistant.preprocess_model_ref else None),
        "postprocess_model_ref": (str(assistant.postprocess_model_ref) if assistant.postprocess_model_ref else None),
        "multimodal_model_ref": (str(assistant.multimodal_model_ref) if assistant.multimodal_model_ref else None),
        "skill_uuids": [
            str(binding.skill.uuid)
            for binding in assistant.skill_bindings.select_related("skill").all()
            if binding.enabled
        ],
        "mcp_uuids": [
            str(binding.mcp.uuid) for binding in assistant.mcp_bindings.select_related("mcp").all() if binding.enabled
        ],
        "datasource_binding_uuids": [str(binding.uuid) for binding in assistant.datasource_bindings.all()],
    }


def _metric_rows():
    """Return configured metric rows, falling back to built-in defaults."""

    return list(EvaluationMetric.objects.filter(enabled=True).order_by("name"))


def create_evaluation_run(
    *,
    dataset,
    requested_by=None,
    case_uuids=None,
    config=None,
):
    """Create one queued batch with a frozen dataset and metric snapshot."""

    cases = dataset.cases.all()
    if case_uuids:
        try:
            case_uuids = [str(uuid.UUID(str(value))) for value in case_uuids]
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("EVALUATION_CASES_INVALID") from exc
        cases = cases.filter(uuid__in=case_uuids)
    case_count = cases.count()
    if case_uuids and case_count != len(set(map(str, case_uuids))):
        raise ValueError("EVALUATION_CASES_INVALID")
    if case_count == 0:
        raise ValueError("EVALUATION_DATASET_EMPTY")
    metric_configs = resolve_metric_configs(_metric_rows())
    snapshot = {
        "dataset": {
            "uuid": str(dataset.uuid),
            "name": dataset.name,
            "version": dataset.version,
        },
        "assistant": _assistant_snapshot(dataset.assistant),
        "metrics": metrics_snapshot(metric_configs),
        "lensnode_uuid": None,
        "requested_by": str(requested_by.pk) if requested_by else None,
        "created_at": timezone.now().isoformat(),
        "case_uuids": [str(uuid) for uuid in cases.values_list("uuid", flat=True)],
        "case_expectations": {str(case.uuid): {"expected_answer": case.expected_answer,
            "expected_facts": case.expected_facts, "expected_sources": case.expected_sources} for case in cases},
        **(config or {}),
    }
    return EvaluationRun.objects.create(
        dataset=dataset,
        requested_by=requested_by,
        config_snapshot=snapshot,
        total_cases=case_count,
        status=EvaluationRun.Status.QUEUED,
    )


def _metric_configs_from_snapshot(evaluation_run):
    """Return the frozen metric definitions of one batch."""

    snapshot = evaluation_run.config_snapshot or {}
    metrics = snapshot.get("metrics")
    if isinstance(metrics, list) and metrics:
        return metrics
    return metrics_snapshot(resolve_metric_configs(_metric_rows()))


def _resolve_node(evaluation_run):
    """Return an online compatible evaluation node for this assistant."""

    from lens.services import _compatible_execution_lensnodes

    assistant = evaluation_run.dataset.assistant
    if assistant.lensnode_id:
        candidates = LensNode.objects.filter(
            pk=assistant.lensnode_id, pool=LensNode.Pool.EVALUATION,
            status=LensNode.Status.ONLINE, enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            token_revoked=False,
        )
    else:
        candidates = _compatible_execution_lensnodes(assistant, run_source=Run.Source.EVALUATION)
    candidates = list(candidates)
    remembered = (evaluation_run.config_snapshot or {}).get("lensnode_uuid")
    candidates.sort(key=lambda node: (str(node.uuid) != remembered, node.created_at))
    return next((node for node in candidates if evaluation_node_capacity(node) > 0), None)


def _remember_node(evaluation_run, node):
    """Persist the node assignment so capacity checks stay stable."""

    if node is None:
        return
    snapshot = dict(evaluation_run.config_snapshot or {})
    if snapshot.get("lensnode_uuid") == str(node.uuid):
        return
    snapshot["lensnode_uuid"] = str(node.uuid)
    evaluation_run.config_snapshot = snapshot
    evaluation_run.save(update_fields=["config_snapshot", "updated_at"])


def _case_session(evaluation_run, case):
    """Create one isolated evaluation session for a case."""

    assistant = evaluation_run.dataset.assistant
    return Session.objects.create(
        assistant=assistant,
        user=evaluation_run.requested_by,
        title=f"Evaluation: {case.input[:60]}",
        source=Session.Source.EVALUATION,
        title_manually_edited=True,
        title_generation_status=Session.TitleGenerationStatus.SKIPPED,
    )


def ensure_case_run(evaluation_run, case, node=None):
    """Return the case Run, creating it once per batch and case."""

    from lens.services import create_execution_run

    key = f"{case_run_key(evaluation_run, case)}:{uuid.uuid4()}"
    existing = Run.objects.filter(idempotency_key=key).first()
    if existing is not None:
        return existing
    session = _case_session(evaluation_run, case)
    return create_execution_run(
        session=session,
        question=case.input,
        idempotency_key=key,
        enqueue=False,
        run_source=Run.Source.EVALUATION,
        evaluation_lensnode=node,
    )


def _pending_results(evaluation_run):
    """Return results whose case Run has not been created yet."""

    return evaluation_run.results.filter(source_run__isnull=True).select_related("case").order_by("created_at", "uuid")


def _enqueue_run(run):
    """Enqueue one case Run on the worker queue."""

    from lens.tasks import enqueue_answer_run_task

    enqueue_answer_run_task(run.uuid, 0)


def dispatch_available_cases(evaluation_run):
    """Dispatch pending cases while the evaluation node has free capacity."""

    node = _resolve_node(evaluation_run)
    if node is None:
        return 0
    node = LensNode.objects.select_for_update().filter(
        pk=node.pk, pool=LensNode.Pool.EVALUATION, status=LensNode.Status.ONLINE,
        enrollment_status=LensNode.EnrollmentStatus.APPROVED, token_revoked=False,
    ).first()
    if node is None:
        return 0
    _remember_node(evaluation_run, node)
    remaining = evaluation_node_capacity(node)
    if remaining <= 0:
        return 0
    dispatched = 0
    for result in _pending_results(evaluation_run):
        if remaining <= 0:
            break
        try:
            run = ensure_case_run(evaluation_run, result.case, node)
        except Exception as exc:
            result.error = str(exc)
            result.save(update_fields=["error", "updated_at"])
            logger.warning(
                "evaluation %s case %s: dispatch failed: %s",
                evaluation_run.uuid,
                result.case_id,
                exc,
            )
            continue
        result.source_run = run
        result.source_run_uuid = run.uuid
        result.save(update_fields=["source_run", "source_run_uuid", "updated_at"])
        transaction.on_commit(lambda run=run: _enqueue_run(run))
        dispatched += 1
        remaining -= 1
    return dispatched


@transaction.atomic
def start_evaluation_run(evaluation_run_uuid):
    """Create case result rows and dispatch the first batch of cases."""

    evaluation_run = (
        EvaluationRun.objects.select_for_update().select_related("dataset__assistant").get(uuid=evaluation_run_uuid)
    )
    if evaluation_run.status not in {
        EvaluationRun.Status.QUEUED,
        EvaluationRun.Status.RUNNING,
    }:
        return evaluation_run
    if not evaluation_run.results.exists():
        for case in evaluation_run.dataset.cases.filter(
            uuid__in=(evaluation_run.config_snapshot or {}).get("case_uuids", [])
        ):
            EvaluationResult.objects.get_or_create(
                evaluation_run=evaluation_run,
                case=case,
            )
    evaluation_run.status = EvaluationRun.Status.RUNNING
    evaluation_run.started_at = evaluation_run.started_at or timezone.now()
    evaluation_run.save(update_fields=["status", "started_at", "updated_at"])
    dispatched = dispatch_available_cases(evaluation_run)
    if dispatched == 0:
        logger.info(
            "evaluation %s: waiting for evaluation node capacity",
            evaluation_run.uuid,
        )
    return evaluation_run


def _known_datasource_paths(evaluation_run):
    """Return datasource storage keys for best-effort citation verification."""

    from lens.models import DataSourceItem

    binding_ids = (evaluation_run.config_snapshot or {}).get("assistant", {}).get("datasource_binding_uuids") or []
    if not binding_ids:
        return None
    datasource_ids = evaluation_run.dataset.assistant.datasource_bindings.filter(uuid__in=binding_ids).values_list(
        "datasource_id", flat=True
    )
    keys = list(
        DataSourceItem.objects.filter(datasource_id__in=datasource_ids).values_list("storage_key", flat=True)[:5000]
    )
    return keys or None


@transaction.atomic
def score_case_result(result):
    """Score one finished case Run and persist the frozen evidence."""

    run = result.source_run
    if run is None:
        return result
    metric_configs = _metric_configs_from_snapshot(result.evaluation_run)
    expected = (result.evaluation_run.config_snapshot or {}).get("case_expectations", {}).get(str(result.case.uuid))
    case = SimpleNamespace(**expected) if expected is not None else result.case
    evaluation = evaluate_case_result(
        run,
        case,
        metric_configs,
        known_paths=_known_datasource_paths(result.evaluation_run),
    )
    freeze_run_result(
        evaluation_run=result.evaluation_run,
        case=result.case,
        run=run,
        scores=evaluation["scores"],
        issues=evaluation["issues"],
    )
    result.refresh_from_db()
    result.execution_metrics = evaluation["execution_metrics"]
    result.passed = evaluation["passed"]
    result.error = "" if run.status == Run.Status.DONE else (run.error or "")
    result.save(
        update_fields=[
            "execution_metrics",
            "passed",
            "error",
            "updated_at",
        ]
    )
    return result


def finalize_case_result(run_uuid):
    """Score the case Run that just reached a terminal state."""

    run = Run.objects.select_related("output_message").filter(uuid=run_uuid).first()
    if run is None:
        return None
    result = (
        EvaluationResult.objects.select_related("case", "evaluation_run__dataset__assistant")
        .filter(source_run=run)
        .first()
    )
    if result is None:
        return None
    if run.status not in TERMINAL_RUN_STATUSES:
        return result
    scored = score_case_result(result)
    advance_evaluation_run(result.evaluation_run_id)
    return scored


def _finalize_stale_results(evaluation_run):
    """Score results whose Run is terminal but was never finalized."""

    finalized = 0
    results = evaluation_run.results.select_related("source_run__output_message", "case").exclude(
        source_run__isnull=True
    )
    for result in results:
        if result.scores:
            continue
        if result.source_run.status in TERMINAL_RUN_STATUSES:
            score_case_result(result)
            finalized += 1
    return finalized


def _aggregate(evaluation_run):
    """Aggregate scored results into the batch counters and coverage."""

    results = list(evaluation_run.results.select_related("case").order_by("created_at", "uuid"))
    summary = aggregate_results(results, _metric_configs_from_snapshot(evaluation_run))
    completed = sum(1 for result in results if result.scores)
    evaluation_run.completed_cases = completed
    evaluation_run.passed_cases = summary["passed_cases"]
    evaluation_run.failed_cases = summary["failed_cases"]
    evaluation_run.aggregate_score = summary["aggregate_score"]
    evaluation_run.coverage = summary["coverage"]
    evaluation_run.save(
        update_fields=[
            "completed_cases",
            "passed_cases",
            "failed_cases",
            "aggregate_score",
            "coverage",
            "updated_at",
        ]
    )
    return summary


@transaction.atomic
def advance_evaluation_run(evaluation_run_uuid):
    """Dispatch more cases, then complete the batch when every case settled."""

    evaluation_run = (
        EvaluationRun.objects.select_for_update().select_related("dataset__assistant").get(uuid=evaluation_run_uuid)
    )
    if evaluation_run.status not in {
        EvaluationRun.Status.QUEUED,
        EvaluationRun.Status.RUNNING,
    }:
        return evaluation_run
    if evaluation_run.status == EvaluationRun.Status.QUEUED and not evaluation_run.results.exists():
        return start_evaluation_run(evaluation_run_uuid)
    _finalize_stale_results(evaluation_run)
    dispatch_available_cases(evaluation_run)
    pending = evaluation_run.results.filter(source_run__isnull=True).count()
    active = evaluation_run.results.filter(
        source_run__status__in=[
            Run.Status.QUEUED,
            Run.Status.RUNNING,
            Run.Status.STREAMING,
        ]
    ).count()
    if pending == 0 and active == 0 and evaluation_run.results.count() == evaluation_run.total_cases:
        summary = _aggregate(evaluation_run)
        evaluation_run.status = EvaluationRun.Status.COMPLETED
        evaluation_run.finished_at = timezone.now()
        evaluation_run.error = ""
        evaluation_run.save(update_fields=["status", "finished_at", "error", "updated_at"])
        logger.info(
            "evaluation %s completed: score=%s passed=%d failed=%d",
            evaluation_run.uuid,
            summary["aggregate_score"],
            summary["passed_cases"],
            summary["failed_cases"],
        )
    return evaluation_run


def _cancel_active_runs(evaluation_run, reason):
    """Cancel in-flight case Runs and freeze their current state."""

    active = evaluation_run.results.filter(
        source_run__status__in=[
            Run.Status.QUEUED,
            Run.Status.RUNNING,
            Run.Status.STREAMING,
        ]
    ).select_related("source_run")
    now = timezone.now()
    cancelled = []
    for result in active:
        run = result.source_run
        run.status = Run.Status.CANCELLED
        run.error = reason
        run.finished_at = now
        run.save(update_fields=["status", "error", "finished_at", "updated_at"])
        if hasattr(run, "execution"):
            run.execution.status = RunExecution.Status.CANCELLED
            run.execution.finished_at = now
            run.execution.save(update_fields=["status", "finished_at"])
        cancelled.append(run)
    return cancelled


@transaction.atomic
def cancel_evaluation_run(evaluation_run_uuid, reason="EVALUATION_CANCELLED"):
    """Cancel one batch and its in-flight case Runs."""

    from lens.services import cancel_run_on_lensnode

    evaluation_run = (
        EvaluationRun.objects.select_for_update().select_related("dataset__assistant").get(uuid=evaluation_run_uuid)
    )
    if evaluation_run.status in {
        EvaluationRun.Status.COMPLETED,
        EvaluationRun.Status.CANCELLED,
    }:
        return evaluation_run
    cancelled = _cancel_active_runs(evaluation_run, reason)
    evaluation_run.status = EvaluationRun.Status.CANCELLED
    evaluation_run.finished_at = timezone.now()
    evaluation_run.save(update_fields=["status", "finished_at", "updated_at"])
    transaction.on_commit(lambda runs=cancelled: [cancel_run_on_lensnode(run) for run in runs])
    return evaluation_run


@transaction.atomic
def retry_failed_cases(evaluation_run_uuid):
    """Reset failed and inconclusive cases so they run again."""

    evaluation_run = (
        EvaluationRun.objects.select_for_update().select_related("dataset__assistant").get(uuid=evaluation_run_uuid)
    )
    if evaluation_run.status in {
        EvaluationRun.Status.QUEUED,
        EvaluationRun.Status.RUNNING,
        EvaluationRun.Status.PAUSED,
    }:
        raise ValueError("EVALUATION_RUN_ACTIVE")
    retried = 0
    for result in evaluation_run.results.exclude(passed=True).exclude(source_run__isnull=True):
        result.source_run = None
        result.source_run_uuid = None
        result.scores = {}
        result.passed = None
        result.issues = []
        result.error = ""
        result.output_snapshot = ""
        result.citations_snapshot = []
        result.trace_summary = {}
        result.execution_metrics = {}
        result.save(
            update_fields=[
                "source_run",
                "source_run_uuid",
                "scores",
                "passed",
                "issues",
                "error",
                "output_snapshot",
                "citations_snapshot",
                "trace_summary",
                "execution_metrics",
                "updated_at",
            ]
        )
        retried += 1
    if retried:
        evaluation_run.status = EvaluationRun.Status.QUEUED
        evaluation_run.finished_at = None
        evaluation_run.error = ""
        evaluation_run.save(update_fields=["status", "finished_at", "error", "updated_at"])
    return retried


def reconcile_evaluation_runs():
    """Advance queued or stale batches from a periodic worker task."""

    advanced = 0
    batch_ids = EvaluationRun.objects.filter(
        status__in=[EvaluationRun.Status.QUEUED, EvaluationRun.Status.RUNNING]
    ).values_list("uuid", flat=True)
    for batch_id in list(batch_ids):
        try:
            advance_evaluation_run(batch_id)
        except Exception:
            logger.exception("evaluation %s: reconcile failed", batch_id)
            continue
        advanced += 1
    return advanced


def build_report(evaluation_run):
    """Return the live report payload for one batch."""

    results = list(evaluation_run.results.select_related("case").order_by("created_at", "uuid"))
    summary = aggregate_results(results, _metric_configs_from_snapshot(evaluation_run))
    clusters = {}
    for result in results:
        for issue in result.issues or []:
            code = str(issue.get("code") or "unknown")
            cluster = clusters.setdefault(
                code,
                {"code": code, "count": 0, "cases": []},
            )
            cluster["count"] += 1
            cluster["cases"].append(
                {
                    "uuid": str(result.case.uuid),
                    "input": (result.case.input or "")[:160],
                }
            )
    return {
        "evaluation_run": {
            "uuid": str(evaluation_run.uuid),
            "status": evaluation_run.status,
            "dataset": {
                "uuid": str(evaluation_run.dataset.uuid),
                "name": evaluation_run.dataset.name,
                "version": evaluation_run.dataset.version,
            },
            "created_at": evaluation_run.created_at,
            "started_at": evaluation_run.started_at,
            "finished_at": evaluation_run.finished_at,
            "error": evaluation_run.error,
            "config_snapshot": evaluation_run.config_snapshot,
        },
        "summary": {
            "total_cases": len(results),
            "completed_cases": sum(1 for result in results if result.scores),
            "passed_cases": summary["passed_cases"],
            "failed_cases": summary["failed_cases"],
            "inconclusive_cases": summary["inconclusive_cases"],
            "aggregate_score": summary["aggregate_score"],
            "coverage": summary["coverage"],
        },
        "metrics": summary["per_metric"],
        "failure_clusters": sorted(clusters.values(), key=lambda item: item["count"], reverse=True),
    }


def assistant_can_run_evaluation(assistant):
    """Return whether an assistant can host evaluation runs."""

    if assistant is None:
        return False
    if assistant.lensnode_id:
        return assistant.lensnode.pool == LensNode.Pool.EVALUATION
    return select_evaluation_lensnode() is not None


__all__ = [
    "advance_evaluation_run",
    "assistant_can_run_evaluation",
    "build_report",
    "cancel_evaluation_run",
    "case_run_key",
    "create_evaluation_run",
    "ensure_case_run",
    "finalize_case_result",
    "reconcile_evaluation_runs",
    "retry_failed_cases",
    "start_evaluation_run",
]
