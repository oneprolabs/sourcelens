"""Create immutable, Run-bound snapshots for Plugin Tool calls."""

import re
from copy import deepcopy

from django.db import transaction

from lens.models import Connection, ExecutionSnapshot, Run

from .audit import create_invocation_audit
from .registry import READ_ONLY_TOOL_CAPABILITIES
from .tool_providers import ToolProviderError, get_tool_provider


CALL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SENSITIVE_KEYS = frozenset({
    "access_token",
    "api_key",
    "app_secret",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
})
ACTIVE_RUN_STATUSES = frozenset({
    Run.Status.RUNNING,
    Run.Status.STREAMING,
})
TOOL_SOURCES = frozenset({
    "model_tool",
    "decision_gate",
    "decision_rank",
})


class ToolSnapshotError(ValueError):
    """Raised with a stable code and HTTP status for Tool snapshot requests."""

    def __init__(self, code, status_code):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@transaction.atomic
def create_tool_execution_snapshot(
    run_uuid,
    lensnode,
    connection_uuid,
    tool_key,
    call_id,
    arguments,
    source="model_tool",
):
    """Authorize one frozen Tool binding and persist its execution snapshot."""

    run = (
        Run.objects.select_for_update(of=("self",))
        .select_related("execution", "session__user")
        .filter(uuid=run_uuid)
        .first()
    )
    if run is None:
        raise ToolSnapshotError("RUN_NOT_FOUND", 404)
    if run.lensnode_id != lensnode.pk:
        raise ToolSnapshotError("RUN_NODE_MISMATCH", 403)
    if run.status not in ACTIVE_RUN_STATUSES:
        raise ToolSnapshotError("RUN_NOT_ACTIVE", 409)
    if not isinstance(call_id, str) or not CALL_ID_PATTERN.fullmatch(call_id):
        raise ToolSnapshotError("TOOL_CALL_ID_INVALID", 400)
    if not isinstance(tool_key, str) or not tool_key:
        raise ToolSnapshotError("TOOL_NOT_AUTHORIZED", 403)
    source = str(source or "model_tool")
    if source not in TOOL_SOURCES:
        raise ToolSnapshotError("TOOL_REQUEST_INVALID", 400)

    frozen_plugin, frozen_tool = _frozen_tool(
        run.execution.loaded_plugins,
        connection_uuid,
        tool_key,
    )
    if frozen_plugin is None or frozen_tool is None:
        raise ToolSnapshotError("TOOL_NOT_AUTHORIZED", 403)
    connection = (
        Connection.objects.select_related("secret_version__material")
        .filter(uuid=connection_uuid)
        .first()
    )
    if connection is None:
        raise ToolSnapshotError("CONNECTION_UNAVAILABLE", 409)
    if connection.plugin_key != frozen_plugin.get("plugin_key"):
        raise ToolSnapshotError("TOOL_NOT_AUTHORIZED", 403)
    if connection.status != Connection.Status.ACTIVE:
        raise ToolSnapshotError("CONNECTION_DISABLED", 409)
    secret_version = connection.secret_version
    if secret_version is None or secret_version.status != "active":
        raise ToolSnapshotError("SECRET_VERSION_DISABLED", 409)
    if secret_version.material.status != "active":
        raise ToolSnapshotError("SECRET_MATERIAL_DISABLED", 409)

    _validate_frozen_tool(frozen_tool, arguments)
    try:
        provider = get_tool_provider(
            connection.plugin_key,
            frozen_plugin.get("plugin_version"),
        )
        endpoint, normalized_arguments = provider.validate_request(
            connection.endpoint,
            connection.allowed_scope,
            tool_key,
            arguments,
        )
    except ToolProviderError as exc:
        raise ToolSnapshotError("TOOL_ARGUMENTS_INVALID", 400) from exc
    _reject_sensitive_values(connection.config)
    _reject_sensitive_values(connection.allowed_scope)
    resolved_config = {
        "endpoint": endpoint,
        "connection_config": deepcopy(connection.config),
        "allowed_scope": deepcopy(connection.allowed_scope),
        "tool": {
            "key": tool_key,
            "capability": frozen_tool.get("capability"),
            "side_effect": frozen_tool.get("side_effect"),
        },
        "arguments": normalized_arguments,
    }
    if source != "model_tool":
        resolved_config["source"] = source
    existing = ExecutionSnapshot.objects.filter(
        run=run,
        invocation_id=call_id,
    ).first()
    if existing is not None:
        _validate_idempotent_snapshot(
            existing,
            connection,
            tool_key,
            resolved_config,
        )
        return existing, False
    snapshot = ExecutionSnapshot.objects.create(
        kind=ExecutionSnapshot.Kind.TOOL_INVOKE,
        connection=connection,
        run=run,
        secret_version=secret_version,
        plugin_key=frozen_plugin["plugin_key"],
        plugin_version=frozen_plugin["plugin_version"],
        protocol_version=frozen_plugin["protocol_version"],
        tool_key=tool_key,
        invocation_id=call_id,
        resolved_config=resolved_config,
    )
    create_invocation_audit(
        snapshot,
        lensnode=lensnode,
        actor=run.session.user,
        capability=frozen_tool.get("capability") or "",
        resource_summary=_resource_summary(normalized_arguments),
    )
    return snapshot, True


def _resource_summary(arguments):
    """Return resource identities without storing Tool search or file input."""

    summary = {
        key: arguments[key]
        for key in ("repository", "project", "issue_key")
        if arguments.get(key)
    }
    for key in ("repositories", "projects"):
        values = arguments.get(key)
        if isinstance(values, list):
            summary[key] = values[:50]
    return summary


def _frozen_tool(loaded_plugins, connection_uuid, tool_key):
    """Return the frozen Plugin and Tool declarations for one Run."""

    connection_id = str(connection_uuid or "")
    for plugin in loaded_plugins or []:
        if (
            not isinstance(plugin, dict)
            or str(plugin.get("connection_uuid") or "") != connection_id
        ):
            continue
        for tool in plugin.get("tools") or []:
            if isinstance(tool, dict) and tool.get("key") == tool_key:
                return plugin, tool
    return None, None


def _validate_frozen_tool(tool, arguments):
    """Validate model arguments against the frozen bounded JSON schema."""

    if (
        tool.get("side_effect") != "none"
        or tool.get("capability") not in READ_ONLY_TOOL_CAPABILITIES
    ):
        raise ToolSnapshotError("TOOL_NOT_AUTHORIZED", 403)
    schema = tool.get("input_schema") or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    if not isinstance(arguments, dict):
        raise ToolSnapshotError("TOOL_ARGUMENTS_INVALID", 400)
    if set(arguments).difference(properties) or any(
        name not in arguments for name in required
    ):
        raise ToolSnapshotError("TOOL_ARGUMENTS_INVALID", 400)
    for name, value in arguments.items():
        expected = (properties.get(name) or {}).get("type")
        if expected == "string" and not isinstance(value, str):
            raise ToolSnapshotError("TOOL_ARGUMENTS_INVALID", 400)
        if expected == "integer" and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ToolSnapshotError("TOOL_ARGUMENTS_INVALID", 400)


def _validate_idempotent_snapshot(
    snapshot,
    connection,
    tool_key,
    resolved_config,
):
    """Reject reuse of one Tool call identity for different work."""

    if (
        snapshot.kind != ExecutionSnapshot.Kind.TOOL_INVOKE
        or snapshot.connection_id != connection.pk
        or snapshot.tool_key != tool_key
        or snapshot.resolved_config != resolved_config
    ):
        raise ToolSnapshotError("TOOL_CALL_CONFLICT", 409)


def _reject_sensitive_values(value):
    """Reject credential-shaped keys from persisted non-secret config."""

    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                raise ToolSnapshotError("CONNECTION_CONFIG_UNSAFE", 409)
            _reject_sensitive_values(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_values(nested)
