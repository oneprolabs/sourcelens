"""Read-only tenant overview with explicit tenant-admin authorization."""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import get_user_membership
from accounts.delegation import is_delegated_request
from accounts.permissions import HasPlatformSuperAdmin
from accounts.services.tenant_overview import tenant_overview


class TenantOverviewView(APIView):
    """Read the current tenant, requiring delegation for platform operators."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return bounded summaries for the authenticated membership only."""

        delegated = is_delegated_request()
        if HasPlatformSuperAdmin().has_permission(request, self) and not delegated:
            raise PermissionDenied("Tenant delegation is required.")
        membership = get_user_membership(request.user)
        if not (
            membership
            and membership.role == "tenant_admin"
            and membership.status == "active"
            and membership.tenant.status == "active"
        ):
            raise PermissionDenied("An active tenant administrator is required.")
        return Response({**tenant_overview(membership.tenant), "delegated": delegated})
