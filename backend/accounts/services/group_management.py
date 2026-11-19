"""Review tenant group changes against current membership and assistant grants."""

import hashlib
import json

from django.contrib.auth.models import Group
from django.core import signing
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied

from accounts.audit import record_audit
from accounts.models import Tenant, TenantGroup, TenantMembership
from accounts.permissions import HasPlatformSuperAdmin
from accounts.services.member_management import CanManageTenantMembers, MemberSelectionChanged, require_member_authority

SALT = "accounts.group-deletion.v1"


class CanManageTenantGroups(CanManageTenantMembers):
    """Keep membership administration outside platform identities and delegation."""

    def has_permission(self, request, view):
        """A staff flag or platform role cannot replace tenant group authority."""
        return super().has_permission(request, view) and not HasPlatformSuperAdmin().has_permission(request, view)


def group_queryset(tenant):
    """Load ownership evidence once for each group without reading conversation content."""
    return (
        TenantGroup.objects.filter(tenant=tenant)
        .select_related("auth_group")
        .prefetch_related("auth_group__user_set__membership", "auth_group__assistant_grants__assistant")
        .order_by("name", "uuid")
    )


def group_evidence(group):
    """Keep foreign associations out of projections while binding them to the version."""
    members, assistants, foreign = [], {}, False
    identities = []
    for user in group.auth_group.user_set.all():
        membership = getattr(user, "membership", None)
        tenant_id = str(membership.tenant_id) if membership else None
        identities.append((user.pk, tenant_id))
        if membership and membership.tenant_id == group.tenant_id:
            members.append(user.pk)
        else:
            foreign = True
    grants = []
    for grant in group.auth_group.assistant_grants.all():
        assistant = grant.assistant
        grants.append((grant.pk, str(assistant.uuid), str(assistant.tenant_id)))
        if assistant.tenant_id == group.tenant_id:
            assistants[str(assistant.uuid)] = {"uuid": str(assistant.uuid), "name": assistant.name}
        else:
            foreign = True
    version = hashlib.sha256(
        json.dumps(
            [
                str(group.pk),
                str(group.tenant_id),
                group.name,
                group.description,
                str(group.updated_at),
                sorted(identities),
                sorted(grants),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "version": version,
        "member_count": len(members),
        "assistant_count": len(assistants),
        "assistants": sorted(assistants.values(), key=lambda row: (row["name"], row["uuid"])),
        "foreign": foreign,
    }


def current_group_tenant(request, *, lock=False):
    """Resolve current membership instead of a cached or delegated tenant."""
    tenant_id = TenantMembership.objects.filter(user_id=request.user.pk).values_list("tenant_id", flat=True).first()
    queryset = Tenant.objects.select_for_update(no_key=True) if lock else Tenant.objects.all()
    tenant = queryset.filter(pk=tenant_id).first()
    if tenant is None:
        raise PermissionDenied("MEMBER_ADMIN_AUTHORITY_CHANGED")
    if not CanManageTenantGroups().has_permission(request, None):
        raise PermissionDenied("MEMBER_ADMIN_AUTHORITY_CHANGED")
    require_member_authority(request, tenant)
    return tenant


class GroupSelection(serializers.Serializer):
    """Bind group actions to the exact metadata and associations reviewed."""

    uuid = serializers.UUIDField()
    version = serializers.CharField(max_length=64)


class GroupDeletionInput(serializers.Serializer):
    """Bound batch deletion and require an explicit server-generated review."""

    groups = GroupSelection(many=True, allow_empty=False, max_length=100)
    preview = serializers.BooleanField(required=True)
    confirmation = serializers.CharField(required=False, max_length=100000)

    def validate(self, attrs):
        """Reject duplicates and an unreviewed destructive request."""
        if len({row["uuid"] for row in attrs["groups"]}) != len(attrs["groups"]):
            raise serializers.ValidationError({"groups": "DUPLICATE_GROUPS"})
        if not attrs["preview"] and not attrs.get("confirmation"):
            raise serializers.ValidationError({"confirmation": "GROUP_REVIEW_REQUIRED"})
        return attrs


@transaction.atomic
def delete_groups(request, data):
    """Delete only a reviewed local selection, keeping users and assistants intact."""
    tenant = current_group_tenant(request, lock=True)
    versions = {row["uuid"]: row["version"] for row in data["groups"]}
    rows = list(TenantGroup.objects.select_for_update().filter(tenant=tenant, pk__in=versions).order_by("uuid"))
    if len(rows) != len(versions):
        raise NotFound("GROUP_NOT_FOUND")
    list(Group.objects.select_for_update().filter(pk__in=[row.auth_group_id for row in rows]).order_by("pk"))
    rows = list(group_queryset(tenant).filter(pk__in=versions))
    reviewed = []
    for row in rows:
        evidence = group_evidence(row)
        if evidence["version"] != versions[row.pk]:
            raise MemberSelectionChanged("GROUP_SELECTION_CHANGED")
        reviewed.append(
            {
                "uuid": str(row.pk),
                "name": row.name,
                "version": evidence["version"],
                "member_count": evidence["member_count"],
                "assistant_count": evidence["assistant_count"],
                "assistants": evidence["assistants"],
                "skip_reason": "foreign_associations" if evidence["foreign"] else "",
            }
        )
    proof = {"actor": request.user.pk, "tenant": str(tenant.pk), "groups": reviewed}
    if data["preview"]:
        return {"groups": reviewed, "confirmation": signing.dumps(proof, salt=SALT, compress=True)}
    try:
        previous = signing.loads(data["confirmation"], salt=SALT, max_age=600)
    except signing.BadSignature:
        raise MemberSelectionChanged("GROUP_REVIEW_EXPIRED") from None
    if previous != proof:
        raise MemberSelectionChanged("GROUP_SELECTION_CHANGED")
    deleted, skipped = [], []
    for row, review in zip(rows, reviewed):
        if review["skip_reason"]:
            skipped.append({"uuid": str(row.pk), "reason": review["skip_reason"]})
            continue
        identity = str(row.pk)
        Group.objects.filter(pk=row.auth_group_id).delete()
        record_audit(
            action="group.delete",
            request=request,
            tenant=tenant,
            actor=request.user,
            resource_type="group",
            resource_id=identity,
            metadata={"member_count": review["member_count"], "assistant_count": review["assistant_count"]},
        )
        deleted.append(identity)
    return {"deleted": len(deleted), "skipped": len(skipped), "deleted_uuids": deleted, "skipped_items": skipped}
