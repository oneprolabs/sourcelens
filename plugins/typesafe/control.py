"""Control-plane contracts for the TypeSafe AI decision Plugin."""

import json
import re
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from lens.plugins.contracts import ToolProviderError
from lens.plugins.providers.base import (
    DatasourceProvider,
    DatasourceProviderError,
    PluginRequestContext,
    retry_after_seconds,
)


PLUGIN_API_VERSION = 1
PLUGIN_KEY = "typesafe"
PLUGIN_VERSION = "1.4.0"
TYPESAFE_MODEL = "jev-1.13.0"
TYPESAFE_API_SUFFIX = "/v1/systemone"
MAX_STATE_LENGTH = 100_000
MAX_INSTRUCTIONS_LENGTH = 2_000
MAX_CRITERIA_LENGTH = 32_000
MAX_ENDPOINT_LENGTH = 500
MAX_MODELS_RESPONSE_BYTES = 256_000
PATH_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9._~-]{1,128}")


class TypeSafeApi(Protocol):
    """Minimal TypeSafe API used by connection validation."""

    def list_models(self):
        """Return model names available to the authenticated account."""


class TypeSafeApiClient:
    """HTTP implementation of the documented TypeSafe API interface."""

    def __init__(self, client, endpoint, secret):
        self._client = client
        self._endpoint = endpoint
        self._secret = secret

    def list_models(self):
        """List models through the non-billable TypeSafe models endpoint."""

        if self._client is None:
            raise DatasourceProviderError("PLUGIN_HTTP_CLIENT_REQUIRED")
        try:
            with self._client.stream(
                "GET",
                _api_url(self._endpoint, "/v1/models"),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._secret}",
                    "User-Agent": "SourceLens-LensNode",
                },
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    raise DatasourceProviderError(
                        "TYPESAFE_REDIRECT_REJECTED"
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_MODELS_RESPONSE_BYTES:
                        raise DatasourceProviderError(
                            "TYPESAFE_RESPONSE_TOO_LARGE"
                        )
                retry_after = response.headers.get("Retry-After")
                status_code = response.status_code
        except DatasourceProviderError:
            raise
        except httpx.HTTPError as exc:
            raise DatasourceProviderError(
                "TYPESAFE_REQUEST_FAILED"
            ) from exc

        if status_code != 200:
            raise DatasourceProviderError(
                _error_code(status_code),
                retry_after=retry_after_seconds(retry_after),
            )
        try:
            payload = json.loads(
                body,
                parse_constant=_reject_non_finite,
            )
        except (TypeError, UnicodeDecodeError, ValueError, RecursionError):
            raise DatasourceProviderError("TYPESAFE_RESPONSE_INVALID")
        return _model_names(payload)


class TypeSafeConnectionProvider(DatasourceProvider):
    """Validate an administrator-configured TypeSafe-compatible connection."""

    key = PLUGIN_KEY

    def validate_connection(self, endpoint, connection_config):
        """Require a safe HTTPS System One API origin."""

        parsed = _parse_endpoint(endpoint, DatasourceProviderError)
        _model(connection_config)
        return parsed

    def http_origins(self, endpoint, connection_config=None):
        """Return the bare origin allowed for host-managed HTTP clients."""

        endpoint = self.validate_connection(endpoint, connection_config)
        return (_origin(endpoint),)

    def validate_connection_scope(self, connection_scope):
        """Return an empty scope because TypeSafe has no resource allowlist."""

        if connection_scope not in ({}, None):
            raise DatasourceProviderError("TYPESAFE_SCOPE_INVALID")
        return {}

    def validate_live_connection(
        self,
        secret,
        endpoint="",
        connection_config=None,
        client=None,
        request_context=None,
    ):
        """Validate credentials through the non-billable models endpoint."""

        if not isinstance(secret, str) or not secret.strip():
            raise DatasourceProviderError("TYPESAFE_API_KEY_REQUIRED")
        endpoint = self.validate_connection(endpoint, connection_config)
        context = request_context or PluginRequestContext(
            timeout_seconds=15,
        )
        models = context.run(
            TypeSafeApiClient(client, endpoint, secret.strip()).list_models
        )
        return {
            "status": "configured",
            "model": _model(connection_config),
            "available_models": models,
        }

    def validate_datasource_source_type(self, source_type):
        """Reject datasource use because this plugin exposes tools only."""

        del source_type
        raise DatasourceProviderError("TYPESAFE_DATASOURCE_UNSUPPORTED")


class TypeSafeToolProvider:
    """Validate typed decision arguments before a Run snapshot is created."""

    def validate_request(
        self,
        endpoint,
        connection_scope,
        tool_key,
        arguments,
    ):
        """Normalize one bounded TypeSafe question request."""

        normalized_endpoint = _parse_endpoint(endpoint, ToolProviderError)
        if connection_scope not in ({}, None):
            raise ToolProviderError("TYPESAFE_SCOPE_INVALID")
        if not isinstance(arguments, dict):
            raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")
        if tool_key == "typesafe_noul":
            return normalized_endpoint, _noul_arguments(arguments)
        if tool_key == "typesafe_choice":
            return normalized_endpoint, _choice_arguments(arguments)
        if tool_key == "typesafe_score":
            return normalized_endpoint, _score_arguments(arguments)
        raise ToolProviderError("TYPESAFE_TOOL_UNSUPPORTED")


def _parse_endpoint(value, error_type):
    """Return a canonical HTTPS base endpoint or raise the given error."""

    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
        parsed.port
    except ValueError as exc:
        raise error_type("TYPESAFE_ENDPOINT_INVALID") from exc
    path = _base_path(parsed.path)
    if (
        len(text) > MAX_ENDPOINT_LENGTH
        or parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or path is None
    ):
        raise error_type("TYPESAFE_ENDPOINT_INVALID")
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, path, "", "")
    )


def _origin(endpoint):
    """Return the bare scheme and authority of one canonical endpoint."""

    parsed = urlsplit(endpoint)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _api_url(endpoint, suffix):
    """Append one documented API path to a normalized base endpoint."""

    return endpoint.rstrip("/") + suffix


def _base_path(path):
    """Return a safe base path prefix or None for an invalid path."""

    if path in {"", "/"}:
        return ""
    segments = path.strip("/").split("/")
    if any(
        not segment
        or segment in {".", ".."}
        or PATH_SEGMENT_PATTERN.fullmatch(segment) is None
        for segment in segments
    ):
        return None
    normalized = "/" + "/".join(segments)
    if normalized.endswith(TYPESAFE_API_SUFFIX):
        normalized = normalized[: -len(TYPESAFE_API_SUFFIX)]
    return normalized


def _model(config):
    """Return a bounded model identifier, defaulting to the Jev preset."""

    if config in ({}, None):
        return TYPESAFE_MODEL
    if not isinstance(config, dict) or set(config) != {"model"}:
        raise DatasourceProviderError("TYPESAFE_MODEL_INVALID")
    model = config.get("model")
    if (
        not isinstance(model, str)
        or not model.strip()
        or len(model) > 128
        or any(ord(char) < 32 for char in model)
    ):
        raise DatasourceProviderError("TYPESAFE_MODEL_INVALID")
    return model.strip()


def _noul_arguments(arguments):
    """Validate and normalize a Noul request."""

    allowed = {"state", "instructions", "criteria_true", "criteria_false"}
    _keys(arguments, allowed, {"state", "instructions"})
    normalized = _common_arguments(arguments)
    for key in ("criteria_true", "criteria_false"):
        if key in arguments and arguments[key] != "":
            normalized[key] = _text(arguments[key], MAX_INSTRUCTIONS_LENGTH)
    return normalized


def _choice_arguments(arguments):
    """Validate and normalize a Choice rubric."""

    _keys(
        arguments,
        {"state", "instructions", "criteria"},
        {"state", "instructions", "criteria"},
    )
    normalized = _common_arguments(arguments)
    normalized["criteria"] = _json_criteria(
        arguments.get("criteria"),
        dict,
        1,
        255,
    )
    return normalized


def _score_arguments(arguments):
    """Validate and normalize a Score rubric."""

    _keys(
        arguments,
        {"state", "instructions", "criteria"},
        {"state", "instructions", "criteria"},
    )
    normalized = _common_arguments(arguments)
    normalized["criteria"] = _json_criteria(
        arguments.get("criteria"),
        list,
        2,
        10,
    )
    return normalized


def _keys(arguments, allowed, required):
    """Reject unknown or missing argument names."""

    if (
        set(arguments).difference(allowed)
        or set(required).difference(arguments)
    ):
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")


def _common_arguments(arguments):
    """Validate state and instructions shared by all question types."""

    return {
        "state": _text(arguments.get("state"), MAX_STATE_LENGTH),
        "instructions": _text(
            arguments.get("instructions"),
            MAX_INSTRUCTIONS_LENGTH,
        ),
    }


def _text(value, maximum):
    """Return one non-empty bounded text value."""

    if not isinstance(value, str) or not value.strip():
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")
    value = value.strip()
    if len(value) > maximum or "\x00" in value:
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")
    return value


def _json_criteria(value, expected_type, minimum, maximum):
    """Parse and bound JSON criteria accepted by the TypeSafe API."""

    if not isinstance(value, str) or len(value) > MAX_CRITERIA_LENGTH:
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")
    try:
        parsed = json.loads(value, parse_constant=_reject_non_finite)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID") from exc
    if not isinstance(parsed, expected_type):
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")
    if not minimum <= len(parsed) <= maximum:
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")
    if isinstance(parsed, dict) and any(
        not isinstance(key, str) or not key.strip() or len(key) > 160
        for key in parsed
    ):
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")
    values = parsed.values() if isinstance(parsed, dict) else parsed
    if any(
        not isinstance(item, str)
        and not (expected_type is dict and item is None)
        for item in values
    ):
        raise ToolProviderError("TYPESAFE_ARGUMENTS_INVALID")
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def _reject_non_finite(value):
    """Reject non-standard JSON numeric constants."""

    raise ValueError(value)


def _model_names(payload):
    """Validate and project the documented GET /v1/models response."""

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list) or len(models) > 1000:
        raise DatasourceProviderError("TYPESAFE_RESPONSE_INVALID")
    names = []
    for item in models:
        name = item.get("name") if isinstance(item, dict) else None
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 128
            or any(ord(char) < 32 for char in name)
        ):
            raise DatasourceProviderError("TYPESAFE_RESPONSE_INVALID")
        names.append(name.strip())
    return names


def _error_code(status_code):
    """Map TypeSafe model endpoint errors to stable provider codes."""

    return {
        401: "TYPESAFE_ACCESS_DENIED",
        403: "TYPESAFE_ACCESS_DENIED",
        404: "TYPESAFE_ENDPOINT_NOT_FOUND",
        429: "TYPESAFE_RATE_LIMITED",
    }.get(status_code, "TYPESAFE_REQUEST_FAILED")


DATASOURCE_PROVIDER = TypeSafeConnectionProvider()
TOOL_PROVIDER = TypeSafeToolProvider()
