"""Control-plane helpers for trusted plugin execution."""

from urllib.parse import urlsplit, urlunsplit

import httpx


class PluginRuntimeError(RuntimeError):
    """Raised when a plugin runtime lease cannot be acquired."""


def lease_url(ai_gateway_url):
    """Build the plugin lease endpoint from the configured API URL."""

    parsed = urlsplit(str(ai_gateway_url or ""))
    path = parsed.path.rstrip("/")
    marker = "/api/lens/lensnode/ai-gateway"
    if marker in path:
        path = path.split(marker, 1)[0] + "/api/lens/plugin-runtime/leases"
    else:
        path = "/api/lens/plugin-runtime/leases"
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/", "", ""))


def tool_snapshot_url(ai_gateway_url):
    """Build the Tool snapshot endpoint from the configured API URL."""

    return (
        lease_url(ai_gateway_url).rsplit("/leases/", 1)[0]
        + "/tool-snapshots/"
    )


def create_plugin_tool_snapshot(
    client,
    ai_gateway_url,
    token,
    run_uuid,
    connection_uuid,
    tool_key,
    call_id,
    arguments,
    source="model_tool",
):
    """Authorize one model Tool call and return its opaque snapshot."""

    body = {
        "run_uuid": str(run_uuid),
        "connection_uuid": str(connection_uuid),
        "tool_key": str(tool_key),
        "call_id": str(call_id),
        "arguments": arguments,
    }
    if source and source != "model_tool":
        body["source"] = str(source)
    response = client.post(
        tool_snapshot_url(ai_gateway_url),
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.is_error:
        raise PluginRuntimeError("PLUGIN_TOOL_SNAPSHOT_REQUEST_FAILED")
    payload = _json_object(
        response,
        "PLUGIN_TOOL_SNAPSHOT_INVALID_RESPONSE",
    )
    expected = {
        "run_uuid": str(run_uuid),
        "connection_uuid": str(connection_uuid),
        "tool_key": str(tool_key),
        "invocation_id": str(call_id),
    }
    if (
        not isinstance(payload, dict)
        or not payload.get("snapshot_uuid")
        or any(
            str(payload.get(key) or "") != value
            for key, value in expected.items()
        )
    ):
        raise PluginRuntimeError("PLUGIN_TOOL_SNAPSHOT_INVALID_RESPONSE")
    return payload


def acquire_plugin_lease(client, ai_gateway_url, token, snapshot_uuid):
    """Acquire an opaque, snapshot-bound lease without returning secrets."""

    if not snapshot_uuid:
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_REQUIRED")
    response = client.post(
        lease_url(ai_gateway_url),
        json={"snapshot_uuid": str(snapshot_uuid)},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.is_error:
        raise PluginRuntimeError("PLUGIN_LEASE_REQUEST_FAILED")
    payload = _json_object(response, "PLUGIN_LEASE_INVALID_RESPONSE")
    lease_uuid = payload.get("lease_uuid")
    if not lease_uuid:
        raise PluginRuntimeError("PLUGIN_LEASE_INVALID_RESPONSE")
    return {
        "lease_uuid": str(lease_uuid),
        "snapshot_uuid": str(payload.get("snapshot_uuid") or snapshot_uuid),
        "expires_at": payload.get("expires_at"),
    }


def retrieve_plugin_material(client, ai_gateway_url, token, lease_uuid):
    """Resolve lease-bound material in memory for the trusted plugin."""

    if not lease_uuid:
        raise PluginRuntimeError("PLUGIN_LEASE_REQUIRED")
    response = client.post(
        f"{lease_url(ai_gateway_url)}{lease_uuid}/material/",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.is_error:
        raise PluginRuntimeError("PLUGIN_MATERIAL_REQUEST_FAILED")
    payload = _json_object(response, "PLUGIN_MATERIAL_INVALID_RESPONSE")
    value = payload.get("value")
    if not isinstance(value, str) or not value:
        raise PluginRuntimeError("PLUGIN_MATERIAL_INVALID_RESPONSE")
    return {
        "plugin_key": str(payload.get("plugin_key") or ""),
        "endpoint": str(payload.get("endpoint") or ""),
        "value": value,
    }


def fetch_plugin_snapshot(client, ai_gateway_url, token, snapshot_uuid):
    """Fetch non-sensitive execution data for one snapshot."""

    if not snapshot_uuid:
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_REQUIRED")
    response = client.get(
        f"{lease_url(ai_gateway_url).rsplit('/leases/', 1)[0]}"
        f"/snapshots/{snapshot_uuid}/",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.is_error:
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_REQUEST_FAILED")
    payload = _json_object(response, "PLUGIN_SNAPSHOT_INVALID_RESPONSE")
    if not isinstance(payload.get("resolved_config"), dict):
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_INVALID_RESPONSE")
    return payload


def _json_object(response, error_code):
    """Return one JSON object from a trusted control-plane response."""

    try:
        payload = response.json()
    except ValueError as exc:
        raise PluginRuntimeError(error_code) from exc
    if not isinstance(payload, dict):
        raise PluginRuntimeError(error_code)
    data = payload.get("data")
    if isinstance(data, dict) and (
        "code" in payload or "message" in payload
    ):
        payload = data
    return payload
