import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Run, RunOutputFile, SharedQAFile

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=SharedQAFile)
@receiver(post_delete, sender=RunOutputFile)
def purge_deliverable_bytes(sender, instance, **kwargs):
    """Delete stored bytes when a deliverable record is removed.

    Django cascades database rows but leaves FileField bytes in storage.
    Removing them here keeps both private and shared deliverables bounded
    to live database records across every deletion path.
    """

    if not instance.file:
        return
    try:
        instance.file.delete(save=False)
    except Exception:
        logger.warning(
            "Failed to purge deliverable bytes for %s",
            instance.uuid,
            exc_info=True,
        )


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
