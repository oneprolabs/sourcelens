"""Use framework request overrides without reducing final-answer reasoning."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from lensnode.agent_runtime.capabilities import CapabilityBoundaryMiddleware
from lensnode.agent_runtime.messages import runtime_message
from lensnode.agent_runtime.summarization import build_summarization_middleware
from lensnode.config import load_config
from lensnode.gateway_model import LensGatewayChatModel


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("phase,expected", [
    ("planning", "medium"), ("execution", "low"), ("completed", None),
    ("review", None), ("compacted_review", None), ("failed_tool", None), ("mixed_tool_batch", None), ("no_tools", None),
    ("explicit_request", "high"), ("explicit_model", "high"),
])
def test_reasoning_uses_plan_state_and_preserves_explicit_overrides(phase, expected, asynchronous):
    """Only successful tool follow-ups during an active plan use lower effort."""

    model = LensGatewayChatModel(
        model_ref="model", ai_gateway_url="http://gateway/", token="test",
        reasoning_effort="high" if phase == "explicit_model" else None,
    )
    messages = [HumanMessage(content="Prepare a sourced report.")]
    if phase == "review":
        messages.append(runtime_message("Correct the unsupported claim."))
    if phase == "mixed_tool_batch":
        messages.append(ToolMessage(content="Tool failed", tool_call_id="failed", status="error"))
    messages.append(ToolMessage(
        content="source facts", tool_call_id="fetch", status="error" if phase == "failed_tool" else "success",
    ))
    settings = {"temperature": 0}
    if phase == "explicit_request":
        settings["reasoning_effort"] = "high"
    request = ModelRequest(
        model=model, messages=messages, tools=[] if phase == "no_tools" else [{"name": "read_file"}],
        model_settings=settings,
        state={"messages": messages, "todos": [{"content": "Verify sources", "status": (
            "completed" if phase == "completed" else "in_progress"
        )}]},
    )
    middleware = CapabilityBoundaryMiddleware(
        require_initial_plan=True, planning_reasoning_effort="medium", execution_reasoning_effort="low",
        review_state={"answer_nudges": 1} if phase == "compacted_review" else {},
    )
    middleware.initial_plan_exists = phase != "planning"

    async def handle_async(updated):
        return updated

    updated = (
        asyncio.run(middleware.awrap_model_call(request, handle_async))
        if asynchronous else middleware.wrap_model_call(request, lambda updated: updated)
    )

    assert updated.model_settings.get("reasoning_effort", model.reasoning_effort) == expected
    assert updated.model_settings["temperature"] == 0
    assert request.model_settings == settings


def test_framework_override_reaches_gateway_and_final_call_restores_provider_default():
    """Changing effort on one call must not mutate the shared model or next call."""

    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "Verified report"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model = LensGatewayChatModel(
            model_ref="model", ai_gateway_url="http://gateway/", token="test", http_client=client,
        )
        middleware = CapabilityBoundaryMiddleware(execution_reasoning_effort="low")
        messages = [HumanMessage(content="Prepare a report."), ToolMessage(content="Facts", tool_call_id="fetch")]
        request = ModelRequest(
            model=model, messages=messages, tools=[{"name": "read_file"}],
            state={"messages": messages, "todos": [{"content": "Verify", "status": "in_progress"}]},
        )
        middleware.wrap_model_call(request, lambda updated: model.invoke(messages, **updated.model_settings))
        final_request = request.override(state={"todos": [{"content": "Verify", "status": "completed"}]})
        middleware.wrap_model_call(final_request, lambda updated: model.invoke(messages, **updated.model_settings))

    assert payloads[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in payloads[1]
    assert model.reasoning_effort is None


def test_summary_uses_independent_reasoning_setting():
    config = SimpleNamespace(
        ai_gateway_url="http://gateway/", token="test", request_timeout_s=120,
        summary_trigger_tokens=32000, summary_keep_tokens=12000, summary_reasoning_effort="none",
    )
    middleware = build_summarization_middleware(config, "model", lambda *_: None)

    assert middleware.model.reasoning_effort == "none"


@pytest.mark.parametrize("value,expected", [(None, "none"), ("", None), ("medium", "medium")])
def test_reasoning_stage_settings_can_be_overridden_or_disabled(monkeypatch, value, expected):
    for name in ("LENSNODE_EXECUTION_REASONING_EFFORT", "LENSNODE_SUMMARY_REASONING_EFFORT"):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    config = load_config()

    assert config.execution_reasoning_effort == expected
    assert config.summary_reasoning_effort == ("none" if value is None else expected)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("feedback_phase,review_state,expected", [
    ("delivery", {}, "medium"),
    ("analysis", {}, "medium"),
    ("delivery", {"convergence_nudges": 1, "delivery_turns": 2}, "medium"),
    ("delivery", {"answer_nudges": 1}, None),
    (None, {}, None),
])
def test_delivery_analysis_uses_planning_effort_without_overriding_real_review(
    asynchronous, feedback_phase, review_state, expected,
):
    """A completed evidence page's delivery hint is not a failed evidence review."""

    feedback = runtime_message("Write the sourced report.")
    if feedback_phase:
        feedback.additional_kwargs["lensnode_phase"] = feedback_phase
    messages = [
        HumanMessage(content="Prepare a report."),
        ToolMessage(content="All evidence pages read.", tool_call_id="page", status="success"),
        feedback,
    ]
    request = ModelRequest(
        model=LensGatewayChatModel(model_ref="model", ai_gateway_url="http://gateway/", token="test"),
        messages=messages, tools=[{"name": "write_file"}],
        state={"messages": messages, "todos": [{"content": "Write report", "status": "in_progress"}]},
    )
    middleware = CapabilityBoundaryMiddleware(
        planning_reasoning_effort="medium", execution_reasoning_effort="none", review_state=review_state,
    )

    async def handle(updated):
        return updated

    updated = (
        asyncio.run(middleware.awrap_model_call(request, handle))
        if asynchronous else middleware.wrap_model_call(request, lambda updated: updated)
    )
    assert updated.model_settings.get("reasoning_effort") == expected


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("phase", ["delivery", "analysis"])
def test_delivery_file_followup_returns_to_execution_effort(asynchronous, phase):
    """The delivery analysis override must not stick to later file operations."""

    messages = [
        HumanMessage(content="Prepare a report."),
        runtime_message("Analyze the evidence and prepare the report.", phase=phase),
        AIMessage(content="", tool_calls=[{"name": "write_file", "args": {}, "id": "write"}]),
        ToolMessage(content="Report draft saved.", tool_call_id="write", status="success"),
    ]
    request = ModelRequest(
        model=LensGatewayChatModel(model_ref="model", ai_gateway_url="http://gateway/", token="test"),
        messages=messages, tools=[{"name": "read_file"}],
        state={"messages": messages, "todos": [{"content": "Check saved report", "status": "in_progress"}]},
    )
    middleware = CapabilityBoundaryMiddleware(planning_reasoning_effort="medium", execution_reasoning_effort="none")

    async def handle(updated):
        return updated

    updated = (
        asyncio.run(middleware.awrap_model_call(request, handle))
        if asynchronous else middleware.wrap_model_call(request, lambda updated: updated)
    )
    assert updated.model_settings["reasoning_effort"] == "none"
