"""Administrative views for trusted installed plugins."""

import uuid
from datetime import timedelta

from django.db.models import Count
from django.db.models.deletion import ProtectedError
from django.http import FileResponse
from django.utils import timezone
from lens.models import (
    Connection,
    CredentialLease,
    ExecutionSnapshot,
    PluginInvocation,
)
from lens.plugins.datasource_access import (
    datasource_access_failure_detail,
    validate_connection_datasource_access,
)
from lens.plugins.http import PluginHttpClientError, plugin_http_pool
from lens.plugins.providers import (
    DatasourceProviderError,
    get_datasource_provider,
)
from lens.plugins.registry import (
    PluginNotFoundError,
    discover_plugins,
    installed_plugin,
)
from lens.plugins.tool_snapshots import (
    ACTIVE_RUN_STATUSES,
    ToolSnapshotError,
    create_tool_execution_snapshot,
)
from lens.serializers import (
    ConnectionSerializer,
    PluginInvocationSerializer,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from .base import BaseAdminViewSet, LensNodeAuthMixin

SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)


def _active_connection_secret(connection):
    """Return active secret material or a stable lifecycle response."""

    if connection.status != Connection.Status.ACTIVE:
        return Response(
            {"detail": "CONNECTION_DISABLED"},
            status=status.HTTP_409_CONFLICT,
        )
    version = connection.secret_version
    if version is None or version.status != "active":
        return Response(
            {"detail": "SECRET_VERSION_DISABLED"},
            status=status.HTTP_409_CONFLICT,
        )
    if version.material.status != "active":
        return Response(
            {"detail": "SECRET_MATERIAL_DISABLED"},
            status=status.HTTP_409_CONFLICT,
        )
    secret = version.get_value()
    if not secret:
        return Response(
            {"detail": "SECRET_UNAVAILABLE"},
            status=status.HTTP_409_CONFLICT,
        )
    return secret


def _resource_option_selection(connection, resource, request):
    """Return the manifest-approved dependency selection for one resource."""

    if not resource:
        return None
    try:
        schema = installed_plugin(connection.plugin_key).datasource_schema
    except PluginNotFoundError as exc:
        raise DatasourceProviderError("PLUGIN_NOT_FOUND") from exc
    if schema is None:
        raise DatasourceProviderError("resource options are unsupported")
    fields = schema.get("properties", {})
    matches = [
        field
        for field in fields.values()
        if field.get("format") == "provider-resource-option"
        and field.get("resource") == resource
    ]
    if len(matches) != 1:
        raise DatasourceProviderError("resource options are unsupported")
    dependency = matches[0]["depends_on"]
    provider_dependency = dependency
    if dependency.endswith("ies"):
        provider_dependency = dependency[:-3] + "y"
    elif dependency.endswith("s"):
        provider_dependency = dependency[:-1]
    value = request.query_params.get(dependency)
    if not value and provider_dependency != dependency:
        value = request.query_params.get(provider_dependency)
    if not value:
        raise DatasourceProviderError("resource dependency is required")
    return {provider_dependency: value}


def _connection_http_client(provider, connection):
    """Return the host-managed HTTP client for one saved Connection."""

    origins = provider.http_origins(
        connection.endpoint,
        connection.config,
    )
    return plugin_http_pool.bind(
        connection.plugin_key,
        connection.uuid,
        origins,
    )


class PluginRuntimeNoStoreMixin:
    """Prevent caching of Plugin Runtime responses in every outcome."""

    def finalize_response(self, request, response, *args, **kwargs):
        """Attach the runtime cache policy after DRF handles the request."""

        response = super().finalize_response(
            request,
            response,
            *args,
            **kwargs,
        )
        response["Cache-Control"] = "no-store"
        return response


class PluginRegistryViewSet(BaseAdminViewSet, ViewSet):
    """Expose installed Plugin identities without runtime internals."""

    http_method_names = ["get", "post", "head", "options"]
    lookup_field = "key"

    @staticmethod
    def _icon_url(plugin):
        """Return the stable admin asset URL for one optional Plugin icon."""

        if not plugin.icon:
            return None
        return f"/api/lens/admin/plugins/{plugin.key}/icon/"

    def list(self, request):
        """List installed Plugins visible to administrators."""

        del request
        plugins = []
        for key in sorted({plugin.key for plugin in discover_plugins()}):
            try:
                plugins.append(installed_plugin(key))
            except PluginNotFoundError:
                continue
        return Response(
            [
                {
                    "key": plugin.key,
                    "version": plugin.version,
                    "protocol_version": plugin.protocol_version,
                    "capability_family": plugin.capability_family,
                    "display_name": plugin.display_name,
                    "description": plugin.description,
                    "assistant_guidance": plugin.assistant_guidance,
                    "icon_url": self._icon_url(plugin),
                    "datasource_source_type": (
                        plugin.datasource_source_type
                    ),
                    "datasource": plugin.datasource,
                }
                for plugin in plugins
            ]
        )

    @action(detail=True, methods=["get"], url_path="tools")
    def tools(self, request, key=None):
        """List safe model-facing tools from the installed Plugin."""

        del request
        try:
            plugin = installed_plugin(key)
        except PluginNotFoundError:
            return Response({"detail": "PLUGIN_NOT_FOUND"}, status=404)
        return Response(
            [
                {
                    "key": tool.key,
                    "description": tool.description,
                    "capability": tool.capability,
                    "capability_family": tool.capability_family,
                    "side_effect": tool.side_effect,
                    "input_schema": tool.input_schema,
                }
                for tool in plugin.tools
            ]
        )

    @action(detail=True, methods=["get"], url_path="manifest")
    def manifest(self, request, key=None):
        """Return safe metadata and form schemas for one Plugin."""

        del request
        try:
            plugin = installed_plugin(key)
        except PluginNotFoundError:
            return Response({"detail": "PLUGIN_NOT_FOUND"}, status=404)
        return Response(
            {
                "key": plugin.key,
                "version": plugin.version,
                "protocol_version": plugin.protocol_version,
                "capability_family": plugin.capability_family,
                "display_name": plugin.display_name,
                "description": plugin.description,
                "assistant_guidance": plugin.assistant_guidance,
                "icon_url": self._icon_url(plugin),
                "datasource_source_type": plugin.datasource_source_type,
                "datasource": plugin.datasource,
                "connection_schema": plugin.connection_schema,
                "datasource_schema": plugin.datasource_schema,
                "tools": [
                    {
                        "key": tool.key,
                        "description": tool.description,
                        "capability": tool.capability,
                        "capability_family": tool.capability_family,
                        "side_effect": tool.side_effect,
                        "input_schema": tool.input_schema,
                    }
                    for tool in plugin.tools
                ],
            }
        )

    @action(detail=True, methods=["get"], url_path="icon")
    def icon(self, request, key=None):
        """Serve one validated icon stored inside the trusted Plugin package."""

        del request
        try:
            plugin = installed_plugin(key)
        except PluginNotFoundError:
            return Response({"detail": "PLUGIN_NOT_FOUND"}, status=404)
        if not plugin.icon:
            return Response({"detail": "PLUGIN_ICON_NOT_FOUND"}, status=404)
        icon_path = plugin.path / plugin.icon
        content_types = {
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
        }
        response = FileResponse(
            icon_path.open("rb"),
            content_type=content_types[icon_path.suffix.lower()],
        )
        response["Cache-Control"] = "private, max-age=86400"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class PluginInvocationViewSet(BaseAdminViewSet):
    """Expose secret-free Plugin execution audit records to administrators."""

    http_method_names = ["get", "head", "options"]
    serializer_class = PluginInvocationSerializer
    queryset = PluginInvocation.objects.select_related(
        "snapshot",
        "connection",
        "datasource",
        "run",
        "actor",
        "lensnode",
    )

    def get_queryset(self):
        """Apply bounded exact-match audit filters."""

        queryset = super().get_queryset()
        for field in ("plugin_key", "kind", "status", "tool_key"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value[:128]})
        connection_uuid = self.request.query_params.get("connection_uuid")
        if connection_uuid:
            queryset = queryset.filter(connection__uuid=connection_uuid)
        return queryset


class ConnectionViewSet(BaseAdminViewSet):
    """Admin CRUD for reusable Plugin connections."""

    queryset = (
        Connection.objects.all()
        .select_related("secret_version")
        .annotate(
            assistant_usage_count=Count(
                "assistant_bindings",
                distinct=True,
            ),
            datasource_usage_count=Count(
                "datasources",
                distinct=True,
            ),
        )
    )
    serializer_class = ConnectionSerializer

    @action(detail=False, methods=["post"], url_path="resource-preview")
    def resource_preview(self, request, *args, **kwargs):
        """Discover selectable resources using an unsaved secret in memory."""

        del args, kwargs
        if not isinstance(request.data, dict):
            return Response({"detail": "REQUEST_INVALID"}, status=400)
        plugin_key = request.data.get("plugin_key")
        secret_value = request.data.get("secret_value")
        endpoint = request.data.get("endpoint", "")
        config = request.data.get("config", {})
        try:
            provider = get_datasource_provider(plugin_key)
            endpoint = provider.validate_connection(endpoint, config)
            origins = provider.http_origins(endpoint, config)
            with plugin_http_pool.temporary(plugin_key, origins) as client:
                resources = provider.discover_connection_resources(
                    secret_value,
                    endpoint=endpoint,
                    connection_config=config,
                    query=request.data.get("query", ""),
                    cursor=request.data.get("cursor", ""),
                    limit=request.data.get("limit", 50),
                    client=client,
                )
        except (
            DatasourceProviderError,
            PluginHttpClientError,
            PluginNotFoundError,
        ) as exc:
            return Response({"detail": str(exc)}, status=400)
        response = Response(resources)
        response["Cache-Control"] = "no-store"
        return response

    @action(detail=True, methods=["get"], url_path="resource-candidates")
    def resource_candidates(self, request, *args, **kwargs):
        """Discover selectable resources using the stored Connection secret."""

        del args, kwargs
        connection = self.get_object()
        secret_or_response = _active_connection_secret(connection)
        if isinstance(secret_or_response, Response):
            return secret_or_response
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            return Response({"detail": "RESOURCE_LIMIT_INVALID"}, status=400)
        try:
            provider = get_datasource_provider(connection.plugin_key)
            client = _connection_http_client(provider, connection)
            resources = provider.discover_connection_resources(
                secret_or_response,
                endpoint=connection.endpoint,
                connection_config=connection.config,
                query=request.query_params.get("query", ""),
                cursor=request.query_params.get("cursor", ""),
                limit=limit,
                client=client,
            )
        except (DatasourceProviderError, PluginHttpClientError) as exc:
            return Response({"detail": str(exc)}, status=400)
        response = Response(resources)
        response["Cache-Control"] = "no-store"
        return response

    def get_queryset(self):
        """Filter connections by Plugin or lifecycle status."""

        queryset = super().get_queryset()
        plugin_key = self.request.query_params.get("plugin_key")
        status_value = self.request.query_params.get("status")
        if plugin_key:
            queryset = queryset.filter(plugin_key=plugin_key)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    def destroy(self, request, *args, **kwargs):
        """Reject deletion while a connection is referenced."""

        connection = self.get_object()
        if (
            connection.datasources.exists()
            or connection.assistant_bindings.exists()
            or connection.execution_snapshots.exists()
        ):
            return Response(
                {"detail": "CONNECTION_IN_USE"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {"detail": "CONNECTION_IN_USE"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="validate")
    def validate_connection(self, request, *args, **kwargs):
        """Validate stored authentication without returning the secret."""

        del request, args, kwargs
        connection = self.get_object()
        secret_or_response = _active_connection_secret(connection)
        if isinstance(secret_or_response, Response):
            return secret_or_response
        try:
            provider = get_datasource_provider(connection.plugin_key)
            client = _connection_http_client(provider, connection)
            result = provider.validate_live_connection(
                secret_or_response,
                endpoint=connection.endpoint,
                connection_config=connection.config,
                client=client,
            )
        except (DatasourceProviderError, PluginHttpClientError) as exc:
            return Response(
                {"status": "failed", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"status": "success", **result})

    @action(
        detail=True,
        methods=["post"],
        url_path="validate-datasource",
    )
    def validate_datasource(self, request, *args, **kwargs):
        """Validate access to each configured datasource resource."""

        del args, kwargs
        connection = self.get_object()
        if (
            not isinstance(request.data, dict)
            or set(request.data) != {"datasource_config"}
        ):
            return Response({"detail": "REQUEST_INVALID"}, status=400)
        try:
            result = validate_connection_datasource_access(
                connection,
                request.data.get("datasource_config"),
            )
        except (
            DatasourceProviderError,
            PluginHttpClientError,
            PluginNotFoundError,
        ) as exc:
            response = Response(
                {"valid": False, "detail": str(exc), "resources": []},
                status=status.HTTP_400_BAD_REQUEST,
            )
            response["Cache-Control"] = "no-store"
            return response
        response_status = (
            status.HTTP_200_OK
            if result.get("valid")
            else status.HTTP_400_BAD_REQUEST
        )
        response_data = dict(result)
        if not result.get("valid"):
            response_data["detail"] = datasource_access_failure_detail(
                result
            )
        response = Response(response_data, status=response_status)
        response["Cache-Control"] = "no-store"
        return response

    @action(detail=True, methods=["get"], url_path="resources")
    def resources(self, request, *args, **kwargs):
        """Discover resources only inside the Connection allowlist."""

        del args, kwargs
        connection = self.get_object()
        secret_or_response = _active_connection_secret(connection)
        if isinstance(secret_or_response, Response):
            return secret_or_response
        try:
            provider = get_datasource_provider(connection.plugin_key)
            client = _connection_http_client(provider, connection)
            resource = request.query_params.get("resource", "")
            selection = _resource_option_selection(
                connection,
                resource,
                request,
            )
            if selection is None:
                resources = provider.discover_resources(
                    connection.allowed_scope,
                    secret_or_response,
                    endpoint=connection.endpoint,
                    connection_config=connection.config,
                    client=client,
                )
            else:
                resources = provider.discover_resource_options(
                    connection.allowed_scope,
                    secret_or_response,
                    resource,
                    selection,
                    endpoint=connection.endpoint,
                    connection_config=connection.config,
                    client=client,
                )
        except (DatasourceProviderError, PluginHttpClientError) as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(resources)


class PluginToolExecutionSnapshotView(
    PluginRuntimeNoStoreMixin,
    LensNodeAuthMixin,
    APIView,
):
    """Create one authorized execution snapshot for a Plugin Tool call."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        """Authorize a Run-bound Tool call for the requesting LensNode."""

        node = self._authenticate_lensnode(request)
        if node is None:
            return Response({"detail": "LENSNODE_UNAUTHORIZED"}, status=401)
        if not isinstance(request.data, dict):
            return Response({"detail": "TOOL_REQUEST_INVALID"}, status=400)
        run_uuid = _parse_uuid(request.data.get("run_uuid"))
        connection_uuid = _parse_uuid(request.data.get("connection_uuid"))
        if run_uuid is None or connection_uuid is None:
            return Response({"detail": "TOOL_REQUEST_INVALID"}, status=400)
        try:
            snapshot, created = create_tool_execution_snapshot(
                run_uuid=run_uuid,
                lensnode=node,
                connection_uuid=connection_uuid,
                tool_key=request.data.get("tool_key"),
                call_id=request.data.get("call_id"),
                arguments=request.data.get("arguments"),
            )
        except ToolSnapshotError as exc:
            return Response(
                {"detail": exc.code},
                status=exc.status_code,
            )
        response = Response(
            {
                "snapshot_uuid": str(snapshot.uuid),
                "run_uuid": str(snapshot.run.uuid),
                "connection_uuid": str(snapshot.connection.uuid),
                "tool_key": snapshot.tool_key,
                "invocation_id": snapshot.invocation_id,
                "plugin_key": snapshot.plugin_key,
                "plugin_version": snapshot.plugin_version,
                "protocol_version": snapshot.protocol_version,
            },
            status=(
                status.HTTP_201_CREATED if created else status.HTTP_200_OK
            ),
        )
        return response


class PluginCredentialLeaseView(
    PluginRuntimeNoStoreMixin,
    LensNodeAuthMixin,
    APIView,
):
    """Issue a short-lived opaque lease for a node-owned execution snapshot."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        """Create a lease after authenticating the owning LensNode."""

        node = self._authenticate_lensnode(request)
        if node is None:
            return Response({"detail": "LENSNODE_UNAUTHORIZED"}, status=401)
        snapshot_uuid = request.data.get("snapshot_uuid")
        snapshot = (
            ExecutionSnapshot.objects.select_related(
                "datasource",
                "run",
                "connection",
                "secret_version__material",
            )
            .filter(uuid=snapshot_uuid)
            .first()
        )
        if snapshot is None:
            return Response({"detail": "SNAPSHOT_NOT_FOUND"}, status=404)
        if _snapshot_node_id(snapshot) != node.pk:
            return Response({"detail": "SNAPSHOT_NODE_MISMATCH"}, status=403)
        if not _tool_snapshot_run_is_active(snapshot):
            return Response({"detail": "RUN_NOT_ACTIVE"}, status=409)
        if snapshot.connection.status != snapshot.connection.Status.ACTIVE:
            return Response({"detail": "CONNECTION_DISABLED"}, status=409)
        if (
            snapshot.secret_version is not None
            and snapshot.secret_version.status != "active"
        ):
            return Response({"detail": "SECRET_VERSION_DISABLED"}, status=409)
        if (
            snapshot.kind == ExecutionSnapshot.Kind.TOOL_INVOKE
            and snapshot.secret_version is not None
            and snapshot.secret_version.material.status != "active"
        ):
            return Response({"detail": "SECRET_MATERIAL_DISABLED"}, status=409)
        now = timezone.now()
        lease = CredentialLease.objects.create(
            snapshot=snapshot,
            lensnode=node,
            expires_at=now + timedelta(minutes=5),
        )
        response = Response(
            {
                "lease_uuid": str(lease.uuid),
                "snapshot_uuid": str(snapshot.uuid),
                "expires_at": lease.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )
        return response


class PluginCredentialMaterialView(
    PluginRuntimeNoStoreMixin,
    LensNodeAuthMixin,
    APIView,
):
    """Return lease-bound secret material to the authenticated LensNode."""

    authentication_classes = []
    permission_classes = []

    def post(self, request, lease_uuid):
        """Resolve a non-expired lease without exposing snapshot metadata."""

        node = self._authenticate_lensnode(request)
        if node is None:
            return Response({"detail": "LENSNODE_UNAUTHORIZED"}, status=401)
        lease = (
            CredentialLease.objects.select_related(
                "snapshot__secret_version",
                "snapshot__connection",
                "snapshot__run",
            )
            .filter(uuid=lease_uuid, lensnode=node)
            .first()
        )
        if lease is None:
            return Response({"detail": "LEASE_NOT_FOUND"}, status=404)
        now = timezone.now()
        if lease.revoked_at is not None or lease.expires_at <= now:
            return Response({"detail": "LEASE_EXPIRED"}, status=410)
        if not _tool_snapshot_run_is_active(lease.snapshot):
            return Response({"detail": "RUN_NOT_ACTIVE"}, status=409)
        if (
            lease.snapshot.connection.status
            != lease.snapshot.connection.Status.ACTIVE
        ):
            return Response({"detail": "CONNECTION_DISABLED"}, status=409)
        secret_version = lease.snapshot.secret_version
        if secret_version is not None and secret_version.status != "active":
            return Response({"detail": "SECRET_VERSION_DISABLED"}, status=409)
        if (
            secret_version is not None
            and secret_version.material.status != "active"
        ):
            return Response({"detail": "SECRET_MATERIAL_DISABLED"}, status=409)
        value = secret_version.get_value() if secret_version else ""
        if not value:
            return Response({"detail": "SECRET_UNAVAILABLE"}, status=409)
        PluginInvocation.objects.filter(
            snapshot=lease.snapshot,
            status=PluginInvocation.Status.AUTHORIZED,
        ).update(
            status=PluginInvocation.Status.MATERIALIZED,
            materialized_at=now,
        )
        response = Response(
            {
                "lease_uuid": str(lease.uuid),
                "plugin_key": lease.snapshot.plugin_key,
                "endpoint": lease.snapshot.resolved_config.get("endpoint", ""),
                "value": value,
            },
            status=status.HTTP_200_OK,
        )
        return response


class PluginExecutionSnapshotView(
    PluginRuntimeNoStoreMixin,
    LensNodeAuthMixin,
    APIView,
):
    """Return non-sensitive execution data to the owning LensNode."""

    authentication_classes = []
    permission_classes = []

    def get(self, request, snapshot_uuid):
        """Resolve one snapshot for a node without returning its secret."""

        node = self._authenticate_lensnode(request)
        if node is None:
            return Response({"detail": "LENSNODE_UNAUTHORIZED"}, status=401)
        snapshot = (
            ExecutionSnapshot.objects.select_related(
                "connection",
                "datasource",
                "run",
            )
            .filter(uuid=snapshot_uuid)
            .first()
        )
        if snapshot is None or _snapshot_node_id(snapshot) != node.pk:
            return Response({"detail": "SNAPSHOT_NOT_FOUND"}, status=404)
        payload = {
            "snapshot_uuid": str(snapshot.uuid),
            "connection_uuid": str(snapshot.connection.uuid),
            "plugin_key": snapshot.plugin_key,
            "plugin_version": snapshot.plugin_version,
            "protocol_version": snapshot.protocol_version,
            "resolved_config": _safe_snapshot_config(
                snapshot.resolved_config,
                allow_resource_tokens=snapshot.plugin_key == "feishu",
            ),
        }
        if snapshot.kind == ExecutionSnapshot.Kind.DATASOURCE_SYNC:
            payload["datasource_uuid"] = str(snapshot.datasource.uuid)
        elif snapshot.kind == ExecutionSnapshot.Kind.TOOL_INVOKE:
            payload.update(
                {
                    "run_uuid": str(snapshot.run.uuid),
                    "tool_key": snapshot.tool_key,
                    "invocation_id": snapshot.invocation_id,
                }
            )
        else:
            return Response({"detail": "SNAPSHOT_NOT_FOUND"}, status=404)
        response = Response(
            payload,
            status=status.HTTP_200_OK,
        )
        return response


def _safe_snapshot_config(
    value,
    *,
    allow_resource_tokens=False,
    path=(),
):
    """Remove credential-shaped keys before returning snapshot data."""

    if isinstance(value, dict):
        resource_identity = (
            allow_resource_tokens
            and path == ("datasource_config", "resources")
            and set(value) == {"kind", "token"}
            and isinstance(value.get("kind"), str)
            and isinstance(value.get("token"), str)
        )
        return {
            key: _safe_snapshot_config(
                nested,
                allow_resource_tokens=allow_resource_tokens,
                path=path + (key,),
            )
            for key, nested in value.items()
            if (
                str(key).lower() not in SENSITIVE_KEYS
                or (resource_identity and key == "token")
            )
        }
    if isinstance(value, list):
        return [
            _safe_snapshot_config(
                item,
                allow_resource_tokens=allow_resource_tokens,
                path=path,
            )
            for item in value
        ]
    return value


def _parse_uuid(value):
    """Return a UUID or None for malformed external identifiers."""

    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _snapshot_node_id(snapshot):
    """Return the LensNode owner for a valid typed execution snapshot."""

    if (
        snapshot.kind == ExecutionSnapshot.Kind.DATASOURCE_SYNC
        and snapshot.datasource_id is not None
    ):
        return snapshot.datasource.lensnode_id
    if (
        snapshot.kind == ExecutionSnapshot.Kind.TOOL_INVOKE
        and snapshot.run_id is not None
    ):
        return snapshot.run.lensnode_id
    return None


def _tool_snapshot_run_is_active(snapshot):
    """Return whether a snapshot is executable under its Run lifecycle."""

    if snapshot.kind != ExecutionSnapshot.Kind.TOOL_INVOKE:
        return True
    return (
        snapshot.run_id is not None
        and snapshot.run.status in ACTIVE_RUN_STATUSES
    )
