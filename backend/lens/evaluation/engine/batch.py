"""Batch orchestration for offline Agent Evaluation runs."""

import logging
import uuid
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from lens.evaluation.calibration import judge_gate, summarize_calibration
from lens.evaluation.engine.runner import (
    destination_nodes_with_capacity,
    evaluation_candidate_nodes,
    evaluation_execution_pool,
    evaluation_node_capacity,
)
from lens.evaluation.evaluators.trace import load_trace_events
from lens.evaluation.judge import judge_config, judge_result
from lens.evaluation.models import EvaluationResult, EvaluationRun
from lens.evaluation.scoring import (
    EVALUATION_SCORING_VERSION,
    aggregate_results,
    apply_judge_verdict,
    ensure_default_metrics,
    evaluate_case_result,
    metrics_snapshot,
    resolve_metric_configs,
)
from lens.evaluation.snapshots import (
    freeze_evaluation_results_for_runs,
    freeze_run_result,
    trace_summary_from_events,
)
from lens.models import (
    EvaluationMetric,
    GlobalSetting,
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
WAITING_NO_NODE = "NO_EVALUATION_NODE"
WAITING_NODE_BUSY = "EVALUATION_NODE_BUSY"
DEFAULT_NODE_WAIT_TIMEOUT_SECONDS = 1800
DEFAULT_NODE_BUSY_TIMEOUT_SECONDS = 7200
MAX_DISPATCH_ATTEMPTS = 3
QUEUED_REENQUEUE_SECONDS = 900


def case_run_key(evaluation_run, case):
    """Return the idempotency key for one case inside one batch."""

    key = f"{IDEMPOTENCY_PREFIX}:{evaluation_run.uuid}:{case.uuid}"
    retry_key = (evaluation_run.config_snapshot or {}).get("case_retry_keys", {}).get(str(case.uuid))
    return f"{key}:{retry_key}" if retry_key else key


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
    """Return all configured metric rows so disabled ones stay disabled."""

    ensure_default_metrics()
    return list(EvaluationMetric.objects.all().order_by("name"))


@transaction.atomic
def create_evaluation_run(
    *,
    dataset,
    requested_by=None,
    case_uuids=None,
    config=None,
):
    """Create one queued batch with a frozen dataset and metric snapshot."""

    from lens.evaluation.models import EvaluationDataset

    dataset = EvaluationDataset.objects.select_for_update().get(pk=dataset.pk)
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
    runtime_config = dict(config or {})
    label = str(runtime_config.pop("label", "") or "")
    snapshot = {
        "dataset": {
            "uuid": str(dataset.uuid),
            "name": dataset.name,
            "version": dataset.version,
            "revision": dataset.revisions.order_by("-revision").values_list("revision", flat=True).first(),
        },
        "assistant": _assistant_snapshot(dataset.assistant),
        "metrics": metrics_snapshot(metric_configs),
        "judge": judge_config(),
        "lensnode_uuid": None,
        "requested_by": str(requested_by.pk) if requested_by else None,
        "created_at": timezone.now().isoformat(),
        "platform_version": getattr(settings, "APP_VERSION", "") or "",
        "scoring_version": EVALUATION_SCORING_VERSION,
        "case_uuids": [str(uuid) for uuid in cases.values_list("uuid", flat=True)],
        "case_expectations": {
            str(case.uuid): {
                "input": case.input,
                "expected_answer": case.expected_answer,
                "expected_facts": case.expected_facts,
                "answer_match_mode": (case.config or {}).get("answer_match_mode"),
                "expected_sources": case.expected_sources,
                "baseline": (case.config or {}).get("baseline"),
            }
            for case in cases
        },
        "label": label,
        "options": runtime_config,
    }
    evaluation_run = EvaluationRun.objects.create(
        dataset=dataset,
        requested_by=requested_by,
        config_snapshot=snapshot,
        total_cases=case_count,
        status=EvaluationRun.Status.QUEUED,
    )
    EvaluationResult.objects.bulk_create(
        [EvaluationResult(evaluation_run=evaluation_run, case=case) for case in cases]
    )
    return evaluation_run


def _metric_configs_from_snapshot(evaluation_run):
    """Return the frozen metric definitions of one batch."""

    snapshot = evaluation_run.config_snapshot or {}
    metrics = snapshot.get("metrics")
    if isinstance(metrics, list):
        return metrics
    return metrics_snapshot(resolve_metric_configs(_metric_rows()))


def _select_node(evaluation_run):
    """Return the next dispatch node and a waiting reason when none is free.

    Only nodes in the globally configured pool are considered.
    """

    assistant = evaluation_run.dataset.assistant
    if not evaluation_candidate_nodes(assistant):
        return None, WAITING_NO_NODE
    available = destination_nodes_with_capacity(assistant)
    if not available:
        return None, WAITING_NODE_BUSY
    remembered = (evaluation_run.config_snapshot or {}).get("lensnode_uuid")
    available.sort(key=lambda node: str(node.uuid) != remembered)
    return available[0], ""


def _remember_node(evaluation_run, node):
    """Persist node provenance so reports survive later node changes."""

    if node is None:
        return
    snapshot = dict(evaluation_run.config_snapshot or {})
    provenance = {
        "lensnode_uuid": str(node.uuid),
        "lensnode_pool": node.pool,
        "lensnode_agent_version": node.agent_version or "",
        "lensnode_protocol_version": node.protocol_version or "",
    }
    changed = {
        key: value
        for key, value in provenance.items()
        if snapshot.get(key) != value
    }
    if not changed:
        return
    snapshot.update(changed)
    evaluation_run.config_snapshot = snapshot
    evaluation_run.save(update_fields=["config_snapshot", "updated_at"])


def _setting_seconds(key, default):
    """Return a positive integer GlobalSetting value or the default."""

    setting = GlobalSetting.objects.filter(key=key).first()
    if setting is None:
        return default
    try:
        value = int(setting.value)
    except (TypeError, ValueError):
        return default
    return max(value, 1)


def _waiting_timeout_seconds():
    """Return how long a batch may wait for a missing node before failing."""

    return _setting_seconds(
        "evaluation.node_wait_timeout_s",
        DEFAULT_NODE_WAIT_TIMEOUT_SECONDS,
    )


def _busy_timeout_seconds():
    """Return how long a batch may queue behind a full node before failing."""

    return _setting_seconds(
        "evaluation.node_busy_timeout_s",
        DEFAULT_NODE_BUSY_TIMEOUT_SECONDS,
    )


def _record_waiting(evaluation_run, reason):
    """Persist or clear the current waiting reason for the report UI.

    A reason change keeps the original ``since`` so a flapping node cannot
    restart the expiry clock forever.
    """

    snapshot = dict(evaluation_run.config_snapshot or {})
    current = snapshot.get("waiting")
    if not reason:
        if snapshot.pop("waiting", None) is None:
            return
    else:
        if isinstance(current, dict) and current.get("reason") == reason:
            return
        previous_since = current.get("since") if isinstance(current, dict) else None
        snapshot["waiting"] = {
            "reason": reason,
            "since": previous_since or timezone.now().isoformat(),
        }
    evaluation_run.config_snapshot = snapshot
    evaluation_run.save(update_fields=["config_snapshot", "updated_at"])
    if reason:
        logger.warning(
            "evaluation %s: waiting for node (%s)",
            evaluation_run.uuid,
            reason,
        )


def _expire_unserviceable_batch(evaluation_run):
    """Fail batches that waited too long without any case actually executing.

    A batch with running cases is progressing even when the node has no free
    slot for the next case, so capacity pressure alone must not expire it.
    """

    waiting = (evaluation_run.config_snapshot or {}).get("waiting")
    if not isinstance(waiting, dict):
        return False
    runs = Run.objects.select_for_update().filter(
        pk__in=evaluation_run.results.values("source_run_id")
    )
    executing = any(run.status in {Run.Status.RUNNING, Run.Status.STREAMING} for run in runs)
    if executing:
        _record_waiting(evaluation_run, "")
        return False
    reason = waiting.get("reason")
    if reason == WAITING_NO_NODE:
        timeout_seconds = _waiting_timeout_seconds()
        error = "EVALUATION_NODE_UNAVAILABLE"
    elif reason == WAITING_NODE_BUSY:
        timeout_seconds = _busy_timeout_seconds()
        error = "EVALUATION_NODE_BUSY_TIMEOUT"
    else:
        return False
    since = parse_datetime(str(waiting.get("since") or ""))
    if since is None:
        return False
    if timezone.is_naive(since):
        since = timezone.make_aware(since, timezone.utc)
    if timezone.now() - since < timedelta(seconds=timeout_seconds):
        return False
    from lens.services import cancel_run_on_lensnode

    cancelled = _cancel_active_runs(evaluation_run, error)
    transaction.on_commit(lambda runs=cancelled: [cancel_run_on_lensnode(run) for run in runs])
    evaluation_run.status = EvaluationRun.Status.FAILED
    evaluation_run.error = error
    evaluation_run.finished_at = timezone.now()
    evaluation_run.save(
        update_fields=["status", "error", "finished_at", "updated_at"]
    )
    logger.warning(
        "evaluation %s: failed after waiting %.0fs (%s)",
        evaluation_run.uuid,
        (timezone.now() - since).total_seconds(),
        reason,
    )
    return True


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

    key = case_run_key(evaluation_run, case)
    existing = Run.objects.filter(idempotency_key=key).first()
    if existing is not None:
        return existing
    session = _case_session(evaluation_run, case)
    frozen = (evaluation_run.config_snapshot or {}).get("case_expectations", {}).get(str(case.uuid), {})
    return create_execution_run(
        session=session,
        question=frozen.get("input", case.input),
        idempotency_key=key,
        enqueue=False,
        run_source=Run.Source.EVALUATION,
        evaluation_lensnode=node,
    )


def _pending_results(evaluation_run):
    """Return results that still need a case Run.

    ``source_run_uuid`` is retained after retention deletes the source Run, so a
    result that already executed must never be dispatched again. Results that
    exhausted their dispatch attempts are permanently failed instead of being
    retried forever.
    """

    return (
        evaluation_run.results.filter(
            source_run__isnull=True,
            source_run_uuid__isnull=True,
            dispatch_attempts__lt=MAX_DISPATCH_ATTEMPTS,
        )
        .select_related("case")
        .order_by("created_at", "uuid")
    )


def _enqueue_run(run):
    """Enqueue one case Run on the worker queue."""

    from lens.tasks import enqueue_answer_run_task

    enqueue_answer_run_task(run.uuid, 0)


def dispatch_available_cases(evaluation_run):
    """Dispatch pending cases while an evaluation node has free capacity."""

    node, waiting_reason = _select_node(evaluation_run)
    if node is None:
        _record_waiting(evaluation_run, waiting_reason)
        return 0
    node = LensNode.objects.select_for_update().filter(
        pk=node.pk,
        pool=evaluation_execution_pool(),
        status=LensNode.Status.ONLINE,
        enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        token_revoked=False,
    ).first()
    if node is None:
        _record_waiting(evaluation_run, WAITING_NO_NODE)
        return 0
    _remember_node(evaluation_run, node)
    remaining = evaluation_node_capacity(node)
    if remaining <= 0:
        _record_waiting(evaluation_run, WAITING_NODE_BUSY)
        return 0
    _record_waiting(evaluation_run, "")
    dispatched = 0
    for result in _pending_results(evaluation_run):
        if remaining <= 0:
            break
        try:
            run = ensure_case_run(evaluation_run, result.case, node)
        except Exception as exc:
            attempts = (result.dispatch_attempts or 0) + 1
            result.dispatch_attempts = attempts
            result.error = f"EVALUATION_DISPATCH_FAILED: {str(exc)[:500]}"
            if attempts >= MAX_DISPATCH_ATTEMPTS:
                result.passed = False
                result.execution_metrics = {
                    "dispatch_failed": True,
                    "attempts": attempts,
                }
            result.save(
                update_fields=[
                    "dispatch_attempts",
                    "error",
                    "passed",
                    "execution_metrics",
                    "updated_at",
                ]
            )
            logger.warning(
                "evaluation %s case %s: dispatch failed (attempt %d/%d): %s",
                evaluation_run.uuid,
                result.case_id,
                attempts,
                MAX_DISPATCH_ATTEMPTS,
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
    result_count = evaluation_run.results.count()
    if result_count == 0 or result_count != evaluation_run.total_cases:
        evaluation_run.status = EvaluationRun.Status.FAILED
        evaluation_run.error = (
            "EVALUATION_CASES_MISSING" if evaluation_run.total_cases else "EVALUATION_DATASET_EMPTY"
        )
        evaluation_run.finished_at = timezone.now()
        evaluation_run.save(
            update_fields=["status", "error", "finished_at", "updated_at"]
        )
        logger.warning(
            "evaluation %s: frozen case results do not match the batch",
            evaluation_run.uuid,
        )
        return evaluation_run
    evaluation_run.status = EvaluationRun.Status.RUNNING
    evaluation_run.started_at = evaluation_run.started_at or timezone.now()
    evaluation_run.save(
        update_fields=["status", "started_at", "updated_at"]
    )
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
    queryset = DataSourceItem.objects.filter(datasource_id__in=datasource_ids)
    manifest_limit = 5000
    if queryset.count() > manifest_limit:
        # A truncated manifest would mark valid citations as unresolved, so skip
        # the existence check instead of guessing.
        return None
    keys = list(
        queryset.order_by("storage_key").values_list("storage_key", flat=True)[:manifest_limit]
    )
    return keys or None


def _judge_case_result(result, run, case, evaluation):
    """Return Judge evidence for one scored case without raising on failure.

    The Judge is an optional enrichment: a non-DONE Run, a missing model, a
    failed call, or invalid output becomes a status payload instead of an
    exception, so deterministic scores stay authoritative.
    """

    if run.status != Run.Status.DONE:
        return {"status": "skipped", "reason": "run_not_done"}
    judge_settings = (result.evaluation_run.config_snapshot or {}).get("judge")
    if not isinstance(judge_settings, dict):
        judge_settings = judge_config()
    model_ref = str(judge_settings.get("model_ref") or "").strip()
    if not model_ref:
        return {
            "status": "not_configured",
            "error_code": "EVALUATION_JUDGE_NOT_CONFIGURED",
        }
    output = run.output_message.content if run.output_message_id else ""
    return judge_result(
        case=case,
        output=output,
        citations=run.citations or [],
        execution_metrics=evaluation["execution_metrics"],
        model_ref=model_ref,
        secondary_model_ref=judge_settings.get("secondary_model_ref") or "",
        user_id=result.evaluation_run.requested_by_id,
    )


def score_case_result(result):
    """Score one finished case Run and persist the frozen evidence.

    The Judge call runs before the write block so a slow model never holds a
    database transaction open.
    """

    run = result.source_run
    if run is None:
        return result
    metric_configs = _metric_configs_from_snapshot(result.evaluation_run)
    expected = (result.evaluation_run.config_snapshot or {}).get("case_expectations", {}).get(str(result.case.uuid))
    case = SimpleNamespace(**expected) if expected is not None else result.case
    events = load_trace_events(run)
    evaluation = evaluate_case_result(
        run,
        case,
        metric_configs,
        known_paths=_known_datasource_paths(result.evaluation_run),
        events=events,
    )
    judge_evidence = _judge_case_result(result, run, case, evaluation)
    apply_judge_verdict(evaluation, judge_evidence)
    with transaction.atomic():
        freeze_run_result(
            evaluation_run=result.evaluation_run,
            case=result.case,
            run=run,
            scores=evaluation["scores"],
            issues=evaluation["issues"],
            judge_evidence=judge_evidence,
            trace_summary=trace_summary_from_events(events),
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


def finalize_evaluation_results_for_retention(run_ids):
    """Score unscored case results before retention deletes their source Runs."""

    if not run_ids:
        return 0
    results = (
        EvaluationResult.objects.filter(source_run_id__in=run_ids, scores={})
        .select_related(
            "case",
            "source_run__output_message",
            "evaluation_run__dataset__assistant",
        )
    )
    scored = 0
    for result in results:
        if result.source_run is None:
            continue
        frozen = {
            "output_snapshot": result.output_snapshot,
            "citations_snapshot": result.citations_snapshot,
            "trace_summary": result.trace_summary,
        }
        try:
            score_case_result(result)
        except Exception:
            logger.exception(
                "evaluation %s case %s: pre-retention scoring failed",
                result.evaluation_run_id,
                result.case_id,
            )
            continue
        result.refresh_from_db()
        restore = []
        for field, value in frozen.items():
            if value and getattr(result, field) != value:
                setattr(result, field, value)
                restore.append(field)
        if restore:
            result.save(update_fields=[*restore, "updated_at"])
        scored += 1
    freeze_evaluation_results_for_runs(run_ids)
    return scored


def _is_settled_result(result):
    """Return True when a case result finished or permanently failed."""

    return bool(result.scores) or (result.dispatch_attempts or 0) >= MAX_DISPATCH_ATTEMPTS


def _legacy_overall(evaluation_run):
    """Return True when a batch was frozen under an older scoring version."""

    snapshot = evaluation_run.config_snapshot or {}
    return str(snapshot.get("scoring_version") or "") != EVALUATION_SCORING_VERSION


def _aggregate(evaluation_run):
    """Aggregate scored results into the batch counters and coverage."""

    results = list(evaluation_run.results.select_related("case").order_by("created_at", "uuid"))
    summary = aggregate_results(
        results,
        _metric_configs_from_snapshot(evaluation_run),
        legacy_overall=_legacy_overall(evaluation_run),
    )
    completed = sum(1 for result in results if _is_settled_result(result))
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
        EvaluationRun.Status.PAUSED,
    }:
        return evaluation_run
    if evaluation_run.status == EvaluationRun.Status.PAUSED:
        # Drain semantics: in-flight cases still finish and get scored, but no
        # new case is dispatched until the batch is resumed.
        _finalize_stale_results(evaluation_run)
        _aggregate(evaluation_run)
        return evaluation_run
    if evaluation_run.status == EvaluationRun.Status.QUEUED:
        return start_evaluation_run(evaluation_run_uuid)
    _finalize_stale_results(evaluation_run)
    dispatch_available_cases(evaluation_run)
    if _expire_unserviceable_batch(evaluation_run):
        return evaluation_run
    pending = _pending_results(evaluation_run).count()
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
    """Cancel in-flight case Runs without overwriting a Run that just finished."""

    active_statuses = [
        Run.Status.QUEUED,
        Run.Status.RUNNING,
        Run.Status.STREAMING,
    ]
    active = evaluation_run.results.filter(
        source_run__status__in=active_statuses
    ).select_related("source_run")
    now = timezone.now()
    cancelled = []
    for result in active:
        run = result.source_run
        updated = Run.objects.filter(
            pk=run.pk,
            status__in=active_statuses,
        ).update(
            status=Run.Status.CANCELLED,
            error=reason,
            finished_at=now,
            updated_at=now,
        )
        if not updated:
            continue
        RunExecution.objects.filter(
            run=run,
            status__in=[
                RunExecution.Status.QUEUED,
                RunExecution.Status.DISPATCHED,
                RunExecution.Status.RUNNING,
            ],
        ).update(status=RunExecution.Status.CANCELLED, finished_at=now)
        run.refresh_from_db()
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
def pause_evaluation_run(evaluation_run_uuid, reason=""):
    """Stop dispatching new cases while in-flight cases drain and get scored."""

    evaluation_run = EvaluationRun.objects.select_for_update().get(uuid=evaluation_run_uuid)
    if evaluation_run.status not in {
        EvaluationRun.Status.QUEUED,
        EvaluationRun.Status.RUNNING,
    }:
        raise ValueError("EVALUATION_RUN_NOT_PAUSABLE")
    snapshot = dict(evaluation_run.config_snapshot or {})
    snapshot["pause"] = {
        "paused_at": timezone.now().isoformat(),
        "reason": str(reason or "")[:200],
    }
    snapshot.pop("waiting", None)
    evaluation_run.config_snapshot = snapshot
    evaluation_run.status = EvaluationRun.Status.PAUSED
    evaluation_run.save(update_fields=["config_snapshot", "status", "updated_at"])
    logger.info("evaluation %s paused", evaluation_run.uuid)
    return evaluation_run


@transaction.atomic
def resume_evaluation_run(evaluation_run_uuid):
    """Mark a paused batch active again; the caller dispatches the remainder."""

    evaluation_run = EvaluationRun.objects.select_for_update().get(uuid=evaluation_run_uuid)
    if evaluation_run.status != EvaluationRun.Status.PAUSED:
        raise ValueError("EVALUATION_RUN_NOT_PAUSED")
    snapshot = dict(evaluation_run.config_snapshot or {})
    pause = snapshot.pop("pause", None)
    if isinstance(pause, dict):
        snapshot["last_pause"] = pause
    snapshot.pop("waiting", None)
    evaluation_run.config_snapshot = snapshot
    evaluation_run.status = (
        EvaluationRun.Status.RUNNING if evaluation_run.started_at else EvaluationRun.Status.QUEUED
    )
    evaluation_run.error = ""
    evaluation_run.save(update_fields=["config_snapshot", "status", "error", "updated_at"])
    logger.info("evaluation %s resumed", evaluation_run.uuid)
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
    retryable = evaluation_run.results.exclude(passed=True)
    if evaluation_run.status == EvaluationRun.Status.COMPLETED:
        retryable = retryable.exclude(
            source_run__isnull=True,
            source_run_uuid__isnull=True,
            dispatch_attempts=0,
        )
    snapshot = dict(evaluation_run.config_snapshot or {})
    retry_keys = dict(snapshot.get("case_retry_keys") or {})
    for result in retryable:
        retry_keys[str(result.case_id)] = str(uuid.uuid4())
        result.source_run = None
        result.source_run_uuid = None
        result.dispatch_attempts = 0
        result.scores = {}
        result.passed = None
        result.issues = []
        result.error = ""
        result.output_snapshot = ""
        result.citations_snapshot = []
        result.trace_summary = {}
        result.execution_metrics = {}
        result.judge_evidence = {}
        result.save(
            update_fields=[
                "source_run",
                "source_run_uuid",
                "dispatch_attempts",
                "scores",
                "passed",
                "issues",
                "error",
                "output_snapshot",
                "citations_snapshot",
                "trace_summary",
                "execution_metrics",
                "judge_evidence",
                "updated_at",
            ]
        )
        retried += 1
    if retried:
        snapshot["case_retry_keys"] = retry_keys
        snapshot.pop("waiting", None)
        evaluation_run.config_snapshot = snapshot
        evaluation_run.status = EvaluationRun.Status.QUEUED
        evaluation_run.finished_at = None
        evaluation_run.error = ""
        evaluation_run.save(update_fields=["config_snapshot", "status", "finished_at", "error", "updated_at"])
    return retried


def _reenqueue_stuck_case_runs(evaluation_run, *, stale_seconds=QUEUED_REENQUEUE_SECONDS):
    """Re-publish Celery work for queued case Runs whose message was lost."""

    cutoff = timezone.now() - timedelta(seconds=stale_seconds)
    stuck = (
        evaluation_run.results.filter(
            source_run__status=Run.Status.QUEUED,
            source_run__created_at__lt=cutoff,
            source_run__execution__status=RunExecution.Status.QUEUED,
            source_run__execution__dispatch_id__isnull=True,
        )
        .select_related("source_run")
    )
    reenqueued = 0
    for result in stuck:
        run = result.source_run
        if run is None:
            continue
        try:
            _enqueue_run(run)
        except Exception:
            logger.exception(
                "evaluation %s: failed to re-enqueue run %s",
                evaluation_run.uuid,
                run.uuid,
            )
            continue
        reenqueued += 1
    if reenqueued:
        logger.info(
            "evaluation %s: re-enqueued %d queued case runs",
            evaluation_run.uuid,
            reenqueued,
        )
    return reenqueued


def reconcile_evaluation_runs():
    """Advance queued or stale batches and recover lost case dispatches."""

    advanced = 0
    reenqueued = 0
    batch_ids = EvaluationRun.objects.filter(
        status__in=[
            EvaluationRun.Status.QUEUED,
            EvaluationRun.Status.RUNNING,
            EvaluationRun.Status.PAUSED,
        ]
    ).values_list("uuid", flat=True)
    for batch_id in list(batch_ids):
        try:
            evaluation_run = advance_evaluation_run(batch_id)
        except Exception:
            logger.exception("evaluation %s: reconcile failed", batch_id)
            continue
        advanced += 1
        if evaluation_run.status in {
            EvaluationRun.Status.QUEUED,
            EvaluationRun.Status.RUNNING,
            EvaluationRun.Status.PAUSED,
        }:
            try:
                reenqueued += _reenqueue_stuck_case_runs(evaluation_run)
            except Exception:
                logger.exception(
                    "evaluation %s: re-enqueue sweep failed",
                    batch_id,
                )
    if reenqueued:
        logger.info("evaluation reconcile: re-enqueued %d runs", reenqueued)
    return advanced


def _judge_summary(results, snapshot):
    """Summarize Judge coverage so an unconfigured model is explicit."""

    settings_snapshot = snapshot.get("judge")
    if not isinstance(settings_snapshot, dict):
        settings_snapshot = {}
    statuses = {}
    inter_judge = {"comparable_cases": 0, "answer_agree": 0, "evidence_agree": 0}
    for result in results:
        evidence = result.judge_evidence or {}
        status = str(evidence.get("status") or "missing")
        statuses[status] = statuses.get(status, 0) + 1
        agreement = evidence.get("inter_judge")
        if not isinstance(agreement, dict) or agreement.get("status") != "ok":
            continue
        inter_judge["comparable_cases"] += 1
        inter_judge["answer_agree"] += 1 if agreement.get("answer_agree") is True else 0
        inter_judge["evidence_agree"] += 1 if agreement.get("evidence_agree") is True else 0
    model_ref = str(settings_snapshot.get("model_ref") or "")
    return {
        "configured": bool(model_ref),
        "model_ref": model_ref,
        "secondary_model_ref": str(settings_snapshot.get("secondary_model_ref") or ""),
        "prompt_version": settings_snapshot.get("prompt_version") or "",
        "statuses": statuses,
        "judged_cases": statuses.get("ok", 0),
        "failed_cases": statuses.get("judge_failed", 0),
        "inter_judge": inter_judge,
    }


def build_report(evaluation_run):
    """Return the live report payload for one batch."""

    results = list(
        evaluation_run.results.select_related("case")
        .prefetch_related("calibrations")
        .order_by("created_at", "uuid")
    )
    summary = aggregate_results(
        results,
        _metric_configs_from_snapshot(evaluation_run),
        legacy_overall=_legacy_overall(evaluation_run),
    )
    calibration = summarize_calibration(results)
    judge_summary = _judge_summary(results, evaluation_run.config_snapshot or {})
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
    snapshot = evaluation_run.config_snapshot or {}
    pause = snapshot.get("pause")
    if not isinstance(pause, dict):
        pause = {}
    return {
        "evaluation_run": {
            "uuid": str(evaluation_run.uuid),
            "status": evaluation_run.status,
            "dataset": {
                "uuid": str(evaluation_run.dataset.uuid),
                "name": snapshot.get("dataset", {}).get("name", evaluation_run.dataset.name),
                "version": snapshot.get("dataset", {}).get("version", evaluation_run.dataset.version),
                "revision": snapshot.get("dataset", {}).get("revision"),
            },
            "created_at": evaluation_run.created_at,
            "started_at": evaluation_run.started_at,
            "finished_at": evaluation_run.finished_at,
            "error": evaluation_run.error,
            "config_snapshot": snapshot,
        },
        "calibration": calibration,
        "judge": judge_summary,
        "judge_gate": judge_gate(calibration, judge_summary["configured"]),
        "provenance": {
            "platform_version": snapshot.get("platform_version", ""),
            "scoring_version": snapshot.get("scoring_version", ""),
            "lensnode": {
                "uuid": snapshot.get("lensnode_uuid") or "",
                "pool": snapshot.get("lensnode_pool") or "",
                "agent_version": snapshot.get("lensnode_agent_version") or "",
            },
            "waiting": snapshot.get("waiting") or {},
            "paused_at": pause.get("paused_at") or "",
            "pause_reason": pause.get("reason") or "",
        },
        "summary": {
            "total_cases": len(results),
            "completed_cases": sum(1 for result in results if _is_settled_result(result)),
            "passed_cases": summary["passed_cases"],
            "failed_cases": summary["failed_cases"],
            "inconclusive_cases": summary["inconclusive_cases"],
            "aggregate_score": summary["aggregate_score"],
            "quality_score": summary["quality_score"],
            "health_score": summary["health_score"],
            "quality_suppressed": summary["quality_suppressed"],
            "health_suppressed": summary["health_suppressed"],
            "quality_conclusion": summary["quality_conclusion"],
            "legacy_overall": summary["legacy_overall"],
            "coverage": summary["coverage"],
        },
        "metrics": summary["per_metric"],
        "failure_clusters": sorted(clusters.values(), key=lambda item: item["count"], reverse=True),
    }


def assistant_can_run_evaluation(assistant):
    """Return whether an assistant has any online node it can evaluate on."""

    if assistant is None:
        return False
    return bool(evaluation_candidate_nodes(assistant))


__all__ = [
    "advance_evaluation_run",
    "assistant_can_run_evaluation",
    "build_report",
    "cancel_evaluation_run",
    "case_run_key",
    "create_evaluation_run",
    "ensure_case_run",
    "finalize_case_result",
    "finalize_evaluation_results_for_retention",
    "pause_evaluation_run",
    "reconcile_evaluation_runs",
    "resume_evaluation_run",
    "retry_failed_cases",
    "start_evaluation_run",
]
