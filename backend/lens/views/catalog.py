"""Shared ownership and opt-in behavior for Skill and MCP catalog endpoints."""

from django.db.models import BooleanField, Count, Exists, OuterRef, Q, Value
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from accounts.access import get_user_tenant
from accounts.audit import record_audit
from accounts.delegation import get_active_delegation, is_delegated_request
from accounts.permissions import HasPlatformSuperAdmin, HasTenantAdminAccess
from lens.catalog import usable_catalog_resources
from lens.models import TenantMCPServer, TenantSkill


class CatalogOwnershipMixin:
    """Restrict writes to the owner and keep official tenant opt-ins separate."""

    permission_classes = [HasTenantAdminAccess | HasPlatformSuperAdmin]
    catalog_kind = "skill"

    def initial(self, request, *args, **kwargs):
        """Require a resolved owner even for collection-level actions."""
        super().initial(request, *args, **kwargs)
        self.catalog_tenant()

    def get_serializer_context(self):
        """Tell validators whether this write creates a platform template."""
        context = super().get_serializer_context()
        context["catalog_official"] = self.catalog_tenant() is None
        return context

    def catalog_tenant(self):
        """Resolve private ownership while platform operations remain global."""
        platform = HasPlatformSuperAdmin().has_permission(self.request, self)
        if platform and (
            self.request.headers.get("X-Catalog-Scope") == "official"
            or not is_delegated_request()
        ):
            delegation = get_active_delegation(self.request.user)
            if is_delegated_request() and delegation is None:
                raise PermissionDenied("An active delegation is required.")
            if delegation and delegation.mode == "readonly" and self.request.method not in {"GET", "HEAD", "OPTIONS"}:
                raise PermissionDenied("This delegation is read only.")
            return None
        if not HasTenantAdminAccess().has_permission(self.request, self):
            raise PermissionDenied("Tenant administration access is required.")
        tenant = get_user_tenant(self.request.user)
        if tenant is None:
            raise PermissionDenied("An active tenant membership is required.")
        return tenant

    def get_queryset(self):
        """Expose official templates and the current tenant's private resources."""
        queryset = super().get_queryset()
        tenant = self.catalog_tenant()
        if tenant is None:
            queryset = queryset.filter(tenant__isnull=True)
        else:
            queryset = queryset.filter(Q(tenant=tenant) | Q(tenant__isnull=True, enabled=True))
        if self.request.query_params.get("catalog") == "official":
            queryset = queryset.filter(tenant__isnull=True)
        elif self.request.query_params.get("catalog") == "private":
            queryset = queryset.filter(tenant=tenant) if tenant is not None else queryset.none()
        if self.request.query_params.get("usable") == "true":
            queryset = usable_catalog_resources(queryset, tenant)
        entry_model = TenantSkill if self.catalog_kind == "skill" else TenantMCPServer
        field = "skill" if self.catalog_kind == "skill" else "mcp"
        bindings = "assistantskill" if self.catalog_kind == "skill" else "assistantmcp"
        if tenant is not None:
            queryset = queryset.annotate(
                tenant_opted_in=Exists(
                    entry_model.objects.filter(tenant=tenant, **{field: OuterRef("pk")}, enabled=True)
                ),
                bound_count=Count(bindings, filter=Q(**{f"{bindings}__assistant__tenant": tenant}), distinct=True),
            )
        else:
            queryset = queryset.annotate(
                tenant_opted_in=Value(False, output_field=BooleanField()),
                bound_count=Count(bindings, distinct=True),
                enabled_tenant_count=Count("tenant_entries", filter=Q(tenant_entries__enabled=True), distinct=True),
            )
        return queryset.order_by("name", "uuid")

    def check_object_permissions(self, request, obj):
        """Official templates are read-only to tenant administrators."""
        super().check_object_permissions(request, obj)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and self.action != "enablement":
            if obj.tenant_id is None and self.catalog_tenant() is not None:
                raise PermissionDenied("Only platform administrators may change official resources.")

    def perform_create(self, serializer):
        """Assign ownership from the authenticated request."""
        serializer.save(tenant=self.catalog_tenant())

    @action(detail=True, methods=["post"])
    def enablement(self, request, *args, **kwargs):
        """Enable or disable one published official resource for this tenant."""
        resource = self.get_object()
        tenant = self.catalog_tenant()
        if tenant is None or resource.tenant_id is not None:
            raise PermissionDenied("Select an official resource within a tenant.")
        enabled = request.data.get("enabled")
        if not isinstance(enabled, bool):
            raise ValidationError({"enabled": "A boolean is required."})
        model = TenantSkill if self.catalog_kind == "skill" else TenantMCPServer
        field = "skill" if self.catalog_kind == "skill" else "mcp"
        model.objects.update_or_create(tenant=tenant, **{field: resource}, defaults={"enabled": enabled})
        resource.tenant_opted_in = enabled
        return Response(self.get_serializer(resource).data)

    def finalize_response(self, request, response, *args, **kwargs):
        """Audit successful catalog writes without recording payload values."""
        response = super().finalize_response(request, response, *args, **kwargs)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and 200 <= response.status_code < 300:
            payload = response.data if isinstance(response.data, dict) else {}
            record_audit(
                action=f"catalog.{self.catalog_kind}.{self.action}",
                request=request,
                actor=request.user,
                tenant=get_user_tenant(request.user) if self.catalog_tenant() is not None else None,
                resource_type=self.catalog_kind,
                resource_id=str(kwargs.get("uuid") or payload.get("uuid", "")),
            )
        return response
