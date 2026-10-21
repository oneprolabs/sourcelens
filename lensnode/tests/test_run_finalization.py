"""Terminal contracts shared by normal completion and forced wrap-up."""

import threading
from types import SimpleNamespace

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage

from lensnode.agent_runtime.decision_policy import build_decision_policy
from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.execution import _run_agent_with_turn_limit
from lensnode.agent_runtime.finalization import finalize_run
from lensnode.agent_runtime.runtime import _build_evidence_middleware
from lensnode.gateway_model import LensGatewayChatModel, RunCancelledError
from lensnode.plugin_agent import AgentExtension, AgentExtensions


def run_state(*, evidence=None, manifest=None, verdicts=None, checks=()):
    """Build a completed execution with real extension composition."""

    class Extension(AgentExtension):
        def completion_checks(self, answer):
            return list(checks)

    class Policy:
        def post_run_checks(self, question, answer, evidence):
            return dict(verdicts or {})

    evidence = dict(evidence or {})
    evidence["_deliverables"] = manifest if manifest is not None else {}
    return SimpleNamespace(
        command={"answer_language": "en", "_deliverables": evidence["_deliverables"]},
        runtime_evidence=evidence,
        agent_extensions=AgentExtensions([Extension()] if checks else []),
        decision_policy=Policy(),
        evidence_requirement="tool_result",
        required_capabilities=[],
        capability_middleware=None,
        runtime_mode=SimpleNamespace(execution_gates=True),
        model=SimpleNamespace(stop_reason=None),
        question="Summarize the available evidence.",
        cancel_event=threading.Event(),
    )


def mode_review_state(monkeypatch, *, command=None, evidence=None, checks=(), verdict="supported"):
    """Assemble the real review policy and middleware around a model-call recorder."""

    state = run_state(evidence=evidence, checks=checks)
    state.command.update(command or {})
    state.command["decision_model"] = {
        "model_ref": "review-model",
        "gates": {"answer_supported": {"max_state_chars": 100_000}},
    }
    state.decision_policy = build_decision_policy(state.command, SimpleNamespace(), SimpleNamespace())
    reviewed = []

    def review(gate, **kwargs):
        reviewed.append(gate)
        return verdict

    monkeypatch.setattr(state.decision_policy.runner, "evaluate_choice", review)
    state.evidence_middleware = _build_evidence_middleware(state)
    return state, reviewed


def test_deep_mode_without_reviewer_reports_unavailable_instead_of_verified():
    """Selecting deep reasoning cannot certify an answer without a review binding."""

    state = run_state()
    state.command["deep_thinking"] = True
    state.decision_policy = build_decision_policy(state.command, SimpleNamespace(), SimpleNamespace())
    state.evidence_middleware = None
    result = finalize_run(state, "Observed work.", truncated=False, termination_reason=None)
    assert result.outcome == "completed"
    assert result.termination_detail["assessment"]["review"] == "inconclusive"
    assert "review_unavailable" in result.termination_detail["warnings"]


def test_normal_mode_does_not_report_truncated_review_material_as_an_attempted_review(monkeypatch):
    state, reviewed = mode_review_state(
        monkeypatch, command={"deep_thinking": False}, evidence={"review_completeness": "incomplete"}
    )
    result = finalize_run(state, "Observed work.", truncated=False, termination_reason=None)

    assert reviewed == []
    assert result.termination_detail["assessment"]["review"] == "not_requested"
    assert "review_material_incomplete" not in result.termination_detail.get("warnings", [])


def test_normal_mode_does_not_reuse_a_checkpoint_review(monkeypatch):
    state, reviewed = mode_review_state(monkeypatch, command={"deep_thinking": False})
    state.resume_state = SimpleNamespace(decision_gates={"answer_supported": "unsupported"})
    result = finalize_run(state, "Observed work.", truncated=False, termination_reason=None)

    assert reviewed == []
    assert result.outcome == "completed"
    assert result.termination_detail["assessment"]["review"] == "not_requested"
    assert "decision_gates" not in result.termination_detail


def test_normal_mode_keeps_convergence_guards_without_calling_the_reviewer(monkeypatch):
    state, reviewed = mode_review_state(monkeypatch, command={"deep_thinking": False})
    state.evidence_middleware.interval = 1
    nudge = state.evidence_middleware.before_model({"messages": []}, None)

    assert nudge["messages"]
    assert state.evidence_middleware.convergence_nudges == 1
    assert reviewed == []


@pytest.mark.parametrize("code", ["tool_evidence_unread", "artifact_delivery_failed"])
def test_normal_mode_keeps_programmatic_completion_checks(monkeypatch, code):
    state, reviewed = mode_review_state(
        monkeypatch, command={"deep_thinking": False}, checks=[{"code": code}]
    )
    candidate = {"messages": [AIMessage(content="The report is attached.")]}
    correction = state.evidence_middleware.after_model(candidate, None)
    assert correction["jump_to"] == "model"
    result = finalize_run(state, "The report is attached.", truncated=False, termination_reason=None)

    assert reviewed == []
    assert result.outcome == "partial"
    assert result.termination_detail["completion_issues"] == [{"code": code}]
    assert result.deliverables == {}


def test_normal_mode_keeps_required_attachment_checks_without_review(monkeypatch):
    state, reviewed = mode_review_state(monkeypatch, command={"deep_thinking": False})
    state.evidence_requirement = "artifact"
    result = finalize_run(state, "The report is attached.", truncated=False, termination_reason=None)

    assert reviewed == []
    assert result.outcome == "partial"
    assert result.termination_detail["capability"] == "artifact_delivery"


@pytest.mark.parametrize("enabled,outcome", [(False, "completed"), (True, "partial")])
def test_only_deep_mode_can_request_semantic_correction(monkeypatch, enabled, outcome):
    state, reviewed = mode_review_state(
        monkeypatch, command={"deep_thinking": enabled}, verdict="unsupported"
    )
    correction = state.evidence_middleware.after_model({"messages": [AIMessage(content="A claim.")]}, None)
    result = finalize_run(state, "A claim.", truncated=False, termination_reason=None)

    assert (correction is not None and correction.get("jump_to") == "model") is enabled
    assert bool(reviewed) is enabled
    assert result.outcome == outcome


@pytest.mark.parametrize("command,review_calls,review", [
    ({"deep_thinking": False}, 0, "not_requested"),
    ({"deep_thinking": True}, 1, "supported"),
    ({}, 1, "supported"),
])
def test_real_graph_review_follows_thinking_mode(monkeypatch, command, review_calls, review):
    state, reviewed = mode_review_state(
        monkeypatch, command=command, evidence={"evidence_completeness": "complete"}
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "message": {"content": "Observed work.", "finish_reason": "stop"},
            "usage": {"prompt_tokens": 9, "completion_tokens": 1},
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        state.model = LensGatewayChatModel(
            model_ref="main-model", ai_gateway_url="http://gateway/", token="test", http_client=client
        )
        graph = create_agent(state.model, tools=[], middleware=[state.evidence_middleware])
        answer, truncated, reason = _run_agent_with_turn_limit(
            graph, [{"role": "user", "content": state.question}], max_turns=0, model=state.model
        )
        result = finalize_run(state, answer, truncated=truncated, termination_reason=reason)

    assert result.answer == "Observed work."
    assert result.outcome == "completed"
    assert len(requests) == 1
    assert len(reviewed) == review_calls
    assert result.termination_detail["assessment"]["review"] == review


@pytest.mark.parametrize("evidence,verdicts,reason", [
    ({}, {"answer_supported": "supported"}, None),
    ({"evidence_completeness": "incomplete"}, {"answer_supported": "supported"}, None),
    ({}, {"review_completeness": "incomplete"}, None),
    ({}, {"answer_supported": "unsupported"}, "evidence_insufficient"),
])
def test_final_result_keeps_source_and_review_failures_distinct(evidence, verdicts, reason):
    state = run_state(evidence=evidence, verdicts=verdicts)
    result = finalize_run(state, "Observed work.", truncated=False, termination_reason=None)

    assert result.answer == "Observed work."
    assert result.outcome == ("partial" if reason else "completed")
    assert result.termination_detail.get("reason") == reason
    assert result.termination_detail["decision_gates"] == verdicts


def test_checked_attachment_survives_source_gap_and_budget_wrapup():
    state = run_state(evidence={"evidence_completeness": "incomplete"},
                      manifest={"deliverables/report.html": "output-1"})
    state.evidence_requirement = "artifact"
    state.runtime_evidence["artifact_required"] = True
    result = finalize_run(state, "The partial report is attached.", truncated=True,
                          termination_reason="token_budget_wrapup")

    assert result.outcome == "partial"
    assert result.termination_detail["reason"] == "token_budget_wrapup"
    assert result.termination_detail["warnings"] == ["evidence_incomplete"]
    assert result.termination_detail["trigger"] == "token_budget_wrapup"
    assert result.deliverables == {"deliverables/report.html": "output-1"}
    assert state.command["_deliverables"] == result.deliverables


def test_rejected_completion_clears_every_publication_candidate():
    state = run_state(manifest={"deliverables/report.html": "output-1"},
                      checks=[{"code": "unsupported_claim"}])
    result = finalize_run(state, "An unsupported assertion.", truncated=False, termination_reason=None)

    assert "unsupported assertion" not in result.answer
    assert result.outcome == "partial"
    assert result.termination_detail["completion_issues"] == [{"code": "unsupported_claim"}]
    assert result.deliverables == state.command["_deliverables"] == state.runtime_evidence["_deliverables"] == {}


def test_missing_required_attachment_cannot_be_reported_as_completed():
    state = run_state(verdicts={"answer_supported": "supported"})
    state.evidence_requirement = "artifact"
    result = finalize_run(state, "The file is attached.", truncated=False, termination_reason=None)

    assert result.outcome == "partial"
    assert result.termination_detail["capability"] == "artifact_delivery"
    assert "No downloadable attachment" in result.answer
    assert not result.deliverables


def test_final_result_is_a_snapshot_of_publication_state():
    state = run_state(manifest={"deliverables/report.html": "output-1"})
    result = finalize_run(state, "Attached.", truncated=False, termination_reason=None)
    state.command["_deliverables"].clear()

    assert result.deliverables == {"deliverables/report.html": "output-1"}


def test_publication_manifest_overrides_a_stale_evidence_copy():
    state = run_state(manifest={"deliverables/old.html": "old-output"})
    state.command["_deliverables"] = {}
    state.evidence_requirement = "artifact"
    result = finalize_run(state, "The file is attached.", truncated=False, termination_reason=None)

    assert result.outcome == "partial"
    assert not result.deliverables
    assert state.runtime_evidence["_deliverables"] is state.command["_deliverables"]


def test_cancellation_prevents_final_review_and_publication():
    state = run_state()
    state.cancel_event.set()
    with pytest.raises(RunCancelledError):
        finalize_run(state, "Attached.", truncated=False, termination_reason=None)


@pytest.mark.parametrize("offloaded", [False, True])
def test_changed_wrapup_answer_keeps_graph_evidence_and_coverage(offloaded):
    """A changed answer needs fresh review against the same actual tool material."""

    reviewed = []

    class Policy:
        def has_evidence_gates(self):
            return True

        def post_run_checks(self, question, answer, evidence):
            reviewed.append(evidence)
            return {"answer_supported": "supported"}

    state = run_state()
    state.decision_policy = Policy()
    gate = EvidenceGateMiddleware(state.decision_policy, state.question, evidence=state.runtime_evidence)
    state.evidence_middleware = gate
    content = ("Tool result too large, the result was saved in the filesystem. Preview: a documented fact."
               if offloaded else "A documented fact.")
    gate.after_model({"messages": [
        ToolMessage(name="read_workspace_file", tool_call_id="read", content=content),
        AIMessage(content="Initial answer."),
    ]}, None)
    result = finalize_run(state, "A revised final answer.", truncated=False, termination_reason=None)

    assert reviewed[-1]["retrieved_evidence"] == reviewed[0]["retrieved_evidence"]
    assert len(reviewed) == 2
    assert result.outcome == "completed"
    if offloaded:
        assert result.termination_detail["assessment"]["review"] == "inconclusive"
        assert result.termination_detail["warnings"] == ["review_material_incomplete"]


def test_review_failure_retains_answer_as_unverified_instead_of_losing_output():
    class Policy:
        def has_evidence_gates(self):
            return True

        def post_run_checks(self, *args):
            raise RuntimeError("Review service unavailable")

    state = run_state(manifest={"deliverables/report.html": "output-1"})
    state.decision_policy = Policy()
    state.evidence_middleware = EvidenceGateMiddleware(state.decision_policy, state.question,
                                                      evidence=state.runtime_evidence)
    result = finalize_run(state, "Available findings are attached.", truncated=False, termination_reason=None)

    assert result.answer == "Available findings are attached."
    assert result.outcome == "completed"
    assert result.termination_detail["warnings"] == ["review_unavailable"]
    assert result.termination_detail["assessment"]["review"] == "inconclusive"
    assert result.termination_detail["decision_gates"]["answer_supported"] == "inconclusive"
    assert result.deliverables == {"deliverables/report.html": "output-1"}


def test_cancellation_during_review_prevents_final_acceptance():
    state = run_state()

    class Policy:
        def post_run_checks(self, *args):
            state.cancel_event.set()
            return {"answer_supported": "supported"}

    state.decision_policy = Policy()
    with pytest.raises(RunCancelledError):
        finalize_run(state, "An answer.", truncated=False, termination_reason=None)


def test_turn_limit_before_first_answer_retains_tools_for_terminal_review():
    """Forced synthesis must be reviewed even if after_model never reviewed a draft."""

    reviewed = []

    class Policy:
        def has_evidence_gates(self):
            return True

        def post_run_checks(self, question, answer, evidence):
            reviewed.append(evidence)
            return {"answer_supported": "supported"}

    class Agent:
        def stream(self, *args, **kwargs):
            yield {"messages": [
                AIMessage(content="", tool_calls=[{"id": "read", "name": "read_file", "args": {}}]),
                ToolMessage(name="read_file", tool_call_id="read", content="A documented fact."),
            ]}

    state = run_state()
    state.decision_policy = Policy()
    state.evidence_middleware = EvidenceGateMiddleware(state.decision_policy, state.question,
                                                      evidence=state.runtime_evidence)
    state.model.invoke = lambda *args, **kwargs: AIMessage(content="A final synthesis.")
    answer, truncated, reason = _run_agent_with_turn_limit(
        Agent(), [{"role": "user", "content": state.question}], 1, model=state.model,
        on_review_state=state.evidence_middleware.observe_tools,
    )
    result = finalize_run(state, answer, truncated=truncated, termination_reason=reason)

    assert result.answer == "A final synthesis."
    assert reviewed[-1]["retrieved_evidence"][0]["content"] == "A documented fact."
    assert result.termination_detail["reason"] == "turn_limit"


def test_new_tool_evidence_invalidates_an_unchanged_answers_review():
    reviewed = []

    class Policy:
        def has_evidence_gates(self):
            return True

        def post_run_checks(self, question, answer, evidence):
            reviewed.append(evidence)
            return {"answer_supported": "supported"}

    state = run_state()
    state.decision_policy = Policy()
    gate = EvidenceGateMiddleware(state.decision_policy, state.question, evidence=state.runtime_evidence)
    state.evidence_middleware = gate
    gate.after_model({"messages": [
        ToolMessage(name="read_file", tool_call_id="read", content="Old evidence."),
        AIMessage(content="An answer."),
    ]}, None)
    gate.observe_tools({"messages": [
        ToolMessage(name="read_file", tool_call_id="read-2", content="Corrected evidence."),
    ]})
    finalize_run(state, "An answer.", truncated=False, termination_reason=None)

    assert len(reviewed) == 2
    assert reviewed[-1]["retrieved_evidence"][0]["content"] == "Corrected evidence."


def test_unavailable_gate_does_not_ask_the_model_to_rewrite_a_supported_answer():
    """A failed reviewer is not evidence that the answer needs correction."""

    class Policy:
        def has_evidence_gates(self):
            return True

        def post_run_checks(self, *args):
            return {"review_available": False, "answer_supported": "inconclusive"}

    state = run_state()
    state.decision_policy = Policy()
    gate = EvidenceGateMiddleware(state.decision_policy, state.question, evidence=state.runtime_evidence)
    state.evidence_middleware = gate
    assert gate.after_model({"messages": [AIMessage(content="Available findings.")]}, None) is None
    result = finalize_run(state, "Available findings.", truncated=False, termination_reason=None)

    assert result.outcome == "completed"
    assert result.termination_detail["warnings"] == ["review_unavailable"]
    assert result.termination_detail["assessment"]["review"] == "inconclusive"
