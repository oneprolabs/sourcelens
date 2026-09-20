"""Control-plane contracts for the TypeSafe AI decision Plugin."""

import json
from urllib.parse import urlsplit, urlunsplit

from lens.plugins.contracts import ToolProviderError
from lens.plugins.providers.base import (
    DatasourceProvider,
    DatasourceProviderError,
)


PLUGIN_API_VERSION = 1
PLUGIN_KEY = "typesafe"
PLUGIN_VERSION = "1.0.0"
TYPESAFE_MODEL = "jev-1.13.0"
MAX_STATE_LENGTH = 100_000
MAX_INSTRUCTIONS_LENGTH = 2_000
MAX_CRITERIA_LENGTH = 32_000


class TypeSafeConnectionProvider(DatasourceProvider):
    """Validate an administrator-configured TypeSafe-compatible connection."""

    key = PLUGIN_KEY

    def validate_connection(self, endpoint, connection_config):
        """Require a safe HTTPS System One API origin."""

        parsed = _parse_endpoint(endpoint, DatasourceProviderError)
        _model(connection_config)
        return parsed

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
        """Validate stored fields without sending a billable evaluation."""

        del client, request_context
        if not isinstance(secret, str) or not secret.strip():
            raise DatasourceProviderError("TYPESAFE_API_KEY_REQUIRED")
        self.validate_connection(endpoint, connection_config)
        return {"status": "configured"}

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
    """Return a canonical HTTPS endpoint or raise the given error."""

    try:
        parsed = urlsplit(str(value or "").strip())
        parsed.port
    except ValueError as exc:
        raise error_type("TYPESAFE_ENDPOINT_INVALID") from exc
    normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise error_type("TYPESAFE_ENDPOINT_INVALID")
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


DATASOURCE_PROVIDER = TypeSafeConnectionProvider()
TOOL_PROVIDER = TypeSafeToolProvider()
