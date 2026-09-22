"""Model-facing tools backed by installed Plugin runtime packages."""

import json
import threading
import time

import httpx

from .plugin_package_loader import (
    PluginPackageLoadError,
    load_runtime_contract,
)
from .plugin_http import PluginHttpClientError
from .plugin_runtime import (
    PluginRuntimeError,
    acquire_plugin_lease,
    create_plugin_tool_snapshot,
    fetch_plugin_snapshot,
    retrieve_plugin_material,
)


class PluginToolError(RuntimeError):
    """Raised when a frozen Plugin Tool cannot be safely registered."""


RESULT_MAX_BYTES = 900_000


def build_plugin_tools(
    command,
    config,
    http_client,
    emit_event=None,
    plugin_http_pool=None,
):
    """Build tools from installed runtimes in frozen Plugin bindings."""

    loaded_plugins = command.get("loaded_plugins") or []
    if not loaded_plugins:
        return []
    if not isinstance(loaded_plugins, list):
        raise PluginToolError("PLUGIN_BINDINGS_INVALID")
    tools = []
    seen_keys = set()
    result_cache = {}
    result_cache_lock = threading.Lock()
    for binding in loaded_plugins:
        if not isinstance(binding, dict):
            raise PluginToolError("PLUGIN_BINDING_INVALID")
        plugin_key = str(binding.get("plugin_key") or "")
        plugin_version = str(binding.get("plugin_version") or "")
        if binding.get("protocol_version") != 1:
            raise PluginToolError("PLUGIN_RUNTIME_UNSUPPORTED")
        connection_uuid = str(binding.get("connection_uuid") or "")
        if not connection_uuid:
            raise PluginToolError("PLUGIN_CONNECTION_REQUIRED")
        try:
            contract = load_runtime_contract(plugin_key, plugin_version)
        except PluginPackageLoadError as exc:
            raise PluginToolError("PLUGIN_RUNTIME_UNSUPPORTED") from exc
        for definition in binding.get("tools") or []:
            tool_key = str(
                definition.get("key")
                if isinstance(definition, dict)
                else ""
            )
            if not tool_key:
                raise PluginToolError("PLUGIN_TOOL_INVALID")
            if definition.get("exposure") == "internal":
                continue
            capability_family = str(
                definition.get("capability_family") or "plugin"
            )
            if capability_family != "plugin":
                raise PluginToolError("PLUGIN_TOOL_INVALID")
            if tool_key in seen_keys:
                raise PluginToolError("PLUGIN_TOOL_NAME_CONFLICT")

            def executor(
                selected_tool_key,
                arguments,
                runtime,
                *,
                _contract=contract,
                _connection_uuid=connection_uuid,
                _plugin_key=plugin_key,
                _plugin_version=plugin_version,
            ):
                return _execute_plugin_tool(
                    command,
                    config,
                    http_client,
                    _connection_uuid,
                    _plugin_key,
                    _plugin_version,
                    selected_tool_key,
                    runtime,
                    arguments,
                    _contract.execute_tool,
                    emit_event,
                    plugin_http_pool=plugin_http_pool,
                    http_origins=_contract.http_origins,
                    http_post_paths=_contract.http_post_paths,
                    result_cache=result_cache,
                    result_cache_lock=result_cache_lock,
                )

            if not callable(contract.build_tool):
                raise PluginToolError("PLUGIN_TOOL_INVALID")
            try:
                registered = contract.build_tool(definition, executor)
            except Exception as exc:
                raise PluginToolError("PLUGIN_TOOL_INVALID") from exc
            if getattr(registered, "name", None) != tool_key:
                raise PluginToolError("PLUGIN_TOOL_INVALID")
            registered.metadata = {
                **(getattr(registered, "metadata", None) or {}),
                "capability_family": capability_family,
                "plugin_key": plugin_key,
                "plugin_version": plugin_version,
                "capability": str(
                    definition.get("capability") or ""
                ),
            }
            seen_keys.add(tool_key)
            tools.append(registered)
    return tools


def _execute_plugin_tool(
    command,
    config,
    http_client,
    connection_uuid,
    plugin_key,
    plugin_version,
    tool_key,
    runtime,
    arguments,
    handler,
    emit_event,
    plugin_http_pool=None,
    http_origins=None,
    http_post_paths=None,
    result_cache=None,
    result_cache_lock=None,
    source="model_tool",
):
    """Authorize, lease, and execute one Tool without exposing secret."""

    call_id = str(getattr(runtime, "tool_call_id", "") or "")
    cache_key = _plugin_result_cache_key(
        plugin_key,
        plugin_version,
        connection_uuid,
        tool_key,
        arguments,
    )
    if result_cache is not None and result_cache_lock is not None:
        with result_cache_lock:
            cached = result_cache.get(cache_key)
        if cached is not None:
            _emit(
                emit_event,
                "tool.plugin.cache_hit",
                {"plugin": plugin_key, "tool": tool_key},
            )
            return cached
    started = time.monotonic()
    _emit(
        emit_event,
        "tool.plugin.start",
        {
            "plugin": plugin_key,
            "tool": tool_key,
            "invocation_id": call_id,
        },
    )
    material = None
    error = ""
    try:
        if not call_id:
            raise PluginRuntimeError("PLUGIN_TOOL_CALL_ID_REQUIRED")
        snapshot = create_plugin_tool_snapshot(
            http_client,
            config.ai_gateway_url,
            config.token,
            command.get("run_uuid"),
            connection_uuid,
            tool_key,
            call_id,
            arguments,
            source=source,
        )
        snapshot_uuid = snapshot["snapshot_uuid"]
        resolved = fetch_plugin_snapshot(
            http_client,
            config.ai_gateway_url,
            config.token,
            snapshot_uuid,
        )
        normalized_arguments, endpoint, connection_config = _validate_snapshot(
            resolved,
            command.get("run_uuid"),
            plugin_key,
            plugin_version,
            tool_key,
            call_id,
        )
        lease = acquire_plugin_lease(
            http_client,
            config.ai_gateway_url,
            config.token,
            snapshot_uuid,
        )
        material = retrieve_plugin_material(
            http_client,
            config.ai_gateway_url,
            config.token,
            lease["lease_uuid"],
        )
        if (
            material.get("plugin_key") != plugin_key
            or material.get("endpoint", "").rstrip("/")
            != endpoint.rstrip("/")
        ):
            raise PluginRuntimeError("PLUGIN_MATERIAL_MISMATCH")
        provider_client = http_client
        if plugin_http_pool is not None:
            origins = (
                http_origins(endpoint)
                if callable(http_origins)
                else (endpoint,)
            )
            post_paths = (
                http_post_paths(endpoint)
                if callable(http_post_paths)
                else ()
            )
            provider_client = plugin_http_pool.bind(
                plugin_key,
                connection_uuid,
                origins,
                post_paths,
            )
        result = handler(
            tool_key,
            provider_client,
            normalized_arguments,
            material["value"],
            endpoint,
            connection_config,
        )
    except PluginRuntimeError as exc:
        error = str(exc)
        result = {"ok": False, "error": error}
    except (httpx.HTTPError, PluginHttpClientError):
        error = "PLUGIN_REQUEST_FAILED"
        result = {"ok": False, "error": error}
    except Exception:
        error = "PLUGIN_EXECUTION_FAILED"
        result = {"ok": False, "error": error}
    finally:
        if material is not None:
            material["value"] = ""
        _emit(
            emit_event,
            "tool.plugin.done",
            {
                "plugin": plugin_key,
                "tool": tool_key,
                "invocation_id": call_id,
                "ok": not error,
                "error": error,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
    encoded = _json(result)
    if (
        error == ""
        and result_cache is not None
        and result_cache_lock is not None
    ):
        with result_cache_lock:
            result_cache[cache_key] = encoded
    return encoded


def _plugin_result_cache_key(
    plugin_key,
    plugin_version,
    connection_uuid,
    tool_key,
    arguments,
):
    """Build a stable per-run key for identical read-only Plugin calls."""

    return (
        str(plugin_key),
        str(plugin_version),
        str(connection_uuid),
        str(tool_key),
        json.dumps(
            arguments or {},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    )


def _validate_snapshot(
    snapshot,
    run_uuid,
    plugin_key,
    plugin_version,
    tool_key,
    call_id,
):
    """Return frozen Plugin inputs from the matching snapshot."""

    resolved_config = snapshot.get("resolved_config")
    if (
        str(snapshot.get("run_uuid") or "") != str(run_uuid or "")
        or snapshot.get("plugin_key") != plugin_key
        or snapshot.get("plugin_version") != plugin_version
        or snapshot.get("tool_key") != tool_key
        or str(snapshot.get("invocation_id") or "") != call_id
        or not isinstance(resolved_config, dict)
        or not isinstance(resolved_config.get("arguments"), dict)
    ):
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    endpoint = str(resolved_config.get("endpoint") or "").rstrip("/")
    connection_config = resolved_config.get("connection_config") or {}
    allowed_scope = resolved_config.get("allowed_scope")
    if (
        not endpoint
        or not isinstance(connection_config, dict)
        or not isinstance(allowed_scope, dict)
    ):
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    connection_config = dict(connection_config)
    connection_config["__allowed_scope"] = allowed_scope
    return resolved_config["arguments"], endpoint, connection_config


def _json(value):
    """Serialize a bounded provider result for model context."""

    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) <= RESULT_MAX_BYTES:
        return payload
    return json.dumps(
        {"ok": False, "error": "PLUGIN_RESULT_TOO_LARGE"},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _emit(callback, event_type, payload):
    """Emit an optional Plugin audit event."""

    if callback is not None:
        callback(event_type, payload)
