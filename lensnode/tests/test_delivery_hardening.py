"""Reproduce interrupted publication and incorrect terminal delivery claims."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.execution import _run_agent_with_turn_limit
from lensnode.agent_runtime.runtime import _enforce_delivery_status


class Policy:
    """Enable convergence without a remote verification model."""

    def has_evidence_gates(self):
        return True

    def post_run_checks(self, *args):
        return {"answer_supported": "supported"}


def test_recent_file_write_preserves_a_bounded_publication_window():
    evidence = {"artifact_required": True, "_deliverables": {}}
    review = {"convergence_nudges": 2, "delivery_turns": 8}
    gate = EvidenceGateMiddleware(Policy(), "report", evidence=evidence, review_state=review)
    state = {"messages": [ToolMessage(
        name="write_file", tool_call_id="write", content="Updated file /report.html"
    )]}

    assert gate.before_model(state, None) is None
    evidence["_deliverables"]["report.html"] = "output-1"
    assert gate.before_model({"messages": [ToolMessage(
        name="save_deliverable", tool_call_id="save", content='{"ok":true}'
    )]}, None) is None
    assert gate.before_model({}, None) == {"jump_to": "end"}


def test_replayed_write_does_not_extend_publication_forever():
    evidence = {"artifact_required": True}
    review = {"convergence_nudges": 2, "delivery_turns": 8}
    state = {"messages": [ToolMessage(name="write_file", tool_call_id="same", content="Updated file /report.html")]}
    first = EvidenceGateMiddleware(Policy(), "report", evidence=evidence, review_state=review)
    assert first.before_model(state, None) is None
    restored = EvidenceGateMiddleware(Policy(), "report", evidence=evidence, review_state=review)
    assert restored.before_model(state, None) is None
    assert restored.before_model(state, None) == {"jump_to": "end"}


@pytest.mark.parametrize("name,content,status", [
    ("write_file", "Error: file could not be written", "error"),
    ("read_file", "Previously written report", "success"),
])
def test_unrelated_or_failed_tools_do_not_grant_publication_grace(name, content, status):
    gate = EvidenceGateMiddleware(Policy(), "report", evidence={"artifact_required": True},
                                  review_state={"convergence_nudges": 2, "delivery_turns": 8})
    state = {"messages": [ToolMessage(name=name, content=content, status=status, tool_call_id="tool")]}
    assert gate.before_model(state, None) == {"jump_to": "end"}


def test_convergence_end_is_an_explicit_truncation():
    class Agent:
        def stream(self, *args, **kwargs):
            yield {"messages": [ToolMessage(name="read_file", tool_call_id="read", content="Collected facts")]}

    model = SimpleNamespace(stop_reason=None, invoke=lambda *args, **kwargs: AIMessage(content="Partial answer"))
    result = _run_agent_with_turn_limit(
        Agent(), [{"role": "user", "content": "question"}], 0, model=model,
        execution_evidence={"convergence_exhausted": True},
    )

    assert result == ("Partial answer", True, "evidence_convergence_limit")


def test_missing_artifact_cannot_publish_a_success_claim():
    answer = "HTML 已生成并交付（save_deliverable 已调用）。"
    result = _enforce_delivery_status(answer, {"answer_language": "zh-CN"}, "artifact")
    assert "已生成并交付" not in result
    assert "未完成" in result


def test_missing_artifact_status_does_not_switch_an_english_answer_to_profile_chinese():
    result = _enforce_delivery_status("The report is attached.", {"answer_language": "zh-CN"}, "artifact")
    assert result.startswith("File delivery is incomplete.")


def test_successful_artifact_and_text_answers_remain_intact():
    answer = "The report is attached."
    command = {"_deliverables": {"report.html": "output-1"}}
    assert _enforce_delivery_status(answer, command, "artifact") == answer
    assert _enforce_delivery_status("Business summary", {}, "tool_result") == "Business summary"
