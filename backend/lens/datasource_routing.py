"""Select session data and wait asynchronously for ready versions."""

import re
import uuid
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from .models import DataSource, Run, RunExecution, SessionDataSource


class DatasourceRoutingError(RuntimeError):
    """A datasource cannot be prepared for a question."""


def selected_bindings(assistant, question):
    """Match catalog identities inside the administrator-defined scope."""

    mode = (assistant.settings or {}).get("datasource_routing", "auto")
    bindings = list(assistant.datasource_bindings.select_related(
        "datasource", "item"
    ))
    if mode == "selected":
        return bindings
    if not bindings:
        bindings = [SimpleNamespace(
            datasource=source, item=None, item_id=None, required=True,
            mount_name=f"ds_{source.uuid.hex}",
        ) for source in DataSource.objects.filter(status="active")]
    bindings = [b for b in bindings if b.datasource.status == "active"]
    if mode == "all":
        return bindings
    query = question.casefold()
    matches = []
    for binding in bindings:
        source = binding.datasource
        config = source.datasource_config or {}
        names = [source.name, config.get("repository", "")]
        names.extend(config.get("repositories") or [])
        names.extend(config.get("projects") or [])
        identities = {
            name.casefold().strip().split("/")[-1]
            for name in names if isinstance(name, str) and name.strip()
        }
        if any(re.search(
            r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])",
            query,
        ) for name in identities):
            matches.append(binding)
    if matches:
        return matches
    if assistant.datasource_bindings.exists():
        return bindings
    raise DatasourceRoutingError("DATASOURCE_NO_MATCH")


def _sync_task(source, run):
    """Reuse active sync work or enqueue one task under a datasource lock."""

    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    from .tasks import register_datasource_sync_task, source_sync_task

    source = DataSource.objects.select_for_update().get(pk=source.pk)
    active = TaskExecution.objects.filter(
        module="lens_datasource",
        metadata__datasource_uuid=str(source.uuid),
    ).exclude(status__in=TaskStatus.get_completed_statuses()).first()
    if active:
        return active.task_id
    if source.source_type == "managed_workspace":
        raise DatasourceRoutingError("DATASOURCE_UPLOAD_REQUIRED")
    task_id = uuid.uuid4().hex
    register_datasource_sync_task(
        source, task_id, "question", created_by=run.session.user,
    )
    transaction.on_commit(lambda: source_sync_task.apply_async(
        args=[str(source.uuid), "question", task_id],
    ))
    return task_id


@transaction.atomic
def prepare_run_datasources(run):
    """Return False while sync is pending, without holding a worker asleep."""

    from agentcore_task.adapters.django.models import TaskExecution
    from agentcore_task.constants import TaskStatus

    from .datasource_snapshots import capture_session_datasources

    locked = Run.objects.select_for_update().get(pk=run.pk)
    if locked.status != Run.Status.QUEUED:
        return False
    execution = RunExecution.objects.select_for_update().get(run=run)
    state = dict(execution.runtime_snapshot or {})
    pending = dict(state.get("datasource_sync_tasks") or {})
    rows = list(SessionDataSource.objects.select_for_update().filter(
        session=run.session
    ).select_related("datasource", "item", "version"))
    missing = []
    for row in rows:
        if row.datasource.status != "active":
            raise DatasourceRoutingError("DATASOURCE_DISABLED")
        if row.version_id:
            continue
        missing.append(row)
    if not missing:
        return True
    if (timezone.now() - run.created_at).total_seconds() > 600:
        raise DatasourceRoutingError("DATASOURCE_SYNC_TIMEOUT")
    waiting = False
    for row in missing:
        source = row.datasource
        items = source.items.filter(status="active")
        if row.item_id:
            items = items.filter(pk=row.item_id)
        ready = list(items)
        if ready and all(item.versions.filter(status="ready").exists()
                         for item in ready):
            binding = SimpleNamespace(
                datasource=source, item=row.item, item_id=row.item_id,
                mount_name=row.mount_name, required=True,
            )
            row.delete()
            capture_session_datasources(
                run.session, run.session.assistant, bindings=[binding],
            )
            continue
        key = str(source.uuid)
        task_id = pending.get(key)
        if task_id:
            task = TaskExecution.objects.filter(task_id=task_id).first()
            if task is None or task.status in TaskStatus.get_completed_statuses():
                raise DatasourceRoutingError("DATASOURCE_SYNC_FAILED")
        else:
            pending[key] = _sync_task(source, run)
        waiting = True
    state["datasource_sync_tasks"] = pending
    state["datasource_waiting"] = waiting
    execution.runtime_snapshot = state
    execution.save(update_fields=["runtime_snapshot"])
    return not waiting
