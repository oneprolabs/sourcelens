"""Explicit scoring uses the selected model without claiming business evidence."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest


def scoring_command():
    """Return a model selection with explicit score authorization."""

    return {
        "run_uuid": "run-score",
        "decision_model": {"model_ref": "chosen-model", "score": {"kind": "score", "timeout_seconds": 3}},
    }


def build_tool(command, client):
    """Build a scorer through the runtime's public tool factory."""

    from lensnode.agent_runtime.builtin_decisions import build_decision_score_tool

    config = SimpleNamespace(ai_gateway_url="http://backend/api/lens/lensnode/ai-gateway/", token="node-token")
    return build_decision_score_tool(command, config, client)


def test_no_tool_without_a_frozen_score_binding():
    with httpx.Client() as client:
        assert build_tool({}, client) is None
        assert build_tool({"decision_model": {"model_ref": "chosen-model"}}, client) is None


def test_score_returns_numeric_rubric_position_and_metered_usage():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json={"answers": [{"type": "score", "name": "score", "score": 1.6}], "usage": {"total_tokens": 5}}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        scorer = build_tool(scoring_command(), client)
        result = json.loads(scorer.invoke({
            "state": "MR 42 merged, validation unavailable.", "instructions": "Assess completion from these facts.",
            "criteria": ["not started", "in progress", "completed"],
        }))
    assert result["ok"] is True
    assert result["score"] == pytest.approx(1.6)
    assert result["usage"]["total_tokens"] == 5
    assert requests[0]["model_ref"] == "chosen-model"
    assert requests[0]["source"] == "decision_score"
    assert requests[0]["questions"][0]["levels"] == [
        {"label": "not started"}, {"label": "in progress"}, {"label": "completed"},
    ]
    assert "capability_family" not in scorer.metadata


@pytest.mark.parametrize("answer", [{"type": "refusal"}, {"type": "score", "name": "score", "score": 7}])
def test_unavailable_scores_never_invent_a_rating(answer):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"answers": [answer]}))
    ) as client:
        result = json.loads(build_tool(scoring_command(), client).invoke({
            "state": "No completion evidence.", "instructions": "Assess completion.", "criteria": ["pending", "done"],
        }))
    assert result["ok"] is False
    assert "score" not in result


def test_score_budget_survives_runtime_restoration():
    command = scoring_command()
    state = {"used": 16}
    from lensnode.agent_runtime.builtin_decisions import build_decision_score_tool

    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("Budget exhausted"))) as client:
        scorer = build_decision_score_tool(command, SimpleNamespace(), client, runtime_state=state)
        result = json.loads(scorer.invoke({
            "state": "Evidence", "instructions": "Score", "criteria": ["pending", "done"],
        }))
    assert result["ok"] is False
    assert result["reason"] == "budget_exceeded"


def test_parallel_score_requests_do_not_contend_for_the_run_budget():
    budget_lock = threading.Lock()

    def handle(request):
        if not budget_lock.acquire(timeout=0.1):
            return httpx.Response(429)
        try:
            time.sleep(0.2)
            return httpx.Response(200, json={"answers": [{"type": "score", "name": "score", "score": 0.9}]})
        finally:
            budget_lock.release()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        scorer = build_tool(scoring_command(), client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(scorer.invoke, {
                "state": f"Person {index}", "instructions": "Score completion", "criteria": ["pending", "done"],
            }) for index in range(2)]
            results = [json.loads(future.result()) for future in futures]
    assert all(result["ok"] for result in results)
