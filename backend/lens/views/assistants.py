"""Assistant CRUD and public assistant metadata views."""

from django.db import transaction
from django.db.models import Prefetch
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasRequiredFeature

from core.paginations import APIPagination
from lens.models import (
    Assistant,
    AssistantDataSourceBinding,
    DataSource,
    DataSourceItem,
    user_sees_all_assistants,
)
from lens.serializers import AssistantListSerializer, AssistantSerializer
from .base import BaseAuthenticatedViewSet


class AssistantPagination(APIPagination):
    """Keep Assistant collection responses bounded and predictable."""

    max_page_size = 100


class AssistantViewSet(BaseAuthenticatedViewSet):
    """Manage assistants and their lifecycle.

    Anyone authenticated may list/retrieve the assistants visible to them;
    creating, editing, archiving, and restoring assistants (including
    visibility and access grants) requires the admin console feature.
    """

    http_method_names = ["get", "post", "put", "patch", "head", "options"]
    required_feature = "admin_console"
    queryset = Assistant.objects.select_related("lensnode").prefetch_related(
        "skill_bindings",
        "mcp_bindings",
        "plugin_bindings__connection",
        "access_grants__group",
        "access_grants__user",
        Prefetch(
            "collaboration_members",
            queryset=Assistant.objects.order_by("name", "uuid"),
        ),
    )
    serializer_class = AssistantSerializer
    pagination_class = AssistantPagination

    @action(
        detail=True,
        methods=["get", "post", "patch", "delete"],
        url_path="datasources",
    )
    def datasources(self, request, uuid=None):
        """List or bind datasource resources to an assistant."""
        assistant = self.get_object()
        if request.method == "GET":
            return Response(AssistantSerializer(assistant).data["datasource_bindings"])
        binding_uuid = request.data.get("binding_uuid")
        if request.method in ("PATCH", "DELETE"):
            try:
                binding = assistant.datasource_bindings.get(uuid=binding_uuid)
            except (TypeError, AssistantDataSourceBinding.DoesNotExist):
                return Response({"binding_uuid": "Binding not found"}, status=404)
            if request.method == "DELETE":
                binding.delete()
                return Response(status=204)
            mount_name = request.data.get("mount_name")
            if mount_name is not None:
                if not mount_name.replace("_", "").isalnum():
                    return Response({"mount_name": "Invalid mount name"}, status=400)
                binding.mount_name = mount_name
            if "required" in request.data:
                binding.required = bool(request.data["required"])
            binding.save(update_fields=["mount_name", "required", "updated_at"])
            return Response(AssistantSerializer(assistant).data["datasource_bindings"])
        try:
            datasource = DataSource.objects.get(
                uuid=request.data["datasource_uuid"]
            )
        except (KeyError, DataSource.DoesNotExist):
            return Response({"datasource_uuid": "Datasource not found"}, status=404)
        item_uuid = request.data.get("item_uuid")
        item = None
        if item_uuid:
            try:
                item = DataSourceItem.objects.get(
                    uuid=item_uuid, datasource=datasource
                )
            except DataSourceItem.DoesNotExist:
                return Response({"item_uuid": "Item not found"}, status=404)
        mount_name = str(request.data.get("mount_name") or "source")
        if not mount_name.replace("_", "").isalnum():
            return Response({"mount_name": "Invalid mount name"}, status=400)
        binding = AssistantDataSourceBinding.objects.create(
            assistant=assistant, datasource=datasource, item=item,
            mount_name=mount_name, required=bool(request.data.get("required", True)),
        )
        return Response(AssistantSerializer(assistant).data["datasource_bindings"], status=201)

    def get_serializer_class(self):
        """Use the compact contract for the collection endpoint."""

        if self.action == "list":
            return AssistantListSerializer
        return super().get_serializer_class()

    def get_permissions(self):
        """Require the admin console feature for write actions."""

        if self.action == "datasources" and self.request.method != "GET":
            return [permissions.IsAuthenticated(), HasRequiredFeature()]
        if self.action in (
            "create",
            "update",
            "partial_update",
            "archive",
            "restore",
        ):
            return [permissions.IsAuthenticated(), HasRequiredFeature()]
        return super().get_permissions()

    def get_queryset(self):
        """Scope assistants to those the caller may see."""

        if self.action == "list":
            queryset = Assistant.objects.select_related("lensnode").prefetch_related(
                "skill_bindings",
                "mcp_bindings",
                "plugin_bindings__connection",
                Prefetch(
                    "collaboration_members",
                    queryset=Assistant.objects.order_by("name", "uuid"),
                ),
            )
        else:
            queryset = super().get_queryset()
        queryset = queryset.visible_to(self.request.user).filter(
            is_system=False
        )
        if self.action == "restore":
            return queryset.filter(status=Assistant.Status.ARCHIVED)
        if self.action == "archive":
            return queryset.filter(status=Assistant.Status.ACTIVE)

        archived = self.request.query_params.get("archived", "").lower()
        if archived == "true":
            if not user_sees_all_assistants(self.request.user):
                return queryset.none()
            return queryset.filter(status=Assistant.Status.ARCHIVED)
        return queryset.filter(status=Assistant.Status.ACTIVE)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def archive(self, request, *args, **kwargs):
        """Archive an active assistant without deleting its data."""

        assistant = self.get_object()
        assistant = Assistant.objects.select_for_update().get(pk=assistant.pk)
        assistant.status = Assistant.Status.ARCHIVED
        assistant.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(assistant).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def restore(self, request, *args, **kwargs):
        """Restore an archived assistant to active use."""

        assistant = self.get_object()
        assistant = Assistant.objects.select_for_update().get(pk=assistant.pk)
        assistant.status = Assistant.Status.ACTIVE
        assistant.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(assistant).data)


class PublicAssistantView(APIView):
    """Public read-only assistant metadata for the shared chat page."""

    authentication_classes = []
    permission_classes = []

    def get(self, request, slug):
        """Return minimal metadata for an active assistant by slug."""

        assistant = Assistant.objects.filter(
            slug=slug,
            status=Assistant.Status.ACTIVE,
            visibility=Assistant.Visibility.PUBLIC,
        ).first()
        if assistant is None:
            return Response(
                {"detail": "Assistant not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                "name": assistant.name,
                "description": assistant.description,
                "slug": assistant.slug,
                "status": assistant.status,
            }
        )
