"""Asynchronous delivery and recovery of committed account notifications."""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from accounts.models import ApiKeyControlNotice
from accounts.services.api_key_notices import deliver_notice, enqueue_notice


@shared_task(ignore_result=True, queue="backend")
def deliver_api_key_notice_task(notice_uuid):
    """Deliver one durable notice without placing recipient or key details in the broker."""
    deliver_notice(notice_uuid)


@shared_task(ignore_result=True, queue="backend")
def dispatch_pending_api_key_notices_task():
    """Recover pending notices when the original on-commit broker dispatch was unavailable."""
    cutoff = timezone.now() - timedelta(minutes=2)
    ids = list(
        ApiKeyControlNotice.objects.filter(status="pending", updated_at__lte=cutoff)
        .order_by("updated_at")
        .values_list("pk", flat=True)[:100]
    )
    for notice_uuid in ids:
        enqueue_notice(notice_uuid)
