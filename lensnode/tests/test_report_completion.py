"""Regression coverage for report delivery, source coverage, and bounded wrap-up."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from lensnode.agent_runtime.capabilities import CapabilityBoundaryMiddleware
from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.outcomes import _finalize_runtime_outcome

class _Policy:
    """Enable grounding middleware without a remote model."""

    def has_evidence_gates(self):
        return True

    def post_run_checks(self, *args):
        return {"answer_supported": "supported"}


def test_convergence_reserves_a_bounded_delivery_window_and_records_exhaustion():
    """Search wrap-up leaves time for writing, host staging, and a final answer."""

    evidence = {"artifact_required": True, "_deliverables": {}}
    gate = EvidenceGateMiddleware(_Policy(), "report", evidence=evidence, interval=2, max_convergence_nudges=1)
    state = {"messages": []}
    gate.before_model(state, None)
    assert gate.before_model(state, None)["messages"]
    delivery = gate.before_model(state, None)
    assert "jump_to" not in delivery
    assert "/deliverables/" in delivery["messages"][0].content
    for _ in range(7):
        assert gate.before_model(state, None) is None
    assert gate.before_model(state, None) == {"jump_to": "end"}
    assert evidence["convergence_exhausted"] is True


def test_final_answer_without_required_artifact_is_redirected_to_delivery():
    """A grounded Markdown answer does not fulfill a file request."""

    evidence = {"artifact_required": True, "_deliverables": {}}
    gate = EvidenceGateMiddleware(_Policy(), "report", evidence=evidence)
    correction = gate.after_model({"messages": [AIMessage(content="Here is the report.")]}, None)
    assert correction["jump_to"] == "model"
    assert "/deliverables/" in correction["messages"][0].content
    evidence["_deliverables"]["report.html"] = "output-1"
    assert gate.after_model({"messages": [AIMessage(content="Report ready.")]}, None) is None


def test_partial_activity_coverage_survives_checkpoint_and_marks_report_partial():
    """A successful HTTP result can still represent an incomplete source."""

    middleware = CapabilityBoundaryMiddleware(required_capabilities=["plugin"])
    request = SimpleNamespace(
        tool=SimpleNamespace(metadata={"plugin_key": "gitlab"}),
        tool_call={"name": "gitlab_activity_summary", "args": {"projects": ["*"]}},
    )
    middleware._record_result(
        request,
        ToolMessage(
            content='{"ok":true,"projects_truncated":true,"projects":[],"_evidence":{"complete":false}}',
            tool_call_id="call-1",
        ),
    )
    restored = CapabilityBoundaryMiddleware(required_capabilities=["plugin"])
    restored.restore_state(middleware.export_state())
    outcome, detail = _finalize_runtime_outcome(
        capability_middleware=restored,
        evidence_requirement="tool_result",
        required_capabilities=["plugin"],
        truncated=False,
        stop_reason=None,
        runtime_evidence={"required_sources": ["plugin:gitlab_activity_summary"]},
    )
    assert outcome == "partial"
    assert detail["reason"] == "source_coverage_incomplete"


def test_one_successful_source_does_not_hide_another_missing_required_source():
    """GitLab success cannot make a report with missing GitHub data complete."""

    middleware = SimpleNamespace(
        successful_evidence=[
            {
                "capability": "plugin",
                "tool": "gitlab_activity_summary",
                "source": "plugin:gitlab_activity_summary",
                "request_sha256": "hash",
                "coverage_complete": True,
            }
        ]
    )
    outcome, detail = _finalize_runtime_outcome(
        capability_middleware=middleware,
        evidence_requirement="tool_result",
        required_capabilities=["plugin"],
        truncated=False,
        stop_reason=None,
        runtime_evidence={"required_sources": ["plugin:github_activity_summary", "plugin:gitlab_activity_summary"]},
    )
    assert outcome == "partial"
    assert detail["reason"] == "source_coverage_incomplete"
    assert detail["sources"] == ["plugin:github_activity_summary"]


def test_delivery_budget_survives_checkpoint_resume():
    """Restoring the gate does not grant another delivery window."""

    evidence = {"artifact_required": True}
    review = {}
    gate = EvidenceGateMiddleware(
        _Policy(), "report", evidence=evidence, review_state=review, interval=1, max_convergence_nudges=1
    )
    for _ in range(9):
        gate.before_model({}, None)
    restored = EvidenceGateMiddleware(
        _Policy(), "report", evidence=evidence, review_state=review, interval=1, max_convergence_nudges=1
    )
    assert restored.before_model({}, None) == {"jump_to": "end"}


def test_complete_retry_recovers_same_request_coverage():
    """A complete retry replaces a truncated result for identical parameters."""

    middleware = SimpleNamespace(
        successful_evidence=[
            {"source": "plugin:github_activity_summary", "request_sha256": "same", "coverage_complete": False},
            {"source": "plugin:github_activity_summary", "request_sha256": "same", "coverage_complete": True},
        ],
        successful_capabilities={"plugin"},
        blocked_capabilities={},
        failure_diagnostics=[],
    )
    outcome, detail = _finalize_runtime_outcome(
        capability_middleware=middleware,
        evidence_requirement="tool_result",
        required_capabilities=["plugin"],
        truncated=False,
        stop_reason=None,
        runtime_evidence={"required_sources": ["plugin:github_activity_summary"]},
    )
    assert detail is None or detail.get("reason") != "source_coverage_incomplete"
