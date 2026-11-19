"""Tenant-scoped encrypted environment variable sets."""

import logging

from django.db.models.deletion import ProtectedError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.access import get_user_tenant
from accounts.permissions import HasTenantOrLegacyAdminAccess
from lens.models import EnvironmentVariableSet
from lens.serializers import EnvironmentVariableSetSerializer
from .base import BaseAuthenticatedViewSet


LOGGER = logging.getLogger(__name__)


class EnvironmentVariableSetViewSet(BaseAuthenticatedViewSet):
    """Tenant-admin CRUD for encrypted Skill and MCP environment values."""

    permission_classes = [HasTenantOrLegacyAdminAccess]
    queryset = EnvironmentVariableSet.objects.prefetch_related(
        "skill_bindings__skill",
        "skill_bindings__assistant",
        "mcp_bindings__mcp",
        "mcp_bindings__assistant",
    )
    serializer_class = EnvironmentVariableSetSerializer

    def get_queryset(self):
        """Limit variable sets to the caller's tenant."""

        return super().get_queryset().filter(tenant=get_user_tenant(self.request.user))

    def destroy(self, request, *args, **kwargs):
        """Reject deleting a set that is still bound to an Assistant."""

        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {"detail": "ENVIRONMENT_VARIABLE_SET_IN_USE"},
                status=status.HTTP_409_CONFLICT,
            )

    @action(detail=True, methods=["post"], url_path="reveal")
    def reveal(self, request, uuid=None):
        """Reveal values only during an explicit tenant-admin edit action."""

        variable_set = self.get_object()
        LOGGER.info(
            "environment_variable_set_reveal actor_id=%s set_uuid=%s "
            "source_ip=%s request_id=%s result=success",
            request.user.pk,
            variable_set.uuid,
            request.META.get("REMOTE_ADDR", ""),
            request.headers.get("X-Request-ID", ""),
        )
        response = Response(
            {
                "values": [
                    {"key": key, "value": value}
                    for key, value in variable_set.get_values().items()
                ]
            }
        )
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        return response
