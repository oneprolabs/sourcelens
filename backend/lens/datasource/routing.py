"""Select session data and use the current LensNode copies."""

import logging

from django.db import transaction

from ..models import Run, RunExecution, SessionDataSource

logger = logging.getLogger(__name__)


class DatasourceRoutingError(RuntimeError):
    """A datasource cannot be prepared for a question."""


def selected_bindings(assistant, question):
    """Return every data source explicitly bound to the assistant."""

    del question
    return list(assistant.datasource_bindings.select_related(
        "datasource", "item"
    ))


def _unavailable_datasource_uuids(run, rows):
    """Return the session datasource snapshots without usable files.

    Datasource content lives on the executing LensNode, so readiness is
    asked of that node rather than inferred from the control-plane DB. A
    probe failure (offline node, timeout, unknown shape) is treated as
    available so a transient control-plane fault never blocks a run.
    """

    lensnode = run.lensnode
    if lensnode is None:
        return set()
    from .services import (
        check_datasource_path,
        datasource_storage_target_path,
    )

    unavailable = set()
    for row in rows:
        if row.datasource.status != "active":
            continue
        try:
            target = datasource_storage_target_path(row.datasource, lensnode)
            result = check_datasource_path(
                lensnode,
                target,
                row.datasource.source_type,
            )
        except Exception:
            # Fail open: a transient control-plane fault must not block a run.
            logger.warning(
                "Datasource readiness probe failed for %s",
                row.uuid,
                exc_info=True,
            )
            continue
        if result.get("exists") is False or result.get("is_empty") is True:
            unavailable.add(str(row.uuid))
    return unavailable


def _session_snapshots(run, skipped_uuids):
    """Return the dispatch snapshots with unavailable optional rows removed.

    Rebuilt from the session each run so a datasource that becomes available
    (or unavailable) between attempts is reflected, never a stale list.
    """

    from .packages import run_datasource_snapshots

    return [
        snapshot
        for snapshot in run_datasource_snapshots(run)
        if str(snapshot.get("snapshot_uuid")) not in skipped_uuids
    ]


def prepare_run_datasources(run):
    """Drop unavailable datasources before the run dispatches.

    A datasource with no usable files is skipped whenever at least one bound
    datasource can still answer. A required empty datasource fails the run
    with a named error only when nothing usable is left.
    """

    rows = list(
        SessionDataSource.objects.filter(session=run.session).select_related(
            "datasource"
        )
    )
    unavailable = _unavailable_datasource_uuids(run, rows)
    with transaction.atomic():
        locked = Run.objects.select_for_update().get(pk=run.pk)
        if locked.status != Run.Status.QUEUED:
            return False
        execution = RunExecution.objects.select_for_update().get(run=run)
        state = dict(execution.runtime_snapshot or {})
        disabled_required = [
            row.datasource.name
            for row in rows
            if row.datasource.status != "active" and row.required
        ]
        if disabled_required:
            raise DatasourceRoutingError("DATASOURCE_DISABLED")
        usable = [
            row
            for row in rows
            if row.datasource.status == "active"
            and str(row.uuid) not in unavailable
        ]
        empty = [
            row
            for row in rows
            if row.datasource.status == "active"
            and str(row.uuid) in unavailable
        ]
        if not usable:
            fatal = [row for row in empty if row.required]
            if fatal:
                raise DatasourceRoutingError(
                    "DATASOURCE_UNAVAILABLE:"
                    + ", ".join(row.datasource.name for row in fatal)
                )
        # An empty source is dropped when another source can answer; only an
        # all-empty required selection fails the run.
        skipped_uuids = {
            str(row.uuid)
            for row in rows
            if row.datasource.status != "active" and not row.required
        }
        skipped_uuids.update(str(row.uuid) for row in empty)
        state["datasource_snapshots"] = _session_snapshots(
            run, skipped_uuids
        )
        state.pop("datasource_sync_tasks", None)
        state["datasource_waiting"] = False
        execution.runtime_snapshot = state
        execution.save(update_fields=["runtime_snapshot"])
        return True
