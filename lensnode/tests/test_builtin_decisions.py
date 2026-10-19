"""Built-in decisions preserve portable answers and existing fallback semantics."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from lensnode.agent_runtime.decision_gates import DecisionRunner
from lensnode.agent_runtime.decision_policy import (
    DecisionRanker,
    ModelDecisionPolicy,
    NullDecisionRanker,
    build_decision_policy,
    build_decision_ranker,
)


@pytest.mark.parametrize("decision_model", [{}, {"gates": {"search_needed": {}}, "analyses": {"plan_quality": {}}}])
def test_missing_model_keeps_original_policy_without_requests_or_decision_events(decision_model):
    client = Mock()
    emit = Mock()
    inner = ModelDecisionPolicy()
    payload = {"decision_model": decision_model}
    policy = build_decision_policy(payload, config(), client, inner=inner, emit_event=emit)
    ranker = build_decision_ranker(payload, config(), client, emit_event=emit)

    assert policy is inner
    assert isinstance(ranker, NullDecisionRanker)
    assert ranker.rank("plan_quality", [{"label": "original", "content": "keep order"}]) is None
    assert ranker.as_tools() == []
    client.post.assert_not_called()
    emit.assert_not_called()


def command():
    """Return a frozen built-in model and host-owned decision definitions."""

    return {
        "run_uuid": "run-1",
        "decision_model": {
            "model_ref": "model-1",
            "gates": {
                "search_needed": {"kind": "noul", "threshold": 0.5, "margin": 0.1},
                "answer_supported": {"kind": "choice"},
            },
            "analyses": {
                "evidence_relevance": {
                    "kind": "score",
                    "rubric": ["irrelevant", "related", "direct_answer"],
                },
            },
        },
    }


def config():
    """Return gateway configuration without any provider credential."""

    return SimpleNamespace(ai_gateway_url="http://backend/api/v1/lens/lensnode/ai-gateway/", token="node-token")


def test_predicate_uses_backend_gateway_and_never_loads_a_plugin():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "answers": [{"type": "predicate", "name": "search_needed", "probability": 0.8}],
                "usage": {"total_tokens": 7},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        runner = DecisionRunner(command(), config(), client)
        assert runner.evaluate("search_needed", question="Find the API") == pytest.approx(0.8)
    assert requests[0].url.path == "/api/v1/lens/lensnode/decision-gateway/"
    assert requests[0].headers["Authorization"] == "Bearer node-token"


def test_choice_preserves_selected_label_without_inventing_probabilities():
    def handle(request):
        return httpx.Response(
            200, json={"answers": [{"type": "choice", "name": "answer_supported", "choice": "supported"}], "usage": {}}
        )

    events = []
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        runner = DecisionRunner(
            command(), config(), client, emit_event=lambda event, payload: events.append((event, payload))
        )
        assert runner.evaluate_choice("answer_supported", question="Review this answer") == "supported"
    assert events[-1][1]["value"] == "supported"
    assert not any("plugin" in event for event, _ in events)


def test_gateway_waits_for_the_frozen_decision_timeout():
    """The node must allow the backend to finish within the frozen model deadline."""

    requests = []
    selected = command()
    selected["decision_model"]["gates"]["answer_supported"]["timeout_seconds"] = 10

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"answers": [
            {"type": "choice", "name": "answer_supported", "choice": "supported"},
        ]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        runner = DecisionRunner(selected, config(), client)
        assert runner.evaluate_choice("answer_supported", question="Review this answer") == "supported"
    assert requests[0].extensions["timeout"]["read"] == 11


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "refusal"},
        {"type": "predicate", "name": "search_needed", "probability": True},
        {"type": "predicate", "name": "wrong", "probability": 0.8},
    ],
)
def test_refusals_and_invalid_answers_fall_back(answer):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"answers": [answer]}))
    ) as client:
        assert DecisionRunner(command(), config(), client).evaluate("search_needed", question="Find the API") is None


def test_ranking_uses_numeric_scores_without_probability_distributions():
    def handle(request):
        import json

        payload = json.loads(request.content)
        score = 2.0 if payload["input"] == "direct" else 0.2
        return httpx.Response(
            200,
            json={
                "answers": [{"type": "score", "name": "evidence_relevance", "score": score}],
                "usage": {"total_tokens": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        ranked = DecisionRanker(command(), config(), client).rank(
            "evidence_relevance", [{"label": "weak", "content": "related"}, {"label": "strong", "content": "direct"}]
        )
    assert [entry["label"] for entry in ranked["ranked"]] == ["strong", "weak"]
    assert ranked["ranked"][0]["score"] == 2.0
    assert ranked["ranked"][0]["probability"] is None
    assert ranked["usage"]["total_tokens"] == 4


def test_ranking_does_not_contend_for_the_gateway_run_budget_lock():
    budget_lock = threading.Lock()

    def handle(request):
        if not budget_lock.acquire(timeout=0.2):
            return httpx.Response(429, json={"detail": "Run token budget is unavailable."})
        try:
            time.sleep(0.3)
            return httpx.Response(
                200,
                json={
                    "answers": [{"type": "score", "name": "evidence_relevance", "score": 1}],
                    "usage": {"total_tokens": 2},
                },
            )
        finally:
            budget_lock.release()

    candidates = [{"label": str(index), "content": str(index)} for index in range(3)]
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        ranked = DecisionRanker(command(), config(), client).rank("evidence_relevance", candidates)

    assert len(ranked["ranked"]) == len(candidates)
    assert ranked["failed"] == []
    assert ranked["usage"]["total_tokens"] == 6


def test_refusal_keeps_metered_usage_in_the_failed_model_event():
    events = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"answers": [{"type": "refusal"}], "usage": {"total_tokens": 3}})
        )
    ) as client:
        runner = DecisionRunner(
            command(), config(), client, emit_event=lambda event, payload: events.append((event, payload))
        )
        assert runner.evaluate("search_needed", question="Find the API") is None
    failed = next(payload for event, payload in events if event == "model.failed")
    assert failed["usage"]["total_tokens"] == 3
    assert failed["fallback_reason"] == "refusal"


def test_decision_timeout_falls_back_without_retrying():
    requests = []

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("Decision timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        runner = DecisionRunner(command(), config(), client)
        assert runner.evaluate("search_needed", question="Find the API") is None
    assert len(requests) == 1
