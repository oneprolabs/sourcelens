import logging

from django.db import transaction
from django.db.models import QuerySet
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from .models import MessageAttachment, Run, RunOutputFile, Session, SharedQAFile

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=Run)
def preserve_evaluation_evidence_scope(sender, instance, **kwargs):
    """Keep collaboration restrictions after any ORM deletion clears family links."""
    from .evaluation.evidence import preserve_evaluation_evidence

    origin = kwargs.get("origin")
    connection = transaction.get_connection(kwargs.get("using") or "default")
    deletion = connection.atomic_blocks[-1]
    if getattr(origin, "_evaluation_evidence_preserved", None) is deletion:
        return
    if isinstance(origin, QuerySet) and origin.model is Run:
        run_ids = origin.values_list("pk", flat=True)
    elif isinstance(origin, QuerySet) and origin.model is Session:
        run_ids = Run.objects.filter(session__in=origin).values_list("pk", flat=True)
    elif isinstance(origin, Session):
        run_ids = Run.objects.filter(session=origin).values_list("pk", flat=True)
    else:
        preserve_evaluation_evidence([instance.pk])
        return
    preserve_evaluation_evidence(run_ids)
    origin._evaluation_evidence_preserved = deletion


@receiver(post_delete, sender=Run)
def clear_evaluation_deletion_scope(sender, instance, **kwargs):
    """A reused queryset must check new families on its next deletion."""
    origin = kwargs.get("origin")
    if getattr(origin, "_evaluation_evidence_preserved", False):
        del origin._evaluation_evidence_preserved


@receiver(post_save, sender=Run)
def capture_collaboration_evidence_scope(sender, instance, created=False, raw=False, update_fields=None, **kwargs):
    """Seal provider authority before a collaborator can be detached or cleaned up."""
    fields = {"parent_run", "parent_run_id", "session", "session_id", "tenant", "tenant_id"}
    if raw or not (created or update_fields is None or fields.intersection(update_fields)):
        return
    if not instance.parent_run_id and instance.session.assistant.tenant_id == instance.tenant_id:
        return
    from .evaluation.evidence import preserve_evaluation_evidence

    preserve_evaluation_evidence([instance.pk])


@receiver(pre_save, sender=Run)
def retain_collaboration_evidence_scope(sender, instance, raw=False, update_fields=None, **kwargs):
    """A stale full model save cannot clear a restriction sealed by another operation."""
    if raw or instance._state.adding or instance.configuration_evidence_restricted:
        return
    if update_fields is None or "configuration_evidence_restricted" in update_fields:
        instance.configuration_evidence_restricted = Run.objects.filter(
            pk=instance.pk, configuration_evidence_restricted=True
        ).exists()


@receiver(post_delete, sender=SharedQAFile)
@receiver(post_delete, sender=RunOutputFile)
@receiver(post_delete, sender=MessageAttachment)
def purge_deliverable_bytes(sender, instance, **kwargs):
    """Delete stored attachment and deliverable bytes after a committed deletion.

    Django cascades database rows but leaves FileField bytes in storage.
    Removing them here keeps both private and shared deliverables bounded
    to live database records across every deletion path.
    """

    if not instance.file:
        return
    storage = instance.file.storage
    name = instance.file.name

    def purge():
        """Keep files intact when a surrounding deletion transaction rolls back."""

        try:
            storage.delete(name)
        except Exception:
            logger.warning("Failed to purge file bytes for %s", instance.uuid, exc_info=True)

    transaction.on_commit(purge)


@receiver(post_save, sender=Run)
def capture_terminal_run(sender, instance, raw=False, **kwargs):
    """Persist alert work in the same transaction as a terminal Run save."""

    if not raw:
        from .alerts import capture_run_alert

        capture_run_alert(instance)


@receiver(post_save, sender="agentcore_task_tracker.TaskExecution")
def capture_terminal_datasource_task(sender, instance, raw=False, **kwargs):
    """Capture datasource task saves without modifying the vendor package."""

    if not raw:
        from .alerts import capture_task_alert

        capture_task_alert(instance)
