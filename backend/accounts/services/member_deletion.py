"""Versioned member deletion with reviewed resource transfer and link revocation."""

import hashlib
import json

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework import serializers

from accounts.audit import record_audit
from accounts.models import Profile, TenantApiKey
from accounts.services.member_management import (
    MemberSelection,
    MemberSelectionChanged,
    _locked_selection,
)
from lens.models import (
    Assistant,
    CredentialLease,
    MessageAttachment,
    Run,
    RunDiagnostic,
    RunDiagnosticTurn,
    RunOutputFile,
    RunTraceEvent,
    Session,
    SharedQA,
    SharedQAFile,
)
from lens.session_deletion import delete_sessions

SALT = "accounts.member-deletion.v1"


class DeletionMemberInput(MemberSelection):
    """Require exact mailbox confirmation without trimming meaningful input."""

    confirmation_email = serializers.CharField(required=False, trim_whitespace=False, max_length=254)


class MemberDeletionInput(serializers.Serializer):
    """Bound a reviewed batch and keep removal separate from ordinary member edits."""

    members = DeletionMemberInput(many=True, allow_empty=False, max_length=100)
    preview = serializers.BooleanField(required=True)
    confirmation = serializers.CharField(required=False, max_length=100000)

    def validate(self, attrs):
        """Do not accept repeated identities or implicit confirmation."""
        if len({row["id"] for row in attrs["members"]}) != len(attrs["members"]):
            raise serializers.ValidationError({"members": "DUPLICATE_MEMBERS"})
        if not attrs["preview"] and (
            not attrs.get("confirmation") or any("confirmation_email" not in row for row in attrs["members"])
        ):
            raise serializers.ValidationError({"confirmation": "MEMBER_DELETION_REVIEW_REQUIRED"})
        return attrs


def _skip_reason(member, actor, tenant):
    """Protect the current administrator, platform identity, foreign history and live work."""
    user = member.user
    if user.pk == actor.pk:
        return "self"
    profile = getattr(user, "profile", None)
    if user.is_superuser or (profile and profile.platform_role == "super_admin"):
        return "platform_identity"
    sessions = Session.objects.filter(user=user)
    runs = Run.objects.filter(session__user=user)
    shares = SharedQA.objects.filter(published_by=user)
    from agentcore_metering.adapters.django.models import LLMConfig

    if (
        sessions.exclude(tenant=tenant).exists()
        or runs.exclude(tenant=tenant).exists()
        or RunOutputFile.objects.filter(Q(session__user=user) | Q(run__session__user=user))
        .exclude(tenant=tenant)
        .exists()
        or RunTraceEvent.objects.filter(run__in=runs).exclude(tenant=tenant).exists()
        or CredentialLease.objects.filter(snapshot__run__in=runs).exclude(tenant=tenant).exists()
        or Assistant.objects.filter(owner_user=user).exclude(tenant=tenant).exists()
        or shares.filter(run__isnull=True).exclude(assistant__tenant=tenant).exists()
        or shares.filter(run__isnull=False).exclude(run__tenant=tenant, run__session__tenant=tenant).exists()
        or LLMConfig.objects.filter(user=user).exclude(ownership__tenant=tenant).exists()
        or TenantApiKey.objects.filter(created_by=user).exclude(tenant=tenant).exists()
    ):
        return "foreign_history"
    if (
        runs.filter(status__in=["queued", "running", "streaming", "awaiting_user_input"]).exists()
        or RunDiagnostic.objects.filter(
            Q(run__in=runs) | Q(requested_by=user), status__in=["queued", "running"]
        ).exists()
        or RunDiagnosticTurn.objects.filter(
            Q(diagnostic__run__in=runs) | Q(requested_by=user),
            status__in=["queued", "running"],
        ).exists()
    ):
        return "active_work"
    return ""


def _impact(member, tenant):
    """Bind confirmation to actual resource identities without loading private conversation text."""
    from agentcore_metering.adapters.django.models import LLMConfig

    collections = {
        "assistants": (
            Assistant.objects.filter(tenant=tenant, owner_user=member.user),
            ("uuid", "updated_at"),
        ),
        "shares": (
            SharedQA.objects.filter(published_by=member.user),
            ("uuid", "updated_at"),
        ),
        "sessions": (Session.objects.filter(user=member.user), ("uuid", "updated_at")),
        "runs": (Run.objects.filter(session__user=member.user), ("uuid", "updated_at")),
        "attachments": (
            MessageAttachment.objects.filter(session__user=member.user),
            ("uuid", "updated_at"),
        ),
        "output_files": (
            RunOutputFile.objects.filter(Q(session__user=member.user) | Q(run__session__user=member.user)),
            ("uuid", "updated_at"),
        ),
        "shared_files": (
            SharedQAFile.objects.filter(share__published_by=member.user),
            ("uuid", "updated_at"),
        ),
        "api_keys": (
            TenantApiKey.objects.filter(created_by=member.user),
            ("uuid", "key_version", "status"),
        ),
        "models": (LLMConfig.objects.filter(user=member.user), ("uuid", "updated_at")),
    }
    digest = hashlib.sha256()
    counts = {}
    for name, (queryset, fields) in collections.items():
        digest.update(name.encode())
        counts[name] = 0
        for values in queryset.order_by("pk").values_list(*fields).iterator(chunk_size=1000):
            digest.update(json.dumps(values, default=str).encode())
            counts[name] += 1
    return counts, digest.hexdigest()


@transaction.atomic
def delete_members(*, request, data):
    """Apply exactly the reviewed impact or require a fresh confirmation."""
    tenant, members = _locked_selection(request, data)
    user_ids = [member.user_id for member in members]
    list(get_user_model().objects.select_for_update(no_key=True).filter(pk__in=user_ids).order_by("pk"))
    list(Profile.objects.select_for_update().filter(user_id__in=user_ids).order_by("user_id"))
    assistant_ids = set(
        Assistant.objects.filter(tenant=tenant, owner_user_id__in=user_ids).values_list("pk", flat=True)
    )
    assistant_ids.update(
        Session.objects.filter(tenant=tenant, user_id__in=user_ids).values_list("assistant_id", flat=True)
    )
    list(Assistant.objects.select_for_update().filter(pk__in=assistant_ids).order_by("uuid"))
    list(Session.objects.select_for_update().filter(tenant=tenant, user_id__in=user_ids).order_by("uuid"))
    list(Run.objects.select_for_update().filter(tenant=tenant, session__user_id__in=user_ids).order_by("uuid"))
    tenant, members = _locked_selection(request, data)
    versions = {row["id"]: row["version"] for row in data["members"]}
    reviewed, manifest = [], []
    for member in members:
        reason = _skip_reason(member, request.user, tenant)
        impact, digest = ({}, "") if reason == "foreign_history" else _impact(member, tenant)
        identity = {
            "id": member.user_id,
            "email": member.user.email,
            "version": versions[member.user_id],
        }
        assistants = (
            []
            if reason == "foreign_history"
            else list(
                Assistant.objects.filter(tenant=tenant, owner_user=member.user)
                .order_by("name", "uuid")
                .values("uuid", "name")[:20]
            )
        )
        reviewed.append(
            {
                **identity,
                "impact": impact,
                "assistants": assistants,
                "skip_reason": reason,
            }
        )
        manifest.append({**identity, "digest": digest, "skip_reason": reason})
    proof = {"actor": request.user.pk, "tenant": str(tenant.pk), "manifest": manifest}
    if data["preview"]:
        return {
            "members": reviewed,
            "confirmation": signing.dumps(proof, salt=SALT, compress=True),
        }
    try:
        original = signing.loads(data["confirmation"], salt=SALT, max_age=600)
    except signing.BadSignature:
        raise MemberSelectionChanged("MEMBER_DELETION_REVIEW_EXPIRED") from None
    if proof != original:
        raise MemberSelectionChanged("MEMBER_DELETION_IMPACT_CHANGED")
    emails = {row["id"]: row["confirmation_email"] for row in data["members"]}
    if any(emails[row["id"]] != row["email"] for row in reviewed if not row["skip_reason"]):
        raise serializers.ValidationError({"members": "MEMBER_DELETION_EMAIL_REQUIRED"})
    deleted = []
    skipped = [{"id": row["id"], "reason": row["skip_reason"]} for row in reviewed if row["skip_reason"]]
    from agentcore_metering.adapters.django.models import LLMConfig

    try:
        for member, review in zip(members, reviewed):
            if review["skip_reason"]:
                continue
            user_id = member.user_id
            Assistant.objects.filter(tenant=tenant, owner_user_id=user_id).update(
                owner_user=request.user, updated_at=timezone.now()
            )
            SharedQA.objects.filter(published_by_id=user_id).delete()
            LLMConfig.objects.filter(user_id=user_id, ownership__tenant=tenant).update(user=None)
            delete_sessions(Session.objects.filter(user_id=user_id, tenant=tenant))
            get_user_model().objects.filter(pk=user_id).delete()
            record_audit(
                action="member.delete",
                request=request,
                tenant=tenant,
                actor=request.user,
                resource_type="member",
                resource_id=str(user_id),
                metadata={"impact": review["impact"]},
            )
            deleted.append(user_id)
    except ProtectedError:
        raise MemberSelectionChanged("MEMBER_DELETION_DEPENDENCIES_CHANGED") from None
    return {
        "deleted": len(deleted),
        "skipped": len(skipped),
        "deleted_ids": deleted,
        "skipped_items": skipped,
    }
