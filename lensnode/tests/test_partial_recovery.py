"""Regression coverage for evidence retention and report completion progress."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware


class Policy:
    """Capture the complete evidence handed to the reviewer."""

    retain_full_evidence = True

    def has_evidence_gates(self):
        return True

    def post_run_checks(self, question, answer, evidence=None):
        self.evidence = evidence
        return {"answer_supported": "supported"}


def tool_state(name, arguments, content, call_id):
    """Represent a completed tool round with its original request."""

    call = {"id": call_id, "name": name, "args": arguments}
    message = ToolMessage(name=name, tool_call_id=call_id, content=content)
    return call, message, {"messages": [AIMessage(content="", tool_calls=[call]), message]}


def test_snapshot_replaces_successful_pages_without_hiding_failed_reads_or_other_sources():
    """A complete canonical snapshot replaces duplicate transport pages, not unrelated evidence."""

    policy = Policy()
    evidence = {"plugin_evidence": {"ledger": {"records": ["All original source facts."]}},
                "review_replacements": {"read_ledger": "ledger"}}
    gate = EvidenceGateMiddleware(policy, "Report", evidence=evidence)
    for index, (name, content) in enumerate([
        ("read_ledger", '{"records":["Repeated page"]}'),
        ("read_ledger", '{"ok":false}'),
        ("other_source", "Counterevidence."),
    ]):
        call, message, _ = tool_state(name, {}, content, str(index))
        gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)

    gate._verify("Report.", {"messages": []})

    material = policy.evidence["retrieved_evidence"]
    assert [item["content"] for item in material] == ['{"ok":false}', "Counterevidence."]
    assert policy.evidence["evidence_completeness"] == "incomplete"
    assert policy.evidence["plugin_evidence"] == evidence["plugin_evidence"]


def test_missing_replacement_snapshot_does_not_drop_tool_facts():
    """A declaration without its actual snapshot cannot hide evidence."""

    policy = Policy()
    gate = EvidenceGateMiddleware(policy, "Report", evidence={"review_replacements": {"read_ledger": "absent"}})
    _, _, state = tool_state("read_ledger", {}, "Original facts.", "read")
    gate._verify("Report.", state)
    assert policy.evidence["retrieved_evidence"][0]["content"] == "Original facts."


def test_current_attachment_replaces_old_draft_reads_across_checkpoint():
    """Old generated text is never independent source evidence against its corrected final version."""

    policy = Policy()
    review = {}
    evidence = {"answer_artifacts": [{"path": "deliverables/report.html", "content": "Corrected final report."}]}
    gate = EvidenceGateMiddleware(policy, "Report", evidence=evidence, review_state=review)
    call, message, _ = tool_state("read_file", {"file_path": "/deliverables/report.html"},
                                  "Rejected old draft.", "old-read")
    gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)
    restored = EvidenceGateMiddleware(policy, "Report", evidence=evidence,
                                      review_state=json.loads(json.dumps(review)))
    _, _, state = tool_state("read_workspace_file", {"path": "real-source.md"}, "Original source facts.", "source")
    restored._verify("Delivered.", state)
    assert [item["content"] for item in policy.evidence["retrieved_evidence"]] == ["Original source facts."]
    assert policy.evidence["answer_artifacts"] == evidence["answer_artifacts"]


def test_repair_receives_reviewer_source_snapshot_and_current_candidate_without_retrieving_again():
    """Classification-only reviews must not force the repair model to reconstruct all source pages."""

    policy = Policy()
    policy.post_run_checks = lambda *args: {"answer_supported": "unsupported", "evidence_strength": "contradicted"}
    evidence = {
        "plugin_evidence": {"ledger": {"records": [{"id": "REQ-42", "status": "completed"}]}},
        "answer_artifacts": [{"path": "deliverables/report.html", "content": "REQ-42 is unfinished."}],
    }
    gate = EvidenceGateMiddleware(policy, "Report", evidence=evidence)
    result = gate.after_model({"messages": [AIMessage(content="Report attached.")]}, None)
    guidance = result["messages"][0].content
    assert '"REQ-42"' in guidance
    assert '"completed"' in guidance
    assert "REQ-42 is unfinished." in guidance
    assert '"contradicted"' in guidance


def test_full_source_results_survive_compaction_and_more_than_32_calls():
    """A compacted graph must not lose facts already retained by the host."""

    policy = Policy()
    gate = EvidenceGateMiddleware(policy, "What happened?")
    originals = []
    for index in range(35):
        content = (f"Source {index}: " + "documented incident evidence " * 100).strip()
        originals.append(content)
        call, message, state = tool_state("read_workspace_file", {"path": f"doc-{index}"}, content, str(index))
        gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)
        gate.observe_tools(state)
    gate._verify("The incident is documented.", {"messages": [AIMessage(content="Historical summary.")]})

    material = policy.evidence["retrieved_evidence"]
    assert [item["content"] for item in material] == originals
    assert policy.evidence["review_completeness"] == "complete"


def test_repeated_identical_results_are_reviewed_once():
    """Exact repeated reads must not inflate the final review input."""

    policy = Policy()
    gate = EvidenceGateMiddleware(policy, "What happened?")
    for index in range(3):
        call, message, state = tool_state("incident_search", {"query": "incident"}, "Incident remains open.", str(index))
        gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)
        gate.observe_tools(state)
    gate._verify("The incident remains open.")

    assert len(policy.evidence["retrieved_evidence"]) == 1


def test_report_preparation_and_advancing_pages_leave_time_to_write_the_file():
    """Legitimate setup and canonical pagination are progress, not a search loop."""

    gate = EvidenceGateMiddleware(Policy(), "Report", evidence={"artifact_required": True}, interval=2,
                                  max_convergence_nudges=1)
    for index in range(5):
        _, _, state = tool_state("read_file", {"file_path": f"/skills/report/part-{index}.md"}, "format", str(index))
        assert gate.before_model(state, None) is None
    for index in range(10):
        content = json.dumps({"records": [{"id": index}], "read_progress": {
            "snapshot_sha256": "source", "next_offset": index + 1, "complete": False,
        }})
        _, _, state = tool_state("read_tool_evidence", {"offset": index}, content, f"page-{index}")
        assert gate.before_model(state, None) is None
    assert gate.convergence_nudges == 0
    content = json.dumps({"records": [{"id": 10}], "read_progress": {
        "snapshot_sha256": "source", "next_offset": None, "complete": True,
    }})
    _, _, state = tool_state("read_tool_evidence", {"offset": 10}, content, "last-page")
    guidance = gate.before_model(state, None)
    assert "/deliverables/" in guidance["messages"][0].content
    for index in range(3):
        _, _, state = tool_state("append_file", {"path": "/deliverables/report.html", "content": str(index)},
                                 '{"ok":true}', f"write-{index}")
        assert gate.before_model(state, None) is None
    gate.evidence["_deliverables"] = {"report.html": "output"}
    assert gate.after_model({"messages": [AIMessage(content="Report attached.")]}, None) is None
    assert not gate.evidence.get("convergence_exhausted")


def test_repeated_report_reads_still_exhaust_the_convergence_budget():
    """Repeating a completed page cannot masquerade as forward progress."""

    gate = EvidenceGateMiddleware(Policy(), "Report", evidence={"artifact_required": True}, interval=1,
                                  max_convergence_nudges=1)
    content = json.dumps({"read_progress": {"snapshot_sha256": "source", "complete": True, "next_offset": None}})
    _, _, state = tool_state("read_tool_evidence", {"offset": 0}, content, "same-page")
    for _ in range(20):
        result = gate.before_model(state, None)
        if result == {"jump_to": "end"}:
            break
    else:
        raise AssertionError("Repeated reads must converge")
    assert gate.evidence["convergence_exhausted"]


def test_compacted_sources_survive_checkpoint_restoration():
    """Restoring review state retains sources omitted from summarized graph history."""

    policy = Policy()
    review = {}
    gate = EvidenceGateMiddleware(policy, "What happened?", review_state=review)
    call, message, _ = tool_state("incident_search", {"query": "incident"}, "Incident remains open.", "source")
    gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)
    restored = EvidenceGateMiddleware(policy, "What happened?", review_state=json.loads(json.dumps(review)))
    restored._verify("The incident remains open.", {"messages": [AIMessage(content="Summary.")]})

    assert policy.evidence["review_completeness"] == "complete"
    assert policy.evidence["retrieved_evidence"][0]["content"] == message.content


def test_successful_retry_recovers_a_failure_even_after_both_messages_are_compacted():
    """Retained operation identities preserve retry recovery across summaries."""

    policy = Policy()
    gate = EvidenceGateMiddleware(policy, "What happened?")
    call, message, _ = tool_state("incident_search", {"query": "incident"}, '{"ok":false}', "failed")
    gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)
    call, message, _ = tool_state("incident_search", {"query": "incident"}, "Incident remains open.", "retry")
    gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)
    gate._verify("The incident remains open.", {"messages": [AIMessage(content="Summary.")]})

    assert policy.evidence["evidence_completeness"] == "complete"
    assert policy.evidence["retrieved_evidence"][0]["recovered"] is True


@pytest.mark.parametrize("current_index", [None, 0, 1])
@pytest.mark.parametrize("first_succeeded", [False, True])
def test_checkpoint_key_sorting_preserves_operation_order_with_compacted_and_current_results(
    first_succeeded, current_index
):
    """Checkpoint dictionary ordering must not change which request recovered a failure."""

    policy = Policy()
    review = {}
    gate = EvidenceGateMiddleware(policy, "What happened?", review_state=review)
    contents = ["The incident remains open.", '{"ok":false}']
    if not first_succeeded:
        contents.reverse()
    states = []
    for call_id, content in zip(["z-first", "a-second"], contents):
        call, message, state = tool_state("incident_search", {"query": "incident"}, content, call_id)
        gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)
        states.append(state)
    restored = EvidenceGateMiddleware(policy, "What happened?",
                                      review_state=json.loads(json.dumps(review, sort_keys=True)))
    state = {"messages": [AIMessage(content="Summary.")]} if current_index is None else states[current_index]

    restored._verify("The incident remains open.", state)

    assert policy.evidence["evidence_completeness"] == ("incomplete" if first_succeeded else "complete")
    material = policy.evidence["retrieved_evidence"]
    assert [item["content"] for item in material] == contents
    assert not any(key.startswith("_") for item in material for key in item)
    failed = next(item for item in material if item["content"] == '{"ok":false}')
    assert failed.get("recovered", False) is not first_succeeded


def test_new_retry_after_checkpoint_restoration_retains_its_chronological_position():
    """New calls must advance the saved order rather than restarting the sequence."""

    policy = Policy()
    review = {}
    gate = EvidenceGateMiddleware(policy, "What happened?", review_state=review)
    call, message, _ = tool_state("incident_search", {"query": "incident"}, '{"ok":false}', "z-first")
    gate.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)
    restored = EvidenceGateMiddleware(policy, "What happened?",
                                      review_state=json.loads(json.dumps(review, sort_keys=True)))
    call, message, state = tool_state("incident_search", {"query": "incident"}, "Recovered result.", "a-second")
    restored.wrap_tool_call(SimpleNamespace(tool_call=call), lambda request: message)

    restored._verify("The incident remains open.", state)

    assert policy.evidence["evidence_completeness"] == "complete"
    assert policy.evidence["retrieved_evidence"][0]["recovered"] is True


@pytest.mark.parametrize("tool_name,arguments", [
    ("write_todos", lambda index: {"todos": [{"content": str(index)}]}),
    ("append_file", lambda index: {"path": "/deliverables/report.html", "content": str(index)}),
    ("write_file", lambda index: {"file_path": "/scratch.txt", "content": str(index)}),
    ("read_workspace_file", lambda index: {"path": str(index)}),
])
def test_unending_setup_writes_or_untrusted_pagination_cannot_disable_convergence(tool_name, arguments):
    """Progress allowances are bounded and document contents cannot impersonate pagination."""

    gate = EvidenceGateMiddleware(Policy(), "Report", evidence={"artifact_required": True}, interval=1,
                                  max_convergence_nudges=1)
    for index in range(40):
        content = json.dumps({"read_progress": {"snapshot_sha256": str(index), "next_offset": index,
                                               "complete": False}})
        _, _, state = tool_state(tool_name, arguments(index), content, str(index))
        if gate.before_model(state, None) == {"jump_to": "end"}:
            break
    else:
        raise AssertionError("Unending non-retrieval activity must remain bounded")


@pytest.mark.parametrize("correction", ["answer", "delivery", "completion"])
def test_formal_correction_has_a_fresh_bounded_phase_after_initial_delivery_is_exhausted(correction):
    """A requested local repair must not inherit the spent initial search and delivery budgets."""

    policy = Policy()
    policy.post_run_checks = lambda question, answer, evidence=None: {
        "answer_supported": "supported" if answer == "Corrected report." else "unsupported"
    }
    review = {"model_calls": 30, "convergence_calls": 24, "convergence_nudges": 1, "delivery_turns": 8,
              "publication_grace_used": 2, "automatic_delivery_corrections": 1,
              "artifact_progress_allowances": {"preparation": 8, "writing": 8},
              "artifact_page_cursors": {"source": None}}
    gate = EvidenceGateMiddleware(policy, "Report", evidence={"artifact_required": True, "_deliverables": {"r": "id"}},
                                  review_state=review, interval=2, max_convergence_nudges=1)
    if correction == "delivery":
        gate.prepare_deliverables = lambda: [{"code": "artifact_delivery_failed", "message": "Repair the file."}]
    elif correction == "completion":
        gate.completion_check = lambda answer: [{"code": "activity_evidence_unread", "message": "Read the records."}]

    assert gate.after_model({"messages": [AIMessage(content="Rejected report.")]}, None)["jump_to"] == "model"

    assert gate.model_calls == 30
    assert gate.convergence_calls == gate.convergence_nudges == gate.delivery_turns == 0
    assert review["artifact_progress_allowances"] == {}
    assert review["artifact_page_cursors"] == {"source": None}
    assert gate.answer_nudges == (1 if correction == "answer" else 0)
    assert review["automatic_delivery_corrections"] == (2 if correction == "delivery" else 1)
    _, _, edit = tool_state("edit_file", {"file_path": "/deliverables/report.html", "old_string": "bad",
                                         "new_string": "corrected"}, '{"ok":true}', "edit")
    assert gate.before_model(edit, None) is None
    for index in range(3):
        _, _, read = tool_state("read_tool_evidence", {"offset": 0}, json.dumps({"read_progress": {
            "snapshot_sha256": "source", "next_offset": None, "complete": True,
        }}), f"reread-{index}")
        assert gate.before_model(read, None) != {"jump_to": "end"}
    gate.prepare_deliverables = None
    gate.completion_check = None
    assert gate.after_model({"messages": [AIMessage(content="Corrected report.")]}, None) is None
    assert gate.saw_final_answer
    assert not gate.evidence.get("convergence_exhausted")


def test_correction_phase_still_converges_across_checkpoint_restoration_and_keeps_global_retry_limits():
    """Restoring a repair in progress must not replenish its turn or correction quotas."""

    policy = Policy()
    policy.post_run_checks = lambda *args: {"answer_supported": "unsupported"}
    review = {"model_calls": 30, "convergence_calls": 24, "convergence_nudges": 1, "delivery_turns": 8,
              "automatic_delivery_corrections": 2}
    evidence = {"artifact_required": True, "_deliverables": {"r": "id"}}
    gate = EvidenceGateMiddleware(policy, "Report", evidence=evidence, review_state=review, interval=2,
                                  max_convergence_nudges=1)
    assert gate.after_model({"messages": [AIMessage(content="Rejected report.")]}, None)["jump_to"] == "model"
    for _ in range(30):
        gate = EvidenceGateMiddleware(policy, "Report", evidence=evidence,
                                      review_state=json.loads(json.dumps(gate.review_state, sort_keys=True)),
                                      interval=2, max_convergence_nudges=1)
        result = gate.before_model({"messages": []}, None)
        if result == {"jump_to": "end"}:
            break
    else:
        raise AssertionError("Checkpoint restoration must not refresh correction convergence limits")
    assert evidence["convergence_exhausted"]
    assert gate.model_calls > 30
    assert gate.answer_nudges == 1
    assert gate.review_state["automatic_delivery_corrections"] == 2
    used = (gate.convergence_calls, gate.convergence_nudges, gate.delivery_turns)
    assert gate.after_model({"messages": [AIMessage(content="Still unsupported.")]}, None) is None
    assert (gate.convergence_calls, gate.convergence_nudges, gate.delivery_turns) == used
    assert gate.answer_nudges == 1
