"""Regression coverage for report delivery, source coverage, and bounded wrap-up."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from lensnode.agent_runtime.capabilities import CapabilityBoundaryMiddleware
from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.outcomes import _finalize_runtime_outcome
from lensnode.agent_runtime.routing import _apply_delivery_contract


def test_report_skill_requires_artifact_even_when_classifier_only_requires_data():
    """The selected Skill's file contract remains part of task completion."""

    decision = _apply_delivery_contract(
        {
            "route": "plan_execute",
            "intent": "action",
            "evidence_requirement": "tool_result",
            "required_capabilities": ["plugin", "skill"],
        },
        "帮我总结，每个人的本周五工作内容",
        [],
        ["Generate an HTML report and call save_deliverable."],
        [SimpleNamespace(name="github_activity_summary"), SimpleNamespace(name="save_deliverable")],
    )

    assert decision["evidence_requirement"] == "artifact"
    assert "artifact_delivery" in decision["required_capabilities"]


def test_missing_html_followup_executes_but_unrelated_html_question_does_not():
    """A complaint about the requested missing report resumes delivery."""

    route = {
        "route": "direct_answer",
        "intent": "clarification",
        "evidence_requirement": "none",
        "required_capabilities": [],
    }
    skills = ["Generate an HTML report and call save_deliverable."]
    tools = [SimpleNamespace(name="github_activity_summary"), SimpleNamespace(name="save_deliverable")]
    history = [{"role": "user", "content": "总结每个人的本周五工作内容"}]
    decision = _apply_delivery_contract(route, "为什么没有生成 html 呢", history, skills, tools)

    assert decision["route"] == "plan_execute"
    assert decision["evidence_requirement"] == "artifact"
    assert _apply_delivery_contract(route, "HTML 是什么？", history, skills, tools) == route


class _Policy:
    """Enable grounding middleware without a remote model."""

    def has_evidence_gates(self):
        return True

    def post_run_checks(self, *args):
        return {"answer_supported": "supported"}


def test_convergence_reserves_a_bounded_delivery_window_and_records_exhaustion():
    """Search wrap-up leaves time for writing, saving, and a final answer."""

    evidence = {"artifact_required": True, "_deliverables": {}}
    gate = EvidenceGateMiddleware(_Policy(), "report", evidence=evidence, interval=2, max_convergence_nudges=1)
    state = {"messages": []}
    gate.before_model(state, None)
    assert gate.before_model(state, None)["messages"]
    delivery = gate.before_model(state, None)
    assert "jump_to" not in delivery
    assert "save_deliverable" in delivery["messages"][0].content
    for _ in range(2):
        assert gate.before_model(state, None) is None
    assert gate.before_model(state, None) == {"jump_to": "end"}
    assert evidence["convergence_exhausted"] is True


def test_final_answer_without_required_artifact_is_redirected_to_delivery():
    """A grounded Markdown answer does not fulfill a file request."""

    evidence = {"artifact_required": True, "_deliverables": {}}
    gate = EvidenceGateMiddleware(_Policy(), "report", evidence=evidence)
    correction = gate.after_model({"messages": [AIMessage(content="Here is the report.")]}, None)
    assert correction["jump_to"] == "model"
    assert "save_deliverable" in correction["messages"][0].content
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
            content='{"ok":true,"projects_truncated":true,"projects":[]}',
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
    for _ in range(4):
        gate.before_model({}, None)
    restored = EvidenceGateMiddleware(
        _Policy(), "report", evidence=evidence, review_state=review, interval=1, max_convergence_nudges=1
    )
    assert restored.before_model({}, None) == {"jump_to": "end"}


def test_delivery_contract_respects_text_only_and_unrelated_recent_task():
    """A report in older history cannot turn an unrelated continuation into a report."""

    route = {"route": "direct_answer", "evidence_requirement": "none"}
    tools = [SimpleNamespace(name="github_activity_summary"), SimpleNamespace(name="save_deliverable")]
    skills = ["call save_deliverable"]
    history = [{"role": "user", "content": "总结工作内容"}, {"role": "user", "content": "解释 Python 装饰器"}]
    assert _apply_delivery_contract(route, "继续", history, skills, tools) == route
    assert _apply_delivery_contract(route, "总结工作内容，仅用文字", [], skills, tools) == route


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
