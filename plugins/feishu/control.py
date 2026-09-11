"""Feishu Connection and datasource Provider implementation."""

import json
import re
import secrets
from urllib.parse import urlsplit
from urllib.parse import urlsplit, urlunsplit

import httpx

from lens.plugins.contracts import ToolProviderError
from lens.plugins.http import PluginHttpClientError
from lens.plugins.providers.base import (
    DatasourceProvider,
    DatasourceProviderError,
    PluginRequestContext,
    retry_after_seconds,
)

PLUGIN_API_VERSION = 1
PLUGIN_KEY = "feishu"
PLUGIN_VERSION = "1.0.0"
FEISHU_API_URL = "https://open.feishu.cn"
FEISHU_TOKEN_PATH = (
    "/open-apis/auth/v3/tenant_access_token/internal"
)
FEISHU_DRIVE_FILES_PATH = "/open-apis/drive/v1/files"
FEISHU_META_PATH = "/open-apis/drive/v1/metas/batch_query"
FEISHU_WIKI_NODE_PATH = "/open-apis/wiki/v2/spaces/get_node"
FEISHU_MAX_RESPONSE_BYTES = 128_000
FEISHU_MAX_RESOURCES = 100
FEISHU_MAX_DEPTH = 50
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,255}$")
RESOURCE_PATHS = {
    ("drive", "folder"): "folder",
    ("doc",): "docx",
    ("docs",): "docx",
    ("docx",): "docx",
    ("sheets",): "sheet",
    ("slides",): "slides",
    ("base",): "bitable",
    ("wiki",): "wiki",
}
ALLOWED_DATASOURCE_KEYS = frozenset(
    {
        "delete_missing",
        "incremental",
        "max_depth",
        "recursive",
        "resource_urls",
        "resources",
    }
)

REGISTRATION_URL = "https://accounts.feishu.cn/oauth/v1/app/registration"


def execute_rpc(method, params):
    """Dispatch Plugin-owned control RPC methods."""

    if method == "self_register.begin":
        return begin_self_registration()
    if method == "self_register.poll":
        return poll_self_registration((params or {}).get("device_code"))
    raise DatasourceProviderError("PLUGIN_RPC_METHOD_UNSUPPORTED")


def begin_self_registration(client=None):
    """Start Feishu user-owned application registration."""

    http_client = client or httpx.Client(timeout=15, follow_redirects=False)
    try:
        response = http_client.post(
            REGISTRATION_URL,
            data={
                "action": "begin",
                "archetype": "PersonalAgent",
                "auth_method": "client_secret",
                "request_user_info": "open_id tenant_brand",
            },
        )
        if response.status_code not in (200, 400):
            raise DatasourceProviderError("FEISHU_REGISTRATION_FAILED")
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DatasourceProviderError("FEISHU_REGISTRATION_FAILED") from exc
    uri = payload.get("verification_uri_complete")
    if not isinstance(payload.get("device_code"), str) or not isinstance(uri, str):
        raise DatasourceProviderError("FEISHU_REGISTRATION_RESPONSE_INVALID")
    parsed = urlsplit(uri)
    if parsed.scheme != "https" or parsed.hostname != "accounts.feishu.cn":
        raise DatasourceProviderError("FEISHU_REGISTRATION_RESPONSE_INVALID")
    return {
        "device_code": payload["device_code"],
        "verification_uri_complete": uri,
        "interval": min(60, max(5, int(payload.get("interval") or 5))),
        "expires_in": min(600, max(1, int(payload.get("expires_in") or payload.get("expire_in") or 600))),
    }


def poll_self_registration(device_code, client=None):
    """Poll one Feishu user-owned application registration attempt."""

    if not isinstance(device_code, str) or not device_code:
        raise DatasourceProviderError("FEISHU_DEVICE_CODE_INVALID")
    http_client = client or httpx.Client(timeout=15, follow_redirects=False)
    try:
        response = http_client.post(
            REGISTRATION_URL,
            data={"action": "poll", "device_code": device_code},
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DatasourceProviderError("FEISHU_REGISTRATION_FAILED") from exc
    states = {
        "authorization_pending": "pending", "slow_down": "slow_down",
        "access_denied": "denied", "expired_token": "expired",
        "invalid_grant": "expired",
    }
    if payload.get("error"):
        return {"status": states.get(payload["error"], "error")}
    app_id = payload.get("client_id")
    app_secret = payload.get("client_secret")
    if not isinstance(app_id, str) or not app_id.startswith("cli_") or not isinstance(app_secret, str):
        raise DatasourceProviderError("FEISHU_REGISTRATION_RESPONSE_INVALID")
    return {"status": "success", "app_id": app_id, "app_secret": app_secret}


class FeishuDatasourceProvider(DatasourceProvider):
    """Validate Feishu application and mixed resource configuration."""

    key = "feishu"
    requires_datasource_access_validation = True

    def http_origins(self, endpoint, connection_config=None):
        """Return the fixed Feishu Open Platform API origin."""

        self.validate_connection(endpoint, connection_config)
        return (FEISHU_API_URL,)

    def validate_datasource_source_type(self, source_type):
        """Bind the Provider to the Feishu datasource adapter."""

        if source_type != "feishu":
            raise DatasourceProviderError(
                "Feishu datasource source type must be feishu"
            )
        return source_type

    def validate_connection(self, endpoint, connection_config):
        """Validate an App ID and fix the API endpoint."""

        _app_id(connection_config)
        value = str(endpoint or "").strip().rstrip("/")
        if value and value != FEISHU_API_URL:
            raise DatasourceProviderError(
                "Feishu endpoint must be https://open.feishu.cn"
            )
        return FEISHU_API_URL

    def validate_connection_scope(self, connection_scope):
        """Reject resource lists on reusable Feishu Connections."""

        if connection_scope not in ({}, None):
            raise DatasourceProviderError(
                "Feishu connection scope must be empty"
            )
        return {}

    def validate_live_connection(
        self,
        secret,
        endpoint="",
        connection_config=None,
        client=None,
        request_context=None,
    ):
        """Validate App credentials by requesting a tenant token."""

        self.validate_connection(endpoint, connection_config)
        context = request_context or PluginRequestContext(
            timeout_seconds=15,
        )
        payload = context.run(
            lambda: _tenant_token_response(
                client,
                _app_id(connection_config),
                _secret_value(secret),
            )
        )
        expires_in = payload.get("expire")
        if isinstance(expires_in, bool) or not isinstance(expires_in, int):
            expires_in = 0
        return {
            "authenticated": True,
            "expires_in": max(0, min(expires_in, 86_400)),
        }

    def discover_resources(
        self,
        connection_scope,
        secret,
        endpoint="",
        connection_config=None,
        client=None,
        request_context=None,
    ):
        """Return no Connection-level resource enumeration."""

        self.validate_connection_scope(connection_scope)
        self.validate_connection(endpoint, connection_config)
        del secret, client, request_context
        return {"resources": {}}

    def validate_datasource_config(
        self,
        connection_scope,
        datasource_config,
    ):
        """Classify and normalize mixed Feishu resource URLs."""

        self.validate_connection_scope(connection_scope)
        if not isinstance(datasource_config, dict):
            raise DatasourceProviderError(
                "datasource config must be an object"
            )
        if set(datasource_config).difference(ALLOWED_DATASOURCE_KEYS):
            raise DatasourceProviderError(
                "datasource config contains unsupported fields"
            )
        resource_urls = datasource_config.get("resource_urls")
        if (
            not isinstance(resource_urls, list)
            or not resource_urls
            or len(resource_urls) > FEISHU_MAX_RESOURCES
        ):
            raise DatasourceProviderError(
                "resource_urls must contain 1 through 100 items"
            )
        canonical_urls = []
        resources = []
        identities = {}
        for value in resource_urls:
            canonical_url, resource = _resource_url(value)
            previous_kind = identities.get(resource["token"])
            if previous_kind and previous_kind != resource["kind"]:
                raise DatasourceProviderError(
                    "resource token has conflicting types"
                )
            if previous_kind:
                continue
            identities[resource["token"]] = resource["kind"]
            canonical_urls.append(canonical_url)
            resources.append(resource)
        return {
            "resource_urls": canonical_urls,
            "resources": resources,
            "recursive": _boolean(
                datasource_config.get("recursive", True),
                "recursive",
            ),
            "max_depth": _integer(
                datasource_config.get("max_depth", 10),
                "max_depth",
                1,
                FEISHU_MAX_DEPTH,
            ),
            "incremental": _boolean(
                datasource_config.get("incremental", True),
                "incremental",
            ),
            "delete_missing": _boolean(
                datasource_config.get("delete_missing", False),
                "delete_missing",
            ),
        }

    def validate_datasource_access(
        self,
        secret,
        datasource_config,
        endpoint="",
        connection_config=None,
        client=None,
        request_context=None,
    ):
        """Verify that the App can read every configured resource URL."""

        self.validate_connection(endpoint, connection_config)
        config = self.validate_datasource_config({}, datasource_config)
        context = request_context or PluginRequestContext(
            max_concurrency=5,
            timeout_seconds=15,
            deadline_seconds=60,
        )
        payload = context.run(
            lambda: _tenant_token_response(
                client,
                _app_id(connection_config),
                _secret_value(secret),
            )
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f'Bearer {payload["tenant_access_token"]}',
            "User-Agent": "SourceLens-Control-Plane",
        }
        resource_by_url = dict(
            zip(config["resource_urls"], config["resources"])
        )
        successes, warnings = context.parallel_map(
            config["resource_urls"],
            lambda url: _validate_resource_access(
                client,
                url,
                resource_by_url[url],
                headers,
            ),
            "Feishu resource URL",
        )
        successful_urls = {item["url"] for item in successes}
        failures = {
            item["resource"]: item["code"] for item in warnings
        }
        resources = []
        for url in config["resource_urls"]:
            resource = resource_by_url[url]
            item = {
                "url": url,
                "kind": resource["kind"],
                "accessible": url in successful_urls,
            }
            if not item["accessible"]:
                item["error"] = failures.get(
                    url,
                    "FEISHU_RESOURCE_ACCESS_DENIED",
                )
            resources.append(item)
        return {
            "valid": all(item["accessible"] for item in resources),
            "resources": resources,
        }


class FeishuToolProvider:
    """Reject model Tool calls for the datasource-only Plugin."""

    key = "feishu"

    def validate_request(self, endpoint, allowed_scope, tool_key, arguments):
        """Reject every Tool request because none are declared."""

        del endpoint, allowed_scope, tool_key, arguments
        raise ToolProviderError("tool is unsupported")


def _app_id(connection_config):
    """Return one bounded non-secret Feishu App ID."""

    if not isinstance(connection_config, dict) or set(
        connection_config
    ) != {"app_id"}:
        raise DatasourceProviderError(
            "Feishu connection config requires only app_id"
        )
    app_id = connection_config.get("app_id")
    if (
        not isinstance(app_id, str)
        or not app_id.strip()
        or len(app_id.strip()) > 255
        or any(character.isspace() for character in app_id.strip())
    ):
        raise DatasourceProviderError("Feishu App ID is invalid")
    return app_id.strip()


def _secret_value(value):
    """Return a non-empty App Secret without persisting it."""

    if not isinstance(value, str) or not value:
        raise DatasourceProviderError("FEISHU_SECRET_UNAVAILABLE")
    return value


def _resource_url(value):
    """Return a canonical Feishu URL and typed resource identity."""

    if not isinstance(value, str) or len(value) > 2000:
        raise DatasourceProviderError("Feishu resource URL is invalid")
    try:
        parsed = urlsplit(value.strip())
        parsed.port
    except ValueError as exc:
        raise DatasourceProviderError(
            "Feishu resource URL is invalid"
        ) from exc
    hostname = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme.lower() != "https"
        or not hostname.endswith(".feishu.cn")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or len(parts) < 2
    ):
        raise DatasourceProviderError("Feishu resource URL is invalid")
    prefix = tuple(parts[:-1])
    kind = RESOURCE_PATHS.get(prefix)
    token = parts[-1]
    if kind is None or not TOKEN_PATTERN.fullmatch(token):
        raise DatasourceProviderError(
            "Feishu resource URL type is unsupported"
        )
    canonical = urlunsplit(
        ("https", hostname, f"/{'/'.join(parts)}", "", "")
    )
    return canonical, {"kind": kind, "token": token}


def _boolean(value, field):
    """Return a strict boolean datasource option."""

    if not isinstance(value, bool):
        raise DatasourceProviderError(f"{field} must be a boolean")
    return value


def _integer(value, field, minimum, maximum):
    """Return a bounded integer datasource option."""

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise DatasourceProviderError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return value


def _tenant_token_response(client, app_id, app_secret):
    """Request and validate one bounded tenant-token response."""

    if client is None:
        raise DatasourceProviderError("PLUGIN_HTTP_CLIENT_REQUIRED")
    try:
        with client.stream(
            "POST",
            f"{FEISHU_API_URL}{FEISHU_TOKEN_PATH}",
            json={"app_id": app_id, "app_secret": app_secret},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "SourceLens-Control-Plane",
            },
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                raise DatasourceProviderError("FEISHU_REDIRECT_REJECTED")
            if response.status_code >= 400:
                raise DatasourceProviderError("FEISHU_ACCESS_DENIED")
            body = bytearray()
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > FEISHU_MAX_RESPONSE_BYTES:
                    raise DatasourceProviderError(
                        "FEISHU_RESPONSE_TOO_LARGE"
                    )
                body.extend(chunk)
    except DatasourceProviderError:
        raise
    except httpx.HTTPError as exc:
        raise DatasourceProviderError("FEISHU_REQUEST_FAILED") from exc
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise DatasourceProviderError("FEISHU_RESPONSE_INVALID") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("code") not in (None, 0)
        or not isinstance(payload.get("tenant_access_token"), str)
        or not payload["tenant_access_token"]
    ):
        raise DatasourceProviderError("FEISHU_ACCESS_DENIED")
    return payload


def _validate_resource_access(client, url, resource, headers):
    """Perform one lightweight read request for a Feishu resource."""

    kind = resource["kind"]
    token = resource["token"]
    if kind == "folder":
        payload = _feishu_json(
            client,
            "GET",
            FEISHU_DRIVE_FILES_PATH,
            headers,
            params={"folder_token": token, "page_size": 1},
        )
        data = payload.get("data")
        files = (data.get("files") or []) if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(files, list):
            raise DatasourceProviderError("FEISHU_RESPONSE_INVALID")
    elif kind == "wiki":
        payload = _feishu_json(
            client,
            "GET",
            FEISHU_WIKI_NODE_PATH,
            headers,
            params={"token": token},
        )
        node = (payload.get("data") or {}).get("node")
        if not isinstance(node, dict) or not node.get("obj_token"):
            raise DatasourceProviderError(
                "FEISHU_RESOURCE_ACCESS_DENIED"
            )
    else:
        payload = _feishu_json(
            client,
            "POST",
            FEISHU_META_PATH,
            {**headers, "Content-Type": "application/json"},
            json_body={
                "request_docs": [
                    {
                        "doc_token": token,
                        "doc_type": _metadata_doc_type(kind, token),
                    }
                ],
                "with_url": False,
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DatasourceProviderError("FEISHU_RESPONSE_INVALID")
        metas = data.get("metas") or []
        failed = data.get("failed_list") or []
        if not isinstance(metas, list) or not isinstance(failed, list):
            raise DatasourceProviderError("FEISHU_RESPONSE_INVALID")
        if not any(_metadata_token(item) == token for item in metas):
            raise DatasourceProviderError(
                "FEISHU_RESOURCE_ACCESS_DENIED"
            )
    return {"url": url}


def _metadata_doc_type(kind, token):
    """Return the Drive metadata type for one supported URL kind."""

    if kind == "docx" and token.startswith("doccn"):
        return "doc"
    return kind


def _metadata_token(value):
    """Return the requested token from one metadata result."""

    if not isinstance(value, dict):
        return ""
    request_info = value.get("request_doc_info")
    return value.get("doc_token") or (
        request_info.get("doc_token")
        if isinstance(request_info, dict)
        else ""
    )


def _feishu_json(
    client,
    method,
    path,
    headers,
    *,
    params=None,
    json_body=None,
):
    """Return one bounded Feishu API response without leaking its body."""

    if client is None:
        raise DatasourceProviderError("PLUGIN_HTTP_CLIENT_REQUIRED")
    try:
        with client.stream(
            method,
            f"{FEISHU_API_URL}{path}",
            params=params,
            json=json_body,
            headers=headers,
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                raise DatasourceProviderError("FEISHU_REDIRECT_REJECTED")
            if response.status_code == 429:
                raise DatasourceProviderError(
                    "FEISHU_RESOURCE_RATE_LIMITED",
                    retry_after=retry_after_seconds(
                        response.headers.get("Retry-After")
                    ),
                )
            if response.status_code >= 500:
                raise DatasourceProviderError(
                    "FEISHU_RESOURCE_REQUEST_FAILED"
                )
            if response.status_code >= 400:
                raise DatasourceProviderError(
                    "FEISHU_RESOURCE_ACCESS_DENIED"
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > FEISHU_MAX_RESPONSE_BYTES:
                    raise DatasourceProviderError(
                        "FEISHU_RESPONSE_TOO_LARGE"
                    )
                body.extend(chunk)
    except DatasourceProviderError:
        raise
    except (httpx.HTTPError, PluginHttpClientError) as exc:
        raise DatasourceProviderError(
            "FEISHU_RESOURCE_REQUEST_FAILED"
        ) from exc
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise DatasourceProviderError("FEISHU_RESPONSE_INVALID") from exc
    if not isinstance(payload, dict):
        raise DatasourceProviderError("FEISHU_RESPONSE_INVALID")
    if payload.get("code") not in (None, 0):
        raise DatasourceProviderError("FEISHU_RESOURCE_ACCESS_DENIED")
    return payload


DATASOURCE_PROVIDER = FeishuDatasourceProvider()
TOOL_PROVIDER = FeishuToolProvider()
