"""Assistant CRUD and public assistant metadata views."""

from django.db import transaction
from django.db.models import Prefetch
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import get_user_tenant
from accounts.authentication import tenant_api_key_selected_assistants
from accounts.delegation import is_delegated_request
from accounts.permissions import HasRequiredFeature

from core.paginations import APIPagination
from lens.assistant_deletion import BulkDeletionSerializer, bulk_delete
from lens.assistant_management import (
    AccessSubjectQuerySerializer,
    BulkEnablementSerializer,
    BulkVisibilitySerializer,
    CanManageAssistantConfiguration,
    access_subjects,
    assistant_configuration_scope,
    bulk_enablement,
    bulk_visibility,
    can_read_assistant_configuration,
    management_checks,
    management_tenant,
)
from lens.model_replacement import ModelReplacementSerializer, replace_assistant_models
from lens.models import (
    Assistant,
    AssistantDataSourceBinding,
    DataSource,
    DataSourceItem,
    user_sees_all_assistants,
)
from lens.serializers import AssistantListSerializer, AssistantSerializer, AssistantUsageSerializer
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

    def list(self, request, *args, **kwargs):
        """Add live management checks only for the current tenant administrator."""

        tenant = None
        if request.query_params.get("managed", "").lower() == "true":
            tenant = management_tenant(request)
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = list(page if page is not None else queryset)
        context = self.get_serializer_context()
        context["collaboration_member_uuids"] = {
            str(member.uuid)
            for row in rows
            if row.mode_handler.supports_members
            for member in row.collaboration_members.all()
        }
        if tenant is not None:
            context["management_checks"] = management_checks(rows, tenant, include_usage=True)
        management = self.get_serializer(context=context)
        usage = AssistantUsageSerializer(context=context)
        data = [
            (management if can_read_assistant_configuration(request, row) else usage).to_representation(row)
            for row in rows
        ]
        return self.get_paginated_response(data) if page is not None else Response(data)

    def retrieve(self, request, *args, **kwargs):
        """Return configuration only to administrators of the owning tenant."""

        assistant = self.get_object()
        if can_read_assistant_configuration(request, assistant):
            serializer = self.get_serializer(assistant)
        else:
            serializer = AssistantUsageSerializer(assistant, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="bulk-visibility")
    def bulk_visibility(self, request):
        """Change visibility without broadening another tenant's permissions."""

        tenant = management_tenant(request, write=True)
        serializer = BulkVisibilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(bulk_visibility(request, tenant, **serializer.validated_data))

    @action(detail=False, methods=["post"], url_path="bulk-enablement")
    def bulk_enablement(self, request):
        """Pause or enable a versioned tenant assistant batch."""

        tenant = management_tenant(request, write=True)
        serializer = BulkEnablementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(bulk_enablement(request, tenant, **serializer.validated_data))

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """Preview permanent deletion before accepting exact slug confirmations."""

        serializer = BulkDeletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = management_tenant(request, write=not serializer.validated_data["preview"])
        return Response(bulk_delete(request, tenant, **serializer.validated_data))

    @action(detail=False, methods=["get"], url_path="access-subjects")
    def access_subjects(self, request):
        """Select active users and groups without disclosing foreign identities."""

        tenant = management_tenant(request)
        if is_delegated_request():
            raise PermissionDenied("Assistant member grants are managed by the tenant.")
        serializer = AccessSubjectQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(access_subjects(tenant, **serializer.validated_data))

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
        datasource_queryset = DataSource.objects.filter(tenant_id=assistant.tenant_id)
        try:
            datasource = datasource_queryset.get(
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

        if self.action == "datasources":
            return [permissions.IsAuthenticated(), HasRequiredFeature(), CanManageAssistantConfiguration()]
        if self.action in (
            "create",
            "update",
            "partial_update",
            "archive",
            "restore",
        ):
            return [permissions.IsAuthenticated(), HasRequiredFeature(), CanManageAssistantConfiguration()]
        return super().get_permissions()

    @action(detail=False, methods=["post"], url_path="replace-model")
    def replace_model(self, request):
        """Preview or atomically replace models for an explicit assistant batch."""

        if not assistant_configuration_scope(request)[0]:
            raise PermissionDenied("Assistant administration is required.")
        serializer = ModelReplacementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if get_user_tenant(request.user) is not None or is_delegated_request():
            management_tenant(request, write=not serializer.validated_data["preview"])
        return Response(replace_assistant_models(user=request.user, **serializer.validated_data))

    def get_queryset(self):
        """Scope assistants to those the caller may see."""

        if self.action == "list":
            queryset = Assistant.objects.select_related("lensnode").prefetch_related(
                "skill_bindings__skill",
                "mcp_bindings__mcp",
                "datasource_bindings__datasource",
                "datasource_bindings__item",
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
        tenant = get_user_tenant(self.request.user)
        managed = self.request.query_params.get("managed", "").lower() == "true"
        if tenant is not None and (
            managed or self.action == "datasources" or self.request.method not in permissions.SAFE_METHODS
        ):
            queryset = queryset.filter(tenant=tenant)
        if self.action == "datasources" or self.request.method not in permissions.SAFE_METHODS:
            allowed, tenant_id = assistant_configuration_scope(self.request)
            queryset = queryset.filter(tenant_id=tenant_id) if allowed else queryset.none()
        selected_assistants = tenant_api_key_selected_assistants(
            self.request
        )
        if selected_assistants is not None:
            queryset = queryset.filter(uuid__in=selected_assistants)
        if self.action == "restore":
            return queryset.filter(status=Assistant.Status.ARCHIVED)
        if self.action == "archive":
            return queryset.filter(status=Assistant.Status.ACTIVE)

        archived = self.request.query_params.get("archived", "").lower()
        if archived == "all" and managed and self.action == "list":
            return queryset
        if archived == "true":
            if not user_sees_all_assistants(self.request.user):
                return queryset.none()
            return queryset.filter(status=Assistant.Status.ARCHIVED)
        if (
            self.action == "list"
            and not managed
            and self.request.query_params.get("include_disabled") != "true"
        ):
            queryset = queryset.filter(is_enabled=True)
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
        """Return minimal metadata only for an anonymously visible assistant."""

        assistant = Assistant.objects.visible_to(None).filter(
            slug=slug,
            status=Assistant.Status.ACTIVE,
            is_enabled=True,
            is_system=False,
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
            }
        )
