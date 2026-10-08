from django.db.models.deletion import ProtectedError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from lens.environment_variables import secret_environment_names
from lens.models import EnvironmentVariableSet
from lens.serializers import EnvironmentVariableSetSerializer
from .base import BaseAdminViewSet


class EnvironmentVariableSetViewSet(BaseAdminViewSet):
    """Admin-only CRUD for encrypted Skill and MCP environment values."""

    queryset = EnvironmentVariableSet.objects.prefetch_related(
        "skill_bindings__skill",
        "skill_bindings__assistant",
        "mcp_bindings__mcp",
        "mcp_bindings__assistant",
    )
    serializer_class = EnvironmentVariableSetSerializer

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
        """Return values while masking secret environment variables."""

        variable_set = self.get_object()
        values = variable_set.get_values()
        secret_names = secret_environment_names(variable_set)
        return Response(
            {
                "values": [
                    {
                        "key": key,
                        "value": "********" if key in secret_names else value,
                        "secret": key in secret_names,
                    }
                    for key, value in values.items()
                ]
            }
        )
