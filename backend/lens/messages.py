"""Immutable, idempotent business message publication through the alert outbox."""

import hashlib
import json

from django.db import transaction
from django.utils import timezone

from .alerts import _wake_dispatcher
from .models import AlertOutbox


class PublicationConflict(ValueError):
    """An idempotency key was reused with different content."""


@transaction.atomic
def publish_message(*, topic, idempotency_key, title, body, publisher):
    """Persist validated plain-text content once per topic and business key.

    Callers must validate the public input contract with MessagePublicationSerializer.
    The publisher must be an authenticated operator authorized by the caller.
    """

    identity = json.dumps([topic, idempotency_key], ensure_ascii=False)
    source_uuid = hashlib.sha256(identity.encode()).hexdigest()
    content = {"topic": topic, "idempotency_key": idempotency_key, "title": title, "body": body}
    publication, created = AlertOutbox.objects.get_or_create(
        source_key=f"message:{source_uuid}:terminal",
        defaults={
            "subject": "message",
            "source_uuid": source_uuid,
            "payload": {
                **content,
                "occurred_at": timezone.now().isoformat(),
                "publisher_id": publisher.pk,
                "publisher_name": publisher.username,
            },
        },
    )
    if any(publication.payload.get(key) != value for key, value in content.items()):
        raise PublicationConflict("This topic and idempotency key already identify different message content.")
    if created or publication.processed_at is None:
        transaction.on_commit(_wake_dispatcher)
    return publication, created
