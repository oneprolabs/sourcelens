"""Preview and confirm permanent tenant assistant deletion."""

import hashlib
import json

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from accounts.audit import record_audit
from accounts.delegation import get_active_delegation
from lens.assistant_management import AssistantVersionSerializer, StaleAssistantBatch
from lens.models import (
    Assistant,
    Message,
    MessageAttachment,
    Run,
    RunDiagnostic,
    RunDiagnosticTurn,
    RunOutputFile,
    Session,
    SharedQA,
    SharedQAFile,
)
from lens.session_deletion import delete_sessions

CONFIRMATION_SALT = "lens.assistant-deletion.v1"
CONFIRMATION_MAX_AGE = 600


class DeletionAssistantSerializer(AssistantVersionSerializer):
    """Keep slug confirmation exact, including whitespace and case."""

    confirmation_slug = serializers.CharField(required=False, trim_whitespace=False, max_length=180)


class BulkDeletionSerializer(serializers.Serializer):
    """Bound batch size and require an explicit review before applying."""

    assistants = DeletionAssistantSerializer(many=True, allow_empty=False, max_length=100)
    preview = serializers.BooleanField(required=True)
    confirmation_token = serializers.CharField(required=False, max_length=100000)

    def validate(self, attrs):
        """Reject duplicate rows and missing confirmation without changing records."""

        rows = attrs["assistants"]
        if len({row["uuid"] for row in rows}) != len(rows):
            raise ValidationError({"assistants": "Duplicate assistants are not allowed."})
        if not attrs["preview"] and (
            not attrs.get("confirmation_token") or any("confirmation_slug" not in row for row in rows)
        ):
            raise ValidationError("Preview and exact confirmation slugs are required.")
        return attrs


def _skip_reason(assistant, tenant):
    """Protect active work, independently managed resources and foreign history."""

    sessions = Session.objects.filter(assistant=assistant)
    runs = Run.objects.filter(session__assistant=assistant)
    if sessions.exclude(tenant=tenant).exists() or runs.exclude(tenant=tenant).exists():
        return "foreign_history"
    if RunOutputFile.objects.filter(assistant=assistant).exclude(tenant=tenant).exists():
        return "foreign_history"
    if SharedQA.objects.filter(assistant=assistant, run__isnull=False).exclude(run__tenant=tenant).exists():
        return "foreign_history"
    if runs.filter(status__in=["queued", "running", "streaming", "awaiting_user_input"]).exists():
        return "active_work"
    if RunDiagnostic.objects.filter(run__in=runs, status__in=["queued", "running"]).exists():
        return "active_work"
    if RunDiagnosticTurn.objects.filter(diagnostic__run__in=runs, status__in=["queued", "running"]).exists():
        return "active_work"
    if assistant.evaluation_datasets.exists():
        return "evaluation_data"
    if assistant.collaboration_coordinators.exists():
        return "collaboration_reference"
    if assistant.alert_rules.exists():
        return "automation_reference"
    return None


def _impact(assistant):
    """Hash record identities and revisions without loading conversation content."""

    sessions = Session.objects.filter(assistant=assistant)
    shares = SharedQA.objects.filter(Q(assistant=assistant) | Q(run__session__assistant=assistant))
    collections = {
        "sessions": (sessions, ("uuid", "updated_at")),
        "questions": (Message.objects.filter(session__in=sessions, role="user"), ("uuid",)),
        "messages": (Message.objects.filter(session__in=sessions), ("uuid",)),
        "runs": (Run.objects.filter(session__in=sessions), ("uuid", "updated_at")),
        "shares": (shares, ("uuid", "updated_at")),
        "attachments": (MessageAttachment.objects.filter(session__in=sessions), ("uuid", "updated_at")),
        "output_files": (
            RunOutputFile.objects.filter(Q(assistant=assistant) | Q(session__in=sessions)),
            ("uuid", "updated_at"),
        ),
        "shared_files": (SharedQAFile.objects.filter(share__in=shares), ("uuid", "updated_at")),
    }
    digest = hashlib.sha256()
    counts = {}
    for name, (queryset, fields) in collections.items():
        digest.update(name.encode())
        count = 0
        for record in queryset.order_by("pk").values_list(*fields).iterator(chunk_size=1000):
            digest.update(json.dumps(record, default=str, separators=(",", ":")).encode())
            count += 1
        counts[name] = count
    return counts, digest.hexdigest()


@transaction.atomic
def bulk_delete(request, tenant, *, assistants, preview, confirmation_token=""):
    """Serialize deletion with new work and bind confirmation to the reviewed impact."""

    versions = {row["uuid"]: row["updated_at"] for row in assistants}
    rows = list(
        Assistant.objects.select_for_update().filter(uuid__in=versions, tenant=tenant, is_system=False).order_by("uuid")
    )
    if len(rows) != len(versions):
        raise ValidationError({"assistants": "One or more assistants are unavailable."})
    if any(row.updated_at != versions[row.uuid] for row in rows):
        raise StaleAssistantBatch()
    list(Session.objects.select_for_update().filter(assistant__in=rows).order_by("uuid").values_list("pk", flat=True))
    list(
        Run.objects.select_for_update()
        .filter(session__assistant__in=rows)
        .order_by("uuid")
        .values_list("pk", flat=True)
    )
    list(
        RunDiagnostic.objects.select_for_update()
        .filter(run__session__assistant__in=rows)
        .order_by("uuid")
        .values_list("pk", flat=True)
    )
    delegation = get_active_delegation(request.user)
    actor = delegation.platform_user if delegation else request.user
    manifest = []
    reviewed = []
    for row in rows:
        reason = _skip_reason(row, tenant)
        counts, digest = ({}, "") if reason == "foreign_history" else _impact(row)
        identity = {"uuid": str(row.uuid), "name": row.name, "slug": row.slug, "updated_at": row.updated_at.isoformat()}
        reviewed.append({**identity, "impact": counts, "skip_reason": reason})
        manifest.append({**identity, "digest": digest, "skip_reason": reason})
    confirmation = {
        "actor": str(actor.pk),
        "user": str(request.user.pk),
        "tenant": str(tenant.pk),
        "delegation": str(delegation.pk) if delegation else None,
        "manifest": manifest,
    }
    if preview:
        return {
            "assistants": reviewed,
            "confirmation_token": signing.dumps(confirmation, salt=CONFIRMATION_SALT, compress=True),
        }
    try:
        original = signing.loads(confirmation_token, salt=CONFIRMATION_SALT, max_age=CONFIRMATION_MAX_AGE)
    except signing.BadSignature as exc:
        raise ValidationError({"confirmation_token": "Preview expired or is invalid. Review again."}) from exc
    if original != confirmation:
        raise StaleAssistantBatch("The deletion impact changed. Review again.")
    slugs = {row["uuid"]: row["confirmation_slug"] for row in assistants}
    if any(slugs[row.uuid] != row.slug for row in rows):
        raise ValidationError({"assistants": "Enter each assistant's exact slug."})
    skipped = [{"uuid": row["uuid"], "reason": row["skip_reason"]} for row in reviewed if row["skip_reason"]]
    deleted = [row for row, review in zip(rows, reviewed) if not review["skip_reason"]]
    deleted_uuids = [str(row.uuid) for row in deleted]
    try:
        SharedQA.objects.filter(Q(assistant__in=deleted) | Q(run__session__assistant__in=deleted)).delete()
        delete_sessions(Session.objects.filter(assistant__in=deleted))
        Assistant.objects.filter(pk__in=[row.pk for row in deleted]).delete()
    except ProtectedError as exc:
        raise StaleAssistantBatch("Assistant dependencies changed. Review again.") from exc
    record_audit(
        action="assistant.bulk_delete",
        request=request,
        tenant=tenant,
        actor=actor,
        delegation=delegation,
        resource_type="assistant",
        metadata={"assistant_uuids": deleted_uuids, "deleted": len(deleted), "skipped": len(skipped)},
    )
    return {"deleted": len(deleted), "skipped": len(skipped), "skipped_items": skipped}
