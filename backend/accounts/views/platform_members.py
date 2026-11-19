"""Read-only platform member directory across tenants."""

from django.db.models import Count, F, Q
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import TenantMembership
from accounts.permissions import HasPlatformSuperAdmin


def _member_payload(membership):
    """Serialize one membership as a platform-level ownership summary."""
    user = membership.user
    display_name = user.get_full_name().strip() or user.username
    return {
        "id": user.pk,
        "username": user.username,
        "name": display_name,
        "email": user.email,
        "tenant": {
            "uuid": str(membership.tenant.uuid),
            "name": membership.tenant.name,
            "slug": membership.tenant.slug,
        },
        "role": membership.role,
        "status": membership.status,
        "api_key_count": getattr(membership, "api_key_count", 0),
        "last_login": (user.last_login.isoformat() if user.last_login else None),
    }


class PlatformMemberListView(APIView):
    """List memberships across tenants without exposing tenant internals."""

    permission_classes = [HasPlatformSuperAdmin]

    def get(self, request):
        """Return a filtered, paginated page of member ownership rows."""
        queryset = (
            TenantMembership.objects.select_related("user", "tenant")
            .annotate(
                api_key_count=Count(
                    "user__created_api_keys",
                    filter=Q(
                        user__created_api_keys__tenant=F("tenant"),
                    ),
                    distinct=True,
                )
            )
            .order_by("tenant__name", "user__username")
        )
        tenant_uuid = str(request.query_params.get("tenant") or "").strip()
        if tenant_uuid:
            queryset = queryset.filter(tenant__uuid=tenant_uuid)
        role = str(request.query_params.get("role") or "").strip()
        if role in TenantMembership.Role.values:
            queryset = queryset.filter(role=role)
        search = str(request.query_params.get("search") or "").strip()
        for term in search.split():
            queryset = queryset.filter(
                Q(user__username__icontains=term)
                | Q(user__email__icontains=term)
                | Q(user__first_name__icontains=term)
                | Q(user__last_name__icontains=term)
                | Q(tenant__name__icontains=term)
            )
        counts = queryset.aggregate(
            active=Count("uuid", filter=Q(status="active")),
            invited=Count("uuid", filter=Q(status="invited")),
            disabled=Count("uuid", filter=Q(status="disabled")),
        )
        status_value = str(request.query_params.get("status") or "").strip()
        if status_value in TenantMembership.Status.values:
            queryset = queryset.filter(status=status_value)
        try:
            page = max(int(request.query_params.get("page") or 1), 1)
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get("page_size") or 20)
        except (TypeError, ValueError):
            page_size = 20
        page_size = min(max(page_size, 1), 100)
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "counts": counts,
                "results": [_member_payload(membership) for membership in queryset[start:end]],
            }
        )
