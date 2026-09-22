"""TypeSafe wire contract and host HTTP policy regression tests."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from lensnode.plugin_http import PluginHttpClientPool
from lensnode.plugin_package_loader import load_runtime_contract
from lensnode.plugin_runtime import PluginRuntimeError
from lensnode.plugin_tools import build_plugin_tools


RUNTIME = load_runtime_contract("typesafe", "1.3.0")
ENDPOINT = "https://decision.example:8443"
CONFIG = {"model": "custom-model", "__allowed_scope": {}}
ARGUMENTS = {
    "state": "A support request\nWith details",
    "instructions": "Urgent?",
}
MANIFEST = json.loads(
    (Path(__file__).resolve().parents[3] / "plugins/typesafe/plugin.json")
    .read_text()
)


def payload(answer=None):
    """Return one documented System One response."""

    return {
        "model": "custom-model",
        "answers": {"decision": answer or {"type": "noul", "noul": 0.9}},
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }


@pytest.mark.parametrize(
    "kind,criteria,answer",
    [
        ("noul", None, {"type": "noul", "noul": 0.9}),
        (
            "choice", '{"low": null, "high": "urgent"}',
            {"type": "choice", "choice": "high", "confidence": 0.8,
             "probabilities": {"low": 0.1, "high": 0.9}},
        ),
        (
            "score", '["low", "high"]',
            {"type": "score", "score": 0.9, "confidence": 0.8,
             "probabilities": {"0": 0.1, "1": 0.9},
             "legend": {"0": "low", "1": "high"}},
        ),
    ],
)
def test_tools_use_real_pool_and_authorized_snapshot(kind, criteria, answer):
    seen = []
    events = []
    arguments = dict(ARGUMENTS)
    if criteria:
        arguments["criteria"] = criteria
    key = "typesafe_" + kind

    def control(request):
        if request.url.path.endswith("/tool-snapshots/"):
            body = json.loads(request.content)
            assert "token" not in body["arguments"]
            return httpx.Response(201, json={
                "snapshot_uuid": "snapshot-1", "run_uuid": "run-1",
                "connection_uuid": "connection-1", "tool_key": key,
                "invocation_id": "call-1",
            })
        if request.url.path.endswith("/snapshots/snapshot-1/"):
            return httpx.Response(200, json={
                "run_uuid": "run-1", "plugin_key": "typesafe",
                "plugin_version": "1.3.0", "tool_key": key,
                "invocation_id": "call-1",
                "resolved_config": {
                    "arguments": arguments, "endpoint": ENDPOINT,
                    "connection_config": {"model": "custom-model"},
                    "allowed_scope": {},
                },
            })
        if request.url.path.endswith("/leases/"):
            return httpx.Response(201, json={"lease_uuid": "lease-1"})
        if request.url.path.endswith("/material/"):
            return httpx.Response(200, json={
                "plugin_key": "typesafe", "endpoint": ENDPOINT,
                "value": "test-token",
            })
        raise AssertionError(request.url)

    def provider(request):
        seen.append(request)
        assert str(request.url) == ENDPOINT + "/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-token"
        body = json.loads(request.content)
        assert body["model"] == "custom-model"
        assert body["questions"]["decision"]["type"] == kind
        assert body["state"] == ARGUMENTS["state"]
        return httpx.Response(200, json=payload(answer))

    pool = PluginHttpClientPool(
        timeout=15, verify=True,
        client_factory=lambda **kw: httpx.Client(
            transport=httpx.MockTransport(provider), **kw
        ),
    )
    try:
        with httpx.Client(transport=httpx.MockTransport(control)) as client:
            tool = build_plugin_tools(
                {"run_uuid": "run-1", "loaded_plugins": [{
                    "plugin_key": "typesafe",
                    "plugin_version": "1.3.0",
                    "connection_uuid": "connection-1",
                    "protocol_version": 1,
                    "tools": [
                        {
                            **next(
                                t for t in MANIFEST["tools"]
                                if t["key"] == key
                            ),
                            "exposure": "model",
                        }
                    ],
                }]},
                SimpleNamespace(
                    ai_gateway_url="https://control.example",
                    token="node-token",
                ),
                client, lambda event, data: events.append((event, data)),
                plugin_http_pool=pool,
            )[0]
            schema = tool.tool_call_schema.model_json_schema()
            assert "runtime" not in schema["properties"]
            result = json.loads(tool.func(
                **arguments, runtime=SimpleNamespace(tool_call_id="call-1")
            ))
    finally:
        pool.close()
    assert result["ok"] is True, (result, events, seen)
    assert result["answers"]["decision"] == answer
    assert len(seen) == 1
    assert "test-token" not in json.dumps([result, events])


def test_gateway_base_path_builds_the_system_one_url():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = RUNTIME.execute_tool(
            "typesafe_noul",
            client,
            ARGUMENTS,
            "test-token",
            "https://ai-gateway.vercel.sh/typesafe",
            {"model": "typesafe-ai/jev"},
        )
    assert result["ok"] is True
    assert len(seen) == 1
    assert str(seen[0].url) == (
        "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
    )
    assert json.loads(seen[0].content)["model"] == "typesafe-ai/jev"


@pytest.mark.parametrize("status,expected", [
    (401, "ACCESS_DENIED"), (422, "ARGUMENTS_INVALID"),
    (302, "REDIRECT_REJECTED"), (500, "REQUEST_FAILED"),
])
def test_error_bodies_are_never_returned(status, expected):
    def handler(request):
        return httpx.Response(status, text="test-token private upstream error")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PluginRuntimeError, match=expected) as error:
            RUNTIME.execute_tool(
                "typesafe_noul",
                client,
                ARGUMENTS,
                "test-token",
                ENDPOINT,
                CONFIG,
            )
    assert "test-token" not in str(error.value)


@pytest.mark.parametrize("status", [429, 529])
def test_documented_transient_errors_have_bounded_retries(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "0"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PluginRuntimeError, match="RATE_LIMITED"):
            RUNTIME.execute_tool(
                "typesafe_noul",
                client,
                ARGUMENTS,
                "test-token",
                ENDPOINT,
                CONFIG,
            )
    assert len(calls) == 3


@pytest.mark.parametrize("answer", [
    {}, {"type": "score", "score": 1},
    {"type": "noul", "noul": True}, {"type": "noul", "noul": 1.1},
])
def test_malformed_answers_fail_closed(answer):
    value = payload()
    value["answers"]["decision"] = answer
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=value)
    )) as client:
        with pytest.raises(PluginRuntimeError, match="RESPONSE_INVALID"):
            RUNTIME.execute_tool(
                "typesafe_noul",
                client,
                ARGUMENTS,
                "test-token",
                ENDPOINT,
                CONFIG,
            )


def test_response_size_is_bounded_during_streaming():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 1_000_001)
    )) as client:
        with pytest.raises(PluginRuntimeError, match="RESPONSE_TOO_LARGE"):
            RUNTIME.execute_tool(
                "typesafe_noul",
                client,
                ARGUMENTS,
                "test-token",
                ENDPOINT,
                CONFIG,
            )


@pytest.mark.parametrize("key,criteria", [
    ("typesafe_choice", json.dumps({str(i): None for i in range(256)})),
    ("typesafe_score", '["one"]'),
    ("typesafe_choice", '{"one": 1}'),
    ("typesafe_score", '["one", null]'),
])
def test_invalid_rubrics_never_reach_http(key, criteria):
    with pytest.raises(PluginRuntimeError, match="ARGUMENTS_INVALID"):
        RUNTIME.execute_tool(
            key, None, {**ARGUMENTS, "criteria": criteria},
            "test-token", ENDPOINT, CONFIG,
        )


def test_default_model_matches_the_manifest():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        RUNTIME.execute_tool(
            "typesafe_noul", client, ARGUMENTS, "test-token", ENDPOINT, {},
        )
    default = MANIFEST["connection_schema"]["properties"]["model"]["default"]
    assert json.loads(seen[0].content)["model"] == default


def test_rejects_unknown_connection_config_keys():
    with pytest.raises(PluginRuntimeError, match="MODEL_INVALID"):
        RUNTIME.execute_tool(
            "typesafe_noul",
            None,
            ARGUMENTS,
            "test-token",
            ENDPOINT,
            {"model": "custom-model", "unexpected": True},
        )
