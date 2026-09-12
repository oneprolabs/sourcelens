"""Feishu LensNode datasource runtime entrypoint."""

import json
import re
from urllib.parse import urlsplit

from lensnode.plugin_runtime import PluginRuntimeError

PLUGIN_API_VERSION = 1
PLUGIN_KEY = "feishu"
PLUGIN_VERSION = "1.0.0"
FEISHU_API_URL = "https://open.feishu.cn"
RESOURCE_KINDS = frozenset(
    {"bitable", "docx", "folder", "sheet", "slides", "wiki"}
)
TOOL_KEYS = frozenset({"feishu_get_document"})
FEISHU_MAX_RESPONSE_BYTES = 900_000
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,255}$")
DATASOURCE_CONFIG_KEYS = frozenset(
    {
        "delete_missing",
        "incremental",
        "max_depth",
        "recursive",
        "resource_urls",
        "resources",
    }
)


def http_origins(endpoint):
    """Return the fixed Feishu Open Platform API origin."""

    _endpoint(endpoint)
    return (FEISHU_API_URL,)


def build_tool(definition, executor):
    """Build a read-only Feishu document tool."""
    key = definition.get("key") if isinstance(definition, dict) else None
    if (
        key not in TOOL_KEYS
        or definition.get("side_effect") != "none"
        or definition.get("capability") != "document.read"
    ):
        raise PluginRuntimeError("PLUGIN_TOOL_UNSUPPORTED")
    from langchain.tools import ToolRuntime, tool

    def invoke(runtime: ToolRuntime, **arguments):
        return executor(key, arguments, runtime)

    return tool(
        key,
        description=definition["description"],
        args_schema=definition["input_schema"],
    )(invoke)


def execute_tool(key, client, arguments, secret, endpoint, config):
    """Read an authorized Feishu document through the Open Platform API."""
    if key not in TOOL_KEYS or _endpoint(endpoint) != FEISHU_API_URL:
        raise PluginRuntimeError("PLUGIN_TOOL_UNSUPPORTED")
    token = arguments.get("token") if isinstance(arguments, dict) else None
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    app_id = config.get("app_id") if isinstance(config, dict) else None
    if not isinstance(app_id, str) or not app_id:
        raise PluginRuntimeError("PLUGIN_MATERIAL_MISMATCH")
    app_secret = secret.get("value") if isinstance(secret, dict) else secret
    if not isinstance(app_secret, str) or not app_secret:
        raise PluginRuntimeError("PLUGIN_MATERIAL_MISMATCH")
    tenant_token = _tenant_access_token(client, app_id, app_secret)
    payload = _request_json(
        client,
        "GET",
        "/open-apis/docx/v1/documents/" + token + "/raw_content",
        {"Authorization": "Bearer " + tenant_token},
    )
    data = payload.get("data")
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        raise PluginRuntimeError("FEISHU_RESPONSE_INVALID")
    return {"token": token, "content": content}


def _tenant_access_token(client, app_id, app_secret):
    """Exchange the saved app credentials for a short-lived tenant token."""

    payload = _request_json(
        client,
        "POST",
        FEISHU_API_URL + "/open-apis/auth/v3/tenant_access_token/internal",
        {"Content-Type": "application/json"},
        json_body={"app_id": app_id, "app_secret": app_secret},
        error_code="FEISHU_ACCESS_DENIED",
    )
    token = payload.get("tenant_access_token")
    if (
        payload.get("code") not in (None, 0)
        or not isinstance(token, str)
        or not token
    ):
        raise PluginRuntimeError("FEISHU_ACCESS_DENIED")
    return token


def _request_json(
    client,
    method,
    url,
    headers,
    *,
    json_body=None,
    error_code="FEISHU_DOCUMENT_READ_FAILED",
):
    """Return one bounded JSON response from the host-managed client."""

    if client is None:
        raise PluginRuntimeError("PLUGIN_HTTP_CLIENT_REQUIRED")
    if not url.startswith("https://"):
        url = FEISHU_API_URL + url
    try:
        with client.stream(
            method,
            url,
            headers=headers,
            json=json_body,
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                raise PluginRuntimeError("FEISHU_REDIRECT_REJECTED")
            if response.status_code >= 400:
                raise PluginRuntimeError(error_code)
            body = bytearray()
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > FEISHU_MAX_RESPONSE_BYTES:
                    raise PluginRuntimeError("FEISHU_RESPONSE_TOO_LARGE")
                body.extend(chunk)
    except PluginRuntimeError:
        raise
    except Exception as exc:
        raise PluginRuntimeError("FEISHU_REQUEST_FAILED") from exc
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise PluginRuntimeError("FEISHU_RESPONSE_INVALID") from exc
    if not isinstance(payload, dict):
        raise PluginRuntimeError("FEISHU_RESPONSE_INVALID")
    return payload


def build_datasource_command(snapshot, material, trigger):
    """Build one mixed-resource Feishu synchronization command."""

    if (
        not isinstance(snapshot, dict)
        or snapshot.get("plugin_key") != PLUGIN_KEY
    ):
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    resolved = snapshot.get("resolved_config")
    if not isinstance(resolved, dict):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    endpoint = _endpoint(resolved.get("endpoint"))
    _material(material, endpoint)
    connection = resolved.get("connection_config")
    datasource = resolved.get("datasource_config")
    app_id = _app_id(connection)
    config = _datasource_config(datasource)
    return {
        "source_type": "feishu",
        "datasource_uuid": snapshot.get("datasource_uuid"),
        "target_path": resolved.get("target_path"),
        "sync_policy": resolved.get("sync_policy") or {},
        "trigger": trigger,
        "config": {
            "sync_mode": "resource_list",
            "resources": config["resources"],
            "recursive": config["recursive"],
            "max_depth": config["max_depth"],
            "feishu_incremental": config["incremental"],
            "feishu_delete_missing": config["delete_missing"],
            "app_id": app_id,
            "app_secret": material["value"],
        },
    }


def sync_datasource(command, workspace_path, emit, execute):
    """Execute the Feishu datasource policy through the shared executor."""

    return execute(command, workspace_path, emit)


def _endpoint(value):
    """Require the fixed Feishu API origin from the snapshot."""

    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "open.feishu.cn"
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PluginRuntimeError("PLUGIN_SNAPSHOT_MISMATCH")
    return FEISHU_API_URL


def _material(material, endpoint):
    """Require secret material bound to this Plugin and endpoint."""

    if (
        not isinstance(material, dict)
        or material.get("plugin_key") != PLUGIN_KEY
        or str(material.get("endpoint") or "").rstrip("/") != endpoint
        or not isinstance(material.get("value"), str)
        or not material["value"]
    ):
        raise PluginRuntimeError("PLUGIN_MATERIAL_MISMATCH")


def _app_id(value):
    """Return the frozen non-secret App ID."""

    if not isinstance(value, dict) or set(value) != {"app_id"}:
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    app_id = value.get("app_id")
    normalized = app_id.strip() if isinstance(app_id, str) else ""
    if (
        not isinstance(app_id, str)
        or not normalized
        or len(normalized) > 255
        or any(character.isspace() for character in normalized)
    ):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    return normalized


def _datasource_config(value):
    """Revalidate the normalized mixed-resource configuration."""

    if not isinstance(value, dict) or set(value) != DATASOURCE_CONFIG_KEYS:
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    resources = value.get("resources")
    if not isinstance(resources, list) or not 1 <= len(resources) <= 100:
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    normalized = []
    identities = set()
    for resource in resources:
        if not isinstance(resource, dict) or set(resource) != {
            "kind",
            "token",
        }:
            raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
        kind = resource.get("kind")
        token = resource.get("token")
        if (
            kind not in RESOURCE_KINDS
            or not isinstance(token, str)
            or not TOKEN_PATTERN.fullmatch(token)
            or token in identities
        ):
            raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
        identities.add(token)
        normalized.append({"kind": kind, "token": token})
    recursive = _boolean(value.get("recursive"))
    incremental = _boolean(value.get("incremental"))
    delete_missing = _boolean(value.get("delete_missing"))
    max_depth = value.get("max_depth")
    if (
        isinstance(max_depth, bool)
        or not isinstance(max_depth, int)
        or not 1 <= max_depth <= 50
    ):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    return {
        "resources": normalized,
        "recursive": recursive,
        "max_depth": max_depth,
        "incremental": incremental,
        "delete_missing": delete_missing,
    }


def _boolean(value):
    """Require one normalized boolean option."""

    if not isinstance(value, bool):
        raise PluginRuntimeError("PLUGIN_CONFIG_INVALID")
    return value
