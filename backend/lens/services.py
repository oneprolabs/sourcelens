import asyncio
import copy
import hashlib
import json
import logging
import math
import re
import uuid
from datetime import timedelta
from time import sleep

from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, Max, OuterRef, Q
from django.utils import timezone

from accounts.models import normalize_answer_language

from .assistant_lifecycle import (
    AssistantNotRunnableError,
    lock_assistant_for_new_work,
    smart_collaboration_assistants,
)
from .attachments import (
    AttachmentError,
    attachment_data_url,
    bind_attachments_to_message,
)
from .citations import sanitize_planned_evidence, sanitize_run_citations
from .document_attachments import (
    bind_document_attachments_to_run,
    get_run_document_attachments,
    get_run_document_expectation,
    get_session_document_attachments,
    set_run_document_expectation,
)
from .environment_variables import (
    declared_environment_references,
    expand_environment_references,
)
from .llm import (
    VISION_SUPPORTED,
    model_supports_vision,
    run_completion,
    run_completion_multimodal,
)
from .models import (
    Assistant,
    EnvironmentVariableSet,
    GlobalSetting,
    LensNode,
    Message,
    MessageAttachment,
    Run,
    RunExecution,
    RunOutputFile,
    RunStep,
    RunTraceExport,
    Session,
)
from .plugins.registry import installed_plugin
from .routing_descriptions import build_routing_description
from .runtime_events import public_step_detail, sanitize_termination_detail
from .session_lifecycle import lock_active_session
from .datasource.workspace import build_session_workspace, session_source_dirs
from .session_titles import fallback_session_title
from .trace_context import root_observation_id_for_run, trace_id_for_run

logger = logging.getLogger(__name__)

TERMINAL_RUN_STATUSES = {
    Run.Status.AWAITING_USER_INPUT,
    Run.Status.DONE,
    Run.Status.FAILED,
    Run.Status.CANCELLED,
}
STREAM_POLL_INTERVAL_SECONDS = 0.3
STREAM_PING_INTERVAL_SECONDS = 15

BUSY_RETRY_INTERVAL_S = 5
BUSY_RETRY_WINDOW_S = 120
DOCUMENT_ATTACHMENT_CAPABILITY = "run_document_attachments"
RUN_CHECKPOINT_RESUME_CAPABILITY = "run_checkpoint_resume"
RUN_CHECKPOINT_TTL_HOURS_CAPABILITY = "run_checkpoint_ttl_hours"
RUN_ADMISSION_CHECKPOINT_CAPABILITY = "run_admission_checkpoint_v1"

HISTORY_MAX_PAIRS = 20
HISTORY_MAX_MESSAGE_CHARS = 2000
CLARIFICATION_MAX_PAIRS = 5
CLARIFICATION_MAX_ANSWER_CHARS = 4000
HISTORY_MAX_TOTAL_CHARS = 32000
CLARIFICATION_MAX_ORIGINAL_CHARS = HISTORY_MAX_TOTAL_CHARS
CLARIFICATION_MAX_PROMPT_CHARS = 20000
HISTORY_ARTIFACT_MAX_FILES = 3
MAX_DELEGATION_DEPTH = 3
MAX_SUBAGENTS_PER_RUN = 8

QUERY_REWRITE_HISTORY_TURNS = 3
QUERY_REWRITE_MAX_CHARS = 400


def get_history_budget():
    """Return the conversation history replay budget.

    Admin-tunable via the GlobalSetting key ``lens.history_budget`` holding
    a JSON object with optional ``pairs``, ``message_chars``, and
    ``total_chars`` keys. Missing or invalid values fall back to the module
    defaults so a bad setting can never break history replay.
    """

    setting = GlobalSetting.objects.filter(key="lens.history_budget").first()
    value = setting.value if setting else {}
    if not isinstance(value, dict):
        value = {}
    return {
        "pairs": _bounded_int(value, "pairs", HISTORY_MAX_PAIRS, 1, 20),
        "message_chars": _bounded_int(
            value,
            "message_chars",
            HISTORY_MAX_MESSAGE_CHARS,
            200,
            20000,
        ),
        "total_chars": _bounded_int(
            value,
            "total_chars",
            HISTORY_MAX_TOTAL_CHARS,
            500,
            100000,
        ),
    }


def _bounded_int(mapping, key, default, minimum, maximum):
    """Return an int setting clamped to a safe range, or the default."""

    try:
        value = int(mapping.get(key) or default)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


QUERY_REWRITE_SYSTEM = (
    "You rewrite a user's latest question into ONE concise, self-contained "
    "search query for a document and code knowledge base. Resolve pronouns "
    'and references ("it", "that", "the above") using the conversation. '
    "Keep entity, product, feature and command names. Prefer the terminology "
    "the documents likely use, and fix obvious typos or homophones toward the "
    "domain term. If the question is already clear and self-contained, return "
    "it unchanged. Answer in the SAME language as the question. Output ONLY "
    "the rewritten query text — no quotes, no explanation."
)


MULTIMODAL_INTENT_MAX_CHARS = 600
MULTIMODAL_INTENT_SYSTEM = (
    "You analyze a user's troubleshooting question that includes one or "
    "more screenshots/images plus text. Combine both into ONE concise, "
    "self-contained query for a code and document knowledge base. "
    "Transcribe any error messages, stack traces, log lines, identifiers, "
    "component or file names, and visible UI state from the images into "
    "text, and merge them with the user's wording. Resolve references "
    '("it", "this error", "the above") using the conversation. Keep '
    "entity, product, feature and command names. Answer in the SAME "
    "language as the question. Output ONLY the resulting query text — no "
    "quotes, no explanation."
)


class LensNodeDispatchError(RuntimeError):
    """Raised when a run cannot be dispatched to its LensNode."""


class MultimodalPreprocessingError(RuntimeError):
    """Raised when image preprocessing cannot produce a safe query."""

    code = "IMAGE_PREPROCESSING_FAILED"

    def __init__(self, reason):
        self.reason = reason
        self.code = {
            "ATTACHMENT_UNREADABLE": "IMAGE_ATTACHMENT_UNREADABLE",
            "MODEL_NOT_VISION_CAPABLE": "MODEL_NOT_VISION_CAPABLE",
            "VISION_MODEL_NOT_CONFIGURED": "VISION_MODEL_NOT_CONFIGURED",
            "PROVIDER_QUOTA_EXCEEDED": "VISION_PROVIDER_QUOTA_EXCEEDED",
            "PROVIDER_UNAVAILABLE": "VISION_PROVIDER_UNAVAILABLE",
            "MODEL_CONFIGURATION_INVALID": ("VISION_MODEL_CONFIGURATION_INVALID"),
        }.get(reason, "IMAGE_PREPROCESSING_FAILED")
        super().__init__(self.code)


def lensnode_group_name(lensnode_uuid):
    """Return the Channels group name for a LensNode."""

    return f"lens.lensnode.{lensnode_uuid}"


def invalidate_skill_cache(skill_uuid):
    """Ask every online LensNode to remove one Skill cache directory."""

    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.warning(
            "Cannot invalidate Skill cache %s without a channel layer.",
            skill_uuid,
        )
        return

    nodes = LensNode.objects.filter(
        status=LensNode.Status.ONLINE,
        enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        token_revoked=False,
    ).values_list("uuid", flat=True)
    for lensnode_uuid in nodes:
        try:
            async_to_sync(channel_layer.group_send)(
                lensnode_group_name(lensnode_uuid),
                {
                    "type": "lensnode.command",
                    "payload": {
                        "type": "skill_cache_invalidate",
                        "skill_uuid": str(skill_uuid),
                    },
                },
            )
        except Exception:
            logger.exception(
                "Failed to invalidate Skill cache %s on LensNode %s.",
                skill_uuid,
                lensnode_uuid,
            )


LENSNODE_DISCONNECT_GRACE_SECONDS_DEFAULT = 180


def get_lensnode_disconnect_grace_seconds():
    """Return how long a disconnected node keeps its runs before they fail.

    A blue/green API deploy recycles the container a node is connected to, so
    a WebSocket drop is not proof the node's in-flight runs failed — the node
    reconnects on an interval. Only a node still gone after this window has its
    RUNNING/STREAMING runs marked failed (see
    lens.tasks.check_lensnode_disconnect_grace_period). Admin-tunable via the
    GlobalSetting key ``lensnode.disconnect_grace_s``.
    """

    setting = GlobalSetting.objects.filter(key="lensnode.disconnect_grace_s").first()
    try:
        value = int(
            setting.value if setting else LENSNODE_DISCONNECT_GRACE_SECONDS_DEFAULT
        )
    except (TypeError, ValueError):
        return LENSNODE_DISCONNECT_GRACE_SECONDS_DEFAULT
    return max(1, value)


def schedule_lensnode_disconnect_grace_check(lensnode_uuid, disconnected_at):
    """Schedule the one-shot check that fails a node's runs if it stays gone
    past the grace window.

    Kept here (not in the consumer) so the scheduling policy is a service, not
    transport logic. Uses a Celery countdown task, not an in-process timer: the
    API process itself is what gets recycled on a blue/green switch, so an
    asyncio timer would die with it — the Celery worker is a separate process.
    disconnected_at pins the check to this disconnect episode.

    apply_async talks to the broker; wrap it so a broker hiccup at disconnect
    time can't raise out of the consumer's disconnect() — the periodic idle
    reaper (lens.lensnode_cleanup) remains the backstop for a genuinely dead
    node whose check never got scheduled.
    """

    from .tasks import check_lensnode_disconnect_grace_period

    grace_s = get_lensnode_disconnect_grace_seconds()
    try:
        check_lensnode_disconnect_grace_period.apply_async(
            args=[str(lensnode_uuid), disconnected_at.isoformat()],
            countdown=grace_s,
        )
    except Exception:
        logger.exception(
            "Failed to schedule disconnect grace check for lensnode %s; "
            "its runs will be reaped by the idle sweep instead",
            lensnode_uuid,
        )


AWAITING_RESUME_TTL_HOURS = 24


def get_awaiting_resume_ttl_hours(lensnode=None):
    """Return how long an orphaned run waits for its node to come back.

    Admin-tunable via the GlobalSetting key ``lensnode.resume_ttl_h``. Once
    the deadline passes, the run is failed by the idle sweep instead of
    waiting indefinitely for a node that may never return. The deadline is
    capped by the node's advertised local checkpoint retention so the control
    plane never promises a resume after the checkpoint may have been deleted.
    """

    setting = GlobalSetting.objects.filter(key="lensnode.resume_ttl_h").first()
    try:
        value = int(setting.value if setting else AWAITING_RESUME_TTL_HOURS)
    except (TypeError, ValueError):
        value = AWAITING_RESUME_TTL_HOURS
    labels = lensnode.labels if lensnode else {}
    try:
        node_ttl = float(
            labels.get(
                RUN_CHECKPOINT_TTL_HOURS_CAPABILITY,
                AWAITING_RESUME_TTL_HOURS,
            )
            if isinstance(labels, dict)
            else AWAITING_RESUME_TTL_HOURS
        )
    except (TypeError, ValueError):
        node_ttl = AWAITING_RESUME_TTL_HOURS
    if not math.isfinite(node_ttl):
        node_ttl = AWAITING_RESUME_TTL_HOURS
    return min(max(1, value), max(1, node_ttl))


def get_run_resume_deadline(run, now=None):
    """Bound checkpoint retention by the Run's original wall-clock budget."""

    current = now or timezone.now()
    ttl_deadline = current + timedelta(
        hours=get_awaiting_resume_ttl_hours(run.lensnode)
    )
    if run.started_at is None:
        return ttl_deadline
    try:
        execution = run.execution
    except RunExecution.DoesNotExist:
        return ttl_deadline
    timeout_s = execution.run_timeout_s
    if not timeout_s:
        timeout_s = run_timeout_for_rounds(execution.agent_rounds)
    run_deadline = run.started_at + timedelta(seconds=timeout_s)
    return min(ttl_deadline, run_deadline)


def schedule_awaiting_run_expiration(run_uuid, resume_by):
    """Schedule precise expiry for one checkpoint-resumable Run."""

    from .tasks import expire_awaiting_run

    countdown_s = max((resume_by - timezone.now()).total_seconds(), 0)
    try:
        expire_awaiting_run.apply_async(
            args=[str(run_uuid)],
            countdown=countdown_s,
        )
    except Exception:
        logger.exception(
            "Failed to schedule awaiting-resume expiration for run %s; "
            "it will be reaped by the idle sweep instead",
            run_uuid,
        )


def fail_active_runs_for_lensnode(lensnode_uuid):
    """Fail active runs when their node cannot provide durable resume."""

    now = timezone.now()
    run_ids = list(
        Run.objects.filter(
            lensnode__uuid=lensnode_uuid,
            status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        ).values_list("id", flat=True)
    )
    if not run_ids:
        return 0
    Run.objects.filter(id__in=run_ids).update(
        status=Run.Status.FAILED,
        error="LENSNODE_DISCONNECTED",
        resume_by=None,
        finished_at=now,
        updated_at=now,
    )
    fail_running_steps_for_runs(run_ids)
    return len(run_ids)


def mark_active_runs_awaiting_resume(lensnode_uuid):
    """Mark a confirmed-gone node's active runs as awaiting resume.

    Called from the grace-period check once a node is confirmed still gone
    (see lens.tasks.check_lensnode_disconnect_grace_period), NOT directly on
    every disconnect — a brief drop during a blue/green switch must not fail
    runs the node is still executing and will report on reconnect. The
    status=RUNNING/STREAMING filter is itself the guard against a run that
    finished (left those states) while the node was reconnecting.

    Waiting is represented by RUNNING plus a non-null resume_by deadline.
    Older application versions therefore continue to count the Run as active
    during a blue/green rollback instead of silently ignoring a new status.
    """

    lensnode = LensNode.objects.filter(uuid=lensnode_uuid).first()
    if not supports_run_checkpoint_resume(lensnode):
        return fail_active_runs_for_lensnode(lensnode_uuid)

    now = timezone.now()
    runs = list(
        Run.objects.filter(
            lensnode__uuid=lensnode_uuid,
            status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        ).select_related("execution", "lensnode")
    )
    if not runs:
        return 0
    updated_ids = []
    expired_ids = []
    scheduled_expirations = []
    for run in runs:
        resume_by = get_run_resume_deadline(run, now=now)
        updates = {
            "status": Run.Status.RUNNING,
            "resume_by": resume_by,
            "updated_at": now,
        }
        if resume_by <= now:
            updates.update(
                status=Run.Status.FAILED,
                error="LENSNODE_RESUME_EXPIRED",
                resume_by=None,
                finished_at=now,
            )
        updated = Run.objects.filter(
            id=run.id,
            status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        ).update(**updates)
        if not updated:
            continue
        updated_ids.append(run.id)
        if resume_by <= now:
            expired_ids.append(run.id)
        else:
            scheduled_expirations.append((run.uuid, resume_by))
    fail_running_steps_for_runs(updated_ids)
    if expired_ids:
        RunExecution.objects.filter(
            run_id__in=expired_ids,
            status__in=[
                RunExecution.Status.QUEUED,
                RunExecution.Status.DISPATCHED,
                RunExecution.Status.RUNNING,
            ],
        ).update(status=RunExecution.Status.FAILED, finished_at=now)
    for run_uuid, resume_by in scheduled_expirations:
        schedule_awaiting_run_expiration(run_uuid, resume_by)
    return len(updated_ids)


def resume_awaiting_run(run_id):
    """Resume one parked Run when its LensNode is online and compatible."""

    with transaction.atomic():
        run = (
            Run.objects.select_related(
                "execution",
                "input_message",
                "lensnode",
            )
            .select_for_update(of=("self",))
            .filter(id=run_id)
            .first()
        )
        if (
            run is None
            or run.lensnode is None
            or run.lensnode.status != LensNode.Status.ONLINE
            or run.status not in [Run.Status.RUNNING, Run.Status.STREAMING]
            or run.resume_by is None
            or run.resume_by <= timezone.now()
        ):
            return False
        execution = RunExecution.objects.select_for_update().get(run=run)
        now = timezone.now()
        if execution.status in [
            RunExecution.Status.QUEUED,
            RunExecution.Status.DISPATCHED,
        ]:
            run.status = Run.Status.QUEUED
            run.resume_by = None
            run.last_activity_at = now
            run.error = ""
            run.outcome = ""
            run.termination_detail = {}
            run.save(
                update_fields=[
                    "status",
                    "resume_by",
                    "last_activity_at",
                    "error",
                    "outcome",
                    "termination_detail",
                    "updated_at",
                ]
            )
            execution.status = RunExecution.Status.QUEUED
            execution.dispatch_id = None
            execution.admitted_at = None
            execution.checkpoint_ready_at = None
            execution.save(
                update_fields=[
                    "status",
                    "dispatch_id",
                    "admitted_at",
                    "checkpoint_ready_at",
                ]
            )
            expected_count = get_run_document_expectation(run.uuid)
            transaction.on_commit(
                lambda run_uuid=run.uuid, count=(
                    expected_count if expected_count is not None else -1
                ): _enqueue_answer_run(run_uuid, count)
            )
            return True
        if execution.status != RunExecution.Status.RUNNING:
            return False
        if not supports_run_checkpoint_resume(run.lensnode):
            return False
        if (
            supports_run_admission_checkpoint(run.lensnode)
            and execution.checkpoint_ready_at is None
        ):
            return False
        run.status = Run.Status.STREAMING
        run.last_activity_at = now
        run.save(update_fields=["status", "last_activity_at", "updated_at"])
        execution.dispatch_id = uuid.uuid4()
        execution.save(update_fields=["dispatch_id"])
        dispatch_id = execution.dispatch_id
        transaction.on_commit(
            lambda: dispatch_run_to_lensnode(
                run,
                run_execution_question(run),
                resume=True,
                dispatch_id=dispatch_id,
            )
        )
    return True


def resume_awaiting_runs_for_lensnode(
    lensnode_uuid,
    reported_active_run_uuids=(),
):
    """Re-dispatch a reconnected node's awaiting-resume runs.

    Runs whose node died mid-answer keep RUNNING plus a resume deadline, with
    their checkpoints preserved on the node's workspace volume. When the node
    reconnects (fresh hello), each such run is dispatched again with the
    same run_uuid; the node's checkpointer resumes the run from its last
    checkpoint instead of starting over.

    Runs reported active by the reconnecting node are not re-dispatched. This
    includes both executions that survived the connection drop and completed
    runs whose durable terminal frame is still waiting in the node outbox.
    A failed dispatch is unexpected; the run is kept waiting so a later
    reconnect retries it.
    """

    reported_active = {str(run_uuid) for run_uuid in reported_active_run_uuids or ()}
    lensnode = LensNode.objects.filter(uuid=lensnode_uuid).first()

    report_at = lensnode.updated_at
    awaiting_status = (
        Q(status=Run.Status.RUNNING)
        | Q(
            status=Run.Status.STREAMING,
            last_activity_at__lt=report_at,
        )
        | Q(
            status=Run.Status.STREAMING,
            last_activity_at__isnull=True,
        )
    )
    run_ids = (
        Run.objects.filter(
            lensnode__uuid=lensnode_uuid,
            resume_by__gt=timezone.now(),
        )
        .exclude(uuid__in=reported_active)
        .filter(awaiting_status)
        .values_list("id", flat=True)
    )
    recovered = 0
    for run_id in run_ids:
        try:
            with transaction.atomic():
                run = (
                    Run.objects.select_related(
                        "execution",
                        "input_message",
                    )
                    .select_for_update(of=("self",))
                    .filter(
                        id=run_id,
                        lensnode__uuid=lensnode_uuid,
                    )
                    .first()
                )
                claimed_on_current_report = bool(
                    run is not None
                    and run.status == Run.Status.STREAMING
                    and (
                        run.last_activity_at is None
                        or run.last_activity_at >= report_at
                    )
                )
                if (
                    run is None
                    or run.status not in [Run.Status.RUNNING, Run.Status.STREAMING]
                    or claimed_on_current_report
                    or run.resume_by is None
                    or run.resume_by <= timezone.now()
                ):
                    continue
                now = timezone.now()
                execution = (
                    RunExecution.objects.select_for_update().filter(run=run).first()
                )
                if execution is None:
                    continue
                if execution.status in [
                    RunExecution.Status.QUEUED,
                    RunExecution.Status.DISPATCHED,
                ]:
                    run.status = Run.Status.QUEUED
                    run.resume_by = None
                    run.last_activity_at = now
                    run.error = ""
                    run.outcome = ""
                    run.termination_detail = {}
                    run.save(
                        update_fields=[
                            "status",
                            "resume_by",
                            "last_activity_at",
                            "error",
                            "outcome",
                            "termination_detail",
                            "updated_at",
                        ]
                    )
                    execution.status = RunExecution.Status.QUEUED
                    execution.dispatch_id = None
                    execution.admitted_at = None
                    execution.checkpoint_ready_at = None
                    execution.save(
                        update_fields=[
                            "status",
                            "dispatch_id",
                            "admitted_at",
                            "checkpoint_ready_at",
                        ]
                    )
                    expected_document_count = get_run_document_expectation(run.uuid)
                    transaction.on_commit(
                        lambda run_uuid=run.uuid, count=(
                            expected_document_count
                            if expected_document_count is not None
                            else -1
                        ): _enqueue_answer_run(run_uuid, count)
                    )
                    logger.warning(
                        "never-admitted run requeued run_uuid=%s " "lensnode_uuid=%s",
                        run.uuid,
                        lensnode_uuid,
                    )
                    recovered += 1
                    continue
                if execution.status != RunExecution.Status.RUNNING:
                    continue
                recovery_error = None
                if not supports_run_checkpoint_resume(lensnode):
                    recovery_error = "LENSNODE_RESUME_UNSUPPORTED"
                elif (
                    supports_run_admission_checkpoint(lensnode)
                    and execution.checkpoint_ready_at is None
                ):
                    recovery_error = "LENSNODE_CHECKPOINT_NOT_READY"
                if recovery_error:
                    run.status = Run.Status.FAILED
                    run.error = recovery_error
                    run.resume_by = None
                    run.finished_at = now
                    run.save(
                        update_fields=[
                            "status",
                            "error",
                            "resume_by",
                            "finished_at",
                            "updated_at",
                        ]
                    )
                    execution.status = RunExecution.Status.FAILED
                    execution.finished_at = now
                    execution.save(update_fields=["status", "finished_at"])
                    fail_running_steps_for_runs([run.id])
                    logger.error(
                        "run resume rejected run_uuid=%s lensnode_uuid=%s " "reason=%s",
                        run.uuid,
                        lensnode_uuid,
                        recovery_error,
                    )
                    continue
                run.status = Run.Status.STREAMING
                run.last_activity_at = now
                run.save(
                    update_fields=[
                        "status",
                        "last_activity_at",
                        "updated_at",
                    ]
                )
                execution.dispatch_id = uuid.uuid4()
                execution.save(update_fields=["dispatch_id"])
                dispatch_run_to_lensnode(
                    run,
                    run_execution_question(run),
                    resume=True,
                    dispatch_id=execution.dispatch_id,
                )
        except Exception:
            logger.exception(
                "run %s: resume dispatch failed; keeping it awaiting",
                run_id,
            )
            continue
        logger.info(
            "run resume attempted run_uuid=%s lensnode_uuid=%s " "dispatch_id=%s",
            run.uuid,
            lensnode_uuid,
            execution.dispatch_id,
        )
        recovered += 1
    return recovered


def fail_running_steps_for_runs(run_ids, status=RunStep.Status.FAILED):
    """Finalize in-flight steps for runs failed outside the step context.

    Out-of-band failure paths (lensnode disconnect, orphan reconcile, idle
    sweep) update the Run row directly, so their RUNNING RunStep rows would
    otherwise stay RUNNING forever and disagree with the terminal Run state.
    The caller may use ``DONE`` when the out-of-band terminal result succeeded.
    """

    return RunStep.objects.filter(
        run_id__in=run_ids,
        status=RunStep.Status.RUNNING,
    ).update(status=status, updated_at=timezone.now())


RECONCILE_GRACE_SECONDS = 60
RECONCILE_CONFIRM_GRACE_SECONDS_DEFAULT = 10


def get_reconcile_confirm_grace_seconds():
    """Return how long a reconnect-unreported run waits before being failed.

    A node's ``hello`` on reconnect reports its currently in-flight runs, but
    LensNode redelivers ``run_done`` at-least-once through a durable outbox
    (``LensNodeClient._send_loop``) — a run that finished during the drop is
    legitimately absent from that snapshot while its already-computed answer
    is still safely queued for delivery. Failing it immediately discards a
    correct answer that was moments from arriving. This window gives the
    normal completion path a chance to land first (see
    lens.tasks.confirm_reconcile_orphan). Admin-tunable via the GlobalSetting
    key ``lensnode.reconcile_confirm_grace_s``.
    """

    setting = GlobalSetting.objects.filter(
        key="lensnode.reconcile_confirm_grace_s"
    ).first()
    try:
        value = int(
            setting.value if setting else RECONCILE_CONFIRM_GRACE_SECONDS_DEFAULT
        )
    except (TypeError, ValueError):
        return RECONCILE_CONFIRM_GRACE_SECONDS_DEFAULT
    return max(1, value)


def schedule_reconcile_orphan_confirmation(
    run_uuid,
    minimum_countdown_s=0,
):
    """Schedule the delayed check that fails a run if it's still non-terminal.

    Called from reconcile_lensnode_active_runs for each candidate instead of
    failing it inline, so a run that legitimately finished during the drop
    gets a chance to reach a terminal state through the normal completion
    path (see get_reconcile_confirm_grace_seconds) before being written off.

    apply_async talks to the broker; wrap it so a broker hiccup can't raise
    out of the consumer's hello handler — the periodic idle reaper
    (lens.lensnode_cleanup) remains the backstop if scheduling fails.
    """

    from .tasks import confirm_reconcile_orphan

    countdown_s = max(
        get_reconcile_confirm_grace_seconds(),
        minimum_countdown_s,
    )
    try:
        confirm_reconcile_orphan.apply_async(
            args=[str(run_uuid)],
            countdown=countdown_s,
        )
    except Exception:
        logger.exception(
            "Failed to schedule reconcile-orphan confirmation for run %s; "
            "it will be reaped by the idle sweep instead",
            run_uuid,
        )


def reconcile_lensnode_active_runs(lensnode_uuid, active_run_uuids):
    """Schedule an orphan check for this node's runs it is no longer running.

    On (re)connect a LensNode reports the runs it is actively executing. A
    RUNNING/STREAMING run assigned to the node that the node does not claim
    is a candidate orphan (e.g. the control plane restarted mid-answer and
    the terminal frame was delayed on the dropped socket) — but LensNode
    redelivers at-least-once on reconnect, so a run that just finished is
    legitimately unreported while its result is still in flight. Rather than
    failing candidates inline, this schedules a delayed confirmation per
    candidate so the normal completion path gets a chance to resolve it
    first (see schedule_reconcile_orphan_confirmation). A run that was just
    dispatched is checked only after it reaches the age guard, so a fast node
    restart cannot make it disappear from both reconciliation and resume.
    """

    active = {str(value) for value in (active_run_uuids or [])}
    now = timezone.now()
    lensnode = LensNode.objects.filter(uuid=lensnode_uuid).first()
    candidates = Run.objects.filter(
        lensnode=lensnode,
        status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        execution__status__in=[
            RunExecution.Status.DISPATCHED,
            RunExecution.Status.RUNNING,
        ],
        resume_by__isnull=True,
    ).exclude(uuid__in=active)
    if not supports_run_checkpoint_resume(lensnode):
        age_cutoff = now - timedelta(seconds=RECONCILE_GRACE_SECONDS)
        candidates = candidates.filter(
            Q(started_at__isnull=True) | Q(started_at__lte=age_cutoff)
        )
    candidates = candidates.values_list("uuid", "started_at")
    count = 0
    for run_uuid, started_at in candidates:
        run_age_s = (
            max((now - started_at).total_seconds(), 0) if started_at is not None else 0
        )
        schedule_reconcile_orphan_confirmation(
            run_uuid,
            minimum_countdown_s=max(
                RECONCILE_GRACE_SECONDS - run_age_s,
                0,
            ),
        )
        count += 1
    return count


def _next_sequence(session):
    """Return next message sequence for a session."""

    last_sequence = session.message_set.aggregate(Max("sequence"))["sequence__max"]
    return (last_sequence or 0) + 1


def _attachment_name_tokens(name):
    """Return normalized searchable tokens from an attachment name."""

    return {token.lower() for token in re.split(r"[^\w]+", name or "") if token}


def _clearly_referenced_attachment_kind(question):
    """Return the kind of attachment clearly referenced by the question."""

    lowered = (question or "").lower()
    english_prefix = (
        r"(?:this|that|(?:the\s+)?previous|earlier|uploaded|attached)"
    )
    if re.search(
        rf"\b{english_prefix}\s+(?:image|picture|photo)\b",
        lowered,
    ):
        return "image"
    if re.search(
        rf"\b{english_prefix}\s+(?:document|file|pdf|docx|xlsx|pptx)\b",
        lowered,
    ):
        return "document"
    chinese_reference = any(
        marker in lowered
        for marker in (
            "这张",
            "这个",
            "这份",
            "该",
            "那张",
            "那个",
            "之前",
            "刚才",
            "上一个",
            "上述",
            "上传的",
        )
    )
    if chinese_reference and any(
        marker in lowered for marker in ("图片", "图像", "照片")
    ):
        return "image"
    if chinese_reference and any(
        marker in lowered
        for marker in ("文档", "文件", "pdf", "docx", "xlsx", "pptx")
    ):
        return "document"
    return None


def select_session_attachment_context(session, question, explicit_uuids=None):
    """Select current or clearly referenced attachments for a new Run."""

    explicit = {str(value) for value in explicit_uuids or []}
    images = list(
        MessageAttachment.objects.filter(session=session).order_by(
            "-created_at",
            "-pk",
        )
    )
    documents = get_session_document_attachments(session.uuid)
    candidates = [
        {
            "uuid": str(item.uuid),
            "kind": "image",
            "name": item.original_name,
            "created_at": item.created_at,
        }
        for item in images
    ] + [
        {
            "uuid": item["uuid"],
            "kind": "document",
            "name": item["original_name"],
            "created_at": item.get("created_at", ""),
        }
        for item in documents
    ]
    selected = [item for item in candidates if item["uuid"] in explicit]
    historical = [item for item in candidates if item["uuid"] not in explicit]
    if explicit:
        return selected
    requested_kind = _clearly_referenced_attachment_kind(question)
    if not historical:
        return selected

    question_tokens = _attachment_name_tokens(question)
    named = [
        item
        for item in historical
        if question_tokens & _attachment_name_tokens(item["name"])
    ]
    if named:
        return selected + named

    if requested_kind:
        same_kind = [item for item in historical if item["kind"] == requested_kind]
        # Never substitute an attachment of another kind.  A request for a
        # document must not accidentally inherit an older image (or vice
        # versa) when the requested attachment has expired or is unavailable.
        if not same_kind:
            raise AttachmentError("ATTACHMENT_NOT_FOUND")
        return selected + same_kind[:1]
    return selected


def _explicit_answer_language(question):
    """Return the language explicitly requested by the current message."""

    text = str(question or "").strip().lower()
    if not text:
        return None
    chinese_request = re.search(
        r"(?:用|使用|切换到|切換到|改用|改成|请用|請用)"
        r"(?:简体中文|簡體中文|中文|普通话|普通話)"
        r"(?:回答|回复|回覆|作答|答复|答覆)?",
        text,
    )
    if chinese_request and re.search(
        r"(?:回答|回复|回覆|作答|答复|答覆|语言|語言)|^请|^請",
        text,
    ):
        return "zh-CN"
    chinese_english_request = re.search(
        r"(?:用|使用|切换到|切換到|改用|改成|请用|請用)"
        r"(?:英文|英语|英語)"
        r"(?:回答|回复|回覆|作答|答复|答覆)?",
        text,
    )
    if chinese_english_request:
        return "en-US"
    english_request = re.search(
        r"(?:answer|respond|reply|write|speak|use|switch)"
        r"(?:\s+(?:in|to))?\s+(?:english|en-us|en)\b",
        text,
    )
    if english_request:
        return "en-US"
    english_chinese_request = re.search(
        r"(?:answer|respond|reply|write|speak|use|switch)"
        r"(?:\s+(?:in|to))?\s+(?:chinese|simplified chinese|中文)",
        text,
    )
    if english_chinese_request:
        return "zh-CN"
    return None


def _latest_session_answer_language(session):
    """Return the latest resolved language stored for a Session."""

    latest_run = (
        Run.objects.filter(session=session)
        .select_related("execution")
        .order_by("-input_message__sequence", "-pk")
        .first()
    )
    if latest_run is None:
        return None
    try:
        execution = latest_run.execution
    except RunExecution.DoesNotExist:
        execution = None
    if execution is None:
        return None
    snapshot = execution.runtime_snapshot or {}
    return snapshot.get("answer_language")


def resolve_run_answer_language(session, question, retry_of_run=None):
    """Resolve one Run language without allowing context to override intent."""

    if retry_of_run is not None:
        try:
            execution = retry_of_run.execution
        except RunExecution.DoesNotExist:
            execution = None
        if execution is not None:
            snapshot = execution.runtime_snapshot or {}
            retry_language = snapshot.get("answer_language")
            if retry_language:
                return normalize_answer_language(retry_language)

    explicit_language = _explicit_answer_language(question)
    if explicit_language:
        return explicit_language

    session_language = _latest_session_answer_language(session)
    if session_language:
        return normalize_answer_language(session_language)

    profile = getattr(session.user, "profile", None)
    return normalize_answer_language(getattr(profile, "language", None))


@transaction.atomic
def create_execution_run(
    session,
    question,
    idempotency_key="",
    retry_of_run=None,
    enqueue=True,
    attachment_uuids=None,
    user=None,
    parent_run=None,
    routing_assistant_uuid=None,
    routing_assistant_uuids=None,
    agent_rounds=None,
):
    """Create a queued run for LensNode execution."""

    assistant = lock_assistant_for_new_work(session.assistant, user)
    session = lock_active_session(session)
    session.assistant = assistant

    explicit_routing_assistant_uuids = (
        routing_assistant_uuids
        if routing_assistant_uuids is not None
        else ([routing_assistant_uuid] if routing_assistant_uuid is not None else None)
    )
    if not explicit_routing_assistant_uuids:
        explicit_routing_assistant_uuids = None
    routing_assistant_uuids = _validated_routing_assistant_uuids(
        session,
        user or session.user,
        explicit_routing_assistant_uuids,
    )

    answer_language = resolve_run_answer_language(
        session,
        question,
        retry_of_run,
    )

    if idempotency_key:
        existing = (
            Run.objects.filter(
                session=session,
                idempotency_key=idempotency_key,
            )
            .select_related("output_message")
            .first()
        )
        if existing:
            return existing

    requested_attachment_uuids = [str(value) for value in (attachment_uuids or [])]
    selected_context = select_session_attachment_context(
        session,
        question,
        requested_attachment_uuids,
    )
    requires_document_attachments = any(
        item["kind"] == "document" for item in selected_context
    )
    lensnode = select_execution_lensnode(
        assistant,
        require_document_attachments=requires_document_attachments,
    )

    validate_retry_run(session, retry_of_run)

    if not session.title_manually_edited and not session.message_set.exists():
        fallback_title = fallback_session_title(question)
        if fallback_title:
            session.title = fallback_title
            session.title_generation_status = Session.TitleGenerationStatus.PENDING
            session.save(
                update_fields=[
                    "title",
                    "title_generation_status",
                    "updated_at",
                ]
            )

    input_message = Message.objects.create(
        session=session,
        role=Message.Role.USER,
        content=question,
        sequence=_next_sequence(session),
    )
    output_message = Message.objects.create(
        session=session,
        role=Message.Role.ASSISTANT,
        content="",
        sequence=input_message.sequence + 1,
    )
    run = Run.objects.create(
        session=session,
        status=Run.Status.QUEUED,
        input_message=input_message,
        output_message=output_message,
        retry_of_run=retry_of_run,
        lensnode=lensnode,
        idempotency_key=idempotency_key,
        parent_run=parent_run,
    )
    input_message.run = run
    input_message.save(update_fields=["run"])
    if not session.datasource_snapshots.exists():
        from .datasource.routing import (
            DatasourceRoutingError,
            selected_bindings,
        )
        from .datasource.snapshots import capture_session_datasources

        try:
            bindings = selected_bindings(assistant, question)
        except DatasourceRoutingError as exc:
            raise LensNodeDispatchError(str(exc)) from exc
        capture_session_datasources(session, assistant, bindings=bindings)
    create_run_execution_snapshot(
        run,
        answer_language=answer_language,
        agent_rounds=agent_rounds,
        routing_assistant_uuids=routing_assistant_uuids,
        routing_assistant_explicit=(explicit_routing_assistant_uuids is not None),
    )
    if explicit_routing_assistant_uuids:
        _set_routing_execution_question(
            run,
            explicit_routing_assistant_uuids,
        )
    attachment_order = {
        value: order for order, value in enumerate(requested_attachment_uuids)
    }
    image_uuids = bind_attachments_to_message(
        session,
        input_message,
        requested_attachment_uuids,
        order_by_uuid=attachment_order,
    )
    document_uuids = [
        item["uuid"] for item in selected_context if item["kind"] == "document"
    ]
    if document_uuids and not supports_document_attachments(run.lensnode):
        raise AttachmentError("DOCUMENT_ATTACHMENTS_UNSUPPORTED_BY_LENSNODE")
    documents = bind_document_attachments_to_run(
        session,
        run,
        document_uuids,
        order_by_uuid=attachment_order,
    )
    document_uuid_set = {item["uuid"] for item in documents}
    selected_image_uuids = {
        item["uuid"] for item in selected_context if item["kind"] == "image"
    }
    requested_document_uuids = {
        value
        for value in requested_attachment_uuids
        if value not in selected_image_uuids
    }
    if not requested_document_uuids.issubset(document_uuid_set):
        raise AttachmentError("ATTACHMENT_NOT_FOUND")

    document_count = len(documents)
    execution = run.execution
    runtime_snapshot = dict(execution.runtime_snapshot or {})
    runtime_snapshot["session_attachment_uuids"] = [
        item["uuid"] for item in selected_context if item["kind"] == "image"
    ]
    runtime_snapshot["direct_attachment_uuids"] = requested_attachment_uuids
    runtime_snapshot["document_attachment_count"] = document_count
    execution.runtime_snapshot = runtime_snapshot
    execution.save(update_fields=["runtime_snapshot"])

    set_run_document_expectation(run.uuid, document_count)
    if enqueue:
        transaction.on_commit(lambda: _enqueue_answer_run(run.uuid, document_count))

    return run


def _set_routing_execution_question(run, assistant_uuids):
    """Store the mention-free prompt without changing the visible message."""

    assistant_names_by_uuid = {
        str(uuid): name
        for uuid, name in Assistant.objects.filter(
            uuid__in=assistant_uuids
        ).values_list("uuid", "name")
    }
    assistant_names = [
        assistant_names_by_uuid.get(str(uuid), "") for uuid in assistant_uuids
    ]
    assistant_names = [name for name in assistant_names if name]
    if not assistant_names:
        return
    visible_question = run.input_message.content or ""
    names = "|".join(
        re.escape(name) for name in sorted(assistant_names, key=len, reverse=True)
    )
    pattern = rf"^(?:@(?:{names})(?=\s|$)\s*)+"
    execution_question = re.sub(pattern, "", visible_question, count=1)
    snapshot = dict(run.execution.runtime_snapshot or {})
    snapshot["routing_question"] = execution_question
    run.execution.runtime_snapshot = snapshot
    run.execution.save(update_fields=["runtime_snapshot"])


def _validated_routing_assistant_uuids(session, user, selected_uuids=None):
    """Recheck the live access scope before freezing a smart Run."""

    if session.routing_mode != Session.RoutingMode.SMART:
        if selected_uuids is not None:
            raise AssistantNotRunnableError
        return None
    allowed = smart_collaboration_assistants(
        user,
        session.allowed_assistant_uuids,
        allow_empty=True,
    )
    allowed_ids = {str(item.uuid) for item in allowed}
    if selected_uuids is not None:
        normalized = [str(uuid) for uuid in selected_uuids]
        if not set(normalized).issubset(allowed_ids):
            raise AssistantNotRunnableError
        return normalized
    return sorted(allowed_ids)


def run_execution_question(run):
    """Return this Run's prompt for agent execution, not chat display."""

    snapshot = getattr(getattr(run, "execution", None), "runtime_snapshot", {})
    if isinstance(snapshot, dict) and "routing_question" in snapshot:
        return str(snapshot["routing_question"] or "")
    return str(run.input_message.content or "")


def select_execution_lensnode(
    assistant,
    *,
    require_document_attachments=False,
):
    """Return the bound node or the least-loaded compatible online node."""

    if assistant.lensnode_id:
        return assistant.lensnode

    candidates = _compatible_execution_lensnodes(
        assistant,
        require_document_attachments=require_document_attachments,
    )
    if not candidates:
        raise LensNodeDispatchError("LENSNODE_UNAVAILABLE")
    return min(candidates, key=lambda item: (item.active_runs, item.created_at))


def _compatible_execution_lensnodes(
    assistant,
    *,
    require_document_attachments=False,
):
    """Return online nodes that can execute an unbound Assistant."""

    candidates = []
    queryset = LensNode.objects.filter(
        status=LensNode.Status.ONLINE,
        enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        token_revoked=False,
    ).annotate(
        active_runs=Count(
            "runs",
            filter=Q(
                runs__status__in=[
                    Run.Status.QUEUED,
                    Run.Status.RUNNING,
                    Run.Status.STREAMING,
                ]
            ),
        )
    )
    for lensnode in queryset:
        if execution_task_for_capability(assistant.capability) not in task_names(
            lensnode
        ):
            continue
        if require_document_attachments and not supports_document_attachments(lensnode):
            continue
        candidates.append(lensnode)
    return candidates


@transaction.atomic
def create_delegated_run(
    parent_run,
    assistant_uuid,
    question,
    delegation_key="",
    delegation_group_key="",
):
    """Create one attempt in a logical delegated task."""

    parent_run = (
        Run.objects.select_for_update()
        .select_related(
            "session",
            "session__user",
        )
        .get(pk=parent_run.pk)
    )
    if parent_run.session.routing_mode != Session.RoutingMode.SMART:
        raise LensNodeDispatchError("SUBAGENT_NOT_ALLOWED")
    if parent_run.parent_run_id:
        raise LensNodeDispatchError("SUBAGENT_NESTED_DISABLED")
    if parent_run.status not in {
        Run.Status.RUNNING,
        Run.Status.STREAMING,
    }:
        raise LensNodeDispatchError("PARENT_RUN_NOT_ACTIVE")
    try:
        parent_execution = parent_run.execution
    except RunExecution.DoesNotExist:
        parent_execution = RunExecution.objects.filter(run=parent_run).first()
    if parent_execution is None:
        raise LensNodeDispatchError("PARENT_RUN_EXECUTION_MISSING")
    if parent_execution.status in {
        RunExecution.Status.COMPLETED,
        RunExecution.Status.FAILED,
        RunExecution.Status.CANCELLED,
    }:
        raise LensNodeDispatchError("PARENT_RUN_NOT_ACTIVE")
    ancestry = set()
    current = parent_run
    depth = 0
    while current is not None:
        if current.pk in ancestry or depth >= MAX_DELEGATION_DEPTH:
            raise LensNodeDispatchError("SUBAGENT_DEPTH_EXCEEDED")
        ancestry.add(current.pk)
        current = current.parent_run
        depth += 1

    configured = (parent_run.execution.runtime_snapshot or {}).get("subagents") or []
    allowed = {str(item.get("uuid")) for item in configured if isinstance(item, dict)}
    if str(assistant_uuid) not in allowed:
        raise LensNodeDispatchError("SUBAGENT_NOT_ALLOWED")
    if delegation_key:
        existing = Run.objects.filter(
            parent_run=parent_run,
            idempotency_key=f"delegation:{delegation_key}"[:128],
        ).first()
        if existing is not None:
            return existing
    delegation_group_key = str(delegation_group_key or delegation_key or "")[:96]
    previous_attempt = None
    if delegation_group_key:
        previous_attempt = (
            Run.objects.filter(
                parent_run=parent_run,
                session__assistant__uuid=assistant_uuid,
                execution__runtime_snapshot__delegation_group_key=(
                    delegation_group_key
                ),
            )
            .select_related("session")
            .order_by("created_at", "pk")
            .last()
        )
        if previous_attempt is not None and previous_attempt.status in {
            Run.Status.QUEUED,
            Run.Status.RUNNING,
            Run.Status.STREAMING,
            Run.Status.AWAITING_USER_INPUT,
        }:
            return previous_attempt
    assistant = (
        Assistant.objects.visible_to(parent_run.session.user)
        .filter(
            visibility__in=[
                Assistant.Visibility.PUBLIC,
                Assistant.Visibility.PRIVATE,
            ],
            uuid=assistant_uuid,
            capability__in=[
                Assistant.Capability.GENERAL_CHAT,
                Assistant.Capability.CODE_ANALYSIS,
                Assistant.Capability.KNOWLEDGE_QA,
            ],
            status=Assistant.Status.ACTIVE,
            is_system=False,
        )
        .first()
    )
    if assistant is None:
        raise LensNodeDispatchError("SUBAGENT_UNAVAILABLE")
    if assistant.pk in {
        item.session.assistant_id
        for item in Run.objects.filter(pk__in=ancestry).select_related("session")
    }:
        raise LensNodeDispatchError("SUBAGENT_CYCLE")
    if previous_attempt is None:
        session = Session.objects.create(
            assistant=assistant,
            user=parent_run.session.user,
            title=f"Delegated: {parent_run.uuid}",
            title_manually_edited=True,
        )
    else:
        session = previous_attempt.session
    delegated = create_execution_run(
        session=session,
        question=str(question or "")[:20000],
        enqueue=True,
        parent_run=parent_run,
        retry_of_run=previous_attempt,
        idempotency_key=(
            f"delegation:{delegation_key}"[:128] if delegation_key else ""
        ),
    )
    runtime_snapshot = dict(delegated.execution.runtime_snapshot or {})
    runtime_snapshot["delegation_group_key"] = delegation_group_key
    delegated.execution.runtime_snapshot = runtime_snapshot
    delegated.execution.save(update_fields=["runtime_snapshot"])
    return delegated


def validate_retry_run(session, retry_of_run):
    """Reject Retry links outside the Session or with an existing cycle."""

    if retry_of_run is None:
        return
    seen = set()
    current = retry_of_run
    while current is not None:
        if current.session_id != session.pk:
            raise ValueError("Retry Run must belong to the same Session.")
        if current.pk in seen:
            raise ValueError("Retry Run chain contains a cycle.")
        seen.add(current.pk)
        if current.retry_of_run_id is None:
            return
        current = Run.objects.only(
            "pk",
            "session_id",
            "retry_of_run_id",
        ).get(pk=current.retry_of_run_id)


def _enqueue_answer_run(run_uuid, expected_document_count=0):
    """Enqueue a run after transaction commit."""

    from .tasks import enqueue_answer_run_task

    enqueue_answer_run_task(run_uuid, expected_document_count)


def supports_document_attachments(lensnode):
    """Return whether a LensNode advertised transient document support."""

    labels = lensnode.labels if lensnode else {}
    return bool(
        isinstance(labels, dict) and labels.get(DOCUMENT_ATTACHMENT_CAPABILITY) is True
    )


def assistant_supports_document_attachments(assistant):
    """Return whether an Assistant can execute with Run documents."""

    if assistant.lensnode_id:
        return supports_document_attachments(assistant.lensnode)
    return bool(
        _compatible_execution_lensnodes(
            assistant,
            require_document_attachments=True,
        )
    )


def supports_run_checkpoint_resume(lensnode):
    """Return whether a LensNode can safely continue a checkpointed Run."""

    labels = lensnode.labels if lensnode else {}
    return bool(
        isinstance(labels, dict)
        and labels.get(RUN_CHECKPOINT_RESUME_CAPABILITY) is True
    )


def supports_run_admission_checkpoint(lensnode):
    """Return whether a LensNode acknowledges admission and checkpoints."""

    labels = lensnode.labels if lensnode else {}
    return bool(
        isinstance(labels, dict)
        and labels.get(RUN_ADMISSION_CHECKPOINT_CAPABILITY) is True
    )


def _enqueue_session_title_generation(session_uuid, run_uuid):
    """Enqueue semantic title generation after the answer is committed."""

    from .tasks import (
        SESSION_TITLE_TASK_EXPIRY_SECONDS,
        generate_session_title,
    )

    generate_session_title.apply_async(
        args=[str(session_uuid), str(run_uuid)],
        expires=SESSION_TITLE_TASK_EXPIRY_SECONDS,
    )


def analyze_multimodal_intent(run):
    """Fold a run's image attachments and text into one search query.

    Calls the assistant's multimodal model with the current question and
    attached images, returning a consolidated textual query that drives
    retrieval and the node answer. Skips preprocessing when it is not
    applicable and fails the run when a configured multimodal request cannot
    produce an image-aware query.
    """

    assistant = run.session.assistant
    original = run_execution_question(run)
    selected_uuids = set(
        (run.execution.runtime_snapshot or {}).get(
            "session_attachment_uuids",
            [],
        )
    )
    attachments = list(
        MessageAttachment.objects.filter(
            session=run.session,
            kind=MessageAttachment.Kind.IMAGE,
            uuid__in=selected_uuids,
        ).order_by("created_at", "pk")
    )
    if not selected_uuids:
        attachments = list(
            run.input_message.attachments.filter(kind=MessageAttachment.Kind.IMAGE)
        )
    if not attachments:
        return {
            "question": original,
            "rewritten": False,
            "image_count": 0,
            "status": "skipped",
        }
    if not assistant.multimodal_model_ref:
        raise MultimodalPreprocessingError("VISION_MODEL_NOT_CONFIGURED")

    image_data_urls = []
    for attachment in attachments:
        data_url = attachment_data_url(attachment)
        if data_url:
            image_data_urls.append(data_url)
    if not image_data_urls:
        raise MultimodalPreprocessingError("ATTACHMENT_UNREADABLE")
    try:
        supports_vision = model_supports_vision(assistant.multimodal_model_ref)
    except Exception as exc:
        logger.warning(
            "multimodal model capability check failed for run %s: %s",
            run.uuid,
            exc,
        )
        raise MultimodalPreprocessingError("MODEL_CONFIGURATION_INVALID") from exc
    if supports_vision not in (True, VISION_SUPPORTED):
        raise MultimodalPreprocessingError("MODEL_NOT_VISION_CAPABLE")

    user_text = (
        f"User question: {original or '(no text, analyze the image)'}\n\n"
        + "Combined search query:"
    )
    try:
        result = run_completion_multimodal(
            model_ref=assistant.multimodal_model_ref,
            system=MULTIMODAL_INTENT_SYSTEM,
            user_text=user_text,
            image_data_urls=image_data_urls,
            node_name="lens.multimodal_intent",
            user_id=run.session.user_id,
        )
    except Exception as exc:
        logger.warning("multimodal intent failed for run %s: %s", run.uuid, exc)
        message = str(exc).lower()
        if "429" in message or "quota" in message or "rate limit" in message:
            reason = "PROVIDER_QUOTA_EXCEEDED"
        elif "400" in message or "invalid_parameter" in message:
            reason = "MODEL_CONFIGURATION_INVALID"
        elif "timeout" in message or "unavailable" in message:
            reason = "PROVIDER_UNAVAILABLE"
        else:
            reason = "MODEL_REQUEST_FAILED"
        raise MultimodalPreprocessingError(reason) from exc

    text = " ".join((result.content or "").split())[:MULTIMODAL_INTENT_MAX_CHARS]
    if not text:
        raise MultimodalPreprocessingError("EMPTY_MODEL_RESPONSE")
    return {
        "question": text,
        "rewritten": text != (original or "").strip(),
        "original": original,
        "image_count": len(image_data_urls),
        "usage": result.usage,
        "status": "succeeded",
    }


def build_loaded_skills(assistant):
    """Snapshot active skill bindings for LensNode dispatch."""

    loaded = []
    for binding in assistant.skill_bindings.select_related(
        "skill", "environment_variable_set"
    ).filter(
        enabled=True,
        skill__enabled=True,
    ):
        skill = binding.skill
        content_hash = skill.package_hash or _content_hash(skill.definition)
        loaded.append(
            {
                "skill_uuid": str(skill.uuid),
                "skill_package_name": skill.package_name,
                "skill_name": skill.name,
                "skill_kind": skill.kind,
                "version": skill.version,
                "content_hash": content_hash,
                "definition": skill.definition,
                "package_hash": skill.package_hash,
                "package_size": skill.package_size,
                "package_manifest": skill.package_manifest,
                "load_config": binding.load_config,
                "environment_variable_set_uuid": (
                    str(binding.environment_variable_set.uuid)
                    if binding.environment_variable_set
                    else None
                ),
            }
        )
    return loaded


def build_loaded_mcps(assistant):
    """Snapshot active MCP bindings for LensNode dispatch."""

    loaded = []
    for binding in assistant.mcp_bindings.select_related(
        "mcp", "environment_variable_set"
    ).filter(enabled=True).exclude(mcp__transport="plugin"):
        loaded.append(
            {
                "mcp_uuid": str(binding.mcp.uuid),
                "mcp_name": binding.mcp.name,
                "version": binding.mcp.version,
                "content_hash": _content_hash(
                    {
                        "transport": binding.mcp.transport,
                        "endpoint": binding.mcp.endpoint,
                        "config": binding.mcp.config,
                        "environment": binding.mcp.environment,
                    }
                ),
                "transport": binding.mcp.transport,
                "endpoint": binding.mcp.endpoint,
                "config": binding.mcp.config,
                "load_config": binding.load_config,
                "environment_schema": binding.mcp.environment,
                "environment_variable_set_uuid": (
                    str(binding.environment_variable_set.uuid)
                    if binding.environment_variable_set
                    else None
                ),
            }
        )
    return loaded


def build_loaded_plugins(assistant):
    """Snapshot non-sensitive Plugin tool bindings for LensNode dispatch."""

    loaded = []
    plugins = {}
    direct_bindings = assistant.plugin_bindings.select_related(
        "connection__secret_version__material"
    ).filter(
        enabled=True,
        connection__status="active",
    ).order_by("connection__plugin_key", "connection__uuid")
    adapter_bindings = assistant.mcp_bindings.select_related(
        "mcp__connection__secret_version__material"
    ).filter(
        enabled=True,
        mcp__enabled=True,
        mcp__transport="plugin",
        mcp__connection__status="active",
    ).order_by(
        "mcp__connection__plugin_key",
        "mcp__connection__uuid",
    )
    bindings = [
        (binding.connection, None, True)
        for binding in direct_bindings
    ]
    bindings.extend(
        (binding.mcp.connection, binding.mcp.tools, False)
        for binding in adapter_bindings
    )
    for connection, selected_tool_keys, use_all_tools in bindings:
        secret_version = connection.secret_version
        if secret_version is None or secret_version.status != "active":
            continue
        if secret_version.material.status != "active":
            continue
        plugin = plugins.get(connection.plugin_key)
        if plugin is None:
            plugin = installed_plugin(connection.plugin_key)
            plugins[connection.plugin_key] = plugin
        definitions = {
            tool.key: tool
            for tool in plugin.tools
        }
        tool_keys = (
            [tool.key for tool in plugin.tools]
            if use_all_tools
            else (selected_tool_keys or [])
        )
        tools = []
        for key in tool_keys:
            tool = definitions.get(key)
            if tool is None:
                continue
            tools.append(
                {
                    "key": tool.key,
                    "description": tool.description,
                    "capability": tool.capability,
                    "capability_family": tool.capability_family,
                    "side_effect": tool.side_effect,
                    "input_schema": tool.input_schema,
                }
            )
        if tools:
            loaded.append(
                {
                    "connection_uuid": str(connection.uuid),
                    "plugin_key": plugin.key,
                    "plugin_display_name": plugin.display_name,
                    "plugin_version": plugin.version,
                    "protocol_version": plugin.protocol_version,
                    "plugin_description": plugin.description,
                    "assistant_guidance": plugin.assistant_guidance,
                    "allowed_scope": _public_plugin_scope(
                        connection.allowed_scope
                    ),
                    "tools": tools,
                }
            )
    return loaded


def build_loaded_plugin_skills(assistant, loaded_plugins=None):
    """Build an advisory virtual Skill for each Plugin Connection."""

    loaded_plugins = (
        build_loaded_plugins(assistant)
        if loaded_plugins is None
        else loaded_plugins
    )
    virtual_skills = []
    seen_connections = set()
    for plugin in loaded_plugins:
        if not isinstance(plugin, dict):
            continue
        plugin_key = str(plugin.get("plugin_key") or "").strip()
        connection_uuid = str(plugin.get("connection_uuid") or "").strip()
        version = str(plugin.get("plugin_version") or "").strip()
        if not plugin_key or not connection_uuid or not version:
            continue
        if connection_uuid in seen_connections:
            continue
        seen_connections.add(connection_uuid)
        guidance = plugin.get("assistant_guidance") or {}
        if not isinstance(guidance, dict):
            guidance = {}
        summary = str(
            guidance.get("summary")
            or plugin.get("plugin_description")
            or plugin.get("plugin_display_name")
            or plugin_key
        ).strip()[:600]
        when_to_use = [
            str(value).strip()[:240]
            for value in (guidance.get("when_to_use") or [])[:8]
            if str(value).strip()
        ]
        topics = []
        for topic in (guidance.get("topics") or [])[:24]:
            if not isinstance(topic, dict):
                continue
            key = str(topic.get("key") or "").strip()[:64]
            topic_summary = str(topic.get("summary") or "").strip()[:600]
            details = str(topic.get("details") or "").strip()[:6000]
            if key:
                topics.append(
                    {
                        "key": key,
                        "summary": topic_summary,
                        "details": details,
                    }
                )
        tools = [
            {
                "description": str(item.get("description") or "")[:600],
                "capability": str(item.get("capability") or "")[:128],
                "side_effect": str(item.get("side_effect") or "")[:32],
            }
            for item in (plugin.get("tools") or [])
            if isinstance(item, dict)
        ]
        descriptor = {
            "plugin_virtual": True,
            "plugin_key": plugin_key,
            "plugin_display_name": str(
                plugin.get("plugin_display_name") or plugin_key
            )[:160],
            "plugin_version": version,
            "description": summary,
            "summary": summary,
            "when_to_use": when_to_use,
            "topics": topics,
            "tools": tools,
            "allowed_scope": plugin.get("allowed_scope") or {},
        }
        content_hash = _content_hash(descriptor)
        scope_tag = hashlib.sha256(
            connection_uuid.encode("utf-8")
        ).hexdigest()[:12]
        virtual_skills.append(
            {
                "skill_uuid": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "sourcelens:plugin-skill:" + connection_uuid,
                    )
                ),
                "skill_package_name": (
                    f"plugin-virtual-{plugin_key}-{scope_tag}"
                ),
                "skill_name": (
                    f"{descriptor['plugin_display_name']} Plugin"
                ),
                "skill_kind": "plugin_virtual",
                "version": version,
                "content_hash": content_hash,
                "definition": descriptor,
                "package_hash": None,
                "package_size": 0,
                "package_manifest": {
                    "file_count": 4,
                    "directories": ["references"],
                },
                "load_config": {"mode": "context", "inject": True},
            }
        )
    return virtual_skills


def _public_plugin_scope(scope):
    """Return bounded, non-sensitive Connection scope for Skill references."""

    sensitive = {
        "secret",
        "token",
        "password",
        "credential",
        "material",
        "endpoint",
        "url",
    }

    def clean(value, key=""):
        lowered = key.lower()
        if any(term in lowered for term in sensitive):
            return None
        if isinstance(value, dict):
            result = {}
            for item_key, item_value in value.items():
                cleaned = clean(item_value, str(item_key))
                if cleaned is not None:
                    result[str(item_key)[:64]] = cleaned
            return result
        if isinstance(value, list):
            return [
                cleaned
                for item in value[:200]
                if (cleaned := clean(item, key)) is not None
            ]
        if isinstance(value, (str, int, float, bool)):
            return str(value)[:500] if isinstance(value, str) else value
        return None

    return clean(copy.deepcopy(scope)) or {}


def _content_hash(value):
    """Return a stable sha256 hash for JSON-serializable content."""

    payload = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_loaded_skill_environment(loaded_skills):
    """Add decrypted per-Skill values to an ephemeral runtime payload."""

    runtime_skills = []
    for skill in loaded_skills or []:
        runtime_skill = dict(skill)
        variable_set_uuid = skill.get("environment_variable_set_uuid")
        variable_set = None
        if variable_set_uuid:
            variable_set = EnvironmentVariableSet.objects.filter(
                uuid=variable_set_uuid,
                enabled=True,
            ).first()
        values = variable_set.get_values() if variable_set else {}
        declarations = (skill.get("definition") or {}).get("environment") or []
        declared_names = {
            item.get("name")
            for item in declarations
            if isinstance(item, dict) and item.get("name")
        }
        runtime_skill["environment"] = {
            name: str(values[name]) for name in declared_names if name in values
        }
        runtime_skills.append(runtime_skill)
    return runtime_skills


def resolve_loaded_mcp_environment(loaded_mcps):
    """Add decrypted per-MCP values to an ephemeral runtime payload."""

    runtime_mcps = []
    for mcp in loaded_mcps or []:
        runtime_mcp = dict(mcp)
        variable_set_uuid = mcp.get("environment_variable_set_uuid")
        variable_set = None
        if variable_set_uuid:
            variable_set = EnvironmentVariableSet.objects.filter(
                uuid=variable_set_uuid,
                enabled=True,
            ).first()
        values = variable_set.get_values() if variable_set else {}
        declarations = mcp.get("environment_schema") or []
        declared_names = {
            item.get("name")
            for item in declarations
            if isinstance(item, dict) and item.get("name")
        }
        runtime_mcp["environment"] = {
            name: str(values[name]) for name in declared_names if name in values
        }
        references = declared_environment_references(
            {
                "endpoint": mcp.get("endpoint"),
                "config": mcp.get("config") or {},
            },
            declarations,
        )
        runtime_mcp["endpoint"] = expand_environment_references(
            mcp.get("endpoint"),
            runtime_mcp["environment"],
        )
        runtime_mcp["config"] = expand_environment_references(
            mcp.get("config") or {},
            runtime_mcp["environment"],
        )
        runtime_mcp["environment_resolved"] = all(
            str(runtime_mcp["environment"].get(name) or "") for name in references
        )
        runtime_mcps.append(runtime_mcp)
    return runtime_mcps


def _runtime_subagents(subagents):
    """Resolve sensitive environment values only for each selected subagent."""

    return [
        {
            **subagent,
            "loaded_skills": resolve_loaded_skill_environment(
                subagent.get("loaded_skills")
            ),
            "loaded_mcps": resolve_loaded_mcp_environment(subagent.get("loaded_mcps")),
        }
        for subagent in subagents
        if isinstance(subagent, dict)
    ]


def task_names(lensnode):
    """Return task names reported by a LensNode."""

    names = set()
    for task in lensnode.tasks or []:
        if isinstance(task, dict) and task.get("name"):
            names.add(task["name"])
    return names


def execution_task_for_capability(capability):
    """Return the LensNode execution task for an Assistant capability."""

    return capability


def available_dir_paths(lensnode):
    """Return directory paths reported by a LensNode."""

    paths = set()
    for item in lensnode.available_dirs or []:
        if isinstance(item, str):
            paths.add(item)
        elif isinstance(item, dict) and item.get("path"):
            paths.add(item["path"])
    return paths


def validate_run_dispatch(run):
    """Validate current runtime state and the frozen execution snapshot."""

    assistant = run.session.assistant
    lensnode = run.lensnode
    execution = run.execution
    if assistant.status != Assistant.Status.ACTIVE:
        raise LensNodeDispatchError("ASSISTANT_ARCHIVED")
    if lensnode is None:
        raise LensNodeDispatchError("LENSNODE_REQUIRED")
    if lensnode.status == LensNode.Status.DRAINING:
        raise LensNodeDispatchError("LENSNODE_DRAINING")
    if lensnode.status != LensNode.Status.ONLINE:
        raise LensNodeDispatchError("LENSNODE_OFFLINE")
    if lensnode.enrollment_status != LensNode.EnrollmentStatus.APPROVED:
        raise LensNodeDispatchError("LENSNODE_NOT_APPROVED")
    if lensnode.token_revoked:
        raise LensNodeDispatchError("LENSNODE_TOKEN_REVOKED")
    if execution.task not in task_names(lensnode):
        raise LensNodeDispatchError("LENSNODE_TASK_UNAVAILABLE")

    runtime_skills = resolve_loaded_skill_environment(execution.loaded_skills)
    runtime_mcps = resolve_loaded_mcp_environment(execution.loaded_mcps)
    if (
        execution.task == "general_chat"
        and run.session.routing_mode != Session.RoutingMode.SMART
    ):
        if not runtime_skills and not execution.loaded_plugins:
            raise LensNodeDispatchError("GENERAL_CHAT_SKILL_REQUIRED")
    else:
        available = available_dir_paths(lensnode)
        session_paths = {
            item["path"] for item in session_source_dirs(run.session)
        }
        for item in execution.target_dirs or []:
            path = item.get("path")
            if path not in available and path not in session_paths:
                raise LensNodeDispatchError("LENSNODE_DIR_UNAVAILABLE")

    for skill in runtime_skills:
        declarations = (skill.get("definition") or {}).get("environment") or []
        required = {
            item["name"]
            for item in declarations
            if isinstance(item, dict) and item.get("required") and item.get("name")
        }
        values = skill.get("environment") or {}
        if any(not str(values.get(name) or "") for name in required):
            raise LensNodeDispatchError("SKILL_ENVIRONMENT_REQUIRED")

    for mcp, snapshot_mcp in zip(
        runtime_mcps,
        execution.loaded_mcps or [],
        strict=True,
    ):
        declarations = mcp.get("environment_schema") or []
        required = {
            item["name"]
            for item in declarations
            if isinstance(item, dict) and item.get("required") and item.get("name")
        }
        values = mcp.get("environment") or {}
        referenced = declared_environment_references(
            {
                "endpoint": snapshot_mcp.get("endpoint"),
                "config": snapshot_mcp.get("config") or {},
            },
            declarations,
        )
        if any(not str(values.get(name) or "") for name in required | referenced):
            raise LensNodeDispatchError("MCP_ENVIRONMENT_REQUIRED")


TOKEN_BUDGET_PROFILES = {
    Assistant.TokenBudgetProfile.STANDARD: {
        "max_tokens": 200000,
        "final_reserve_tokens": 40000,
    },
    Assistant.TokenBudgetProfile.DEEP: {
        "max_tokens": 500000,
        "final_reserve_tokens": 75000,
    },
    Assistant.TokenBudgetProfile.UNLIMITED: {
        "max_tokens": 0,
        "final_reserve_tokens": 0,
    },
}

TOKEN_BUDGET_PROFILE_BY_AGENT_ROUNDS = {
    Assistant.AgentRounds.FLASH: Assistant.TokenBudgetProfile.STANDARD,
    Assistant.AgentRounds.FAST: Assistant.TokenBudgetProfile.STANDARD,
    Assistant.AgentRounds.BALANCED: Assistant.TokenBudgetProfile.STANDARD,
    Assistant.AgentRounds.DEEP: Assistant.TokenBudgetProfile.DEEP,
    Assistant.AgentRounds.MAX: Assistant.TokenBudgetProfile.UNLIMITED,
}

RUN_TIMEOUT_SECONDS = 3600

AGENT_TURNS_BY_ROUNDS = {
    Assistant.AgentRounds.FLASH: 5,
    Assistant.AgentRounds.FAST: 13,
    Assistant.AgentRounds.BALANCED: 26,
    Assistant.AgentRounds.DEEP: 50,
    Assistant.AgentRounds.MAX: 100,
}


def token_budget_for_profile(profile):
    """Return the bounded token budget for an Assistant profile."""

    selected = profile if profile in TOKEN_BUDGET_PROFILES else "standard"
    return {
        "profile": selected,
        **TOKEN_BUDGET_PROFILES[selected],
    }


def token_budget_profile_for_rounds(agent_rounds):
    """Return the Token budget profile bound to an execution strategy."""

    return TOKEN_BUDGET_PROFILE_BY_AGENT_ROUNDS.get(
        agent_rounds,
        Assistant.TokenBudgetProfile.STANDARD,
    )


def token_budget_for_rounds(agent_rounds):
    """Return the Token budget bound to an execution strategy."""

    return token_budget_for_profile(
        token_budget_profile_for_rounds(agent_rounds)
    )


def run_timeout_for_rounds(agent_rounds):
    """Return the system wall-clock safety boundary for a Run."""

    del agent_rounds
    return RUN_TIMEOUT_SECONDS


def max_agent_turns_for_rounds(agent_rounds):
    """Return the model-turn budget for one Assistant analysis level."""

    return AGENT_TURNS_BY_ROUNDS.get(
        agent_rounds,
        AGENT_TURNS_BY_ROUNDS[Assistant.AgentRounds.BALANCED],
    )


def tool_budget_for_assistant(assistant):
    """Return an explicit Assistant tool-call budget override, if configured."""

    settings_payload = assistant.settings
    if not isinstance(settings_payload, dict):
        return None
    tool_budget = settings_payload.get("tool_budget")
    if not isinstance(tool_budget, dict) or "max_calls" not in tool_budget:
        return None
    try:
        max_calls = int(tool_budget["max_calls"])
    except (TypeError, ValueError):
        return None
    return {"max_calls": max(max_calls, 0)}


@transaction.atomic
def create_run_execution_snapshot(
    run,
    answer_language=None,
    routing_assistant_uuids=None,
    routing_assistant_explicit=False,
    agent_rounds=None,
):
    """Create or return the per-run LensNode execution snapshot."""

    assistant = run.session.assistant
    agent_rounds = agent_rounds or assistant.agent_rounds
    token_budget = token_budget_for_rounds(agent_rounds)
    profile = getattr(run.session.user, "profile", None)
    answer_language = normalize_answer_language(
        answer_language or getattr(profile, "language", None)
    )
    runtime_snapshot = _build_run_runtime_snapshot(
        assistant,
        run.lensnode,
        answer_language,
        run.session,
        routing_assistant_uuids,
        routing_assistant_explicit,
    )
    tool_budget = tool_budget_for_assistant(assistant)
    if tool_budget is not None:
        runtime_snapshot["tool_budget"] = tool_budget
    loaded_plugins = build_loaded_plugins(assistant)
    loaded_skills = build_loaded_skills(assistant)
    loaded_skills.extend(
        build_loaded_plugin_skills(
            assistant,
            loaded_plugins=loaded_plugins,
        )
    )
    execution, _ = RunExecution.objects.get_or_create(
        run=run,
        defaults={
            "lensnode": run.lensnode,
            "task": execution_task_for_capability(assistant.capability),
            "loaded_skills": loaded_skills,
            "loaded_mcps": build_loaded_mcps(assistant),
            "loaded_plugins": loaded_plugins,
            "agent_rounds": agent_rounds,
            "run_timeout_s": run_timeout_for_rounds(agent_rounds),
            "target_dirs": session_source_dirs(run.session),
            "runtime_snapshot": runtime_snapshot,
            "token_budget_profile": token_budget["profile"],
            "token_budget_max_tokens": token_budget["max_tokens"],
            "token_budget_final_reserve_tokens": token_budget["final_reserve_tokens"],
            "status": RunExecution.Status.QUEUED,
        },
    )
    return execution


def _build_run_runtime_snapshot(
    assistant,
    lensnode,
    answer_language,
    session,
    routing_assistant_uuids=None,
    routing_assistant_explicit=False,
):
    """Return execution provenance that later edits cannot change."""

    model_refs = {
        "agent": str(assistant.agent_model_ref or ""),
        "multimodal": str(assistant.multimodal_model_ref or ""),
    }
    model_config_hashes = {
        name: _llm_config_hash(model_ref)
        for name, model_ref in model_refs.items()
        if model_ref
    }
    settings_payload = assistant.settings or {}
    subagents = []
    if session.routing_mode == Session.RoutingMode.SMART:
        configured = (
            routing_assistant_uuids
            if routing_assistant_uuids is not None
            else session.allowed_assistant_uuids or []
        )
        subagents = list(
            Assistant.objects.visible_to(session.user)
            .filter(
                uuid__in=configured,
                status=Assistant.Status.ACTIVE,
                capability__in=[
                    Assistant.Capability.GENERAL_CHAT,
                    Assistant.Capability.CODE_ANALYSIS,
                    Assistant.Capability.KNOWLEDGE_QA,
                ],
                is_system=False,
            )
            .prefetch_related(
                "skill_bindings__skill",
                "skill_bindings__environment_variable_set",
                "mcp_bindings__mcp",
                "mcp_bindings__environment_variable_set",
                "plugin_bindings__connection__secret_version__material",
            )
        )
        subagents = subagents[:MAX_SUBAGENTS_PER_RUN]
        resolved_subagents = []
        for item in subagents:
            loaded_plugins = build_loaded_plugins(item)
            loaded_skills = build_loaded_skills(item)
            loaded_skills.extend(
                build_loaded_plugin_skills(
                    item,
                    loaded_plugins=loaded_plugins,
                )
            )
            resolved_subagents.append(
                {
                    "uuid": str(item.uuid),
                    "name": item.name,
                    "description": item.description,
                    "routing_description": build_routing_description(
                        item,
                        answer_language,
                    ),
                    "capability": item.capability,
                    "task": execution_task_for_capability(item.capability),
                    "lensnode_uuid": (
                        str(item.lensnode.uuid) if item.lensnode_id else ""
                    ),
                    "target_dirs": (
                        []
                        if item.capability
                        == Assistant.Capability.GENERAL_CHAT
                        else item.selected_dirs
                    ),
                    "workspace_guide": item.workspace_guide,
                    "agent_model_ref": str(item.agent_model_ref or ""),
                    "settings": item.settings or {},
                    "loaded_plugins": loaded_plugins,
                    "loaded_skills": loaded_skills,
                    "loaded_mcps": build_loaded_mcps(item),
                }
            )
        subagents = resolved_subagents
    return {
        "assistant_uuid": str(assistant.uuid),
        "assistant_updated_at": assistant.updated_at.isoformat(),
        "lensnode_uuid": str(lensnode.uuid) if lensnode else "",
        "lensnode_agent_version": (lensnode.agent_version if lensnode else ""),
        "answer_language": normalize_answer_language(answer_language),
        "model_refs": model_refs,
        "model_config_hashes": model_config_hashes,
        "settings": settings_payload,
        "settings_hash": _canonical_hash(settings_payload),
        "workspace_guide": assistant.workspace_guide,
        "assistant_capability": assistant.capability,
        "assistant_mode": assistant.mode,
        "routing_mode": session.routing_mode,
        "allowed_assistant_uuids": list(
            routing_assistant_uuids
            if routing_assistant_uuids is not None
            else session.allowed_assistant_uuids or []
        ),
        "subagents": subagents,
        "routing_assistant_uuids": (
            list(routing_assistant_uuids or []) if routing_assistant_explicit else []
        ),
        "routing_assistant_uuid": (
            str(routing_assistant_uuids[0])
            if routing_assistant_uuids and len(routing_assistant_uuids) == 1
            else ""
        ),
        "routing_assistant_explicit": bool(routing_assistant_explicit),
    }


def _llm_config_hash(model_ref):
    """Return a stable hash for the referenced model configuration."""

    try:
        from agentcore_metering.adapters.django.models import LLMConfig

        config = (
            LLMConfig.objects.filter(uuid=model_ref)
            .values_list(
                "config",
                flat=True,
            )
            .first()
        )
    except (ImportError, ValueError):
        return ""
    return _canonical_hash(config) if config is not None else ""


def _canonical_hash(value):
    """Return the SHA-256 of canonical JSON data."""

    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def build_run_history(run):
    """Return trusted prior Run turns in chronological order."""

    history, _ = _build_run_history_data(run)
    return history


def _question_references_historical_attachment(question):
    """Return whether the current question clearly refers to an older file."""

    lowered = (question or "").lower()
    historical_marker = any(
        marker in lowered
        for marker in (
            "previous",
            "earlier",
            "之前",
            "刚才",
            "上一个",
            "上述",
        )
    )
    return bool(
        historical_marker and _clearly_referenced_attachment_kind(question)
    )


def _run_has_attachment_context(run):
    """Return whether a prior Run used an image or document attachment."""

    has_input_attachments = getattr(run, "has_input_attachments", None)
    if has_input_attachments is None:
        has_input_attachments = run.input_message.attachments.exists()
    if has_input_attachments:
        return True
    try:
        snapshot = run.execution.runtime_snapshot or {}
    except RunExecution.DoesNotExist:
        snapshot = {}
    return bool(
        snapshot.get("session_attachment_uuids")
        or snapshot.get("document_attachment_count")
    )


def _run_has_direct_attachment(run):
    """Return whether the current message directly supplied an attachment."""

    try:
        snapshot = run.execution.runtime_snapshot or {}
    except RunExecution.DoesNotExist:
        snapshot = {}
    if "direct_attachment_uuids" in snapshot:
        return bool(snapshot["direct_attachment_uuids"])
    if run.input_message.attachments.exists():
        return True
    return bool(snapshot.get("direct_attachment_uuids"))


def build_clarification_continuation_question(run, current_question):
    """Restore the original request and answers for a clarification retry."""

    if not run.retry_of_run_id:
        return current_question
    budget = get_history_budget()
    runs_by_id = {
        item.pk: item
        for item in Run.objects.filter(
            session_id=run.session_id,
            input_message__sequence__lte=run.input_message.sequence,
        ).select_related("input_message")
    }
    current = runs_by_id.get(run.pk, run)
    turns = []
    seen = set()
    while current.pk not in seen:
        seen.add(current.pk)
        if not current.retry_of_run_id:
            break
        parent = runs_by_id.get(current.retry_of_run_id)
        if parent is None:
            break
        detail = parent.termination_detail or {}
        request = detail.get("request")
        if (
            parent.status != Run.Status.AWAITING_USER_INPUT
            or detail.get("reason") != "needs_user_input"
            or not isinstance(request, dict)
        ):
            break
        clarification_question = str(request.get("question") or "").strip()
        answer = str(current.input_message.content or "").strip()
        if not clarification_question or not answer:
            break
        turns.append(
            (
                clarification_question[: budget["message_chars"]],
                answer[:CLARIFICATION_MAX_ANSWER_CHARS],
            )
        )
        if len(turns) > min(budget["pairs"], CLARIFICATION_MAX_PAIRS):
            turns.pop()
        current = parent

    if not turns:
        return current_question
    original_question = str(current.input_message.content or "").strip()

    sections = [
        "Original user request:",
        original_question[:CLARIFICATION_MAX_ORIGINAL_CHARS]
        or "(attachment-only request)",
    ]
    selected_turns = []
    clarification_chars = 0
    for clarification_question, answer in turns:
        block = [
            "Clarification question:",
            clarification_question,
            "User clarification:",
            answer,
        ]
        block_chars = sum(len(item) for item in block)
        if clarification_chars + block_chars > budget["total_chars"]:
            continue
        selected_turns.append(block)
        clarification_chars += block_chars
    for block in reversed(selected_turns):
        sections.extend(["", *block])

    raw_question = str(run.input_message.content or "").strip()
    if current_question and current_question.strip() != raw_question:
        sections.extend(
            [
                "",
                "Current execution prompt:",
                current_question[: budget["message_chars"]],
            ]
        )
    return "\n".join(sections)[:CLARIFICATION_MAX_PROMPT_CHARS]


def build_run_history_artifacts(run):
    """Return bounded deliverables from trusted prior Run attempts."""

    all_prior_runs = list(
        Run.objects.filter(
            session=run.session,
            input_message__sequence__lt=run.input_message.sequence,
        )
        .select_related("input_message")
        .prefetch_related("output_files")
        .order_by("input_message__sequence")
    )
    runs_by_id = {item.pk: item for item in all_prior_runs}
    cutoff_sequence = run.input_message.sequence
    if run.retry_of_run_id:
        root, _ = _retry_chain_root(run, runs_by_id)
        cutoff_sequence = root.input_message.sequence
    prior_runs = [
        item for item in all_prior_runs if item.input_message.sequence < cutoff_sequence
    ]
    latest_attempts = _latest_retry_attempts(prior_runs)
    selected = []
    total_bytes = 0
    for prior in reversed(latest_attempts):
        if not _assistant_output_is_trusted(prior):
            continue
        for output in reversed(list(prior.output_files.all())):
            byte_size = int(output.byte_size or 0)
            content_hash = (output.content_hash or "").lower()
            if (
                not output.file
                or len(content_hash) != 64
                or any(
                    character not in "0123456789abcdef" for character in content_hash
                )
                or byte_size > settings.DELIVERABLE_MAX_BYTES
                or total_bytes + byte_size > settings.DELIVERABLE_MAX_BYTES
            ):
                continue
            selected.append(
                {
                    "uuid": str(output.uuid),
                    "filename": output.filename,
                    "content_type": output.content_type,
                    "byte_size": byte_size,
                    "content_hash": content_hash,
                    "source_run_uuid": str(prior.uuid),
                }
            )
            total_bytes += byte_size
            if len(selected) >= HISTORY_ARTIFACT_MAX_FILES:
                return list(reversed(selected))
    return list(reversed(selected))


def build_run_history_metadata(run):
    """Return non-sensitive counts describing Run history filtering."""

    _, metadata = _build_run_history_data(run)
    return metadata


def build_run_history_manifest(run):
    """Return identifiers and hashes for history actually sent to execution."""

    _, metadata = _build_run_history_data(run)
    return metadata["included_history"]


def _build_run_history_data(run):
    """Build trusted history and the corresponding filter counts."""

    budget = get_history_budget()
    all_prior_runs = list(
        Run.objects.filter(
            session=run.session,
            input_message__sequence__lt=run.input_message.sequence,
        )
        .select_related(
            "execution",
            "input_message",
            "output_message",
        )
        .annotate(
            has_input_attachments=Exists(
                MessageAttachment.objects.filter(
                    message_id=OuterRef("input_message_id")
                )
            )
        )
        .order_by("input_message__sequence")
    )
    runs_by_id = {item.pk: item for item in all_prior_runs}
    cutoff_sequence = run.input_message.sequence
    superseded_current_attempts = 0
    if run.retry_of_run_id:
        root, superseded_current_attempts = _retry_chain_root(
            run,
            runs_by_id,
        )
        cutoff_sequence = root.input_message.sequence
    prior_runs = [
        item for item in all_prior_runs if item.input_message.sequence < cutoff_sequence
    ]
    attachment_history_runs_removed = 0
    if _run_has_direct_attachment(run) and not (
        _question_references_historical_attachment(run.input_message.content)
    ):
        attachment_history_runs_removed = sum(
            _run_has_attachment_context(item) for item in prior_runs
        )
        prior_runs = [
            item
            for item in prior_runs
            if not _run_has_attachment_context(item)
        ]
    latest_attempts = _latest_retry_attempts(prior_runs)
    limited_pairs = []
    limited_manifests = []
    total_chars = 0
    for prior in reversed(latest_attempts):
        entries = _trusted_history_entries(
            prior,
            budget["message_chars"],
        )
        pair_chars = sum(len(item["content"]) for item in entries)
        if total_chars + pair_chars > budget["total_chars"]:
            break
        if entries:
            limited_pairs.append(entries)
            message_by_role = {
                "user": prior.input_message,
                "assistant": prior.output_message,
            }
            limited_manifests.append(
                {
                    "run_uuid": str(prior.uuid),
                    "messages": [
                        {
                            "message_uuid": str(message_by_role[item["role"]].uuid),
                            "role": item["role"],
                            "chars": len(item["content"]),
                            "sha256": hashlib.sha256(
                                item["content"].encode()
                            ).hexdigest(),
                        }
                        for item in entries
                        if message_by_role[item["role"]] is not None
                    ],
                }
            )
            total_chars += pair_chars
        if len(limited_pairs) >= budget["pairs"]:
            break
    history = [item for pair in reversed(limited_pairs) for item in pair]
    metadata = {
        "history_runs_before_filtering": len(all_prior_runs),
        "history_runs_after_filtering": len(latest_attempts),
        "superseded_retry_attempts_removed": (
            superseded_current_attempts + len(prior_runs) - len(latest_attempts)
        ),
        "non_completed_assistant_outputs_excluded": sum(
            bool(
                prior.output_message_id and (prior.output_message.content or "").strip()
            )
            and not _assistant_output_is_trusted(prior)
            for prior in latest_attempts
        ),
        "attachment_history_runs_removed": attachment_history_runs_removed,
        "included_history": list(reversed(limited_manifests)),
    }
    return history, metadata


def _retry_chain_root(run, runs_by_id):
    """Return the first Run in a valid Retry chain."""

    seen = set()
    current = run
    attempts = 0
    while current.retry_of_run_id and current.pk not in seen:
        seen.add(current.pk)
        parent = runs_by_id.get(current.retry_of_run_id)
        if parent is None:
            break
        current = parent
        attempts += 1
    return current, attempts


def _latest_retry_attempts(prior_runs):
    """Collapse each Retry chain to its latest prior attempt."""

    runs_by_id = {item.pk: item for item in prior_runs}
    latest_by_root = {}
    for prior in prior_runs:
        root_id = prior.pk
        current = prior
        seen = set()
        while current.retry_of_run_id in runs_by_id and current.pk not in seen:
            seen.add(current.pk)
            current = runs_by_id[current.retry_of_run_id]
            root_id = current.pk
        latest_by_root[root_id] = prior
    return sorted(
        latest_by_root.values(),
        key=lambda item: item.input_message.sequence,
    )


def _trusted_history_entries(run, message_chars=HISTORY_MAX_MESSAGE_CHARS):
    """Build one bounded Run turn, excluding untrusted assistant output."""

    entries = []
    question = (run.input_message.content or "").strip()
    if question:
        entries.append(
            {
                "role": Message.Role.USER,
                "content": question[:message_chars],
            }
        )
    if _assistant_output_is_trusted(run) and run.output_message_id:
        answer = (run.output_message.content or "").strip()
        if answer:
            entries.append(
                {
                    "role": Message.Role.ASSISTANT,
                    "content": answer[:message_chars],
                }
            )
    return entries


def _assistant_output_is_trusted(run):
    """Return whether a Run's assistant output is safe as history."""

    if run.status == Run.Status.DONE:
        return run.outcome in {"", Run.Outcome.COMPLETED}
    return (
        run.status == Run.Status.AWAITING_USER_INPUT
        and (run.termination_detail or {}).get("reason") == "needs_user_input"
    )


def _recent_history_context(run):
    """Return the recent turns as a 'role: content' text block."""

    history = build_run_history(run)[-(QUERY_REWRITE_HISTORY_TURNS * 2) :]
    return "\n".join(f"{item['role']}: {item['content']}" for item in history)


def rewrite_query(run):
    """Rewrite a run's question into a contextual, search-optimized query.

    Uses the assistant's preprocess model to resolve conversational
    references and normalize wording toward the documents' terminology.
    Falls back to the original question when no preprocess model is set
    or the call fails, so dispatch never blocks on this step.
    """

    assistant = run.session.assistant
    original = run_execution_question(run)
    if not assistant.preprocess_model_ref:
        return {"question": original, "rewritten": False}

    context = _recent_history_context(run)
    user = (
        f"Conversation so far:\n{context}\n\n" if context else ""
    ) + f"Latest question: {original}\n\nRewritten search query:"
    try:
        result = run_completion(
            model_ref=assistant.preprocess_model_ref,
            system=QUERY_REWRITE_SYSTEM,
            user=user,
            node_name="lens.query_rewrite",
            user_id=run.session.user_id,
        )
    except Exception as exc:
        return {"question": original, "rewritten": False, "error": str(exc)}

    text = (result.content or "").strip()
    rewritten = " ".join(text.split())[:QUERY_REWRITE_MAX_CHARS]
    if not rewritten:
        return {
            "question": original,
            "rewritten": False,
            "usage": result.usage,
        }
    return {
        "question": rewritten,
        "rewritten": rewritten != original.strip(),
        "original": original,
        "usage": result.usage,
    }


def dispatch_run_to_lensnode(
    run,
    rewritten_question,
    subject_documents=None,
    resume=False,
    dispatch_id=None,
):
    """Send a run_start command to the connected LensNode.

    ``resume`` marks a re-dispatch of a run that already has a checkpoint on
    the node: the node skips re-injecting the question/history and continues
    the run from its last checkpoint.
    """

    execution = run.execution
    runtime_snapshot = execution.runtime_snapshot or {}
    model_refs = runtime_snapshot.get("model_refs") or {}
    runtime_settings = runtime_snapshot.get("settings")
    if not isinstance(runtime_settings, dict):
        runtime_settings = run.session.assistant.settings
    features_payload = (
        runtime_settings.get("features") if isinstance(runtime_settings, dict) else None
    )
    if not isinstance(features_payload, dict):
        features_payload = {}
    if subject_documents is None:
        subject_documents = get_run_document_attachments(run.uuid)
    subject_documents = [
        {
            "uuid": item["uuid"],
            "original_name": item["original_name"],
            "mime_type": item["mime_type"],
            "byte_size": item["byte_size"],
            "content_hash": item["content_hash"],
        }
        for item in subject_documents
    ]
    if subject_documents and not supports_document_attachments(run.lensnode):
        raise LensNodeDispatchError("DOCUMENT_ATTACHMENTS_UNSUPPORTED_BY_LENSNODE")
    channel_layer = get_channel_layer()
    if channel_layer is None:
        raise LensNodeDispatchError("LENS_CHANNEL_LAYER_UNAVAILABLE")

    agent_rounds = (
        execution.agent_rounds
        or run.session.assistant.agent_rounds
        or Assistant.AgentRounds.BALANCED
    )
    run_timeout_s = execution.run_timeout_s or run_timeout_for_rounds(agent_rounds)
    elapsed_s = (
        max((timezone.now() - run.started_at).total_seconds(), 0)
        if run.started_at
        else 0
    )
    remaining_run_timeout_s = max(run_timeout_s - elapsed_s, 0)
    profile = getattr(run.session.user, "profile", None)
    history_artifacts = (
        build_run_history_artifacts(run) if execution.task == "general_chat" else []
    )
    answer_language = normalize_answer_language(
        runtime_snapshot.get("answer_language") or getattr(profile, "language", None)
    )
    selected_image_uuids = set(runtime_snapshot.get("session_attachment_uuids") or [])
    image_attachments = MessageAttachment.objects.filter(
        session=run.session,
        kind=MessageAttachment.Kind.IMAGE,
        uuid__in=selected_image_uuids,
    )
    if not selected_image_uuids:
        image_attachments = run.input_message.attachments.filter(
            kind=MessageAttachment.Kind.IMAGE
        )
    image_data_urls = []
    for attachment in image_attachments.order_by("created_at", "pk"):
        data_url = attachment_data_url(attachment)
        if not data_url:
            raise LensNodeDispatchError("ATTACHMENT_UNREADABLE")
        image_data_urls.append(data_url)
    agent_model_ref = (
        model_refs.get("multimodal")
        or str(run.session.assistant.multimodal_model_ref or "")
        if image_data_urls
        else model_refs.get("agent") or str(run.session.assistant.agent_model_ref or "")
    )
    rewritten_question = build_clarification_continuation_question(
        run,
        rewritten_question,
    )
    trace_cursor = run.trace_events.aggregate(
        last_sequence=Max("sequence"),
        last_attempt=Max("attempt"),
    )
    last_trace_sequence = int(trace_cursor["last_sequence"] or 0)
    last_trace_attempt = int(trace_cursor["last_attempt"] or 0)
    async_to_sync(channel_layer.group_send)(
        lensnode_group_name(run.lensnode.uuid),
        {
            "type": "lensnode.command",
            "payload": {
                "type": "run_start",
                "run_uuid": str(run.uuid),
                "parent_run_uuid": (
                    str(run.parent_run.uuid) if run.parent_run_id else ""
                ),
                "dispatch_id": str(dispatch_id) if dispatch_id else None,
                "task": execution.task,
                "features": features_payload,
                "question": rewritten_question,
                "image_data_urls": image_data_urls,
                "answer_language": answer_language,
                "subject_documents": subject_documents,
                "vision_model_ref": (
                    model_refs.get("multimodal")
                    or str(run.session.assistant.multimodal_model_ref or "")
                ),
                "history": build_run_history(run),
                "history_artifacts": history_artifacts,
                "target_dirs": execution.target_dirs,
                "workspace_guide": runtime_snapshot.get("workspace_guide", ""),
                "assistant_capability": runtime_snapshot.get(
                    "assistant_capability", "general_chat"
                ),
                "routing_mode": runtime_snapshot.get("routing_mode", "direct"),
                "routing_assistant_uuid": runtime_snapshot.get(
                    "routing_assistant_uuid", ""
                ),
                "routing_assistant_uuids": runtime_snapshot.get(
                    "routing_assistant_uuids", []
                ),
                "routing_assistant_explicit": bool(
                    runtime_snapshot.get("routing_assistant_explicit")
                ),
                "subagents": _runtime_subagents(runtime_snapshot.get("subagents", [])),
                "loaded_skills": resolve_loaded_skill_environment(
                    execution.loaded_skills
                ),
                "loaded_mcps": resolve_loaded_mcp_environment(execution.loaded_mcps),
                "loaded_plugins": execution.loaded_plugins,
                "agent_model_ref": (agent_model_ref),
                "agent_rounds": agent_rounds,
                "max_agent_turns": max_agent_turns_for_rounds(agent_rounds),
                "resume": resume,
                "run_timeout_s": run_timeout_s,
                "remaining_run_timeout_s": remaining_run_timeout_s,
                "token_budget": {
                    "profile": execution.token_budget_profile,
                    "max_tokens": execution.token_budget_max_tokens,
                    "final_reserve_tokens": (
                        execution.token_budget_final_reserve_tokens
                    ),
                },
                "tool_budget": runtime_snapshot.get("tool_budget"),
                "trace_cursor": last_trace_sequence,
                "trace_attempt": max(
                    last_trace_attempt + (1 if resume else 0),
                    1,
                ),
                "trace_context": {
                    "trace_id": trace_id_for_run(run.uuid),
                    "root_observation_id": root_observation_id_for_run(run.uuid),
                },
                "settings": runtime_settings,
            },
        },
    )
    logger.info(
        "run command published run_uuid=%s dispatch_id=%s resume=%s",
        run.uuid,
        dispatch_id,
        resume,
    )


def cancel_run_on_lensnode(run):
    """Send a run_cancel command to the connected LensNode."""

    if run.lensnode is None:
        return None
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return None
    async_to_sync(channel_layer.group_send)(
        lensnode_group_name(run.lensnode.uuid),
        {
            "type": "lensnode.command",
            "payload": {
                "type": "run_cancel",
                "run_uuid": str(run.uuid),
            },
        },
    )
    return None


@transaction.atomic
def cancel_descendant_runs(root_run):
    """Cancel active descendant Runs and return their node payloads."""

    pending = [root_run.pk]
    descendants = []
    seen = set()
    while pending:
        parent_id = pending.pop()
        child_ids = list(
            Run.objects.filter(
                parent_run_id=parent_id,
                status__in=[
                    Run.Status.QUEUED,
                    Run.Status.RUNNING,
                    Run.Status.STREAMING,
                ],
            ).values_list("pk", flat=True)
        )
        for child_id in child_ids:
            if child_id in seen:
                continue
            seen.add(child_id)
            pending.append(child_id)
            descendants.append(child_id)
    if not descendants:
        return []
    now = timezone.now()
    runs = list(Run.objects.select_related("lensnode").filter(pk__in=descendants))
    Run.objects.filter(pk__in=descendants).update(
        status=Run.Status.CANCELLED,
        resume_by=None,
        finished_at=now,
        updated_at=now,
    )
    RunExecution.objects.filter(run_id__in=descendants).update(
        status=RunExecution.Status.CANCELLED,
        finished_at=now,
    )
    return runs


def cancel_datasource_sync_on_lensnode(lensnode, task_id):
    """Send a datasource_cancel command to the connected LensNode."""

    if lensnode is None or not task_id:
        return None
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return None
    async_to_sync(channel_layer.group_send)(
        lensnode_group_name(lensnode.uuid),
        {
            "type": "lensnode.command",
            "payload": {
                "type": "datasource_cancel",
                "task_id": str(task_id),
            },
        },
    )
    return None


def cancel_datasource_upload_on_lensnode(lensnode, task_id):
    """Request cancellation of a managed workspace upload."""

    return cancel_datasource_sync_on_lensnode(lensnode, task_id)


def cancel_datasource_conversion_on_lensnode(lensnode, task_id):
    """Request safe cancellation of a managed workspace conversion."""

    if lensnode is None or not task_id:
        return None
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return None
    async_to_sync(channel_layer.group_send)(
        lensnode_group_name(lensnode.uuid),
        {
            "type": "lensnode.command",
            "payload": {
                "type": "datasource_convert_cancel",
                "task_id": str(task_id),
            },
        },
    )
    return None


RUN_ACTIVITY_THROTTLE_SECONDS = 15


def touch_run_activity(run_pk):
    """Bump a run's last_activity_at, throttled to avoid per-token writes.

    Streamed output and node events call this on every frame; the
    conditional update only writes when the stamp is stale, so the idle
    reaper has a fresh signal without a database write per token.
    """

    now = timezone.now()
    Run.objects.filter(pk=run_pk).filter(
        Q(last_activity_at__isnull=True)
        | Q(last_activity_at__lt=now - timedelta(seconds=RUN_ACTIVITY_THROTTLE_SECONDS))
    ).update(last_activity_at=now)


def append_lensnode_output(
    run_uuid,
    content_delta="",
    final_content=None,
    reset=False,
    citations=None,
    planned_evidence=None,
):
    """Persist output content streamed back from a LensNode.

    When reset is True the accumulated content is replaced by the delta
    rather than appended. Reset remains supported for older LensNodes;
    current nodes buffer tool-call turns and publish only final answers.
    """

    run = Run.objects.select_related("output_message").get(uuid=run_uuid)
    if run.status in TERMINAL_RUN_STATUSES:
        return run
    if run.output_message is None:
        return run
    if final_content:
        run.output_message.content = final_content
    elif final_content is not None:
        # an empty final reconciliation must not wipe streamed content
        pass
    elif reset:
        run.output_message.content = content_delta
    else:
        run.output_message.content = f"{run.output_message.content}{content_delta}"
    run.output_message.run = run
    run.output_message.save(update_fields=["content", "run"])
    run_update_fields = []
    if citations is not None:
        run.citations = sanitize_run_citations(citations)
        run_update_fields.append("citations")
    if planned_evidence is not None:
        run.planned_evidence = sanitize_planned_evidence(planned_evidence)
        run_update_fields.append("planned_evidence")
    if run_update_fields:
        run.save(update_fields=run_update_fields)
    touch_run_activity(run.pk)
    return run


def _parse_dispatch_id(dispatch_id):
    """Return a UUID for a protocol dispatch identifier or None."""

    try:
        return uuid.UUID(str(dispatch_id))
    except (TypeError, ValueError, AttributeError):
        return None


@transaction.atomic
def _acknowledge_run_protocol_state(
    run_uuid,
    dispatch_id,
    lensnode_uuid,
    *,
    checkpoint_ready,
):
    """Persist a current dispatch acknowledgement and reject stale frames."""

    parsed_dispatch_id = _parse_dispatch_id(dispatch_id)
    if parsed_dispatch_id is None:
        logger.warning(
            "run acknowledgement ignored reason=invalid_dispatch_id "
            "run_uuid=%s lensnode_uuid=%s",
            run_uuid,
            lensnode_uuid,
        )
        return False
    run = (
        Run.objects.select_for_update()
        .filter(
            uuid=run_uuid,
            lensnode__uuid=lensnode_uuid,
        )
        .first()
    )
    execution = (
        RunExecution.objects.select_for_update().filter(run=run).first()
        if run is not None
        else None
    )
    if execution is None or execution.dispatch_id != parsed_dispatch_id:
        logger.warning(
            "stale dispatch acknowledgement ignored run_uuid=%s "
            "dispatch_id=%s lensnode_uuid=%s",
            run_uuid,
            parsed_dispatch_id,
            lensnode_uuid,
        )
        return False
    if run.status in TERMINAL_RUN_STATUSES:
        logger.warning(
            "late dispatch acknowledgement ignored run_uuid=%s "
            "dispatch_id=%s status=%s",
            run_uuid,
            parsed_dispatch_id,
            run.status,
        )
        return False

    now = timezone.now()
    update_fields = []
    if execution.status == RunExecution.Status.DISPATCHED:
        execution.status = RunExecution.Status.RUNNING
        update_fields.append("status")
    if execution.admitted_at is None:
        execution.admitted_at = now
        update_fields.append("admitted_at")
    if checkpoint_ready and execution.checkpoint_ready_at is None:
        execution.checkpoint_ready_at = now
        update_fields.append("checkpoint_ready_at")
    if update_fields:
        execution.save(update_fields=update_fields)
    if run.resume_by is not None:
        Run.objects.filter(pk=run.pk).update(
            resume_by=None,
            updated_at=now,
        )
    logger.info(
        "run %s run_uuid=%s dispatch_id=%s lensnode_uuid=%s",
        "checkpoint ready" if checkpoint_ready else "admitted",
        run_uuid,
        parsed_dispatch_id,
        lensnode_uuid,
    )
    return True


def acknowledge_run_admitted(run_uuid, dispatch_id, lensnode_uuid):
    """Record that the current dispatch was accepted by its LensNode."""

    return _acknowledge_run_protocol_state(
        run_uuid,
        dispatch_id,
        lensnode_uuid,
        checkpoint_ready=False,
    )


def acknowledge_run_checkpoint_ready(run_uuid, dispatch_id, lensnode_uuid):
    """Record that the current dispatch has a durable initial checkpoint."""

    return _acknowledge_run_protocol_state(
        run_uuid,
        dispatch_id,
        lensnode_uuid,
        checkpoint_ready=True,
    )


@transaction.atomic
def record_lensnode_run_event(run_uuid, step_type, status, detail):
    """Persist a structured LensNode event into a RunStep row.

    Events for a run that already reached a terminal state are dropped:
    a cancelled agent thread can keep emitting for a while, and those
    late events must not rewrite the trace of a settled run. The warning
    makes orphan-thread activity observable.
    """

    run = Run.objects.select_for_update().get(uuid=run_uuid)
    if run.status in TERMINAL_RUN_STATUSES:
        logger.warning(
            "run %s: dropping late %s event (run already %s)",
            run_uuid,
            step_type,
            run.status,
        )
        return None
    if status == RunStep.Status.RUNNING:
        execution = RunExecution.objects.select_for_update().filter(run=run).first()
    else:
        execution = None
    queue_state = (detail or {}).get("queue_state")
    if (
        execution is not None
        and execution.status == RunExecution.Status.DISPATCHED
        and queue_state != "QUEUED"
    ):
        execution.status = RunExecution.Status.RUNNING
        execution.admitted_at = execution.admitted_at or timezone.now()
        execution.save(update_fields=["status", "admitted_at"])
        logger.info(
            "run admitted by first event run_uuid=%s dispatch_id=%s",
            run_uuid,
            execution.dispatch_id,
        )
    if run.resume_by is not None and status == RunStep.Status.RUNNING:
        Run.objects.filter(pk=run.pk, resume_by__isnull=False).update(resume_by=None)
        run.resume_by = None
    sequence = _step_sequence(step_type)
    step, _ = RunStep.objects.get_or_create(
        run=run,
        sequence=sequence,
        defaults={
            "step_type": step_type,
            "status": status,
            "detail": {},
        },
    )
    step.step_type = step_type
    step.status = status
    step.detail = {
        **(step.detail or {}),
        "events": [*(step.detail or {}).get("events", []), detail],
    }
    step.save(update_fields=["step_type", "status", "detail", "updated_at"])
    touch_run_activity(run.pk)
    return step


@transaction.atomic
def finish_lensnode_run(
    run_uuid,
    status,
    error="",
    outcome="",
    termination_detail=None,
    final_content=None,
    citations=None,
    planned_evidence=None,
):
    """Mark a LensNode-dispatched run finished."""

    run = (
        Run.objects.select_related(
            "input_message",
            "output_message",
            "session",
            "session__assistant",
            "session__user",
        )
        .select_for_update(of=("self",))
        .get(uuid=run_uuid)
    )
    if run.status in TERMINAL_RUN_STATUSES:
        return run
    now = timezone.now()

    retryable_admission_error = error == "LENSNODE_BUSY" or (
        error == "LENSNODE_DRAINING" and run.resume_by is not None
    )
    if status == Run.Status.FAILED and retryable_admission_error:
        if run.resume_by is not None:
            logger.info(
                "run %s: resume admission rejected; keeping it awaiting",
                run_uuid,
            )
            run.status = Run.Status.RUNNING
            run.error = ""
            run.outcome = ""
            run.termination_detail = {}
            run.save(
                update_fields=[
                    "status",
                    "error",
                    "outcome",
                    "termination_detail",
                    "updated_at",
                ]
            )
            if run.resume_by > now:
                from .tasks import retry_awaiting_run_resume

                retry_awaiting_run_resume.apply_async(
                    args=[str(run.uuid)],
                    countdown=BUSY_RETRY_INTERVAL_S,
                )
            return run
        elapsed = (now - run.created_at).total_seconds()
        if elapsed < BUSY_RETRY_WINDOW_S:
            logger.warning(
                "run %s: LENSNODE_BUSY (elapsed=%.0fs < window=%ds),"
                " re-queueing in %ds",
                run_uuid,
                elapsed,
                BUSY_RETRY_WINDOW_S,
                BUSY_RETRY_INTERVAL_S,
            )
            run.status = Run.Status.QUEUED
            run.error = ""
            run.outcome = ""
            run.termination_detail = {}
            run.save(
                update_fields=[
                    "status",
                    "error",
                    "outcome",
                    "termination_detail",
                    "updated_at",
                ]
            )
            from .tasks import enqueue_answer_run_task

            expected_document_count = get_run_document_expectation(run_uuid)
            enqueue_answer_run_task(
                run_uuid,
                (
                    expected_document_count
                    if expected_document_count is not None
                    else -1
                ),
                countdown=BUSY_RETRY_INTERVAL_S,
            )
            return run
        logger.error(
            "run %s: LENSNODE_BUSY retry window exceeded (elapsed=%.0fs > %ds),"
            " failing run",
            run_uuid,
            elapsed,
            BUSY_RETRY_WINDOW_S,
        )

    if status == Run.Status.AWAITING_USER_INPUT:
        run.status = Run.Status.AWAITING_USER_INPUT
        run.error = ""
        default_outcome = Run.Outcome.BLOCKED
        execution_status = RunExecution.Status.COMPLETED
    elif status == Run.Status.DONE:
        run.status = Run.Status.DONE
        run.error = ""
        default_outcome = Run.Outcome.COMPLETED
        execution_status = RunExecution.Status.COMPLETED
    else:
        run.status = Run.Status.FAILED
        run.error = error or "LENS_RUN_FAILED"
        default_outcome = Run.Outcome.BLOCKED
        execution_status = RunExecution.Status.FAILED
    valid_outcomes = {choice for choice, _ in Run.Outcome.choices}
    run.outcome = outcome if outcome in valid_outcomes else default_outcome
    run.termination_detail = sanitize_termination_detail(termination_detail or {})
    if final_content and run.output_message is not None:
        run.output_message.content = final_content
        run.output_message.run = run
        run.output_message.save(update_fields=["content", "run"])
    run_result_fields = []
    if citations is not None:
        run.citations = sanitize_run_citations(citations)
        run_result_fields.append("citations")
    if planned_evidence is not None:
        run.planned_evidence = sanitize_planned_evidence(planned_evidence)
        run_result_fields.append("planned_evidence")
    run.resume_by = None
    run.finished_at = now
    run.save(
        update_fields=[
            "status",
            "error",
            "outcome",
            "termination_detail",
            "resume_by",
            "finished_at",
            "updated_at",
            *run_result_fields,
        ]
    )

    # A terminal parent must not leave delegated work running.  This also
    # covers budget/deadline termination paths that bypass the UI cancel API.
    descendants = cancel_descendant_runs(run)
    for descendant in descendants:
        transaction.on_commit(
            lambda child=descendant: cancel_run_on_lensnode(child)
        )

    # A terminal frame can arrive after an individual step's final event (or
    # after an out-of-band cancellation). Close any remaining in-flight steps
    # so trajectory state cannot contradict the terminal Run status.
    fail_running_steps_for_runs(
        [run.pk],
        status=(
            RunStep.Status.DONE
            if run.status in {
                Run.Status.DONE,
                Run.Status.AWAITING_USER_INPUT,
            }
            else RunStep.Status.FAILED
        ),
    )

    if hasattr(run, "execution"):
        run.execution.status = execution_status
        run.execution.finished_at = now
        run.execution.save(update_fields=["status", "finished_at"])

    should_generate_title = (
        run.status == Run.Status.DONE
        and run.outcome != Run.Outcome.BLOCKED
        and run.output_message is not None
        and bool((run.output_message.content or "").strip())
        and not run.session.title_manually_edited
        and run.session.title_generation_status == Session.TitleGenerationStatus.PENDING
    )
    if should_generate_title:
        transaction.on_commit(
            lambda run_uuid=run.uuid, session_uuid=run.session.uuid: (
                _enqueue_session_title_generation(session_uuid, run_uuid)
            )
        )

    if _run_has_trace_observations(run):
        trace_export, _created = RunTraceExport.objects.get_or_create(run=run)
        transaction.on_commit(
            lambda export_uuid=trace_export.uuid: _enqueue_trace_export(export_uuid)
        )

    _promote_next_queued_run(run.session.assistant)
    return run


def _enqueue_trace_export(export_uuid):
    """Enqueue optional trace export after Run state is durable."""

    from .tasks import export_run_trace_task

    export_run_trace_task.delay(str(export_uuid))


def _run_has_trace_observations(run):
    """Return whether persisted RunStep events contain an observation."""

    for detail in run.steps.values_list("detail", flat=True):
        for event in (detail or {}).get("events", []):
            if isinstance(event, dict) and isinstance(
                event.get("observation"),
                dict,
            ):
                return True
    return False


def _promote_next_queued_run(assistant):
    """Enqueue the oldest queued run for this assistant, if any."""

    next_run = (
        Run.objects.filter(
            session__assistant=assistant,
            status=Run.Status.QUEUED,
        )
        .order_by("created_at")
        .first()
    )
    if next_run:
        logger.info(
            "assistant %s: promoting queued run %s",
            assistant.slug,
            next_run.uuid,
        )
        from .tasks import enqueue_answer_run_task

        expected_document_count = get_run_document_expectation(next_run.uuid)
        enqueue_answer_run_task(
            next_run.uuid,
            (expected_document_count if expected_document_count is not None else -1),
        )


def _step_sequence(step_type):
    """Return the canonical sequence for a step type."""

    mapping = {
        RunStep.StepType.QUERY_REWRITE: 0,
        RunStep.StepType.MULTIMODAL: 1,
        RunStep.StepType.RETRIEVAL: 2,
        RunStep.StepType.GENERAL_CHAT: 3,
        RunStep.StepType.ANSWER: 4,
        RunStep.StepType.STREAM: 5,
    }
    return mapping.get(step_type, 4)


def stream_run_events(run):
    """Yield SSE event payloads for a run until it reaches a terminal state."""

    emitted_steps = set()
    emitted_content = ""
    last_status = None
    last_resume_by = None
    last_queue_position = None
    last_ping_at = timezone.now()

    run = _load_run_stream_state(run.pk)
    yield _build_sync_event(run)
    # the sync event already carries the current content; seed emitted_content
    # so the loop streams only new deltas (avoids resending it on reconnect)
    emitted_content = _run_content(run)

    while True:
        run = _load_run_stream_state(run.pk)
        content = _run_content(run)

        resume_by = run.resume_by.isoformat() if run.resume_by else None
        if run.status != last_status or resume_by != last_resume_by:
            last_status = run.status
            last_resume_by = resume_by
            yield {
                "type": "status",
                "status": run.status,
                "resume_by": resume_by,
                "ts": timezone.now().isoformat(),
            }

        if run.status == Run.Status.QUEUED:
            position = _queue_position(run)
            waiting = _datasource_waiting(run)
            if (position, waiting) != last_queue_position:
                last_queue_position = (position, waiting)
                yield {
                    "type": "queue_position",
                    "position": position,
                    "datasource_waiting": waiting,
                    "ts": timezone.now().isoformat(),
                }
        else:
            last_queue_position = None

        for owner, step in _run_stream_steps(run):
            step_key = (
                owner.pk,
                step.sequence,
                step.status,
                step.updated_at,
            )
            if step_key not in emitted_steps:
                emitted_steps.add(step_key)
                yield _build_stream_step_event(owner, step)

        if content != emitted_content:
            if not content.startswith(emitted_content):
                emitted_content = ""
                yield {
                    "type": "token_reset",
                    "ts": timezone.now().isoformat(),
                }
            delta = content[len(emitted_content) :]
            emitted_content = content
            if delta:
                yield {
                    "type": "token",
                    "content": delta,
                    "ts": timezone.now().isoformat(),
                }

        if run.status in TERMINAL_RUN_STATUSES:
            yield _terminal_stream_event(run)
            return

        now = timezone.now()
        if (now - last_ping_at).total_seconds() >= STREAM_PING_INTERVAL_SECONDS:
            last_ping_at = now
            yield {
                "type": "ping",
                "ts": now.isoformat(),
            }

        sleep(STREAM_POLL_INTERVAL_SECONDS)


async def stream_run_events_async(run):
    """Yield SSE event payloads using an async iterator for ASGI streaming."""

    emitted_steps = set()
    emitted_content = ""
    last_status = None
    last_resume_by = None
    last_queue_position = None
    last_ping_at = timezone.now()
    run_pk = run.pk

    run = await sync_to_async(_load_run_stream_state)(run_pk)
    yield _build_sync_event(run)
    # The sync event already carries the current status, resume_by, all
    # steps, and content. Seed the dedup cursors so the loop below emits
    # only genuine deltas instead of replaying the snapshot.
    emitted_content = _run_content(run)
    last_status = run.status
    last_resume_by = run.resume_by.isoformat() if run.resume_by else None
    emitted_steps = {
        (owner.pk, step.sequence, step.status, step.updated_at)
        for owner, step in _run_stream_steps(run)
    }

    while True:
        content = _run_content(run)

        resume_by = run.resume_by.isoformat() if run.resume_by else None
        if run.status != last_status or resume_by != last_resume_by:
            last_status = run.status
            last_resume_by = resume_by
            yield {
                "type": "status",
                "status": run.status,
                "resume_by": resume_by,
                "ts": timezone.now().isoformat(),
            }

        if run.status == Run.Status.QUEUED:
            position = await sync_to_async(_queue_position)(run)
            waiting = await sync_to_async(_datasource_waiting)(run)
            if (position, waiting) != last_queue_position:
                last_queue_position = (position, waiting)
                yield {
                    "type": "queue_position",
                    "position": position,
                    "datasource_waiting": waiting,
                    "ts": timezone.now().isoformat(),
                }
        else:
            last_queue_position = None

        for owner, step in _run_stream_steps(run):
            step_key = (
                owner.pk,
                step.sequence,
                step.status,
                step.updated_at,
            )
            if step_key not in emitted_steps:
                emitted_steps.add(step_key)
                yield _build_stream_step_event(owner, step)

        if content != emitted_content:
            if not content.startswith(emitted_content):
                emitted_content = ""
                yield {
                    "type": "token_reset",
                    "ts": timezone.now().isoformat(),
                }
            delta = content[len(emitted_content) :]
            emitted_content = content
            if delta:
                yield {
                    "type": "token",
                    "content": delta,
                    "ts": timezone.now().isoformat(),
                }

        if run.status in TERMINAL_RUN_STATUSES:
            yield _terminal_stream_event(run)
            return

        now = timezone.now()
        if (now - last_ping_at).total_seconds() >= STREAM_PING_INTERVAL_SECONDS:
            last_ping_at = now
            yield {
                "type": "ping",
                "ts": now.isoformat(),
            }

        await asyncio.sleep(STREAM_POLL_INTERVAL_SECONDS)
        run = await sync_to_async(_load_run_stream_state)(run_pk)


def _load_run_stream_state(run_pk):
    """Load the latest run state needed for SSE snapshots."""

    return (
        Run.objects.select_related(
            "output_message",
            "input_message",
            "session__assistant",
        )
        .prefetch_related(
            "steps",
            "delegated_runs__steps",
            "delegated_runs__input_message",
            "delegated_runs__session__assistant",
        )
        .get(pk=run_pk)
    )


def _run_stream_steps(run):
    """Return parent and direct child steps with their owning Runs."""

    result = [(run, step) for step in run.steps.all()]
    for child in run.delegated_runs.all():
        result.extend((child, step) for step in child.steps.all())
    return sorted(
        result,
        key=lambda item: (
            item[1].updated_at,
            item[0].pk,
            item[1].sequence,
        ),
    )


def _build_stream_step_event(owner, step):
    """Build one public parent or delegated-child SSE step event."""

    detail = public_step_detail(step.detail)
    event = {
        "type": "step",
        "step": step.step_type,
        "status": step.status,
        "detail": detail,
        "sequence": step.sequence,
        "ts": timezone.now().isoformat(),
    }
    if owner.parent_run_id:
        assistant_name = owner.session.assistant.name[:160]
        delegated_task = str(owner.input_message.content or "").strip()[:2000]
        event["delegated_run_uuid"] = str(owner.uuid)
        event["assistant_name"] = assistant_name
        event["delegated_task"] = delegated_task
        for activity in detail.get("events", []):
            activity.setdefault("assistant_name", assistant_name)
            activity.setdefault("delegated_task", delegated_task)
    return event


def _queue_position(run):
    """Return the number of QUEUED runs ahead of this run for the assistant."""

    return Run.objects.filter(
        session__assistant=run.session.assistant,
        status=Run.Status.QUEUED,
        created_at__lt=run.created_at,
    ).count()


def _run_content(run):
    """Return accumulated assistant content for a run."""

    if not run.output_message:
        return ""
    return run.output_message.content


def _build_sync_event(run):
    """Build a persisted snapshot event for new or reconnected SSE clients."""

    steps = []
    for owner, step in _run_stream_steps(run):
        event = _build_stream_step_event(owner, step)
        event.pop("type", None)
        event.pop("ts", None)
        steps.append(event)
    return {
        "type": "sync",
        "status": run.status,
        "resume_by": run.resume_by.isoformat() if run.resume_by else None,
        "outcome": run.outcome,
        "termination_detail": sanitize_termination_detail(run.termination_detail),
        "steps": steps,
        "content": _run_content(run),
        "ts": timezone.now().isoformat(),
    }


def _terminal_stream_event(run):
    """Build the terminal SSE event for a run."""

    if run.status == Run.Status.AWAITING_USER_INPUT:
        return {
            "type": "awaiting_user_input",
            "status": run.status,
            "outcome": run.outcome,
            "termination_detail": sanitize_termination_detail(run.termination_detail),
            "ts": timezone.now().isoformat(),
        }
    if run.status == Run.Status.FAILED:
        return {
            "type": "error",
            "status": run.status,
            "outcome": run.outcome,
            "termination_detail": sanitize_termination_detail(run.termination_detail),
            "error": {
                "code": run.error or "LENS_RUN_FAILED",
                "message": run.error or "Run failed.",
            },
            "ts": timezone.now().isoformat(),
        }
    if run.status == Run.Status.CANCELLED:
        return {
            "type": "error",
            "status": run.status,
            "outcome": run.outcome,
            "termination_detail": sanitize_termination_detail(run.termination_detail),
            "error": {
                "code": "LENS_RUN_CANCELLED",
                "message": "Run was cancelled.",
            },
            "ts": timezone.now().isoformat(),
        }
    return {
        "type": "done",
        "status": run.status,
        "outcome": run.outcome,
        "termination_detail": sanitize_termination_detail(run.termination_detail),
        "ts": timezone.now().isoformat(),
    }


def _datasource_waiting(run):
    """Return whether queued execution is waiting for datasource sync."""

    state = RunExecution.objects.filter(run=run).values_list(
        "runtime_snapshot", flat=True
    ).first() or {}
    return bool(state.get("datasource_waiting"))
