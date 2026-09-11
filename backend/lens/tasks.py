import logging
import uuid
from contextlib import contextmanager
from datetime import timedelta

from celery import shared_task
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .datasource_services import (
    DataSourceDispatchError,
    dispatch_datasource_conversion_async,
    dispatch_datasource_sync_async,
    get_datasource_conversion_timeout_s,
    get_datasource_sync_timeout_s,
    get_datasource_upload_timeout_s,
)
from .datasource_versions import record_datasource_versions
from .models import (
    CredentialLease,
    DataSource,
    ExecutionSnapshot,
    GlobalSetting,
    LensNode,
    PluginInvocation,
    Run,
    RunExecution,
    RunTraceExport,
    ScheduledTask,
    Session,
)

logger = logging.getLogger(__name__)
ANSWER_RUN_DOCUMENT_COUNT_HEADER = "sourcelens_expected_document_count"
SESSION_TITLE_TASK_EXPIRY_SECONDS = 900
SESSION_TITLE_TASK_NAME = "lens.generate_session_title.v2"
DATASOURCE_CANCELLING_STATUS = "CANCELLING"
DATASOURCE_RETRY_SECONDS = 5
DATASOURCE_CAPACITY_LEASE_GRACE_SECONDS = 60
DATASOURCE_ADMISSION_STATE = "admission_state"
DATASOURCE_ADMITTED = "DISPATCHED"
DATASOURCE_QUEUED = "QUEUED"
DATASOURCE_OPERATION_MODULES = [
    "lens_datasource",
    "lens_datasource_conversion",
    "lens_datasource_upload",
]


@shared_task(name="lens.execute_run_diagnostic", queue="lens")
def execute_run_diagnostic(diagnostic_uuid):
    """Generate one diagnosis from its immutable evidence snapshot."""

    from .run_diagnostics import execute_diagnostic

    return execute_diagnostic(diagnostic_uuid)


@shared_task(name="lens.execute_diagnostic_turn", queue="lens")
def execute_diagnostic_turn(turn_uuid):
    """Answer one evidence-bound diagnosis follow-up."""

    from .run_diagnostics import execute_diagnostic_follow_up

    return execute_diagnostic_follow_up(turn_uuid)


@shared_task(
    name="lens.export_run_trace",
    queue="lens",
    bind=True,
    max_retries=3,
)
def export_run_trace_task(task, export_uuid):
    """Export an optional trace from an outbox with bounded retries."""

    from .trace_export import TraceExportError, export_run_trace

    export_record = RunTraceExport.objects.select_related("run").get(uuid=export_uuid)
    if export_record.status in {
        RunTraceExport.Status.COMPLETED,
        RunTraceExport.Status.SKIPPED,
    }:
        return export_record.status
    export_record.status = RunTraceExport.Status.RUNNING
    export_record.attempts += 1
    export_record.last_error_category = ""
    export_record.save(
        update_fields=[
            "status",
            "attempts",
            "last_error_category",
            "updated_at",
        ]
    )
    try:
        exported = export_run_trace(
            export_record.run_id,
            raise_on_failure=True,
        )
    except TraceExportError as exc:
        export_record.last_error_category = exc.category
        if task.request.retries < task.max_retries:
            export_record.status = RunTraceExport.Status.RETRYING
            export_record.save(
                update_fields=[
                    "status",
                    "last_error_category",
                    "updated_at",
                ]
            )
            raise task.retry(exc=exc, countdown=2**task.request.retries)
        export_record.status = RunTraceExport.Status.FAILED
        export_record.save(
            update_fields=[
                "status",
                "last_error_category",
                "updated_at",
            ]
        )
        return export_record.status
    export_record.status = (
        RunTraceExport.Status.COMPLETED if exported else RunTraceExport.Status.SKIPPED
    )
    export_record.exported_at = timezone.now() if exported else None
    export_record.save(update_fields=["status", "exported_at", "updated_at"])
    return export_record.status


@shared_task(name="lens.execute_answer_run", queue="lens", bind=True)
def execute_answer_run(task, run_uuid, expected_document_count=None):
    """Celery entrypoint for executing a Lens run."""

    logger.info("task execute_answer_run received: run_uuid=%s", run_uuid)
    if expected_document_count is None:
        from .document_attachments import set_run_document_expectation

        headers = task.request.headers or {}
        header_value = headers.get(ANSWER_RUN_DOCUMENT_COUNT_HEADER)
        if header_value is None:
            expected_document_count = 0
        else:
            try:
                expected_document_count = int(header_value)
            except (TypeError, ValueError):
                expected_document_count = -1
        if expected_document_count >= 0:
            set_run_document_expectation(run_uuid, expected_document_count)
    from .execution import execute_answer_run as execute_answer_run_service

    run = Run.objects.select_related(
        "session",
        "session__assistant",
        "session__assistant__lensnode",
        "input_message",
        "output_message",
        "lensnode",
    ).get(uuid=run_uuid)
    execute_answer_run_service(
        run,
        expected_document_count=expected_document_count,
    )
    return str(run.uuid)


@shared_task(name=SESSION_TITLE_TASK_NAME, queue="lens")
def generate_session_title(session_uuid, run_uuid):
    """Generate one semantic title without delaying its completed answer."""

    from .session_titles import generate_semantic_session_title

    return generate_semantic_session_title(session_uuid, run_uuid)


@shared_task(name="lens.generate_session_title", queue="lens")
def generate_session_title_legacy(session_uuid, run_uuid):
    """Support title tasks published by the previous application revision."""

    return generate_session_title(session_uuid, run_uuid)


@shared_task(name="lens.expire_stale_session_titles", queue="lens")
def expire_stale_session_titles():
    """Fail title tasks that were not completed within their lease."""

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(seconds=SESSION_TITLE_TASK_EXPIRY_SECONDS)
    stale_statuses = [
        Session.TitleGenerationStatus.PENDING,
        Session.TitleGenerationStatus.GENERATING,
    ]
    active_run_statuses = [
        Run.Status.QUEUED,
        Run.Status.RUNNING,
        Run.Status.STREAMING,
    ]
    return (
        Session.objects.filter(
            title_manually_edited=False,
            title_generation_status__in=stale_statuses,
            updated_at__lt=cutoff,
        )
        .exclude(
            run__status__in=active_run_statuses,
        )
        .update(
            title_generation_status=Session.TitleGenerationStatus.FAILED,
            updated_at=timezone.now(),
        )
    )


def enqueue_answer_run_task(
    run_uuid,
    expected_document_count,
    *,
    countdown=None,
):
    """Enqueue a task payload accepted by old and new workers."""

    options = {
        "args": [str(run_uuid)],
        "headers": {
            ANSWER_RUN_DOCUMENT_COUNT_HEADER: int(expected_document_count),
        },
    }
    if countdown is not None:
        options["countdown"] = countdown
    execute_answer_run.apply_async(**options)


def _get_or_create_source_sync_record(datasource):
    """Return the ScheduledTask mirror for a datasource sync."""

    record, _ = ScheduledTask.objects.get_or_create(
        task_type=ScheduledTask.TaskType.SOURCE_SYNC,
        target_type="datasource",
        target_id=datasource.uuid,
        defaults={
            "name": f"source_sync:{datasource.uuid}",
            "enabled": True,
        },
    )
    return record


def _get_or_create_global_record(task_type):
    """Return the ScheduledTask mirror for a global lens task."""

    record, _ = ScheduledTask.objects.get_or_create(
        task_type=task_type,
        target_type=None,
        target_id=None,
        defaults={
            "name": task_type,
            "enabled": True,
        },
    )
    return record


def register_datasource_sync_task(
    datasource,
    task_id,
    trigger,
    created_by=None,
    metadata=None,
):
    """Register a datasource sync execution before Celery starts it."""

    from agentcore_task.adapters.django import TaskTracker

    task_metadata = _datasource_task_metadata(datasource, trigger)
    task_metadata.update(metadata or {})
    with transaction.atomic():
        DataSource.objects.select_for_update().get(pk=datasource.pk)
        return TaskTracker.register_task(
            task_id=task_id,
            task_name=_datasource_sync_task_name(datasource),
            module="lens_datasource",
            task_args=[str(datasource.uuid)],
            task_kwargs={"trigger": trigger},
            created_by=created_by,
            metadata=task_metadata,
        )


def register_datasource_conversion_task(
    datasource,
    task_id,
    conversion,
    force=False,
    created_by=None,
    metadata=None,
):
    """Register managed workspace conversion before Celery starts it."""

    from agentcore_task.adapters.django import TaskTracker

    lensnode = datasource.lensnode
    task_metadata = {
        "type": "datasource_conversion",
        "datasource_uuid": str(datasource.uuid),
        "datasource_name": datasource.name,
        "source_type": datasource.source_type,
        "lensnode_uuid": str(lensnode.uuid) if lensnode else "",
        "lensnode_name": lensnode.name if lensnode else "",
        "target_path": datasource.target_path,
        "conversion": dict(conversion or {}),
        "force": bool(force),
        "steps": [],
        "logs": [],
        "lensnode_connection_id": lensnode.connection_id if lensnode else "",
    }
    task_metadata.update(metadata or {})
    return TaskTracker.register_task(
        task_id=task_id,
        task_name=_datasource_conversion_task_name(datasource),
        module="lens_datasource_conversion",
        task_args=[str(datasource.uuid)],
        task_kwargs={
            "conversion": dict(conversion or {}),
            "force": bool(force),
        },
        created_by=created_by,
        metadata=task_metadata,
    )


def register_datasource_upload_task(
    datasource,
    task_id,
    filename,
    created_by=None,
    metadata=None,
):
    """Register a managed workspace upload execution."""

    from agentcore_task.adapters.django import TaskTracker

    lensnode = datasource.lensnode
    task_metadata = {
        "type": "datasource_upload",
        "datasource_uuid": str(datasource.uuid),
        "datasource_name": datasource.name,
        "source_type": datasource.source_type,
        "lensnode_uuid": str(lensnode.uuid) if lensnode else "",
        "lensnode_name": lensnode.name if lensnode else "",
        "target_path": datasource.target_path,
        "filename": filename,
        "steps": [],
        "lensnode_connection_id": lensnode.connection_id if lensnode else "",
    }
    task_metadata.update(metadata or {})
    return TaskTracker.register_task(
        task_id=task_id,
        task_name=f"datasource_upload:{datasource.name}",
        module="lens_datasource_upload",
        task_args=[str(datasource.uuid)],
        task_kwargs={"filename": filename},
        created_by=created_by,
        metadata=task_metadata,
    )


def _datasource_sync_task_name(datasource):
    """Return a readable task name for one datasource sync."""

    name = str(getattr(datasource, "name", "") or "").strip()
    if not name:
        return "datasource_sync"
    return f"datasource_sync:{name}"


def _datasource_conversion_task_name(datasource):
    """Return a readable task name for managed workspace conversion."""

    name = str(getattr(datasource, "name", "") or "").strip()
    if not name:
        return "datasource_convert"
    return f"datasource_convert:{name}"


def _datasource_active_statuses(task_status):
    """Return statuses that still own datasource work."""

    return [
        task_status.PENDING,
        *task_status.get_running_statuses(),
        DATASOURCE_CANCELLING_STATUS,
    ]


def _datasource_capacity(lensnode):
    """Return the datasource execution capacity advertised by a LensNode."""

    labels = (lensnode.labels if lensnode else {}) or {}
    value = labels.get("datasource_sync_capacity", 1)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _datasource_capacity_slot_key(lensnode_uuid, slot):
    """Return the cache key for one LensNode datasource capacity slot."""

    return f"lens:datasource-capacity:{lensnode_uuid}:{slot}"


def _datasource_capacity_lease_ttl():
    """Return the expiry used for datasource capacity leases."""

    return (
        max(
            get_datasource_sync_timeout_s(),
            get_datasource_conversion_timeout_s(),
            get_datasource_upload_timeout_s(),
        )
        + DATASOURCE_CAPACITY_LEASE_GRACE_SECONDS
    )


def _acquire_datasource_capacity(lensnode, task_id):
    """Atomically acquire an idempotent datasource slot on a LensNode."""

    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    if lensnode is None:
        return None
    capacity = _datasource_capacity(lensnode)
    lensnode_uuid = str(lensnode.uuid)
    slot_keys = [
        _datasource_capacity_slot_key(lensnode_uuid, slot) for slot in range(capacity)
    ]
    for slot, key in enumerate(slot_keys):
        if cache.get(key) == task_id:
            return slot

    legacy_tasks = TaskExecution.objects.filter(
        module__in=DATASOURCE_OPERATION_MODULES,
        status__in=_datasource_active_statuses(TaskStatus),
        metadata__lensnode_uuid=str(lensnode.uuid),
        metadata__admission_state=DATASOURCE_ADMITTED,
    ).exclude(task_id=task_id)
    legacy_count = sum(
        1
        for metadata in legacy_tasks.values_list("metadata", flat=True)
        if (metadata or {}).get("admission_slot") is None
    )
    occupied_count = sum(cache.get(key) is not None for key in slot_keys)
    if legacy_count + occupied_count >= capacity:
        return None

    for slot, key in enumerate(slot_keys):
        if cache.add(
            key,
            task_id,
            timeout=_datasource_capacity_lease_ttl(),
        ):
            return slot
    return None


def _release_datasource_capacity(task):
    """Release the capacity slot owned by a datasource task."""

    metadata = dict(task.metadata or {})
    lensnode_uuid = metadata.get("lensnode_uuid")
    if not lensnode_uuid:
        return False
    slot = metadata.get("admission_slot")
    if slot is not None:
        key = _datasource_capacity_slot_key(lensnode_uuid, slot)
        if cache.get(key) == task.task_id:
            cache.delete(key)
            return True
        return False
    released = False
    capacity = metadata.get("admission_capacity")
    if capacity is None:
        lensnode = LensNode.objects.filter(uuid=lensnode_uuid).first()
        capacity = _datasource_capacity(lensnode)
    try:
        capacity = max(1, int(capacity))
    except (TypeError, ValueError):
        capacity = 1
    for index in range(capacity):
        key = _datasource_capacity_slot_key(lensnode_uuid, index)
        if cache.get(key) == task.task_id:
            cache.delete(key)
            released = True
    return released


def _datasource_capacity_available(lensnode, task_id):
    """Return whether a datasource operation can acquire a node slot."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.constants import TaskStatus

    if lensnode is None:
        raise DataSourceDispatchError("LENSNODE_REQUIRED")
    slot = _acquire_datasource_capacity(lensnode, task_id)
    if slot is None:
        return False
    TaskTracker.update_task_status(
        task_id,
        TaskStatus.PENDING,
        metadata={
            "admission_slot": slot,
            "admission_capacity": _datasource_capacity(lensnode),
        },
    )
    return True


def _refresh_datasource_lock(datasource_uuid, token, ttl_s):
    """Refresh a queued datasource lock while its task is being retried."""

    key = f"lens:datasource-sync:{datasource_uuid}"
    if cache.get(key) != token:
        return False
    return cache.touch(key, timeout=ttl_s)


def _queue_datasource_task(task_id, message):
    """Keep a datasource task pending while it waits for node capacity."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.constants import TaskStatus

    TaskTracker.update_task_status(
        task_id,
        TaskStatus.PENDING,
        metadata={
            DATASOURCE_ADMISSION_STATE: DATASOURCE_QUEUED,
            "queue_state": "QUEUED",
            "queue_reason": message,
        },
    )


def _mark_datasource_task_dispatched(task_id):
    """Record that a datasource task has acquired a LensNode slot."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.constants import TaskStatus

    TaskTracker.update_task_status(
        task_id,
        TaskStatus.PENDING,
        metadata={
            DATASOURCE_ADMISSION_STATE: DATASOURCE_ADMITTED,
            "queue_state": "DISPATCHED",
        },
    )


def _schedule_source_sync_retry(datasource, trigger, task_id):
    """Retry a queued datasource sync without creating a new task record."""

    source_sync_task.apply_async(
        args=[str(datasource.uuid), trigger],
        kwargs={"task_id": task_id},
        countdown=DATASOURCE_RETRY_SECONDS,
    )


def _schedule_conversion_retry(datasource, conversion, force, task_id):
    """Retry a lock-blocked conversion without creating another task."""

    datasource_conversion_task.apply_async(
        args=[str(datasource.uuid), dict(conversion or {}), bool(force)],
        kwargs={"task_id": task_id},
        countdown=DATASOURCE_RETRY_SECONDS,
    )


def _schedule_upload_retry(datasource_uuid, storage_name, filename, task_id):
    """Retry a queued upload without creating another task record."""

    datasource_upload_task.apply_async(
        args=[str(datasource_uuid), storage_name, filename],
        kwargs={"task_id": task_id},
        countdown=DATASOURCE_RETRY_SECONDS,
    )


def _is_global_task_enabled(task_type, default=True):
    """Return whether a global periodic task is enabled."""

    record = (
        ScheduledTask.objects.filter(
            task_type=task_type,
            target_type=None,
            target_id=None,
        )
        .only("enabled")
        .first()
    )
    if record is None:
        return default
    return bool(record.enabled)


@shared_task(bind=True, name="lens.source_sync", queue="lens")
def source_sync_task(self, datasource_uuid, trigger="scheduled", task_id=None):
    """Celery entrypoint for dispatching datasource sync to a LensNode."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    from .services import cancel_datasource_sync_on_lensnode

    # Track this run under a standalone id, not the Celery task id. The
    # Celery task returns SUCCESS right after dispatching to the LensNode,
    # so a tracked id equal to the Celery id would let the periodic sync
    # mark the run complete prematurely. With a standalone id Celery reports
    # PENDING (unknown id) and the LensNode completion callback owns the
    # final status.
    task_id = task_id or uuid.uuid4().hex
    with transaction.atomic():
        datasource = DataSource.objects.select_for_update().get(uuid=datasource_uuid)
        if datasource.source_type == DataSource.SourceType.MANAGED_WORKSPACE:
            return 0
        record = _get_or_create_source_sync_record(datasource)
        if datasource.status == DataSource.Status.DISABLED:
            if record.enabled:
                record.enabled = False
                record.save(update_fields=["enabled"])
            return 0

        task_execution = register_datasource_sync_task(
            datasource,
            task_id,
            trigger,
        )
        if task_execution.status in TaskStatus.get_completed_statuses():
            return 0
        if task_execution.status == DATASOURCE_CANCELLING_STATUS:
            return 0
        now = timezone.now()
        record.last_status = ScheduledTask.Status.RUNNING
        record.last_error = ""
        record.last_run_at = now
        record.save(update_fields=["last_status", "last_error", "last_run_at"])
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.PENDING,
            metadata={
                "queue_state": "QUEUED",
                "execution_class": "exclusive",
            },
        )
        _append_datasource_task_step(
            task_id,
            "prepare",
            "queued",
            "Datasource sync submitted to the LensNode queue.",
            task_status=TaskStatus.PENDING,
        )
        try:
            lock_key = f"lens:datasource-sync:{datasource.uuid}"
            if cache.get(lock_key) != task_id:
                acquire_datasource_lock(
                    datasource.uuid,
                    token=task_id,
                    ttl_s=get_datasource_sync_timeout_s(),
                )
            if not _datasource_capacity_available(datasource.lensnode, task_id):
                _queue_datasource_task(
                    task_id,
                    "Waiting for LensNode datasource sync capacity.",
                )
                _append_datasource_task_step(
                    task_id,
                    "admission",
                    "queued",
                    "Waiting for LensNode datasource sync capacity.",
                    task_status=TaskStatus.PENDING,
                )
                _refresh_datasource_lock(
                    datasource.uuid,
                    task_id,
                    get_datasource_sync_timeout_s(),
                )
                _schedule_source_sync_retry(datasource, trigger, task_id)
                return 0
            _mark_datasource_task_dispatched(task_id)
        except SourceSyncBusy as exc:
            record.last_error = str(exc)
            record.last_run_at = timezone.now()
            record.save(update_fields=["last_error", "last_run_at"])
            TaskTracker.update_task_status(
                task_id,
                TaskStatus.REVOKED,
                error=str(exc),
                metadata=_datasource_step_metadata(
                    task_id,
                    "lock",
                    "skipped",
                    str(exc),
                ),
            )
            return 0
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.PENDING,
            metadata={"lock_token": task_id},
        )
        _append_datasource_task_step(
            task_id,
            "dispatch",
            "running",
            "Dispatching datasource sync to LensNode.",
        )

    try:
        request_id = dispatch_datasource_sync_async(
            datasource,
            task_id=task_id,
            trigger=trigger,
        )
        with transaction.atomic():
            task_execution = TaskExecution.objects.select_for_update().get(
                task_id=task_id
            )
            cancelling = task_execution.status == DATASOURCE_CANCELLING_STATUS
            TaskTracker.update_task_status(
                task_id,
                task_execution.status if cancelling else TaskStatus.PENDING,
                metadata={
                    "completion_source": "lensnode_callback",
                    "datasource_sync_request_id": request_id,
                    "lock_token": task_id,
                    "queue_state": "DISPATCHED",
                    DATASOURCE_ADMISSION_STATE: DATASOURCE_ADMITTED,
                    "execution_class": "exclusive",
                },
            )
        if cancelling:
            cancel_datasource_sync_on_lensnode(
                datasource.lensnode,
                task_id,
            )
    except Exception as exc:
        release_datasource_lock(datasource.uuid, token=task_id)
        datasource.last_error = str(exc)
        datasource.save(update_fields=["last_error", "updated_at"])
        record.last_status = ScheduledTask.Status.FAILED
        record.last_error = str(exc)
        record.last_run_at = timezone.now()
        record.save(update_fields=["last_status", "last_error", "last_run_at"])
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.FAILURE,
            error=str(exc),
            metadata=_datasource_step_metadata(
                task_id,
                "failed",
                "failed",
                str(exc),
            ),
        )
        raise

    return 0


@shared_task(bind=True, name="lens.datasource_conversion", queue="lens")
def datasource_conversion_task(
    self,
    datasource_uuid,
    conversion,
    force=False,
    task_id=None,
):
    """Dispatch managed workspace conversion to its LensNode."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.constants import TaskStatus

    del self
    task_id = task_id or uuid.uuid4().hex
    datasource = DataSource.objects.select_related("lensnode").get(uuid=datasource_uuid)
    if datasource.source_type != DataSource.SourceType.MANAGED_WORKSPACE:
        return 0
    task_execution = register_datasource_conversion_task(
        datasource,
        task_id,
        conversion,
        force=force,
    )
    if task_execution.status in TaskStatus.get_completed_statuses():
        return 0
    if task_execution.status == DATASOURCE_CANCELLING_STATUS:
        return 0
    if datasource.lensnode is None:
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.FAILURE,
            error="LENSNODE_REQUIRED",
        )
        datasource.last_conversion_status = TaskStatus.FAILURE
        datasource.last_conversion_at = timezone.now()
        datasource.save(
            update_fields=[
                "last_conversion_status",
                "last_conversion_at",
                "updated_at",
            ]
        )
        return 0
    if datasource.status == DataSource.Status.DISABLED:
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.REVOKED,
            error="DATASOURCE_DISABLED",
        )
        datasource.last_conversion_status = TaskStatus.REVOKED
        datasource.last_conversion_at = timezone.now()
        datasource.save(
            update_fields=[
                "last_conversion_status",
                "last_conversion_at",
                "updated_at",
            ]
        )
        return 0

    dispatch_metadata = {
        "lensnode_connection_id": datasource.lensnode.connection_id,
        "queue_state": "QUEUED",
        "execution_class": "exclusive",
    }
    TaskTracker.update_task_status(
        task_id,
        TaskStatus.PENDING,
        metadata=dispatch_metadata,
    )
    datasource.last_conversion_status = TaskStatus.PENDING
    datasource.save(update_fields=["last_conversion_status", "updated_at"])
    _append_datasource_task_step(
        task_id,
        "prepare",
        "queued",
        "Managed workspace conversion submitted to the LensNode queue.",
        task_status=TaskStatus.PENDING,
    )
    try:
        lock_key = f"lens:datasource-sync:{datasource.uuid}"
        if cache.get(lock_key) != task_id:
            acquire_datasource_lock(
                datasource.uuid,
                token=task_id,
                ttl_s=get_datasource_sync_timeout_s(),
            )
        if not _datasource_capacity_available(datasource.lensnode, task_id):
            _queue_datasource_task(
                task_id,
                "Waiting for LensNode datasource sync capacity.",
            )
            _append_datasource_task_step(
                task_id,
                "admission",
                "queued",
                "Waiting for LensNode datasource sync capacity.",
                task_status=TaskStatus.PENDING,
            )
            _refresh_datasource_lock(
                datasource.uuid,
                task_id,
                get_datasource_sync_timeout_s(),
            )
            _schedule_conversion_retry(datasource, conversion, force, task_id)
            return 0
        _mark_datasource_task_dispatched(task_id)
        _append_datasource_task_step(
            task_id,
            "dispatch",
            "running",
            "Dispatching managed workspace conversion to LensNode.",
        )
        request_id = dispatch_datasource_conversion_async(
            datasource,
            task_id=task_id,
            conversion=conversion,
            force=force,
        )
        from agentcore_task.adapters.django.models import TaskExecution

        with transaction.atomic():
            task_execution = TaskExecution.objects.select_for_update().get(
                task_id=task_id
            )
            terminal_status = task_execution.status
            if terminal_status not in TaskStatus.get_completed_statuses():
                terminal_status = ""
            if not terminal_status:
                TaskTracker.update_task_status(
                    task_id,
                    TaskStatus.PENDING,
                    metadata={
                        "completion_source": "lensnode_callback",
                        "datasource_conversion_request_id": request_id,
                        "lock_token": task_id,
                    },
                )
        if terminal_status:
            release_datasource_lock(datasource.uuid, token=task_id)
            if terminal_status == TaskStatus.REVOKED:
                from .services import cancel_datasource_conversion_on_lensnode

                cancel_datasource_conversion_on_lensnode(
                    datasource.lensnode,
                    task_id,
                )
            return 0
    except SourceSyncBusy as exc:
        datasource.last_conversion_status = TaskStatus.PENDING
        datasource.last_conversion_at = timezone.now()
        datasource.save(
            update_fields=[
                "last_conversion_status",
                "last_conversion_at",
                "updated_at",
            ]
        )
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.PENDING,
            error=str(exc),
            metadata=_datasource_step_metadata(
                task_id,
                "lock",
                "queued",
                str(exc),
            ),
        )
        _schedule_conversion_retry(datasource, conversion, force, task_id)
        return 0
    except Exception:
        release_datasource_lock(datasource.uuid, token=task_id)
        datasource.last_conversion_status = TaskStatus.FAILURE
        datasource.last_conversion_at = timezone.now()
        datasource.save(
            update_fields=[
                "last_conversion_status",
                "last_conversion_at",
                "updated_at",
            ]
        )
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.FAILURE,
            error="DATASOURCE_CONVERSION_FAILED",
            metadata=_datasource_step_metadata(
                task_id,
                "failed",
                "failed",
                "DATASOURCE_CONVERSION_FAILED",
            ),
        )
        raise
    return 0


@shared_task(bind=True, name="lens.datasource_upload", queue="lens")
def datasource_upload_task(
    self,
    datasource_uuid,
    storage_name,
    filename,
    task_id=None,
):
    """Send one stored Managed Workspace upload to its LensNode."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    from .datasource_services import dispatch_datasource_upload_async

    task_id = task_id or self.request.id
    datasource = DataSource.objects.select_related("lensnode").get(uuid=datasource_uuid)
    task = TaskExecution.objects.get(task_id=task_id)
    if task.status in TaskStatus.get_completed_statuses():
        return 0
    if datasource.lensnode is None:
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.FAILURE,
            error="LENSNODE_REQUIRED",
        )
        if default_storage.exists(storage_name):
            default_storage.delete(storage_name)
        return 0
    task_metadata = dict(task.metadata or {})
    try:
        lock_key = f"lens:datasource-sync:{datasource.uuid}"
        if cache.get(lock_key) != task_id:
            acquire_datasource_lock(
                datasource.uuid,
                token=task_id,
                ttl_s=get_datasource_upload_timeout_s(),
            )
    except SourceSyncBusy as exc:
        _queue_datasource_task(task_id, str(exc))
        _schedule_upload_retry(
            datasource.uuid,
            storage_name,
            filename,
            task_id,
        )
        return 0
    if not _datasource_capacity_available(datasource.lensnode, task_id):
        _queue_datasource_task(
            task_id,
            "Waiting for LensNode datasource sync capacity.",
        )
        _refresh_datasource_lock(
            datasource.uuid,
            task_id,
            get_datasource_upload_timeout_s(),
        )
        _schedule_upload_retry(
            datasource.uuid,
            storage_name,
            filename,
            task_id,
        )
        return 0
    _mark_datasource_task_dispatched(task_id)
    try:
        with default_storage.open(storage_name, "rb") as stream:
            content = stream.read()
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.STARTED,
            metadata={
                "lock_token": task_id,
                "progress_message": "Uploading file to LensNode.",
            },
        )
        request_id = dispatch_datasource_upload_async(
            datasource,
            task_id,
            filename,
            content,
        )
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.STARTED,
            metadata={"datasource_upload_request_id": request_id},
        )
    except Exception as exc:
        release_datasource_lock(
            datasource.uuid,
            token=task_id,
        )
        TaskTracker.update_task_status(
            task_id,
            TaskStatus.FAILURE,
            error=str(exc) or "DATASOURCE_UPLOAD_FAILED",
        )
        raise
    finally:
        if default_storage.exists(storage_name):
            default_storage.delete(storage_name)
    return 0


def complete_datasource_conversion_task(
    task_id,
    result,
    connection_id=None,
):
    """Complete managed workspace conversion from LensNode callback."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    task = TaskExecution.objects.filter(task_id=task_id).first()
    if task is None:
        return None
    metadata = task.metadata or {}
    owner_connection_id = metadata.get("lensnode_connection_id") or ""
    if (
        connection_id
        and owner_connection_id
        and owner_connection_id != connection_id
        and task.status not in TaskStatus.get_completed_statuses()
    ):
        return task
    datasource_uuid = metadata.get("datasource_uuid")
    datasource = DataSource.objects.filter(uuid=datasource_uuid).first()
    status_value = str(result.get("status") or "failed").lower()
    status_map = {
        "success": (TaskStatus.SUCCESS, "succeeded", ""),
        "cancelled": (
            TaskStatus.REVOKED,
            "cancelled",
            "DATASOURCE_CONVERSION_CANCELLED",
        ),
        "failed": (
            TaskStatus.FAILURE,
            "failed",
            "DATASOURCE_CONVERSION_FAILED",
        ),
    }
    task_status, overall_status, default_error = status_map.get(
        status_value,
        status_map["failed"],
    )
    error = str(result.get("error") or default_error)
    completion_reason = str(result.get("completion_reason") or error or status_value)
    conversion_summary = result.get("conversion_summary") or {}
    if task.status in TaskStatus.get_completed_statuses():
        if datasource_uuid:
            release_datasource_lock(
                datasource_uuid,
                token=metadata.get("lock_token") or task_id,
            )
        return TaskTracker.update_task_status(
            task_id,
            task.status,
            metadata={"conversion_summary": conversion_summary},
        )
    task_result = {
        "overall_status": overall_status,
        "conversion_summary": conversion_summary,
    }
    if datasource is not None:
        datasource.last_conversion_status = task_status
        datasource.last_conversion_at = timezone.now()
        datasource.save(
            update_fields=[
                "last_conversion_status",
                "last_conversion_at",
                "updated_at",
            ]
        )
        if task_status == TaskStatus.SUCCESS:
            record_datasource_versions(datasource)
    lock_token = metadata.get("lock_token") or task_id
    if datasource_uuid:
        release_datasource_lock(datasource_uuid, token=lock_token)
    step_status = "done" if task_status == TaskStatus.SUCCESS else "failed"
    completion_metadata = _datasource_step_metadata(
        task_id,
        "completed" if task_status == TaskStatus.SUCCESS else overall_status,
        step_status,
        (
            "Managed workspace conversion completed."
            if task_status == TaskStatus.SUCCESS
            else error
        ),
        progress_percent=100,
    )
    completion_metadata["conversion_summary"] = conversion_summary
    completion_metadata["completion_reason"] = completion_reason
    if task_status == TaskStatus.SUCCESS:
        completion_metadata.update(
            {
                "phase": "COMPLETED",
                "overall_progress_percent": 100,
                "phase_progress": {
                    "current": 1,
                    "total": 1,
                    "unit": "steps",
                },
                "progress_counts": {
                    "total": int(conversion_summary.get("total") or 0),
                    "candidates": int(conversion_summary.get("candidates") or 0),
                    "processed": int(conversion_summary.get("total") or 0),
                    "converted": int(
                        conversion_summary.get("converted")
                        or conversion_summary.get("success")
                        or 0
                    ),
                    "failed": int(conversion_summary.get("failed") or 0),
                    "skipped": int(conversion_summary.get("skipped") or 0),
                    "unsupported": int(conversion_summary.get("unsupported") or 0),
                },
                "last_substantive_progress_at": timezone.now().isoformat(),
            }
        )
    if task_status == TaskStatus.REVOKED:
        completion_metadata["stop_confirmation_source"] = str(
            result.get("stop_confirmation_source") or "lensnode_callback"
        )
    return TaskTracker.update_task_status(
        task_id,
        task_status,
        result=task_result,
        error=error or None,
        metadata=completion_metadata,
    )


def complete_datasource_upload_task(
    task_id,
    result,
    connection_id=None,
):
    """Complete a managed workspace upload from a LensNode callback."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    task = TaskExecution.objects.filter(task_id=task_id).first()
    if task is None:
        return None
    metadata = task.metadata or {}
    owner_connection_id = metadata.get("lensnode_connection_id") or ""
    if (
        connection_id
        and owner_connection_id
        and owner_connection_id != connection_id
        and task.status not in TaskStatus.get_completed_statuses()
    ):
        return task

    if task.status in TaskStatus.get_completed_statuses():
        release_datasource_lock(
            metadata.get("datasource_uuid"),
            token=metadata.get("lock_token") or task_id,
        )
        return TaskTracker.update_task_status(
            task_id,
            task.status,
            metadata={"upload_result": result},
        )

    status_value = str(result.get("status") or "failed").lower()
    status_map = {
        "success": (TaskStatus.SUCCESS, ""),
        "cancelled": (
            TaskStatus.REVOKED,
            "DATASOURCE_UPLOAD_CANCELLED",
        ),
        "failed": (TaskStatus.FAILURE, "DATASOURCE_UPLOAD_FAILED"),
    }
    task_status, default_error = status_map.get(
        status_value,
        status_map["failed"],
    )
    error = str(result.get("error") or default_error)
    release_datasource_lock(
        metadata.get("datasource_uuid"),
        token=metadata.get("lock_token") or task_id,
    )
    return TaskTracker.update_task_status(
        task_id,
        task_status,
        result=result,
        error=error or None,
        metadata={
            "upload_result": result,
            "progress_message": (
                "Managed workspace upload completed."
                if task_status == TaskStatus.SUCCESS
                else error
            ),
        },
    )


def resolve_datasource_conversion_task_id(request_id, content):
    """Resolve a managed workspace conversion task from callback data."""

    task_id = content.get("task_id") or ""
    if task_id:
        return task_id
    cached_task_id = cache.get(f"lens:datasource_conversion_request:{request_id}")
    if cached_task_id:
        return cached_task_id

    from agentcore_task.adapters.django.models import TaskExecution

    task = TaskExecution.objects.filter(
        module="lens_datasource_conversion",
        metadata__datasource_conversion_request_id=request_id,
    ).first()
    return task.task_id if task else ""


def resolve_datasource_upload_task_id(request_id, content):
    """Resolve an upload task from a LensNode callback."""

    task_id = content.get("task_id") or ""
    if task_id:
        return task_id
    cached_task_id = cache.get(f"lens:datasource_upload_request:{request_id}")
    if cached_task_id:
        return cached_task_id
    from agentcore_task.adapters.django.models import TaskExecution

    task = TaskExecution.objects.filter(
        module="lens_datasource_upload",
        metadata__datasource_upload_request_id=request_id,
    ).first()
    return task.task_id if task else ""


def complete_datasource_sync_task(task_id, result):
    """Complete a datasource sync after LensNode reports final status."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    task = TaskExecution.objects.filter(task_id=task_id).first()
    if task is None:
        return None
    metadata = task.metadata or {}
    datasource_uuid = metadata.get("datasource_uuid")
    datasource = DataSource.objects.filter(uuid=datasource_uuid).first()
    record = None
    if datasource is not None:
        record = _get_or_create_source_sync_record(datasource)

    status_value = str(result.get("status") or "failed").lower()
    success = status_value == "success"
    cancelled = status_value == "cancelled"
    error = result.get("error") or (
        "DATASOURCE_SYNC_CANCELLED" if cancelled else "LENS_SOURCE_SYNC_FAILED"
    )
    changed = result.get("changed")
    if changed is None:
        changed = result.get("synced")
    metrics = {
        "synced": int(result.get("synced") or 0),
        "files": int(result.get("files") or 0),
        "folders": int(result.get("folders") or 0),
        "failed": int(result.get("failed") or 0),
        "scanned": int(result.get("scanned") or 0),
        "changed": int(changed or 0),
        "skipped": int(result.get("skipped") or 0),
        "deleted": int(result.get("deleted") or 0),
        "documents": int(result.get("documents") or 0),
        "by_extension": result.get("by_extension") or {},
        "by_type": result.get("by_type") or {},
        "details": result.get("details") or {},
        "details_truncated": result.get("details_truncated") or {},
        "repository_summaries": result.get("repository_summaries") or [],
        "failed_repositories": result.get("failed_repositories") or [],
        "partial_success": bool(result.get("partial_success")),
        "target_path": result.get("target_path")
        or (datasource.target_path if datasource else ""),
    }
    conversion_summary = result.get("conversion_summary") or {}
    warnings = list(result.get("warnings") or [])
    if result.get("partial_success"):
        warnings.append("DATASOURCE_SYNC_PARTIAL_SUCCESS")
    warnings.extend(conversion_summary.get("warnings") or [])
    if conversion_summary.get("failed"):
        warnings.append("CONVERSION_PARTIAL_FAILED")
    if warnings:
        metrics["warnings"] = list(dict.fromkeys(warnings))
    result_metrics = {
        key: value
        for key, value in metrics.items()
        if key not in {"details", "details_truncated"}
    }
    if task.status in TaskStatus.get_completed_statuses():
        summary_update = {"sync_summary": metrics}
        if conversion_summary:
            summary_update["conversion_summary"] = conversion_summary
        if warnings:
            summary_update["warnings"] = list(dict.fromkeys(warnings))
        if success and not task.result:
            return TaskTracker.update_task_status(
                task_id,
                task.status,
                result=result_metrics,
                metadata=summary_update,
            )
        return TaskTracker.update_task_status(
            task_id,
            task.status,
            metadata=summary_update,
        )

    if datasource is not None:
        if success:
            datasource.last_error = ""
            datasource.last_synced_at = timezone.now()
            if metrics["target_path"]:
                datasource.target_path = metrics["target_path"]
            datasource.save(
                update_fields=[
                    "last_error",
                    "last_synced_at",
                    "target_path",
                    "updated_at",
                ]
            )
        elif not cancelled:
            datasource.last_error = error
            datasource.save(update_fields=["last_error", "updated_at"])

    if record is not None:
        record.last_status = (
            ScheduledTask.Status.SUCCESS if success else ScheduledTask.Status.FAILED
        )
        record.last_error = "" if success or cancelled else error
        record.last_run_at = timezone.now()
        record.last_metrics = result_metrics if success else {}
        record.save(
            update_fields=[
                "last_status",
                "last_error",
                "last_run_at",
                "last_metrics",
            ]
        )

    lock_token = metadata.get("lock_token")
    if datasource_uuid and lock_token:
        release_datasource_lock(
            datasource_uuid,
            token=lock_token,
        )

    if success:
        completion_metadata = _datasource_step_metadata(
            task_id,
            "completed",
            "done",
            "Datasource sync completed.",
            progress_percent=100,
        )
        completion_metadata["sync_summary"] = metrics
        if conversion_summary:
            completion_metadata["conversion_summary"] = conversion_summary
        if warnings:
            completion_metadata["warnings"] = list(dict.fromkeys(warnings))
        return TaskTracker.update_task_status(
            task_id,
            TaskStatus.SUCCESS,
            result=result_metrics,
            metadata=completion_metadata,
        )

    if cancelled:
        return TaskTracker.update_task_status(
            task_id,
            TaskStatus.REVOKED,
            error=error,
            metadata={
                **_datasource_step_metadata(
                    task_id,
                    "cancelled",
                    "failed",
                    error,
                ),
                "completion_reason": str(result.get("completion_reason") or error),
                "stop_confirmation_source": str(
                    result.get("stop_confirmation_source") or "lensnode_callback"
                ),
            },
        )

    return TaskTracker.update_task_status(
        task_id,
        TaskStatus.FAILURE,
        error=error,
        metadata=_datasource_step_metadata(
            task_id,
            "failed",
            "failed",
            error,
        ),
    )


def resolve_datasource_sync_task_id(request_id, content):
    """Resolve a datasource sync task id from callback content."""

    task_id = content.get("task_id") or ""
    if task_id:
        return task_id

    cached_task_id = cache.get(f"lens:datasource_sync_request:{request_id}")
    if cached_task_id:
        return cached_task_id

    from agentcore_task.adapters.django.models import TaskExecution

    task = TaskExecution.objects.filter(
        module="lens_datasource",
        metadata__datasource_sync_request_id=request_id,
    ).first()
    return task.task_id if task else ""


def acquire_datasource_lock(datasource_uuid, token, ttl_s=600):
    """Acquire an expiring sync lock for one datasource."""

    key = f"lens:datasource-sync:{datasource_uuid}"
    acquired = cache.add(key, token, timeout=ttl_s)
    if not acquired and _release_orphaned_datasource_lock(datasource_uuid):
        acquired = cache.add(key, token, timeout=ttl_s)
    if not acquired:
        raise SourceSyncBusy("LENS_SOURCE_SYNC_BUSY")
    return token


def _release_orphaned_datasource_lock(datasource_uuid):
    """Release a datasource lock that no running task still owns."""

    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    key = f"lens:datasource-sync:{datasource_uuid}"
    lock_token = cache.get(key)
    if not lock_token:
        return True

    running_statuses = _datasource_active_statuses(TaskStatus)
    owner_exists = TaskExecution.objects.filter(
        module__in=DATASOURCE_OPERATION_MODULES,
        status__in=running_statuses,
        metadata__datasource_uuid=str(datasource_uuid),
        metadata__lock_token=lock_token,
    ).exists()
    if owner_exists:
        return False

    return release_datasource_lock(datasource_uuid, token=lock_token)


def release_datasource_lock(datasource_uuid, token=None):
    """Release a datasource sync lock when ownership is known."""

    released = False
    if datasource_uuid:
        key = f"lens:datasource-sync:{datasource_uuid}"
        if token is None or cache.get(key) == token:
            cache.delete(key)
            released = True
    if token is not None:
        from agentcore_task.adapters.django.models import TaskExecution

        task = TaskExecution.objects.filter(task_id=token).first()
        if task is not None:
            released = _release_datasource_capacity(task) or released
    return released


def reconcile_orphaned_datasource_conversions(
    lensnode_uuid,
    connection_id,
    active_operations,
):
    """Rebind datasource work after a LensNode reconnect.

    A missing active_operations report identifies a pre-protocol LensNode.
    Preserve its reconnect behavior by rebinding its active work; an empty
    report from a capable LensNode means the work is unreported instead.
    """

    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    active_statuses = _datasource_active_statuses(TaskStatus)
    tasks = TaskExecution.objects.filter(
        module__in=DATASOURCE_OPERATION_MODULES,
        status__in=active_statuses,
        metadata__lensnode_uuid=str(lensnode_uuid),
    ).exclude(
        metadata__lensnode_connection_id=str(connection_id),
    )
    legacy_node = active_operations is None
    operations = {
        str(item.get("task_id")): item
        for item in (active_operations or [])
        if isinstance(item, dict) and item.get("task_id")
    }
    rebound_count = 0
    for task in tasks:
        metadata = dict(task.metadata or {})
        operation = operations.get(task.task_id)
        expected_operation = (
            "sync"
            if task.module == "lens_datasource"
            else ("upload" if task.module == "lens_datasource_upload" else "conversion")
        )
        if metadata.get(DATASOURCE_ADMISSION_STATE) == DATASOURCE_QUEUED:
            metadata["lensnode_connection_id"] = str(connection_id)
            metadata["reconnected_at"] = timezone.now().isoformat()
            metadata.pop("datasource_orphan_confirmation_connection_id", None)
            task.metadata = metadata
            task.save(update_fields=["metadata"])
            continue
        if legacy_node or (
            operation
            and str(operation.get("datasource_uuid"))
            == str(metadata.get("datasource_uuid"))
            and operation.get("operation") == expected_operation
        ):
            metadata["lensnode_connection_id"] = str(connection_id)
            metadata["reconnected_at"] = timezone.now().isoformat()
            if operation:
                metadata["last_lensnode_operation"] = operation
            metadata.pop("datasource_orphan_confirmation_connection_id", None)
            task.metadata = metadata
            task.save(update_fields=["metadata"])
            rebound_count += 1
            continue
        if metadata.get("lensnode_connection_id") != str(connection_id):
            # The operation may have completed while the socket was down. Its
            # buffered terminal frame is sent after hello, so let the new
            # connection deliver it during the confirmation window.
            metadata["lensnode_connection_id"] = str(connection_id)
            metadata["datasource_orphan_confirmation_connection_id"] = str(
                connection_id
            )
            task.metadata = metadata
            task.save(update_fields=["metadata"])
            _schedule_datasource_orphan_confirmation(
                task.task_id,
                connection_id,
            )
    return rebound_count


def _schedule_datasource_orphan_confirmation(task_id, connection_id):
    """Delay failure while LensNode delivers any buffered terminal frame."""

    from .services import get_reconcile_confirm_grace_seconds

    try:
        confirm_orphaned_datasource_conversion.apply_async(
            args=[task_id, str(connection_id)],
            countdown=get_reconcile_confirm_grace_seconds(),
        )
    except Exception:
        logger.exception(
            "Failed to schedule datasource orphan confirmation for task %s",
            task_id,
        )


@shared_task(
    name="lens.confirm_orphaned_datasource_conversion",
    queue="lens",
    ignore_result=True,
)
def confirm_orphaned_datasource_conversion(task_id, connection_id):
    """Fail unreported datasource work after the reconnect grace period."""

    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    task = TaskExecution.objects.filter(task_id=task_id).first()
    if task is None:
        return False
    metadata = dict(task.metadata or {})
    if metadata.get("datasource_orphan_confirmation_connection_id") != str(
        connection_id
    ):
        return False
    now = timezone.now()
    is_upload = task.module == "lens_datasource_upload"
    datasource_uuid = metadata.get("datasource_uuid")
    is_sync = task.module == "lens_datasource"
    error = (
        "DATASOURCE_SYNC_ORPHANED"
        if is_sync
        else (
            "DATASOURCE_UPLOAD_ORPHANED"
            if is_upload
            else "DATASOURCE_CONVERSION_ORPHANED"
        )
    )
    metadata.update(
        {
            "recovery_reason": "LENSNODE_ACTIVE_OPERATION_UNREPORTED",
            "recovery_retryable": True,
            "reconciled_at": now.isoformat(),
            "completion_reason": error,
            "stop_confirmation_source": "lensnode_active_operations_absent",
        }
    )
    updated = TaskExecution.objects.filter(
        pk=task.pk,
        status__in=_datasource_active_statuses(TaskStatus),
        metadata__datasource_orphan_confirmation_connection_id=str(connection_id),
    ).update(
        status=TaskStatus.FAILURE,
        finished_at=now,
        error=error,
        metadata=metadata,
    )
    if not updated:
        return False
    datasource = DataSource.objects.filter(uuid=datasource_uuid).first()
    if datasource is not None:
        if is_sync:
            record = _get_or_create_source_sync_record(datasource)
            record.last_status = ScheduledTask.Status.FAILED
            record.last_error = error
            record.last_run_at = now
            record.save(
                update_fields=[
                    "last_status",
                    "last_error",
                    "last_run_at",
                ]
            )
            datasource.last_error = error
            datasource.save(update_fields=["last_error", "updated_at"])
        else:
            datasource.last_conversion_status = TaskStatus.FAILURE
            datasource.last_conversion_at = now
            datasource.save(
                update_fields=[
                    "last_conversion_status",
                    "last_conversion_at",
                    "updated_at",
                ]
            )
    release_datasource_lock(
        datasource_uuid,
        token=metadata.get("lock_token") or task.task_id,
    )
    return True


def cleanup_stale_datasource_sync_tasks(startup=False):
    """Cancel timed-out datasource work and release orphaned locks.

    Datasource work is completed by LensNode callback after this Celery task
    has dispatched it. A worker restart does not mean the external work was
    interrupted, so startup cleanup still honors the configured timeout.
    """

    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    from .services import (
        cancel_datasource_conversion_on_lensnode,
        cancel_datasource_sync_on_lensnode,
        cancel_datasource_upload_on_lensnode,
    )

    now = timezone.now()
    orphaned_count = 0
    timeout_s = get_datasource_sync_timeout_s()
    conversion_cutoff = now - timedelta(seconds=get_datasource_conversion_timeout_s())
    upload_cutoff = now - timedelta(seconds=get_datasource_upload_timeout_s())
    cutoff = now - timedelta(seconds=timeout_s)
    running_statuses = _datasource_active_statuses(TaskStatus)
    executing_statuses = [
        status for status in running_statuses if status != TaskStatus.PENDING
    ]

    stale = TaskExecution.objects.filter(
        module__in=[
            "lens_datasource",
            "lens_datasource_conversion",
            "lens_datasource_upload",
        ],
        metadata__datasource_uuid__isnull=False,
    ).filter(
        Q(status__in=executing_statuses, started_at__lt=cutoff)
        & ~Q(module="lens_datasource_conversion")
        | Q(
            status__in=executing_statuses,
            started_at__isnull=True,
            created_at__lt=cutoff,
        )
        | Q(
            module="lens_datasource_conversion",
            status__in=executing_statuses,
            started_at__lt=conversion_cutoff,
        )
        | Q(
            module="lens_datasource_upload",
            status__in=executing_statuses,
            started_at__lt=upload_cutoff,
        )
        | Q(
            status=TaskStatus.PENDING,
            created_at__lt=cutoff,
        )
    )

    failed_count = 0
    for task in stale:
        metadata = dict(task.metadata or {})
        datasource_uuid = metadata.get("datasource_uuid")
        datasource = DataSource.objects.filter(uuid=datasource_uuid).first()
        is_conversion = task.module == "lens_datasource_conversion"
        is_upload = task.module == "lens_datasource_upload"
        error = (
            "DATASOURCE_CONVERSION_TIMEOUT"
            if is_conversion
            else (
                "DATASOURCE_UPLOAD_TIMEOUT" if is_upload else "LENS_SOURCE_SYNC_TIMEOUT"
            )
        )
        if is_conversion and task.status != DATASOURCE_CANCELLING_STATUS:
            if datasource is not None:
                datasource.last_conversion_status = DATASOURCE_CANCELLING_STATUS
                datasource.save(update_fields=["last_conversion_status", "updated_at"])
            metadata["timeout_cancel_requested_at"] = now.isoformat()
            metadata["cancellation_state"] = DATASOURCE_CANCELLING_STATUS
            task.status = DATASOURCE_CANCELLING_STATUS
            task.error = ""
            task.metadata = metadata
            task.save(update_fields=["status", "error", "metadata"])
            cancel_datasource_conversion_on_lensnode(
                datasource.lensnode if datasource is not None else None,
                task.task_id,
            )
            continue
        if is_conversion:
            continue
        if datasource is not None:
            if is_upload:
                cancel_datasource_upload_on_lensnode(
                    datasource.lensnode,
                    task.task_id,
                )
            else:
                cancel_datasource_sync_on_lensnode(
                    datasource.lensnode,
                    task.task_id,
                )
            if not is_upload:
                record = _get_or_create_source_sync_record(datasource)
                record.last_status = ScheduledTask.Status.FAILED
                record.last_error = error
                record.last_run_at = now
                record.save(
                    update_fields=[
                        "last_status",
                        "last_error",
                        "last_run_at",
                    ]
                )
        release_datasource_lock(
            datasource_uuid,
            token=metadata.get("lock_token") or task.task_id,
        )
        metadata["timeout_cancelled_at"] = now.isoformat()
        task.status = TaskStatus.FAILURE
        task.finished_at = now
        task.error = error
        task.metadata = metadata
        task.save(update_fields=["status", "finished_at", "error", "metadata"])
        failed_count += 1

    completed = TaskExecution.objects.filter(
        module__in=[
            "lens_datasource",
            "lens_datasource_conversion",
            "lens_datasource_upload",
        ],
        status__in=TaskStatus.get_completed_statuses(),
        metadata__datasource_uuid__isnull=False,
        metadata__lock_token__isnull=False,
        finished_at__gte=cutoff,
    )
    released_count = 0
    for task in completed:
        metadata = task.metadata or {}
        datasource_uuid = metadata.get("datasource_uuid")
        released = release_datasource_lock(
            datasource_uuid,
            token=metadata.get("lock_token"),
        )
        if released:
            released_count += 1
        if released and task.status in [
            TaskStatus.FAILURE,
            TaskStatus.REVOKED,
        ]:
            datasource = DataSource.objects.filter(uuid=datasource_uuid).first()
            if datasource is not None:
                if task.module == "lens_datasource_conversion":
                    cancel_datasource_conversion_on_lensnode(
                        datasource.lensnode,
                        task.task_id,
                    )
                elif task.module == "lens_datasource_upload":
                    cancel_datasource_upload_on_lensnode(
                        datasource.lensnode,
                        task.task_id,
                    )
                else:
                    cancel_datasource_sync_on_lensnode(
                        datasource.lensnode,
                        task.task_id,
                    )

    return {
        "failed": failed_count,
        "locks_released": released_count,
        "orphaned": orphaned_count,
        "timeout_s": timeout_s,
        "startup": startup,
    }


def _datasource_task_metadata(datasource, trigger):
    """Return unified task metadata for datasource synchronization."""

    lensnode = datasource.lensnode
    config = datasource.config or {}
    sync_policy = datasource.sync_policy or {}
    conversion = sync_policy.get("conversion") or {}
    return {
        "type": "datasource",
        "trigger": trigger,
        "datasource_uuid": str(datasource.uuid),
        "datasource_name": datasource.name,
        "source_type": datasource.source_type,
        "repo_url": config.get("repo_url", ""),
        "branch": config.get("branch", ""),
        "auth_scheme": config.get("auth_scheme", ""),
        "document_url": config.get("document_url", ""),
        "app_token": config.get("app_token", ""),
        "doc_ids": config.get("doc_ids", []),
        "sync_mode": config.get("sync_mode", ""),
        "folder_url": config.get("folder_url", ""),
        "folder_token": config.get("folder_token", ""),
        "recursive": config.get("recursive", True),
        "max_depth": config.get("max_depth", ""),
        "credential_configured": bool(datasource.credential_id),
        "lensnode_uuid": str(lensnode.uuid) if lensnode else "",
        "lensnode_name": lensnode.name if lensnode else "",
        "target_path": datasource.target_path,
        "sync_policy": sync_policy,
        "conversion": conversion,
        "conversion_enabled": _conversion_enabled(conversion),
        "sync_interval_seconds": (sync_policy).get("interval_seconds"),
        "steps": [],
        "logs": [],
    }


def _conversion_enabled(conversion):
    """Return whether datasource conversion is enabled."""

    return bool(
        conversion.get("document")
        or conversion.get("image")
        or conversion.get("embedded_image")
    )


def _append_datasource_task_step(
    task_id,
    name,
    status,
    message,
    task_status=None,
):
    """Append one datasource task step to TaskExecution metadata."""

    from agentcore_task.adapters.django import TaskTracker
    from agentcore_task.constants import TaskStatus

    TaskTracker.update_task_status(
        task_id,
        task_status or TaskStatus.STARTED,
        metadata=_datasource_step_metadata(task_id, name, status, message),
    )


def _datasource_step_metadata(
    task_id,
    name,
    status,
    message,
    progress_percent=None,
):
    """Return metadata with an appended datasource task step."""

    from agentcore_task.adapters.django.models import TaskExecution

    task = TaskExecution.objects.filter(task_id=task_id).first()
    metadata = dict(task.metadata or {}) if task else {}
    steps = list(metadata.get("steps") or [])
    steps.append(
        {
            "name": name,
            "status": status,
            "message": message,
            "timestamp": timezone.now().isoformat(),
        }
    )
    result = {
        "steps": steps,
        "progress_step": name,
        "progress_message": message,
    }
    if progress_percent is not None:
        result["progress_percent"] = progress_percent
    return result


class SourceSyncBusy(RuntimeError):
    """Raised when a datasource already has an active sync lock."""


@contextmanager
def datasource_lock(datasource_uuid, ttl_s=600):
    """Acquire an expiring sync lock for one datasource."""

    token = uuid.uuid4().hex
    acquire_datasource_lock(datasource_uuid, token=token, ttl_s=ttl_s)
    try:
        yield
    finally:
        release_datasource_lock(datasource_uuid, token=token)


@shared_task(
    name="lens.check_lensnode_disconnect_grace_period",
    queue="lens",
    ignore_result=True,
)
def check_lensnode_disconnect_grace_period(lensnode_uuid, disconnected_at_iso):
    """Fail a node's runs only if it stays disconnected past the grace window.

    Scheduled once (with a countdown) by LensNodeConsumer.disconnect(). A brief
    WebSocket drop (e.g. a blue/green API recycle) must not fail runs the node
    is still executing, so failure is deferred here. disconnected_at_iso pins
    this to the disconnect episode that scheduled it: if the node reconnected
    (and maybe dropped again) since, disconnected_at has moved on and this
    stale check no-ops, leaving the newer disconnect's own check to handle it.

    Separate from the periodic idle reaper (lensnode_cleanup_task): that
    backstops genuinely stuck runs on live nodes, this handles a confirmed-gone
    node — different purposes, not merged.
    """

    scheduled_at = parse_datetime(disconnected_at_iso)

    def is_same_episode():
        # Tolerant compare: a DB that truncates sub-second precision (or any
        # round-trip skew) must not make the episode pin spuriously mismatch.
        # Real disconnects are always seconds apart (reconnect backoff), so a
        # 1s window identifies the episode unambiguously.
        if node.disconnected_at is None or scheduled_at is None:
            return False
        return abs((node.disconnected_at - scheduled_at).total_seconds()) <= 1

    try:
        node = LensNode.objects.get(uuid=lensnode_uuid)
    except LensNode.DoesNotExist:
        return

    if node.status == LensNode.Status.ONLINE:
        logger.info(
            "LensNode %s reconnected within the grace period; nothing to do",
            lensnode_uuid,
        )
        return

    if not is_same_episode():
        logger.info(
            "LensNode %s disconnected again since this check was scheduled; "
            "deferring to that episode's own check",
            lensnode_uuid,
        )
        return

    # Re-read right before acting to narrow (not eliminate) the race where the
    # node reconnects between the check above and failing its runs.
    node.refresh_from_db()
    if node.status == LensNode.Status.ONLINE or not is_same_episode():
        return

    from .services import mark_active_runs_awaiting_resume

    logger.warning(
        "LensNode %s still disconnected after the grace period; marking "
        "its RUNNING/STREAMING runs as awaiting resume",
        lensnode_uuid,
    )
    mark_active_runs_awaiting_resume(lensnode_uuid)
    reconcile_orphaned_datasource_conversions(
        lensnode_uuid,
        node.connection_id,
        [],
    )


@shared_task(
    name="lens.confirm_reconcile_orphan",
    queue="lens",
    ignore_result=True,
)
def confirm_reconcile_orphan(run_uuid):
    """Park a run only if it's still non-terminal after the confirm window.

    Scheduled (with a countdown) by
    lens.services.schedule_reconcile_orphan_confirmation when a reconnecting
    node's hello doesn't report the run as active. LensNode redelivers
    run_done at-least-once through a durable outbox, so a run that finished
    during the drop reaches a terminal state through the normal completion
    path before this fires. The filtered update is atomic and inherently
    idempotent — no episode-pinning needed (unlike
    check_lensnode_disconnect_grace_period, which guards a shared,
    overwritable LensNode.disconnected_at field): this only ever acts on the
    one run_uuid it was scheduled for, and a run that already left
    RUNNING/STREAMING simply won't match the filter.

    The run stays RUNNING with resume_by set rather than introducing a new
    persisted status that an older blue/green rollback cannot understand.
    """

    now = timezone.now()
    from .services import (
        get_reconcile_confirm_grace_seconds,
        get_run_resume_deadline,
        schedule_awaiting_run_expiration,
    )

    activity_cutoff = now - timedelta(seconds=get_reconcile_confirm_grace_seconds())
    stale_activity = Q(last_activity_at__isnull=True) | Q(
        last_activity_at__lte=activity_cutoff
    )
    run = (
        Run.objects.select_related("lensnode", "execution")
        .filter(
            uuid=run_uuid,
            status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        )
        .filter(stale_activity)
        .first()
    )
    if run is None:
        return
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
    updated = (
        Run.objects.filter(
            pk=run.pk,
            status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        )
        .filter(stale_activity)
        .update(**updates)
    )
    if updated:
        from .services import fail_running_steps_for_runs

        run_id = Run.objects.filter(uuid=run_uuid).values_list("id", flat=True).first()
        if run_id:
            fail_running_steps_for_runs([run_id])
        if resume_by <= now:
            RunExecution.objects.filter(
                run_id=run_id,
                status__in=[
                    RunExecution.Status.QUEUED,
                    RunExecution.Status.DISPATCHED,
                    RunExecution.Status.RUNNING,
                ],
            ).update(
                status=RunExecution.Status.FAILED,
                finished_at=now,
            )
        logger.warning(
            "Run %s still non-terminal after the reconcile confirm grace " "window; %s",
            run_uuid,
            (
                "parking it as awaiting resume"
                if resume_by > now
                else "its original timeout has expired"
            ),
        )
        if resume_by > now:
            schedule_awaiting_run_expiration(run.uuid, resume_by)
        if (
            resume_by > now
            and LensNode.objects.filter(
                pk=run.lensnode_id,
                status=LensNode.Status.ONLINE,
            ).exists()
        ):
            from .services import resume_awaiting_runs_for_lensnode

            resume_awaiting_runs_for_lensnode(run.lensnode.uuid)


@shared_task(
    name="lens.expire_awaiting_run",
    queue="lens",
    ignore_result=True,
)
def expire_awaiting_run(run_uuid):
    """Fail one parked Run when its precise resume deadline passes."""

    now = timezone.now()
    run = (
        Run.objects.filter(
            uuid=run_uuid,
            status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
            resume_by__isnull=False,
        )
        .values("id", "resume_by")
        .first()
    )
    if run is None:
        return 0
    if run["resume_by"] > now:
        from .services import schedule_awaiting_run_expiration

        schedule_awaiting_run_expiration(run_uuid, run["resume_by"])
        return 0
    updated = Run.objects.filter(
        id=run["id"],
        status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        resume_by__lte=now,
    ).update(
        status=Run.Status.FAILED,
        error="LENSNODE_RESUME_EXPIRED",
        resume_by=None,
        finished_at=now,
        updated_at=now,
    )
    if not updated:
        return 0
    from .services import fail_running_steps_for_runs

    fail_running_steps_for_runs([run["id"]])
    RunExecution.objects.filter(
        run_id=run["id"],
        status__in=[
            RunExecution.Status.QUEUED,
            RunExecution.Status.DISPATCHED,
            RunExecution.Status.RUNNING,
        ],
    ).update(status=RunExecution.Status.FAILED, finished_at=now)
    return 1


@shared_task(
    name="lens.retry_awaiting_run_resume",
    queue="lens",
    ignore_result=True,
)
def retry_awaiting_run_resume(run_uuid):
    """Retry checkpoint admission while its bounded deadline remains valid."""

    run = (
        Run.objects.select_related("lensnode")
        .filter(
            uuid=run_uuid,
            status=Run.Status.RUNNING,
            resume_by__gt=timezone.now(),
        )
        .first()
    )
    if run is None or run.lensnode is None:
        return 0
    from .services import resume_awaiting_runs_for_lensnode

    return resume_awaiting_runs_for_lensnode(run.lensnode.uuid)


@shared_task(name="lens.lensnode_health", queue="lens")
def lensnode_health_task():
    """Mark stale online LensNodes offline based on heartbeat age."""

    if not _is_global_task_enabled(ScheduledTask.TaskType.LENSNODE_HEALTH):
        return 0

    record = _get_or_create_global_record(ScheduledTask.TaskType.LENSNODE_HEALTH)
    record.last_status = ScheduledTask.Status.RUNNING
    record.last_error = ""
    record.last_run_at = timezone.now()
    record.save(update_fields=["last_status", "last_error", "last_run_at"])

    setting = GlobalSetting.objects.filter(
        key="lensnode.health.offline_threshold_s"
    ).first()
    threshold_s = int(setting.value if setting else 60)
    now = timezone.now()
    cutoff = now - timedelta(seconds=threshold_s)
    stale_nodes = list(
        LensNode.objects.filter(
            status=LensNode.Status.ONLINE,
            last_heartbeat_at__lt=cutoff,
        ).only("pk", "uuid")
    )
    updated = 0
    from .services import schedule_lensnode_disconnect_grace_check

    for node in stale_nodes:
        transitioned = LensNode.objects.filter(
            pk=node.pk,
            status=LensNode.Status.ONLINE,
            last_heartbeat_at__lt=cutoff,
        ).update(
            status=LensNode.Status.OFFLINE,
            connection_id="",
            disconnected_at=now,
            updated_at=now,
        )
        if transitioned:
            updated += 1
            schedule_lensnode_disconnect_grace_check(node.uuid, now)

    record.last_status = ScheduledTask.Status.SUCCESS
    record.last_metrics = {
        "offline": updated,
        "threshold_s": threshold_s,
    }
    record.last_run_at = timezone.now()
    record.save(update_fields=["last_status", "last_metrics", "last_run_at"])
    return updated


@shared_task(name="lens.lensnode_cleanup", queue="lens")
def lensnode_cleanup_task():
    """Fail stale non-terminal runs that no LensNode has completed."""

    if not _is_global_task_enabled(ScheduledTask.TaskType.LENSNODE_CLEANUP):
        return 0

    record = _get_or_create_global_record(ScheduledTask.TaskType.LENSNODE_CLEANUP)
    record.last_status = ScheduledTask.Status.RUNNING
    record.last_error = ""
    record.last_run_at = timezone.now()
    record.save(update_fields=["last_status", "last_error", "last_run_at"])

    # Reap runs that went silent (idle), not ones that are simply long but
    # still streaming: a live run refreshes last_activity_at continuously,
    # so a long answer survives as long as it keeps producing output. The
    # absolute timeout is a deliberately generous runaway ceiling that
    # still bounds a run which never stops emitting; raise it if you expect
    # single answers to legitimately exceed it.
    idle_setting = GlobalSetting.objects.filter(
        key="lensnode.defaults.idle_timeout"
    ).first()
    idle_timeout_s = int(idle_setting.value if idle_setting else 300)
    setting = GlobalSetting.objects.filter(key="lensnode.defaults.timeout").first()
    timeout_s = int(setting.value if setting else 14400)
    now = timezone.now()
    idle_cutoff = now - timedelta(seconds=idle_timeout_s)
    abs_cutoff = now - timedelta(seconds=timeout_s)
    stale = Run.objects.filter(
        status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        resume_by__isnull=True,
    ).filter(
        Q(last_activity_at__lt=idle_cutoff)
        | Q(last_activity_at__isnull=True, started_at__lt=idle_cutoff)
        | Q(started_at__lt=abs_cutoff)
    )
    stale_ids = list(stale.values_list("id", flat=True))
    count = len(stale_ids)
    if count:
        from .services import fail_running_steps_for_runs

        fail_running_steps_for_runs(stale_ids)
    stale.update(
        status=Run.Status.FAILED,
        error="LENS_RUN_TIMEOUT",
        finished_at=now,
        updated_at=now,
    )
    # Fail awaiting-resume runs whose node never came back before the resume
    # deadline (see services.get_awaiting_resume_ttl_hours).
    expired_resume = Run.objects.filter(
        status__in=[Run.Status.RUNNING, Run.Status.STREAMING],
        resume_by__lt=now,
    )
    expired_resume_ids = list(expired_resume.values_list("id", flat=True))
    expired_count = len(expired_resume_ids)
    if expired_count:
        from .services import fail_running_steps_for_runs

        fail_running_steps_for_runs(expired_resume_ids)
    expired_resume.update(
        status=Run.Status.FAILED,
        error="LENSNODE_RESUME_EXPIRED",
        resume_by=None,
        finished_at=now,
        updated_at=now,
    )
    RunExecution.objects.filter(
        run__status=Run.Status.FAILED,
        status__in=[
            RunExecution.Status.QUEUED,
            RunExecution.Status.DISPATCHED,
            RunExecution.Status.RUNNING,
        ],
    ).update(
        status=RunExecution.Status.FAILED,
        finished_at=timezone.now(),
    )
    datasource_sync_metrics = cleanup_stale_datasource_sync_tasks()

    record.last_status = ScheduledTask.Status.SUCCESS
    record.last_metrics = {
        "failed": count,
        "resume_expired": expired_count,
        "idle_timeout_s": idle_timeout_s,
        "timeout_s": timeout_s,
        "datasource_sync": datasource_sync_metrics,
    }
    record.last_run_at = timezone.now()
    record.save(update_fields=["last_status", "last_metrics", "last_run_at"])
    return count


@shared_task(name="lens.run_retention", queue="lens")
def run_retention_task():
    """Celery entrypoint for deleting old terminal runs."""

    if not _is_global_task_enabled(ScheduledTask.TaskType.RUN_RETENTION):
        return 0

    record = _get_or_create_global_record(ScheduledTask.TaskType.RUN_RETENTION)
    record.last_status = ScheduledTask.Status.RUNNING
    record.last_error = ""
    record.last_run_at = timezone.now()
    record.save(update_fields=["last_status", "last_error", "last_run_at"])

    setting = GlobalSetting.objects.filter(key="retention.run_days").first()
    retention_days = setting.value if setting else 30
    cutoff = timezone.now() - timedelta(days=int(retention_days))

    terminal_runs = Run.objects.filter(
        status__in=[
            Run.Status.AWAITING_USER_INPUT,
            Run.Status.DONE,
            Run.Status.FAILED,
            Run.Status.CANCELLED,
        ],
        finished_at__lt=cutoff,
    )
    run_ids = list(terminal_runs.values_list("id", flat=True))
    run_snapshot_ids = list(
        ExecutionSnapshot.objects.filter(run_id__in=run_ids).values_list(
            "id",
            flat=True,
        )
    )
    datasource_snapshot_ids = list(
        ExecutionSnapshot.objects.filter(
            run__isnull=True,
            created_at__lt=cutoff,
        ).values_list("id", flat=True)
    )
    snapshot_ids = run_snapshot_ids + datasource_snapshot_ids
    if snapshot_ids:
        CredentialLease.objects.filter(snapshot_id__in=snapshot_ids).delete()
        PluginInvocation.objects.filter(snapshot_id__in=snapshot_ids).delete()
        ExecutionSnapshot.objects.filter(id__in=snapshot_ids).delete()

    deleted, _ = terminal_runs.delete()

    record.last_status = ScheduledTask.Status.SUCCESS
    record.last_metrics = {
        "deleted": deleted,
        "plugin_snapshots_deleted": len(snapshot_ids),
        "retention_days": retention_days,
    }
    record.last_run_at = timezone.now()
    record.save(update_fields=["last_status", "last_metrics", "last_run_at"])
    return deleted


@shared_task(name="lens.document_attachment_cleanup", queue="lens")
def document_attachment_cleanup_task():
    """Delete source files whose fixed temporary retention has elapsed."""

    from .document_attachments import cleanup_expired_document_files

    return cleanup_expired_document_files()
