"""Terminal contracts shared by normal completion and forced wrap-up."""

import threading
from types import SimpleNamespace

import pytest

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


def test_cancellation_prevents_final_review_and_publication():
    state = run_state()
    state.cancel_event.set()
    with pytest.raises(RunCancelledError):
        finalize_run(state, "Attached.", truncated=False, termination_reason=None)
