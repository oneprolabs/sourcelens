"""End-to-end Decision execution over the real Plugin HTTP pipeline.

Unlike the unit tests (which stub ``_execute_plugin_tool`` /
``run_decision_tool``), these drive a real TypeSafe runtime through the
snapshot/lease/material flow and the pooled provider client, so the
``http_post_paths`` allowlist and the plugin-side ``project_decision``
projection are exercised for gates and analyses alike.
"""

import json
from types import SimpleNamespace

import httpx

from lensnode.agent_runtime.decision_gates import DecisionRunner
from lensnode.plugin_http import PluginHttpClientPool


ENDPOINT = "https://decision.example:8443"
RUN_UUID = "run-1"
CALL_ID = "gate:search_needed:1"
QUESTION = "Deploy failed, why?"


def _command(**config):
    return {
        "run_uuid": RUN_UUID,
        "decision_gates": [
            {
                "plugin_key": "typesafe",
                "plugin_version": "1.0.0",
                "connection_uuid": "connection-1",
                "gates": {
                    "search_needed": {
                        "tool_key": "typesafe_noul",
                        "kind": "noul",
                        "threshold": 0.5,
                        "margin": 0.1,
                        "max_state_chars": 4000,
                        **config,
                    }
                },
            }
        ],
    }


def test_gate_executes_through_the_real_plugin_pipeline():
    events = []
    posted = {}
    provider_requests = []

    def control(request):
        if request.url.path.endswith("/tool-snapshots/"):
            posted["snapshot"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "snapshot_uuid": "snapshot-1",
                    "run_uuid": RUN_UUID,
                    "connection_uuid": "connection-1",
                    "tool_key": "typesafe_noul",
                    "invocation_id": CALL_ID,
                },
            )
        if request.url.path.endswith("/snapshots/snapshot-1/"):
            return httpx.Response(
                200,
                json={
                    "run_uuid": RUN_UUID,
                    "plugin_key": "typesafe",
                    "plugin_version": "1.0.0",
                    "tool_key": "typesafe_noul",
                    "invocation_id": CALL_ID,
                    "resolved_config": {
                        "arguments": posted["snapshot"]["arguments"],
                        "endpoint": ENDPOINT,
                        "connection_config": {"model": "custom-model"},
                        "allowed_scope": {},
                        "source": "decision_gate",
                    },
                },
            )
        if request.url.path.endswith("/leases/"):
            return httpx.Response(201, json={"lease_uuid": "lease-1"})
        if request.url.path.endswith("/material/"):
            return httpx.Response(
                200,
                json={
                    "plugin_key": "typesafe",
                    "endpoint": ENDPOINT,
                    "value": "test-token",
                },
            )
        raise AssertionError(request.url)

    def provider(request):
        provider_requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "custom-model",
                "answers": {"decision": {"type": "noul", "noul": 0.9}},
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    pool = PluginHttpClientPool(
        timeout=15,
        verify=True,
        client_factory=lambda **options: httpx.Client(
            transport=httpx.MockTransport(provider), **options
        ),
    )
    try:
        with httpx.Client(transport=httpx.MockTransport(control)) as client:
            runner = DecisionRunner(
                _command(),
                SimpleNamespace(
                    ai_gateway_url="https://control.example",
                    token="node-token",
                ),
                client,
                plugin_http_pool=pool,
                emit_event=lambda event, payload: events.append(
                    (event, payload)
                ),
                run_uuid=RUN_UUID,
            )
            value = runner.evaluate("search_needed", question=QUESTION)
    finally:
        pool.close()

    assert value == 0.9
    assert posted["snapshot"]["source"] == "decision_gate"
    assert posted["snapshot"]["call_id"] == CALL_ID
    assert posted["snapshot"]["tool_key"] == "typesafe_noul"
    assert [request.url.path for request in provider_requests] == [
        "/v1/systemone"
    ]
    body = json.loads(provider_requests[0].content)
    assert body["state"] == QUESTION
    assert "searching the workspace" in body["questions"]["decision"][
        "instructions"
    ]
    assert [name for name, _ in events] == [
        "deepagents.decision.gate.start",
        "tool.plugin.start",
        "tool.plugin.done",
        "deepagents.decision.gate.done",
    ]
    assert events[0][1]["source"] == "decision_gate"
    assert events[1][1]["source"] == "decision_gate"
    assert events[-1][1]["verdict"] == "accept"
    assert events[-1][1]["value"] == 0.9


def test_gate_without_a_declared_post_path_falls_back():
    """A runtime that declares no POST path can never reach the provider."""

    events = []
    provider_calls = []

    def control(request):
        if request.url.path.endswith("/tool-snapshots/"):
            return httpx.Response(
                201,
                json={
                    "snapshot_uuid": "snapshot-1",
                    "run_uuid": RUN_UUID,
                    "connection_uuid": "connection-1",
                    "tool_key": "typesafe_noul",
                    "invocation_id": CALL_ID,
                },
            )
        if request.url.path.endswith("/snapshots/snapshot-1/"):
            return httpx.Response(
                200,
                json={
                    "run_uuid": RUN_UUID,
                    "plugin_key": "typesafe",
                    "plugin_version": "1.0.0",
                    "tool_key": "typesafe_noul",
                    "invocation_id": CALL_ID,
                    "resolved_config": {
                        "arguments": {"state": QUESTION, "instructions": "x"},
                        "endpoint": ENDPOINT,
                        "connection_config": {"model": "custom-model"},
                        "allowed_scope": {},
                    },
                },
            )
        if request.url.path.endswith("/leases/"):
            return httpx.Response(201, json={"lease_uuid": "lease-1"})
        if request.url.path.endswith("/material/"):
            return httpx.Response(
                200,
                json={
                    "plugin_key": "typesafe",
                    "endpoint": ENDPOINT,
                    "value": "test-token",
                },
            )
        raise AssertionError(request.url)

    def provider(request):
        provider_calls.append(request)
        raise AssertionError("provider must not be reached")

    pool = PluginHttpClientPool(
        timeout=15,
        verify=True,
        client_factory=lambda **options: httpx.Client(
            transport=httpx.MockTransport(provider), **options
        ),
    )
    try:
        with httpx.Client(transport=httpx.MockTransport(control)) as client:
            runner = DecisionRunner(
                _command(),
                SimpleNamespace(
                    ai_gateway_url="https://control.example",
                    token="node-token",
                ),
                client,
                plugin_http_pool=pool,
                emit_event=lambda event, payload: events.append(
                    (event, payload)
                ),
                run_uuid=RUN_UUID,
            )
            # Drop the declared POST path: the provider POST is refused by
            # the host policy and the gate falls back.
            original = runner._plugin_http_pool
            runner._plugin_http_pool = _PathlessPool(original)
            value = runner.evaluate("search_needed", question=QUESTION)
    finally:
        pool.close()

    assert value is None
    assert provider_calls == []
    assert events[-1][1]["verdict"] == "fallback"
    assert events[-1][1]["fallback_reason"] == "error"


class _PathlessPool:
    """Wrap a pool while dropping every declared POST path."""

    def __init__(self, pool):
        self._pool = pool

    def bind(self, plugin_key, connection_uuid, origins, post_paths=()):
        return self._pool.bind(plugin_key, connection_uuid, origins, ())


def _rank_command():
    return {
        "run_uuid": RUN_UUID,
        "decision_analyses": [
            {
                "plugin_key": "typesafe",
                "plugin_version": "1.0.0",
                "connection_uuid": "connection-1",
                "analyses": {
                    "plan_quality": {
                        "tool_key": "typesafe_score",
                        "kind": "score",
                        "rubric": ["weak", "acceptable", "strong"],
                        "summary": "Prefer safer plans.",
                    }
                },
            }
        ],
    }


def test_rank_executes_through_the_real_plugin_pipeline():
    from lensnode.agent_runtime.decision_policy import DecisionRanker

    events = []
    snapshot_bodies = []
    provider_states = []

    def control(request):
        if request.url.path.endswith("/tool-snapshots/"):
            body = json.loads(request.content)
            snapshot_bodies.append(body)
            return httpx.Response(
                201,
                json={
                    "snapshot_uuid": f"snapshot-{body['call_id']}",
                    "run_uuid": RUN_UUID,
                    "connection_uuid": "connection-1",
                    "tool_key": "typesafe_score",
                    "invocation_id": body["call_id"],
                },
            )
        if "/snapshots/" in request.url.path:
            snapshot_uuid = request.url.path.rstrip("/").rsplit("/", 1)[-1]
            call_id = snapshot_uuid.removeprefix("snapshot-")
            body = next(
                item for item in snapshot_bodies if item["call_id"] == call_id
            )
            return httpx.Response(
                200,
                json={
                    "run_uuid": RUN_UUID,
                    "plugin_key": "typesafe",
                    "plugin_version": "1.0.0",
                    "tool_key": "typesafe_score",
                    "invocation_id": call_id,
                    "resolved_config": {
                        "arguments": body["arguments"],
                        "endpoint": ENDPOINT,
                        "connection_config": {"model": "custom-model"},
                        "allowed_scope": {},
                        "source": "decision_rank",
                    },
                },
            )
        if request.url.path.endswith("/leases/"):
            return httpx.Response(201, json={"lease_uuid": "lease-1"})
        if request.url.path.endswith("/material/"):
            return httpx.Response(
                200,
                json={
                    "plugin_key": "typesafe",
                    "endpoint": ENDPOINT,
                    "value": "test-token",
                },
            )
        raise AssertionError(request.url)

    def provider(request):
        state = json.loads(request.content)["state"]
        provider_states.append(state)
        if "strong" in state:
            probabilities = {"0": 0.05, "1": 0.15, "2": 0.8}
        else:
            probabilities = {"0": 0.6, "1": 0.3, "2": 0.1}
        return httpx.Response(
            200,
            json={
                "model": "custom-model",
                "answers": {
                    "decision": {
                        "type": "score",
                        "score": 2,
                        "confidence": 0.8,
                        "probabilities": probabilities,
                        "legend": {
                            "0": "weak",
                            "1": "acceptable",
                            "2": "strong",
                        },
                    }
                },
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        )

    pool = PluginHttpClientPool(
        timeout=15,
        verify=True,
        client_factory=lambda **options: httpx.Client(
            transport=httpx.MockTransport(provider), **options
        ),
    )
    try:
        with httpx.Client(transport=httpx.MockTransport(control)) as client:
            ranker = DecisionRanker(
                _rank_command(),
                SimpleNamespace(
                    ai_gateway_url="https://control.example",
                    token="node-token",
                ),
                client,
                plugin_http_pool=pool,
                emit_event=lambda event, payload: events.append(
                    (event, payload)
                ),
                run_uuid=RUN_UUID,
            )
            result = ranker.rank(
                "plan_quality",
                [
                    {"label": "weak-plan", "content": "a weak plan"},
                    {"label": "strong-plan", "content": "a strong plan"},
                ],
            )
    finally:
        pool.close()

    assert result["ok"] is True
    assert [item["label"] for item in result["ranked"]] == [
        "strong-plan",
        "weak-plan",
    ]
    assert result["ranked"][0]["score"] == 1.75
    assert result["failed"] == []
    assert sorted(provider_states) == ["a strong plan", "a weak plan"]
    assert sorted(body["call_id"] for body in snapshot_bodies) == [
        "rank:plan_quality:0",
        "rank:plan_quality:1",
    ]
    assert {body["source"] for body in snapshot_bodies} == {"decision_rank"}
    # Candidates are scored concurrently, so the event pairs may interleave.
    assert sorted(name for name, _ in events) == [
        "tool.plugin.done",
        "tool.plugin.done",
        "tool.plugin.start",
        "tool.plugin.start",
    ]
    assert {payload["source"] for _, payload in events} == {"decision_rank"}
    assert sorted(
        payload["invocation_id"]
        for name, payload in events
        if name == "tool.plugin.start"
    ) == ["rank:plan_quality:0", "rank:plan_quality:1"]
