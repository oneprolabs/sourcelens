"""Tenant-scoped user group management."""

from django.contrib.auth.models import Group
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import TenantGroup, TenantMembership
from accounts.services.group_management import (
    CanManageTenantGroups,
    GroupDeletionInput,
    current_group_tenant,
    delete_groups,
    group_evidence,
    group_queryset,
)


class GroupWriteInput(serializers.Serializer):
    """Bound user-provided group metadata before changing the backing group."""

    name = serializers.CharField(max_length=100)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate_name(self, value):
        """Normalize whitespace before checking the tenant-local unique name."""
        return " ".join(value.split())


class GroupMemberInput(serializers.Serializer):
    """Reject malformed member IDs before querying membership."""

    user_id = serializers.IntegerField(min_value=1)


def _group_payload(group):
    """Serialize one tenant group with usage counts."""
    evidence = group_evidence(group)
    return {
        "uuid": str(group.uuid),
        "id": group.auth_group_id,
        "name": group.name,
        "description": group.description,
        "member_count": evidence["member_count"],
        "assistant_count": evidence["assistant_count"],
        "version": evidence["version"],
        "created_at": group.created_at.isoformat(),
    }


def _unique_auth_group_name(name, tenant):
    """Return a globally unique backing auth group name."""
    candidates = [name, f"{name} ({tenant.pk})"]
    for candidate in candidates:
        if not Group.objects.filter(name=candidate).exists():
            return candidate
    base = f"{name} ({tenant.pk})"
    index = 2
    while Group.objects.filter(name=f"{base} {index}").exists():
        index += 1
    return f"{base} {index}"


class TenantGroupListView(APIView):
    """List and create tenant-owned groups."""

    permission_classes = [CanManageTenantGroups]

    def get(self, request):
        """Return groups with member and assistant usage counts."""
        tenant = current_group_tenant(request)
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        queryset = group_queryset(tenant)
        search = str(request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)
        return Response({"results": [_group_payload(group) for group in queryset]})

    @transaction.atomic
    def post(self, request):
        """Create a tenant group and its backing auth group."""
        tenant = current_group_tenant(request, lock=True)
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = GroupWriteInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"]
        if not name:
            return Response(
                {"name": "GROUP_NAME_REQUIRED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if TenantGroup.objects.filter(tenant=tenant, name=name).exists():
            return Response(
                {"name": "GROUP_NAME_TAKEN"},
                status=status.HTTP_409_CONFLICT,
            )
        description = serializer.validated_data.get("description", "")
        with transaction.atomic():
            auth_group = Group.objects.create(name=_unique_auth_group_name(name, tenant))
            group = TenantGroup.objects.create(
                tenant=tenant,
                auth_group=auth_group,
                name=name,
                description=description,
                created_by=request.user,
            )
        return Response(
            _group_payload(group),
            status=status.HTTP_201_CREATED,
        )


class TenantGroupDetailView(APIView):
    """Read, rename, or delete one tenant group."""

    permission_classes = [CanManageTenantGroups]

    def _get_group(self, request, group_uuid):
        tenant = current_group_tenant(request)
        if tenant is None:
            return None
        return group_queryset(tenant).filter(tenant=tenant, uuid=group_uuid).first()

    def get(self, request, group_uuid):
        """Return group metadata and its tenant members."""
        group = self._get_group(request, group_uuid)
        if group is None:
            return Response(
                {"detail": "GROUP_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        members = (
            group.auth_group.user_set.filter(membership__tenant=group.tenant)
            .select_related("membership", "profile")
            .order_by("username", "id")
        )
        payload = _group_payload(group)
        payload["members"] = [
            {
                "id": user.pk,
                "username": user.username or user.email or str(user.pk),
                "email": user.email or "",
                "display_name": (
                    f"{user.first_name or ''} {user.last_name or ''}".strip()
                    or user.username
                    or user.email
                    or str(user.pk)
                ),
                "role": user.membership.role,
                "status": user.membership.status,
            }
            for user in members
        ]
        return Response(payload)

    @transaction.atomic
    def patch(self, request, group_uuid):
        """Rename or describe one tenant group."""
        current_group_tenant(request, lock=True)
        group = self._get_group(request, group_uuid)
        if group is None:
            return Response(
                {"detail": "GROUP_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = GroupWriteInput(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data.get("name")
        description = serializer.validated_data.get("description")
        if name is not None:
            normalized = " ".join(str(name).split())
            if not normalized:
                return Response(
                    {"name": "GROUP_NAME_REQUIRED"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if TenantGroup.objects.filter(tenant=group.tenant, name=normalized).exclude(pk=group.pk).exists():
                return Response(
                    {"name": "GROUP_NAME_TAKEN"},
                    status=status.HTTP_409_CONFLICT,
                )
            group.name = normalized
        if description is not None:
            group.description = str(description).strip()
        group.save()
        return Response(_group_payload(group))

    def delete(self, request, group_uuid):
        """Require the same reviewed proof used by batch deletion."""
        data = {
            "groups": [{"uuid": group_uuid, "version": request.data.get("version")}],
            "preview": False,
            "confirmation": request.data.get("confirmation"),
        }
        serializer = GroupDeletionInput(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(delete_groups(request, serializer.validated_data))


class TenantGroupDeletionView(APIView):
    """Preview or apply a bounded batch of tenant group deletions."""

    permission_classes = [CanManageTenantGroups]

    def post(self, request):
        """Use a signed impact review for single and multiple groups."""
        serializer = GroupDeletionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(delete_groups(request, serializer.validated_data))


class TenantGroupMemberView(APIView):
    """Add or remove one tenant member from a group."""

    permission_classes = [CanManageTenantGroups]

    def _get_group(self, request, group_uuid):
        tenant = current_group_tenant(request, lock=True)
        if tenant is None:
            return None
        return (
            TenantGroup.objects.filter(
                tenant=tenant,
                uuid=group_uuid,
            )
            .select_related("auth_group")
            .first()
        )

    @transaction.atomic
    def post(self, request, group_uuid):
        """Add a tenant member to the group."""
        group = self._get_group(request, group_uuid)
        if group is None:
            return Response(
                {"detail": "GROUP_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = GroupMemberInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data["user_id"]
        membership = (
            TenantMembership.objects.filter(
                tenant=group.tenant,
                user_id=user_id,
                status=TenantMembership.Status.ACTIVE,
                user__is_active=True,
            )
            .select_related("user")
            .first()
        )
        if membership is None:
            return Response(
                {"user_id": "MEMBER_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        group.auth_group.user_set.add(membership.user)
        return Response(_group_payload(group))

    @transaction.atomic
    def delete(self, request, group_uuid, user_id):
        """Remove one member from the group."""
        group = self._get_group(request, group_uuid)
        if group is None:
            return Response(
                {"detail": "GROUP_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not TenantMembership.objects.filter(tenant=group.tenant, user_id=user_id).exists():
            return Response({"detail": "MEMBER_NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)
        group.auth_group.user_set.remove(user_id)
        return Response(_group_payload(group))
