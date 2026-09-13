"""Create immutable resolved configuration for plugin executions."""

from copy import deepcopy
import hashlib
import json

from django.db import transaction

from lens.models import DataSource, ExecutionSnapshot

from .audit import create_invocation_audit
from .providers import DatasourceProviderError, get_datasource_provider
from .registry import PluginRegistryError, installed_plugin

SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "app_secret",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)


def datasource_runtime_target_path(
    datasource,
    lensnode,
    datasource_config,
):
    """Return the stable runtime path for one resolved datasource config."""

    if datasource.target_path:
        return datasource.target_path
    identity = {
        "plugin_key": datasource.plugin_key,
        "source_type": datasource.source_type,
        "datasource_config": datasource_config,
        "sync_policy": datasource.sync_policy or {},
    }
    config_hash = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]
    return (
        f"{lensnode.workspace_path}/datasources/"
        f"{datasource.uuid}-{config_hash}"
    )


def create_datasource_sync_snapshot(datasource, *, lensnode=None):
    """Resolve one external datasource into an immutable execution snapshot."""

    datasource = DataSource.objects.select_related(
        "connection",
        "connection__secret_version",
        "connection__secret_version__material",
        "lensnode",
    ).get(pk=datasource.pk)
    connection = datasource.connection
    if connection is None:
        raise PluginRegistryError("datasource connection is required")
    if connection.status != connection.Status.ACTIVE:
        raise PluginRegistryError("datasource connection is disabled")
    secret_version = connection.secret_version
    if (
        secret_version is None
        or secret_version.status != "active"
        or secret_version.material.status != "active"
        or not secret_version.encrypted_value
    ):
        raise PluginRegistryError(
            "datasource connection secret is unavailable"
        )
    if (
        not datasource.plugin_key
        or datasource.plugin_key != connection.plugin_key
    ):
        raise PluginRegistryError(
            "datasource and connection plugin keys differ"
        )
    lensnode = lensnode or datasource.lensnode
    if lensnode is None:
        raise PluginRegistryError("datasource LensNode is required")
    plugin = installed_plugin(datasource.plugin_key)
    if plugin.datasource is None:
        raise PluginRegistryError("plugin does not support datasources")
    _reject_sensitive_values(connection.config)
    _reject_sensitive_values(connection.allowed_scope)
    _reject_sensitive_values(
        datasource.datasource_config,
        allow_feishu_resource_tokens=datasource.plugin_key == "feishu",
    )
    try:
        provider = get_datasource_provider(
            datasource.plugin_key,
            plugin.version,
        )
        provider.validate_datasource_source_type(datasource.source_type)
        endpoint = provider.validate_connection(
            connection.endpoint,
            connection.config,
        )
        datasource_config = provider.validate_datasource_config(
            connection.allowed_scope,
            datasource.datasource_config,
        )
    except DatasourceProviderError as exc:
        raise PluginRegistryError(str(exc)) from exc
    target_path = datasource_runtime_target_path(
        datasource,
        lensnode,
        datasource_config,
    )
    resolved_config = {
        "endpoint": endpoint,
        "connection_config": deepcopy(connection.config),
        "connection_scope": deepcopy(connection.allowed_scope),
        "datasource_config": datasource_config,
        "sync_policy": deepcopy(datasource.sync_policy),
        "target_path": target_path,
        "publish_artifact": datasource.lensnode_id is None,
        "lensnode_uuid": str(lensnode.uuid),
    }
    with transaction.atomic():
        snapshot = ExecutionSnapshot.objects.create(
            kind=ExecutionSnapshot.Kind.DATASOURCE_SYNC,
            connection=connection,
            datasource=datasource,
            secret_version=connection.secret_version,
            plugin_key=plugin.key,
            plugin_version=plugin.version,
            protocol_version=plugin.protocol_version,
            resolved_config=resolved_config,
        )
        create_invocation_audit(
            snapshot,
            lensnode=lensnode,
            resource_summary=datasource_config,
        )
        return snapshot


def _reject_sensitive_values(
    value,
    *,
    allow_feishu_resource_tokens=False,
):
    """Reject credential-shaped keys from persisted non-secret config."""

    if isinstance(value, dict):
        resource_identity = (
            allow_feishu_resource_tokens
            and set(value) == {"kind", "token"}
            and isinstance(value.get("kind"), str)
            and isinstance(value.get("token"), str)
        )
        for key, nested in value.items():
            sensitive_key = str(key).lower() in SENSITIVE_CONFIG_KEYS
            if sensitive_key and not (resource_identity and key == "token"):
                raise PluginRegistryError(
                    "plugin config cannot contain credentials"
                )
            _reject_sensitive_values(
                nested,
                allow_feishu_resource_tokens=allow_feishu_resource_tokens,
            )
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_values(
                nested,
                allow_feishu_resource_tokens=allow_feishu_resource_tokens,
            )
