"""Terminal contracts shared by normal completion and forced wrap-up."""

import threading
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.execution import _run_agent_with_turn_limit
from lensnode.agent_runtime.finalization import finalize_run
from lensnode.gateway_model import RunCancelledError
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


@pytest.mark.parametrize("evidence,verdicts,reason", [
    ({}, {"answer_supported": "supported"}, None),
    ({"evidence_completeness": "incomplete"}, {"answer_supported": "supported"}, "evidence_incomplete"),
    ({}, {"review_completeness": "incomplete"}, "review_material_incomplete"),
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
    assert result.termination_detail["reason"] == "evidence_incomplete"
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
    assert result.outcome == ("partial" if offloaded else "completed")
    if offloaded:
        assert result.termination_detail["reason"] == "review_material_incomplete"


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
    assert result.outcome == "partial"
    assert result.termination_detail["reason"] == "review_material_incomplete"
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
