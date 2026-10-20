"""Keep execution, source coverage and answer review independent at completion."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from lensnode.agent_runtime.decision_policy import GateDecisionPolicy, ModelDecisionPolicy
from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.outcomes import _finalize_runtime_outcome


def finalize(*, evidence=None, verdicts=None, truncated=False):
    """Finalize an otherwise successful execution without remote services."""

    return _finalize_runtime_outcome(
        capability_middleware=None, evidence_requirement="none", truncated=truncated,
        stop_reason="turn_limit" if truncated else None,
        runtime_evidence=evidence, decision_gates=verdicts,
    )


def test_supported_answer_can_complete_with_a_source_coverage_warning():
    outcome, detail = finalize(evidence={"evidence_completeness": "incomplete"},
                               verdicts={"answer_supported": "supported"})

    assert outcome == "completed"
    assert detail["assessment"] == {"execution": "completed", "coverage": "incomplete", "review": "supported"}
    assert detail["warnings"] == ["evidence_incomplete"]
    assert "reason" not in detail


@pytest.mark.parametrize("verdicts,warning", [
    ({"review_available": False, "answer_supported": "inconclusive"}, "review_unavailable"),
    ({"review_completeness": "incomplete", "answer_supported": "unsupported"}, "review_material_incomplete"),
])
def test_missing_review_is_unknown_without_invalidating_completed_execution(verdicts, warning):
    outcome, detail = finalize(verdicts=verdicts)

    assert outcome == "completed"
    assert detail["assessment"]["review"] == "inconclusive"
    assert warning in detail["warnings"]
    assert "reason" not in detail


def test_unsupported_answer_is_not_hidden_by_a_source_coverage_warning():
    outcome, detail = finalize(
        evidence={"evidence_completeness": "incomplete"},
        verdicts={"answer_supported": "unsupported", "evidence_sufficient": True, "evidence_strength": "direct"},
    )

    assert outcome == "partial"
    assert detail["reason"] == "evidence_insufficient"
    assert detail["assessment"] == {"execution": "completed", "coverage": "incomplete", "review": "unsupported"}
    assert detail["warnings"] == ["evidence_incomplete"]


def test_execution_limit_and_failed_review_both_remain_observable():
    outcome, detail = finalize(truncated=True, verdicts={"answer_supported": "unsupported"})

    assert outcome == "partial"
    assert detail["reason"] == "turn_limit"
    assert detail["blocking_reasons"] == ["turn_limit", "evidence_insufficient"]
    assert detail["assessment"]["execution"] == "partial"
    assert detail["assessment"]["review"] == "unsupported"


def test_explicitly_required_source_gap_still_means_partial():
    outcome, detail = finalize(evidence={"required_sources": ["plugin:source"],
                                         "evidence_completeness": "incomplete"},
                               verdicts={"answer_supported": "supported"})

    assert outcome == "partial"
    assert detail["reason"] == "source_coverage_incomplete"
    assert detail["assessment"]["coverage"] == "incomplete"


def test_unknown_review_does_not_hide_an_execution_failure():
    outcome, detail = finalize(truncated=True, verdicts={"review_available": False})

    assert outcome == "partial"
    assert detail["reason"] == "turn_limit"
    assert detail["assessment"]["review"] == "inconclusive"


def test_canonical_grounding_gate_is_the_only_review_call_when_all_three_are_bound():
    calls = []

    class Runner:
        def evaluate(self, gate, **kwargs):
            calls.append(gate)
            return 0.0

        def evaluate_choice(self, gate, **kwargs):
            calls.append(gate)
            return "supported"

    policy = GateDecisionPolicy(ModelDecisionPolicy(), Runner(), {
        "answer_supported": {"max_state_chars": 128000},
        "evidence_sufficient": {"max_state_chars": 100, "threshold": 0.5},
        "evidence_strength": {"max_state_chars": 100},
    })
    verdicts = policy.post_run_checks("Summarize", "An observed fact.", {"source": "An observed fact."})

    assert calls == ["answer_supported"]
    assert verdicts == {"answer_supported": "supported"}
    assert policy.review_evidence_limit() == 128000


def test_supported_canonical_review_ignores_stale_legacy_verdicts():
    policy = SimpleNamespace(
        has_evidence_gates=lambda: True,
        post_run_checks=lambda *args: {"answer_supported": "supported", "evidence_sufficient": False},
    )
    gate = EvidenceGateMiddleware(policy, "Summarize")

    assert gate.after_model({"messages": [AIMessage(content="Observed facts.")]}, None) is None
    assert gate.answer_nudges == 0
