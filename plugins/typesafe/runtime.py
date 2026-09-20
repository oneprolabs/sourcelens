"""LensNode runtime for the TypeSafe AI decision Plugin."""

import json
import time
from math import isfinite
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from langchain.tools import ToolRuntime, tool
from pydantic import Field

from lensnode.plugin_runtime import PluginRuntimeError


PLUGIN_API_VERSION = 1
PLUGIN_KEY = "typesafe"
PLUGIN_VERSION = "1.0.0"
TYPESAFE_MODEL = "jev-1.13.0"
REQUEST_MAX_BYTES = 1_000_000
RESPONSE_MAX_BYTES = 1_000_000
MAX_STATE_LENGTH = 100_000
MAX_INSTRUCTIONS_LENGTH = 2_000
MAX_CRITERIA_LENGTH = 32_000


def http_origins(endpoint):
    """Return the configured origin approved for pooled HTTP."""

    return (_endpoint(endpoint),)


def build_tool(definition, executor):
    """Create one typed, read-only LangChain tool from a manifest entry."""

    if not isinstance(definition, dict):
        raise PluginRuntimeError("TYPESAFE_TOOL_INVALID")
    if (
        definition.get("capability") != "decision.evaluate"
        or definition.get("side_effect") != "none"
    ):
        raise PluginRuntimeError("TYPESAFE_TOOL_NOT_READ_ONLY")
    key = str(definition.get("key") or "")
    description = str(definition.get("description") or "").strip()
    if key not in {
        "typesafe_noul",
        "typesafe_choice",
        "typesafe_score",
    } or not description:
        raise PluginRuntimeError("TYPESAFE_TOOL_INVALID")

    if key == "typesafe_noul":
        def invoke(
            state: Annotated[str, Field(min_length=1, max_length=100000)],
            instructions: Annotated[
                str,
                Field(min_length=1, max_length=2000),
            ],
            runtime: ToolRuntime,
            criteria_true: Annotated[
                str,
                Field(max_length=2000),
            ] = "",
            criteria_false: Annotated[
                str,
                Field(max_length=2000),
            ] = "",
        ) -> str:
            return executor(
                key,
                {
                    "state": state,
                    "instructions": instructions,
                    "criteria_true": criteria_true,
                    "criteria_false": criteria_false,
                },
                runtime,
            )

        return tool(key, description=description)(invoke)

    def invoke(
        state: Annotated[str, Field(min_length=1, max_length=100000)],
        instructions: Annotated[
            str,
            Field(min_length=1, max_length=2000),
        ],
        criteria: Annotated[str, Field(min_length=2, max_length=32000)],
        runtime: ToolRuntime,
    ) -> str:
        return executor(
            key,
            {
                "state": state,
                "instructions": instructions,
                "criteria": criteria,
            },
            runtime,
        )

    return tool(key, description=description)(invoke)


def execute_tool(key, client, arguments, secret, endpoint, config):
    """Execute one bounded TypeSafe System One evaluation."""

    endpoint = _endpoint(endpoint)
    if not isinstance(secret, str) or not secret.strip():
        raise PluginRuntimeError("TYPESAFE_API_KEY_REQUIRED")
    model = _model(config)
    question = _question(key, arguments)
    response = _request(
        client,
        f"{endpoint}/v1/systemone",
        {
            "Accept": "application/json",
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": "SourceLens-LensNode",
        },
        {
            "state": _state(arguments.get("state")),
            "model": model,
            "questions": {"decision": question},
        },
    )
    return _validate_response(response, question)


def _question(key, arguments):
    """Build one TypeSafe typed question from validated tool arguments."""

    if not isinstance(arguments, dict):
        raise PluginRuntimeError("TYPESAFE_ARGUMENTS_INVALID")
    instructions = _text(
        arguments.get("instructions"),
        MAX_INSTRUCTIONS_LENGTH,
    )
    if key == "typesafe_noul":
        question = {"type": "noul", "instructions": instructions}
        criteria = {}
        if arguments.get("criteria_true"):
            criteria["true"] = _text(
                arguments["criteria_true"],
                MAX_INSTRUCTIONS_LENGTH,
            )
        if arguments.get("criteria_false"):
            criteria["false"] = _text(
                arguments["criteria_false"],
                MAX_INSTRUCTIONS_LENGTH,
            )
        if criteria:
            question["criteria"] = criteria
        return question
    if key not in {"typesafe_choice", "typesafe_score"}:
        raise PluginRuntimeError("TYPESAFE_TOOL_UNSUPPORTED")
    criteria = _criteria(arguments.get("criteria"), key)
    return {
        "type": "choice" if key == "typesafe_choice" else "score",
        "instructions": instructions,
        "criteria": criteria,
    }


def _state(value):
    """Decode JSON-encoded state while preserving ordinary text state."""

    value = _text(value, MAX_STATE_LENGTH)
    if value[:1] not in "[{":
        return value
    try:
        return json.loads(value, parse_constant=_reject_non_finite)
    except (TypeError, ValueError, RecursionError):
        return value


def _criteria(value, key):
    """Decode and bound the rubric for Choice or Score."""

    if not isinstance(value, str) or len(value) > MAX_CRITERIA_LENGTH:
        raise PluginRuntimeError("TYPESAFE_ARGUMENTS_INVALID")
    try:
        parsed = json.loads(value, parse_constant=_reject_non_finite)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PluginRuntimeError("TYPESAFE_ARGUMENTS_INVALID") from exc
    if key == "typesafe_choice":
        valid = isinstance(parsed, dict) and 1 <= len(parsed) <= 255
    else:
        valid = isinstance(parsed, list) and 2 <= len(parsed) <= 10
    if not valid:
        raise PluginRuntimeError("TYPESAFE_ARGUMENTS_INVALID")
    values = parsed.values() if isinstance(parsed, dict) else parsed
    if any(
        not isinstance(item, str)
        and not (isinstance(parsed, dict) and item is None)
        for item in values
    ):
        raise PluginRuntimeError("TYPESAFE_ARGUMENTS_INVALID")
    if isinstance(parsed, dict) and any(
        not option.strip() or len(option) > 160 for option in parsed
    ):
        raise PluginRuntimeError("TYPESAFE_ARGUMENTS_INVALID")
    return parsed


def _model(config):
    """Return the configured model from frozen Connection configuration."""

    if config in ({}, None):
        return TYPESAFE_MODEL
    model = config.get("model") if isinstance(config, dict) else None
    if (
        not isinstance(model, str)
        or not model.strip()
        or len(model) > 128
        or any(ord(char) < 32 for char in model)
    ):
        raise PluginRuntimeError("TYPESAFE_MODEL_INVALID")
    return model.strip()


def _endpoint(value):
    """Return the safe HTTPS endpoint or reject the snapshot."""

    try:
        parsed = urlsplit(str(value or "").strip())
        parsed.port
    except ValueError as exc:
        raise PluginRuntimeError("TYPESAFE_SNAPSHOT_MISMATCH") from exc
    normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PluginRuntimeError("TYPESAFE_SNAPSHOT_MISMATCH")
    return normalized


def _text(value, maximum):
    """Return bounded text accepted by the TypeSafe request."""

    if not isinstance(value, str) or not value.strip():
        raise PluginRuntimeError("TYPESAFE_ARGUMENTS_INVALID")
    value = value.strip()
    if len(value) > maximum or "\x00" in value:
        raise PluginRuntimeError("TYPESAFE_ARGUMENTS_INVALID")
    return value


def _request(client, url, headers, payload):
    """POST JSON with bounded retries for documented transient failures."""

    if len(json.dumps(payload, allow_nan=False).encode()) > REQUEST_MAX_BYTES:
        raise PluginRuntimeError("TYPESAFE_REQUEST_TOO_LARGE")
    for attempt in range(3):
        with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                raise PluginRuntimeError("TYPESAFE_REDIRECT_REJECTED")
            status_code = response.status_code
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > RESPONSE_MAX_BYTES:
                    raise PluginRuntimeError("TYPESAFE_RESPONSE_TOO_LARGE")
            retry_after = response.headers.get("Retry-After")
        if status_code in {429, 529} and attempt < 2:
            _sleep_retry(retry_after, attempt)
            continue
        if status_code != 200:
            raise PluginRuntimeError(_error_code(status_code))
        if len(body) > RESPONSE_MAX_BYTES:
            raise PluginRuntimeError("TYPESAFE_RESPONSE_TOO_LARGE")
        try:
            return json.loads(body, parse_constant=_reject_non_finite)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise PluginRuntimeError("TYPESAFE_RESPONSE_INVALID") from exc
    raise PluginRuntimeError("TYPESAFE_REQUEST_FAILED")


def _sleep_retry(value, attempt):
    """Sleep for a small bounded Retry-After or exponential backoff."""

    try:
        delay = float(value)
        if not isfinite(delay) or delay < 0:
            raise ValueError
        delay = min(delay, 2.0)
    except (TypeError, ValueError):
        delay = 0.25 * (2**attempt)
    time.sleep(delay)


def _error_code(status_code):
    """Map TypeSafe HTTP errors to stable LensNode error codes."""

    return {
        401: "TYPESAFE_ACCESS_DENIED",
        422: "TYPESAFE_ARGUMENTS_INVALID",
        429: "TYPESAFE_RATE_LIMITED",
        529: "TYPESAFE_RATE_LIMITED",
    }.get(status_code, "TYPESAFE_REQUEST_FAILED")


def _validate_response(value, question):
    """Validate typed answers and return only documented response fields."""

    error = "TYPESAFE_RESPONSE_INVALID"
    if not isinstance(value, dict):
        raise PluginRuntimeError(error)
    model = value.get("model")
    answers = value.get("answers")
    usage = value.get("usage")
    if (
        not isinstance(model, str) or not model or len(model) > 128
        or not isinstance(answers, dict) or set(answers) != {"decision"}
        or not isinstance(usage, dict)
    ):
        raise PluginRuntimeError(error)
    answer = answers["decision"]
    kind = question["type"]
    if not isinstance(answer, dict) or answer.get("type") != kind:
        raise PluginRuntimeError(error)
    projected = {"type": kind}
    if kind == "noul":
        projected["noul"] = _number(answer.get("noul"), 1)
    else:
        criteria = question["criteria"]
        keys = set(criteria) if kind == "choice" else {
            str(index) for index in range(len(criteria))
        }
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != keys:
            raise PluginRuntimeError(error)
        probabilities = {
            key: _number(number, 1)
            for key, number in probabilities.items()
        }
        if abs(sum(probabilities.values()) - 1) > 0.01:
            raise PluginRuntimeError(error)
        projected["probabilities"] = probabilities
        projected["confidence"] = _number(answer.get("confidence"), 1)
        if kind == "choice":
            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in keys:
                raise PluginRuntimeError(error)
            projected["choice"] = choice
        else:
            projected["score"] = _number(answer.get("score"), len(keys) - 1)
            projected["legend"] = {
                str(index): text for index, text in enumerate(criteria)
            }
    tokens = {}
    for key in ("input_tokens", "output_tokens"):
        count = usage.get(key)
        if type(count) is not int or count < 0:
            raise PluginRuntimeError(error)
        tokens[key] = count
    return {
        "ok": True,
        "model": model,
        "answers": {"decision": projected},
        "usage": tokens,
    }


def _number(value, maximum):
    """Require a finite number in the documented answer range."""

    if (
        type(value) not in (int, float)
        or not isfinite(value)
        or not 0 <= value <= maximum
    ):
        raise PluginRuntimeError("TYPESAFE_RESPONSE_INVALID")
    return value


def _reject_non_finite(value):
    """Reject non-standard JSON numeric constants."""

    raise ValueError(value)
