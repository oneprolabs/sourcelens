"""Current-tenant profile and logo endpoints."""

import uuid

from django.contrib.auth.models import Group
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Prefetch
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import get_user_tenant
from accounts.audit import record_audit
from accounts.permissions import HasTenantAdminAccess
from accounts.serializers import CurrentTenantSerializer
from accounts.services.member_management import CanManageTenantMembers, member_version, require_member_authority
from accounts.services.member_password_reset import can_reset_member_password
from accounts.services.password_login import active_password_lock, with_password_state

LOGO_MAX_BYTES = 2 * 1024 * 1024
LOGO_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


class CurrentTenantView(APIView):
    """Read and update the caller's tenant profile."""

    permission_classes = [HasTenantAdminAccess]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        """Return the current tenant profile."""
        tenant = get_user_tenant(request.user)
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(CurrentTenantSerializer(tenant).data)

    def patch(self, request):
        """Update editable tenant fields while keeping ownership read-only."""
        tenant = get_user_tenant(request.user)
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = CurrentTenantSerializer(
            tenant,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        record_audit(
            action="tenant.update",
            request=request,
            tenant=tenant,
            actor=request.user,
            resource_type="tenant",
            resource_id=str(tenant.uuid),
            metadata={"fields": sorted(request.data.keys())},
        )
        return Response(serializer.data)


class TenantLogoView(APIView):
    """Upload a tenant logo into private media storage."""

    permission_classes = [HasTenantAdminAccess]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        """Persist one validated image and update the tenant logo URL."""
        tenant = get_user_tenant(request.user)
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        upload = request.FILES.get("logo")
        if upload is None:
            return Response(
                {"logo": "LOGO_REQUIRED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if upload.size > LOGO_MAX_BYTES:
            return Response(
                {"logo": "LOGO_TOO_LARGE"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        content_type = str(getattr(upload, "content_type", "") or "").lower()
        extension = LOGO_CONTENT_TYPES.get(content_type)
        if extension is None:
            return Response(
                {"logo": "LOGO_UNSUPPORTED_TYPE"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_logo = tenant.logo_url or ""
        path = f"tenant-logos/{tenant.uuid}/" f"{uuid.uuid4().hex}.{extension}"
        saved_path = default_storage.save(path, upload)
        tenant.logo_url = default_storage.url(saved_path)
        tenant.save(update_fields=["logo_url", "updated_at"])

        prefix = f"tenant-logos/{tenant.uuid}/"
        if old_logo and prefix in old_logo and old_logo != tenant.logo_url:
            relative = old_logo.split(prefix, 1)[-1]
            default_storage.delete(f"{prefix}{relative}")

        return Response(
            CurrentTenantSerializer(tenant).data,
            status=status.HTTP_200_OK,
        )


class TenantMemberListView(APIView):
    """List tenant members with built-in roles and 30-day activity."""

    permission_classes = [CanManageTenantMembers]

    def get(self, request):
        """Return a filtered, paginated member list."""
        from accounts.models import TenantMembership

        current = (
            TenantMembership.objects.select_related("tenant")
            .filter(
                user_id=request.user.pk,
                user__is_active=True,
                role="tenant_admin",
                status="active",
                tenant__status="active",
            )
            .first()
        )
        tenant = current.tenant if current else None
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from datetime import timedelta

        from django.db.models import Case, Count, F, Q, Value, When
        from django.utils import timezone

        cutoff = timezone.now() - timedelta(days=30)
        queryset = (
            with_password_state(TenantMembership.objects.filter(tenant=tenant))
            .select_related("user", "user__profile")
            .prefetch_related(Prefetch("user__groups", queryset=Group.objects.select_related("tenant_group")))
            .annotate(
                runs_30d=Count(
                    "user__session__run",
                    filter=Q(
                        user__session__tenant=tenant,
                        user__session__run__created_at__gte=cutoff,
                    ),
                    distinct=True,
                )
            )
            .order_by("user__username", "user_id")
        )

        status_counts = dict(
            with_password_state(TenantMembership.objects.filter(tenant=tenant))
            .order_by()
            .annotate(
                display_status=Case(
                    When(
                        status="active",
                        user__is_active=True,
                        password_locked_until__gt=timezone.now(),
                        then=Value("locked"),
                    ),
                    default=F("status"),
                )
            )
            .values_list("display_status")
            .annotate(total=Count("pk"))
            .values_list("display_status", "total")
        )
        status_counts["all"] = sum(status_counts.values())

        search = str(request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(user__username__icontains=search)
                | Q(user__email__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )
        role = str(request.query_params.get("role") or "").strip()
        if role in TenantMembership.Role.values:
            queryset = queryset.filter(role=role)
        status_value = str(request.query_params.get("status") or "").strip()
        if status_value == "locked":
            queryset = queryset.filter(status="active", user__is_active=True, password_locked_until__gt=timezone.now())
        elif status_value == "active":
            queryset = queryset.filter(status="active").filter(
                Q(password_locked_until__isnull=True) | Q(password_locked_until__lte=timezone.now())
            )
        elif status_value in TenantMembership.Status.values:
            queryset = queryset.filter(status=status_value)
        group_id = str(request.query_params.get("group") or "").strip()
        if group_id:
            queryset = queryset.filter(user__groups__id=group_id)

        page = max(_query_int(request, "page", 1), 1)
        page_size = min(max(_query_int(request, "page_size", 20), 1), 100)
        total = queryset.count()
        start = (page - 1) * page_size
        rows = [_member_payload(membership, request.user) for membership in queryset[start : start + page_size]]
        return Response(
            {
                "count": total,
                "status_counts": status_counts,
                "page": page,
                "page_size": page_size,
                "results": rows,
            }
        )


class TenantMemberDetailView(APIView):
    """Update one tenant member's role, status, or groups."""

    permission_classes = [CanManageTenantMembers]

    @transaction.atomic
    def patch(self, request, user_id):
        """Apply one member update with last-admin and self protections."""
        from rest_framework.exceptions import ValidationError

        from accounts.models import Tenant, TenantGroup, TenantMembership

        tenant = get_user_tenant(request.user)
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        Tenant.objects.select_for_update(no_key=True).get(pk=tenant.pk)
        require_member_authority(request, tenant)
        membership = (
            TenantMembership.objects.select_for_update()
            .select_related("user")
            .filter(tenant=tenant, user_id=user_id)
            .first()
        )
        if membership is None:
            return Response(
                {"detail": "MEMBER_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        user = membership.user

        role = request.data.get("role")
        status_value = request.data.get("status")
        group_ids = request.data.get("group_ids")
        disable_api_keys = request.data.get("disable_api_keys", False)
        if type(disable_api_keys) is not bool or (
            disable_api_keys and status_value != TenantMembership.Status.DISABLED
        ):
            raise ValidationError({"disable_api_keys": "INVALID_KEY_DISABLE_OPTION"})

        if role is not None and role not in TenantMembership.Role.values:
            raise ValidationError({"role": "INVALID_ROLE"})
        if status_value is not None and status_value not in TenantMembership.Status.values:
            raise ValidationError({"status": "INVALID_STATUS"})
        if status_value == "invited" and membership.status != "invited":
            raise ValidationError({"status": "INVALID_STATUS_TRANSITION"})
        if status_value == "active" and (
            membership.status == "invited" or (membership.invited_at is not None and membership.joined_at is None)
        ):
            raise ValidationError({"status": "INVITATION_ACCEPTANCE_REQUIRED"})
        valid_group_ids = None
        if group_ids is not None:
            if not isinstance(group_ids, list) or any(type(value) is not int for value in group_ids):
                raise ValidationError({"group_ids": "INVALID_GROUP_IDS"})
            valid_group_ids = list(
                TenantGroup.objects.filter(tenant=tenant, auth_group_id__in=group_ids).values_list(
                    "auth_group_id", flat=True
                )
            )
            if len(valid_group_ids) != len(set(group_ids)):
                raise ValidationError({"group_ids": "INVALID_TENANT_GROUPS"})
        if status_value == TenantMembership.Status.DISABLED and user.pk == request.user.pk:
            raise ValidationError({"status": "CANNOT_DISABLE_SELF"})

        removing_admin = (
            membership.role == TenantMembership.Role.TENANT_ADMIN
            and membership.status == TenantMembership.Status.ACTIVE
            and (
                (role is not None and role != TenantMembership.Role.TENANT_ADMIN)
                or (status_value is not None and status_value != TenantMembership.Status.ACTIVE)
            )
        )
        if removing_admin:
            remaining = TenantMembership.objects.filter(
                tenant=tenant,
                role=TenantMembership.Role.TENANT_ADMIN,
                status=TenantMembership.Status.ACTIVE,
                user__is_active=True,
            ).exclude(pk=membership.pk)
            if not remaining.exists():
                raise ValidationError({"role": "LAST_TENANT_ADMIN_REQUIRED"})

        update_fields = []
        if role is not None and membership.role != role:
            membership.role = role
            update_fields.append("role")
        if status_value is not None and membership.status != status_value:
            membership.status = status_value
            update_fields.append("status")
        if update_fields:
            membership.save(update_fields=[*update_fields, "updated_at"])

        if status_value is not None:
            user.is_active = status_value == TenantMembership.Status.ACTIVE
            user.save(update_fields=["is_active"])

        if group_ids is not None:
            user.groups.set(valid_group_ids)

        disabled_keys = []
        if disable_api_keys:
            from accounts.services.api_key_lifecycle import disable_member_api_keys

            disabled_keys = disable_member_api_keys(request=request, tenant=tenant, user=user)

        record_audit(
            action="member.update",
            request=request,
            tenant=tenant,
            actor=request.user,
            resource_type="member",
            resource_id=str(user.pk),
            metadata={
                "role": membership.role,
                "status": membership.status,
                "group_ids": group_ids,
                "disabled_api_key_ids": disabled_keys,
            },
        )
        return Response(_member_payload(membership, request.user))


def _query_int(request, key, default):
    """Parse one bounded integer query parameter."""
    try:
        return int(request.query_params.get(key, default))
    except (TypeError, ValueError):
        return default


def _member_payload(membership, request_user):
    """Serialize one tenant member without exposing global auth internals."""
    user = membership.user
    profile = getattr(user, "profile", None)
    display_name = (
        f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username or user.email or str(user.pk)
    )
    if membership.invited_at is not None and not (user.first_name or user.last_name):
        display_name = user.email
    until = active_password_lock(membership)
    return {
        "id": user.pk,
        "version": member_version(membership),
        "username": user.username or user.email or str(user.pk),
        "email": user.email or "",
        "display_name": display_name,
        "avatar_url": profile.avatar_url if profile else "",
        "role": membership.role,
        "status": "locked" if until else membership.status,
        "membership_status": membership.status,
        "locked_until": until.isoformat() if until else None,
        "requires_invitation": membership.invited_at is not None and membership.joined_at is None,
        "can_reset_password": can_reset_member_password(membership),
        "groups": sorted(
            [
                {"id": group.pk, "name": group.tenant_group.name if hasattr(group, "tenant_group") else group.name}
                for group in user.groups.all()
                if not hasattr(group, "tenant_group") or group.tenant_group.tenant_id == membership.tenant_id
            ],
            key=lambda group: group["name"],
        ),
        "last_login": (user.last_login.isoformat() if user.last_login else None),
        "date_joined": (user.date_joined.isoformat() if user.date_joined else None),
        "runs_30d": getattr(membership, "runs_30d", 0),
        "is_self": user.pk == request_user.pk,
    }
