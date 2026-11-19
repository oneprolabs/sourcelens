"""Platform-level tenant overview and delegation endpoints."""

from datetime import timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.audit import record_audit
from accounts.models import Tenant, TenantDelegation
from accounts.permissions import HasPlatformSuperAdmin


def _tenant_payload(tenant):
    """Serialize one tenant for the platform overview."""
    return {
        "uuid": str(tenant.uuid),
        "name": tenant.name,
        "slug": tenant.slug,
        "status": tenant.status,
        "created_at": tenant.created_at.isoformat(),
        "member_count": getattr(tenant, "member_count", 0),
        "assistant_count": getattr(tenant, "assistant_count", 0),
        "datasource_count": getattr(tenant, "datasource_count", 0),
        "run_count_7d": getattr(tenant, "run_count_7d", 0),
        "last_active_at": (tenant.last_active_at.isoformat() if getattr(tenant, "last_active_at", None) else None),
    }


class PlatformTenantListView(APIView):
    """List tenants with aggregate usage counts."""

    permission_classes = [HasPlatformSuperAdmin]

    def get(self, request):
        """Return tenant summaries without member or conversation content."""
        now = timezone.now()
        cutoff = now - timedelta(days=7)
        queryset = (
            Tenant.objects.all()
            .annotate(
                member_count=Count("memberships", distinct=True),
                assistant_count=Count("assistants", distinct=True),
                datasource_count=Count("datasources", distinct=True),
                run_count_7d=Count(
                    "runs",
                    filter=Q(runs__created_at__gte=cutoff, runs__created_at__lte=now, runs__source="user"),
                    distinct=True,
                ),
                last_active_at=Max("runs__created_at", filter=Q(runs__source="user", runs__created_at__lte=now)),
            )
            .order_by("name", "uuid")
        )
        status_value = str(request.query_params.get("status") or "").strip()
        if status_value in Tenant.Status.values:
            queryset = queryset.filter(status=status_value)
        search = str(request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(slug__icontains=search))
        return Response({"results": [_tenant_payload(tenant) for tenant in queryset]})


class PlatformTenantDelegationView(APIView):
    """Start a time-bounded delegation into one tenant."""

    permission_classes = [HasPlatformSuperAdmin]

    def post(self, request, tenant_uuid):
        """Create a delegation session and return its id."""
        tenant = Tenant.objects.filter(uuid=tenant_uuid).first()
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            minutes = int(request.data.get("expires_in_minutes") or 60)
        except (TypeError, ValueError):
            minutes = 60
        minutes = min(max(minutes, 5), 480)
        reason = str(request.data.get("reason") or "").strip()[:2000]
        delegation = TenantDelegation.objects.create(
            platform_user=request.user,
            tenant=tenant,
            reason=reason,
            mode=TenantDelegation.Mode.OPERATIONAL,
            expires_at=timezone.now() + timedelta(minutes=minutes),
        )
        record_audit(
            action="delegation.start",
            request=request,
            tenant=tenant,
            actor=request.user,
            delegation=delegation,
            resource_type="tenant",
            resource_id=str(tenant.uuid),
            metadata={
                "reason": reason,
                "mode": delegation.mode,
                "expires_at": delegation.expires_at.isoformat(),
            },
        )
        return Response(
            {
                "delegation_id": str(delegation.uuid),
                "tenant": _tenant_payload(tenant),
                "reason": delegation.reason,
                "expires_at": delegation.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class PlatformDelegationEndView(APIView):
    """End an active delegation session."""

    permission_classes = [HasPlatformSuperAdmin]

    def post(self, request, delegation_uuid):
        """Mark the delegation ended by the current platform user."""
        delegation = TenantDelegation.objects.filter(
            uuid=delegation_uuid,
            platform_user=request.user,
            status=TenantDelegation.Status.ACTIVE,
        ).first()
        if delegation is None:
            return Response(
                {"detail": "DELEGATION_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        delegation.status = TenantDelegation.Status.ENDED
        delegation.ended_at = timezone.now()
        delegation.ended_by = request.user
        delegation.save(update_fields=["status", "ended_at", "ended_by"])
        record_audit(
            action="delegation.end",
            request=request,
            tenant=delegation.tenant,
            actor=request.user,
            delegation=delegation,
            resource_type="tenant",
            resource_id=str(delegation.tenant_id),
        )
        return Response({"detail": "DELEGATION_ENDED"})
