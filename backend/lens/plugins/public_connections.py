"""Resolve public repositories without user-managed authentication."""

import hashlib
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from django.db.models import Q

from lens.models import Connection

from .providers import DatasourceProviderError, get_datasource_provider


ANONYMOUS_CONNECTION_FILTER = Q(secret_version__isnull=True, config={}) & (
    Q(plugin_key="github", allowed_scope={"repositories": ["*"]})
    | Q(plugin_key="gitlab", allowed_scope={"projects": ["*"]})
)


def is_anonymous_connection(connection):
    """Read anonymous repository access from the existing connection policy."""

    scope_key = {"github": "repositories", "gitlab": "projects"}.get(connection.plugin_key)
    return bool(
        scope_key
        and connection.secret_version_id is None
        and not connection.config
        and connection.allowed_scope == {scope_key: ["*"]}
    )


def public_datasource_connection(plugin_key, datasource_config, endpoint=""):
    """Normalize public selections and build an unsaved anonymous connection."""

    if not isinstance(plugin_key, str) or plugin_key not in {"github", "gitlab"}:
        raise DatasourceProviderError("public repository provider is unsupported")
    provider = get_datasource_provider(plugin_key)
    config = dict(datasource_config) if isinstance(datasource_config, dict) else datasource_config
    if plugin_key == "gitlab" and isinstance(config, dict):
        key = "projects" if "projects" in config else "project"
        values = config.get(key)
        if key == "projects" and not isinstance(values, list):
            raise DatasourceProviderError("projects must contain 1 through 50 items")
        values = values if key == "projects" else [values]
        normalized = []
        for value in values:
            if isinstance(value, str) and "://" in value:
                try:
                    parsed = urlsplit(value.strip())
                    origin = provider.validate_connection(f"{parsed.scheme}://{parsed.netloc}", {})
                except ValueError as exc:
                    raise DatasourceProviderError("GitLab project URL is invalid") from exc
                if parsed.query or parsed.fragment or "/-/" in parsed.path:
                    raise DatasourceProviderError("GitLab project URL is invalid")
                if endpoint and provider.validate_connection(endpoint, {}) != origin:
                    raise DatasourceProviderError("GitLab projects must use the same endpoint")
                endpoint = origin
                value = parsed.path.strip("/")
                if value.endswith(".git"):
                    value = value[:-4]
            normalized.append(value)
        config[key] = normalized if key == "projects" else normalized[0]
    endpoint = provider.validate_connection(endpoint or f"https://{plugin_key}.com", {})
    scope_key = "repositories" if plugin_key == "github" else "projects"
    scope = {scope_key: ["*"]}
    normalized = provider.validate_datasource_config(scope, config)
    identity = f"public:{plugin_key}:{hashlib.sha256(endpoint.encode()).hexdigest()[:48]}"
    connection = Connection(
        uuid=uuid5(NAMESPACE_URL, identity),
        name=f"Public {plugin_key.title()}",
        plugin_key=plugin_key,
        endpoint=endpoint,
        allowed_scope=scope,
    )
    return connection, normalized


def persist_public_connection(connection):
    """Reuse one immutable anonymous connection per provider and origin."""

    stored, _ = Connection.objects.get_or_create(
        uuid=connection.uuid,
        defaults={
            "name": connection.name,
            "plugin_key": connection.plugin_key,
            "endpoint": connection.endpoint,
            "allowed_scope": connection.allowed_scope,
        },
    )
    if stored.secret_version_id or stored.config or stored.allowed_scope != connection.allowed_scope:
        raise DatasourceProviderError("public repository connection is invalid")
    if stored.status != Connection.Status.ACTIVE:
        raise DatasourceProviderError("CONNECTION_DISABLED")
    return stored
