"""Select session data and use the current LensNode copies."""

from django.db import transaction

from ..models import Run, RunExecution, SessionDataSource
from .packages import datasource_target_paths


class DatasourceRoutingError(RuntimeError):
    """A datasource cannot be prepared for a question."""


def selected_bindings(assistant, question):
    """Return every data source explicitly bound to the assistant."""

    del question
    return list(assistant.datasource_bindings.select_related(
        "datasource", "item"
    ))


@transaction.atomic
def prepare_run_datasources(run):
    """Use the current LensNode datasource state without starting a sync."""

    locked = Run.objects.select_for_update().get(pk=run.pk)
    if locked.status != Run.Status.QUEUED:
        return False
    execution = RunExecution.objects.select_for_update().get(run=run)
    state = dict(execution.runtime_snapshot or {})
    rows = SessionDataSource.objects.filter(session=run.session).select_related(
        "datasource"
    )
    try:
        datasource_target_paths(run, rows)
    except ValueError as exc:
        raise DatasourceRoutingError(str(exc)) from exc
    for row in rows:
        if row.datasource.status != "active":
            raise DatasourceRoutingError("DATASOURCE_DISABLED")
    state.pop("datasource_sync_tasks", None)
    state["datasource_waiting"] = False
    execution.runtime_snapshot = state
    execution.save(update_fields=["runtime_snapshot"])
    return True
