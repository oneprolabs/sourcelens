"""Shared access validation for saved Plugin Connections."""

from lens.models import Connection

from .http import plugin_http_pool
from .providers import DatasourceProviderError, get_datasource_provider
from .registry import installed_plugin, plugin_requires_secret


def validate_connection_datasource_access(connection, datasource_config):
    """Validate configured resources with one active Connection secret."""

    if connection.status != Connection.Status.ACTIVE:
        raise DatasourceProviderError("CONNECTION_DISABLED")
    version = connection.secret_version
    if version is None:
        if plugin_requires_secret(installed_plugin(connection.plugin_key)):
            raise DatasourceProviderError("SECRET_VERSION_DISABLED")
        secret = ""
    else:
        if version.status != "active":
            raise DatasourceProviderError("SECRET_VERSION_DISABLED")
        if version.material.status != "active":
            raise DatasourceProviderError("SECRET_MATERIAL_DISABLED")
        secret = version.get_value()
        if not secret:
            raise DatasourceProviderError("SECRET_UNAVAILABLE")

    provider = get_datasource_provider(connection.plugin_key)
    if not provider.requires_datasource_access_validation:
        raise DatasourceProviderError(
            "PROVIDER_DATASOURCE_ACCESS_VALIDATION_UNSUPPORTED"
        )
    normalized = provider.validate_datasource_config(
        connection.allowed_scope,
        datasource_config,
    )
    origins = provider.http_origins(
        connection.endpoint,
        connection.config,
    )
    client = plugin_http_pool.bind(
        connection.plugin_key,
        connection.uuid,
        origins,
    )
    result = provider.validate_datasource_access(
        secret,
        normalized,
        endpoint=connection.endpoint,
        connection_config=connection.config,
        client=client,
    )
    return {**result, "datasource_config": normalized}


def datasource_access_failure_detail(result):
    """Return a bounded failure message containing the first failed URL."""

    resources = result.get("resources") if isinstance(result, dict) else []
    failures = [
        resource
        for resource in resources or []
        if isinstance(resource, dict) and not resource.get("accessible")
    ]
    if not failures:
        return "PLUGIN_DATASOURCE_RESOURCE_ACCESS_FAILED"
    first = failures[0]
    return (
        f'{first.get("error") or "PLUGIN_DATASOURCE_RESOURCE_ACCESS_FAILED"}: '
        f'{str(first.get("url") or "")[:2000]}'
    )
