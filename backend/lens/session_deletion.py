"""Delete conversation records in dependency order and clean storage after commit."""

import logging

from django.db import transaction

from lens.document_attachments import delete_session_document_attachments
from lens.models import (
    CredentialLease,
    ExecutionSnapshot,
    PluginInvocation,
    Run,
    RunDiagnostic,
    Session,
    SessionCleanupOperation,
)

logger = logging.getLogger(__name__)


def _clean_session_storage(sessions):
    """Remove temporary documents and wake the durable remote cleanup outbox."""

    for session_uuid, user_id in sessions:
        try:
            delete_session_document_attachments(session_uuid, user_id=user_id)
        except Exception:
            logger.exception("Unable to purge temporary documents for Session %s", session_uuid)
    try:
        from lens.tasks import session_workspace_cleanup_task

        session_workspace_cleanup_task.delay()
    except Exception:
        logger.warning(
            "Session cleanup dispatch failed; durable operations remain pending",
            exc_info=True,
        )


@transaction.atomic
def delete_sessions(queryset):
    """Remove protected run dependents before sessions while retaining published shares."""

    sessions = list(queryset.select_for_update().only("pk", "uuid", "user_id").order_by("uuid"))
    if not sessions:
        return
    session_ids = [session.pk for session in sessions]
    runs = Run.objects.filter(session_id__in=session_ids)
    targets = set(runs.exclude(lensnode=None).values_list("session__uuid", "lensnode__uuid"))
    targets.update(runs.filter(execution__isnull=False).values_list("session__uuid", "execution__lensnode__uuid"))
    SessionCleanupOperation.objects.bulk_create(
        [
            SessionCleanupOperation(session_uuid=session_uuid, lensnode_uuid=node_uuid)
            for session_uuid, node_uuid in targets
        ],
        ignore_conflicts=True,
    )
    snapshots = ExecutionSnapshot.objects.filter(run__in=runs)
    CredentialLease.objects.filter(snapshot__in=snapshots).delete()
    PluginInvocation.objects.filter(snapshot__in=snapshots).delete()
    PluginInvocation.objects.filter(run__in=runs).delete()
    snapshots.delete()
    RunDiagnostic.objects.filter(run__in=runs).delete()
    runs.delete()
    Session.objects.filter(pk__in=session_ids).delete()
    cleanup = [(session.uuid, session.user_id) for session in sessions]
    transaction.on_commit(lambda: _clean_session_storage(cleanup))
