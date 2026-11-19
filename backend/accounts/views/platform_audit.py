"""Platform overview and audit log endpoints."""

from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import AuditLog
from accounts.permissions import HasPlatformSuperAdmin
from accounts.services.platform_overview import platform_overview


def _int_param(request, key, default):
    """Parse one integer query parameter."""
    try:
        return int(request.query_params.get(key, default))
    except (TypeError, ValueError):
        return default


def _audit_payload(record):
    """Serialize one audit record without secret-bearing metadata."""
    return {
        "uuid": str(record.uuid),
        "created_at": record.created_at.isoformat(),
        "action": record.action,
        "result": record.result,
        "actor": (record.actor_user.username if record.actor_user_id else record.actor_type),
        "actor_type": record.actor_type,
        "tenant": (
            {
                "uuid": str(record.tenant.uuid),
                "name": record.tenant.name,
                "slug": record.tenant.slug,
            }
            if record.tenant_id
            else None
        ),
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "delegation_id": (str(record.delegation_id) if record.delegation_id else None),
        "ip_address": record.ip_address,
        "request_id": record.request_id,
        "metadata": record.metadata,
    }


class PlatformOverviewView(APIView):
    """Return platform-wide aggregate counters."""

    permission_classes = [HasPlatformSuperAdmin]

    def get(self, request):
        """Return tenant, member, resource, run, and node counters."""
        data = platform_overview()
        data["recent_audit"] = [
            _audit_payload(record)
            for record in AuditLog.objects.select_related("tenant", "actor_user", "delegation").order_by("-created_at")[
                :5
            ]
        ]
        return Response(data)


class PlatformAuditListView(APIView):
    """List platform and tenant audit records."""

    permission_classes = [HasPlatformSuperAdmin]

    def get(self, request):
        """Return a filtered page of audit records."""
        queryset = AuditLog.objects.select_related(
            "tenant",
            "actor_user",
            "delegation",
        ).order_by("-created_at", "-uuid")
        tenant_uuid = str(request.query_params.get("tenant") or "").strip()
        if tenant_uuid:
            queryset = queryset.filter(tenant__uuid=tenant_uuid)
        result = str(request.query_params.get("result") or "").strip()
        if result in AuditLog.Result.values:
            queryset = queryset.filter(result=result)
        action = str(request.query_params.get("action") or "").strip()
        if action:
            queryset = queryset.filter(action__startswith=action)
        search = str(request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(action__icontains=search)
                | Q(actor_user__username__icontains=search)
                | Q(resource_type__icontains=search)
            )
        page = max(_int_param(request, "page", 1), 1)
        page_size = min(max(_int_param(request, "page_size", 20), 1), 100)
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": [_audit_payload(record) for record in queryset[start:end]],
            }
        )
