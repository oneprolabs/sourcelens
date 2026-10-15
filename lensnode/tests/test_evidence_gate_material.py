"""Regression coverage for evidence lost during final-answer verification."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from lensnode.agent_runtime import runtime as runtime_module
from lensnode.agent_runtime.decision_gates import _gate_arguments
from lensnode.agent_runtime.decision_policy import GateDecisionPolicy, ModelDecisionPolicy, _post_run_state
from lensnode.agent_runtime.evidence_gate import EvidenceGateMiddleware
from lensnode.agent_runtime.evidence_material import bound_review_state, review_material


class _ReviewPolicy:
    """Record the materials submitted to an external answer reviewer."""

    def __init__(self, verdict="supported", completeness=None, review_completeness=None):
        self.verdict = verdict
        self.completeness = completeness
        self.review_completeness = review_completeness
        self.materials = []

    def has_evidence_gates(self):
        return True

    def post_run_checks(self, question, answer, evidence=None):
        self.materials.append(evidence)
        result = {"answer_supported": self.verdict}
        if self.completeness is not None:
            result["evidence_completeness"] = self.completeness
        if self.review_completeness is not None:
            result["review_completeness"] = self.review_completeness
        return result


@pytest.mark.parametrize("budget,expected_calls", [(6000, 1), (4000, 0)])
def test_configured_budget_is_applied_before_material_is_truncated(budget, expected_calls):
    """A larger binding budget must preserve reviewable full answer material."""

    calls = []

    class Runner:
        def evaluate_choice(self, gate, *, question):
            calls.append(json.loads(question))
            return "supported"

    policy = GateDecisionPolicy(
        ModelDecisionPolicy(),
        Runner(),
        {"answer_supported": {"max_state_chars": budget}},
    )
    answer = "A" * 4500
    verdicts = policy.post_run_checks("Question", answer, {"source": "Evidence"})

    assert len(calls) == expected_calls
    if expected_calls:
        assert verdicts == {"answer_supported": "supported"}
        assert calls[0]["answer"] == answer
        assert calls[0]["runtime_evidence"]["source"] == "Evidence"
        assert calls[0]["evidence_completeness"] == "complete"
    else:
        assert verdicts["review_completeness"] == "incomplete"
        assert verdicts.get("evidence_completeness") != "incomplete"


def test_rebounding_complete_material_is_idempotent_at_the_budget_boundary():
    """A second pass must not inflate metadata and truncate usable evidence."""

    material = review_material("q", "a", {"source": "A" * 3850}, 4000)
    rebound = bound_review_state(material, 4000)
    assert rebound == material
    assert json.loads(rebound)["evidence_completeness"] == "complete"


def test_complete_tool_results_respect_the_configured_review_budget():
    """The collector must not truncate evidence below the selected gate budget."""

    reviewed = []

    class Runner:
        def evaluate_choice(self, gate, *, question):
            reviewed.append(json.loads(question))
            return "supported"

    policy = GateDecisionPolicy(ModelDecisionPolicy(), Runner(), {"answer_supported": {"max_state_chars": 6000}})
    content = "Evidence details. " * 150 + "The incident remains open."
    middleware = EvidenceGateMiddleware(policy, "Was it resolved?")
    middleware.after_model({"messages": [
        ToolMessage(name="incident_search", tool_call_id="source", content=content),
        AIMessage(content="The incident remains open."),
    ]}, None)

    assert len(reviewed) == 1
    assert reviewed[0]["runtime_evidence"]["retrieved_evidence"][0]["content"] == content
    assert reviewed[0]["evidence_completeness"] == "complete"


def test_score_results_are_judgments_not_retrieval_evidence():
    policy = _ReviewPolicy()
    middleware = EvidenceGateMiddleware(policy, "Was it completed?")
    middleware.after_model({"messages": [
        ToolMessage(name="decision_score", tool_call_id="score", content='{"ok":true,"score":1.9}'),
        AIMessage(content="Completion could not be verified."),
    ]}, None)
    assert not (policy.materials[0] or {}).get("retrieved_evidence")


def test_skill_instructions_do_not_make_complete_source_evidence_inconclusive():
    """Resource instructions are not facts retrieved for the user's question."""

    policy = _ReviewPolicy()
    middleware = EvidenceGateMiddleware(policy, "Was it completed?")
    middleware.after_model({"messages": [
        AIMessage(content="", tool_calls=[{
            "id": "instructions", "name": "read_file", "args": {"file_path": "/skills/report/OUTPUT_FORMAT.md"},
        }]),
        ToolMessage(name="read_file", tool_call_id="instructions", content="Output format instructions. " * 200),
        ToolMessage(name="incident_search", tool_call_id="source", content="MR 42 remains open."),
        AIMessage(content="MR 42 remains open."),
    ]}, None)
    assert policy.materials[0]["evidence_completeness"] == "complete"
    assert [item["tool"] for item in policy.materials[0]["retrieved_evidence"]] == ["incident_search"]


@pytest.mark.parametrize(
    "completeness,expected,requirement,review_completeness",
    [
        ("complete", "partial", "none", None),
        ("incomplete", "partial", "none", None),
        ("incomplete", "partial", "artifact", None),
        ("complete", "partial", "none", "incomplete"),
    ],
)
def test_terminal_outcome_distinguishes_failed_review_from_incomplete_coverage(
    monkeypatch, completeness, expected, requirement, review_completeness
):
    """A negative full review must not report the business outcome as completed."""

    monkeypatch.setattr(
        runtime_module, "_run_agent_with_turn_limit", lambda *args, **kwargs: ("Final answer.", False, "")
    )
    state = SimpleNamespace(
        max_turns=0,
        runtime_mode=SimpleNamespace(execution_gates=False, general_chat=False),
        new_span=lambda *args: "span",
        emit_agent_event=lambda *args: None,
        enter_span=lambda *args: None,
        exit_span=lambda *args: None,
        initial_messages=[],
        resume_state=None,
        agent=None,
        model=SimpleNamespace(stop_reason=None, token_usage={}),
        checkpoint_thread=None,
        resume_from_graph_checkpoint=False,
        command={"task": "knowledge_qa"},
        cancel_event=None,
        token_budget_wrapup_event=None,
        checkpoint_ready=False,
        initial_checkpoint_seeded=False,
        config=SimpleNamespace(),
        emit_output=None,
        started_at=datetime.now(timezone.utc),
        run_span="run",
        capability_middleware=None,
        evidence_requirement=requirement,
        required_capabilities=[],
        runtime_evidence={"source": "INC-42 remains open."},
        question="Was it resolved?",
        decision_policy=_ReviewPolicy(
            verdict="supported" if review_completeness else "unsupported",
            completeness=completeness,
            review_completeness=review_completeness,
        ),
        consulted_sources=SimpleNamespace(citations=lambda *args: []),
    )
    result = runtime_module.LensDeepAgentRuntime(SimpleNamespace())._execute_agent(state)

    assert result["outcome"] == expected
    if requirement == "artifact":
        assert result["termination_detail"]["reason"] == "evidence_unavailable"
        assert result["termination_detail"]["capability"] == "artifact_delivery"
    elif completeness == "incomplete" and requirement == "none":
        assert result["termination_detail"]["reason"] == "evidence_incomplete"
        assert result["termination_detail"]["error_type"] == "verification"
    elif review_completeness == "incomplete":
        assert result["termination_detail"]["reason"] == "review_material_incomplete"
        assert result["termination_detail"]["decision_gates"]["evidence_completeness"] == "complete"
    elif expected == "partial":
        assert result["termination_detail"]["reason"] == "evidence_insufficient"
        assert result["termination_detail"]["error_type"] == "verification"
    else:
        assert result["termination_detail"]["decision_gates"]["evidence_completeness"] == "incomplete"


@pytest.mark.parametrize("provider", ["github", "gitlab", "jira"])
def test_retrieval_survives_later_file_and_artifact_operations(provider):
    policy = _ReviewPolicy()
    middleware = EvidenceGateMiddleware(policy, "What caused the incident?")
    messages = [
        ToolMessage(
            name=f"{provider}_search",
            tool_call_id="retrieval",
            content=f"{provider}: incident INC-42 was caused by a failed database migration.",
        ),
    ]
    for index in range(12):
        messages.append(
            ToolMessage(
                name=["ls", "write_file", "publish_artifact"][index % 3],
                tool_call_id=f"output-{index}",
                content=f"Created /artifacts/incident-{index}.md successfully.",
            )
        )
    messages.append(AIMessage(content="INC-42 was caused by a failed database migration."))

    assert middleware.after_model({"messages": messages}, None) is None
    evidence = policy.materials[0]["retrieved_evidence"]
    assert any("INC-42" in entry["content"] for entry in evidence)
    assert not any(entry["tool"] in {"ls", "write_file", "publish_artifact"} for entry in evidence)


@pytest.mark.parametrize("provider", ["github", "gitlab", "jira"])
def test_long_answer_keeps_retrieval_in_the_final_gate_input(provider):
    answer = "The migration failed. " * 1000
    evidence = {
        "retrieved_evidence": [
            {
                "tool": f"{provider}_search",
                "content": f"{provider}: INC-42 failed because migration 0018 was invalid.",
            }
        ]
    }
    state = _post_run_state("Why did INC-42 fail?", answer, evidence)

    arguments = _gate_arguments("answer_supported", state, None, None, 4000)

    assert len(arguments["state"]) <= 4000
    assert f"{provider}: INC-42" in arguments["state"]
    assert "migration 0018 was invalid" in arguments["state"]
    assert "incomplete" in arguments["state"].lower()


def test_bounded_review_input_remains_valid_json_and_keeps_all_providers():
    evidence = {
        "retrieved_evidence": [
            {"tool": f"{provider}_search", "content": f"{provider}: INC-42 migration 0018 failed."}
            for provider in ["github", "gitlab", "jira"]
        ]
    }
    state = _post_run_state("Why did INC-42 fail?", "Migration failed. " * 1000, evidence)
    arguments = _gate_arguments("answer_supported", state, None, None, 4000)

    payload = json.loads(arguments["state"])

    assert len(arguments["state"]) <= 4000
    assert payload["evidence_completeness"] == "complete"
    assert payload["review_completeness"] == "incomplete"
    for provider in ["github", "gitlab", "jira"]:
        assert f"{provider}: INC-42" in arguments["state"]


@pytest.mark.parametrize(
    "payload,incomplete",
    [
        ({"ok": True, "possibly_truncated": True}, True),
        ({"ok": True, "projects_truncated": True}, True),
        ({"ok": True, "repositories": [{"errors": {"commits": "REQUEST_FAILED"}}]}, True),
        ({"ok": True, "projects": [{"possibly_truncated": {"commits": True}}]}, True),
        ({"ok": True, "projects": [{"possibly_truncated": {"commits": False}}]}, False),
        ({"ok": False, "error": "REQUEST_FAILED"}, True),
    ],
)
def test_source_contract_limits_reach_answer_review_even_for_short_results(payload, incomplete):
    """API result limits and partial failures cannot imply complete coverage."""

    from lensnode.plugin_package_loader import load_runtime_contract

    if payload.get("ok") is True:
        payload = load_runtime_contract("gitlab", "1.0.0").project_tool_result(
            "gitlab_activity_summary", payload, 900_000
        )
    policy = _ReviewPolicy()
    middleware = EvidenceGateMiddleware(policy, "Summarize activity.")
    middleware.after_model(
        {
            "messages": [
                ToolMessage(name="gitlab_activity_summary", tool_call_id="source", content=json.dumps(payload)),
                AIMessage(content="Activity report."),
            ]
        },
        None,
    )
    assert policy.materials[0]["evidence_completeness"] == ("incomplete" if incomplete else "complete")


def test_reading_a_document_with_error_fields_does_not_change_coverage():
    """Literal source contents are not tool metadata."""

    policy = _ReviewPolicy()
    middleware = EvidenceGateMiddleware(policy, "Explain this JSON.")
    middleware.after_model(
        {
            "messages": [
                ToolMessage(name="read_file", tool_call_id="source", content='{"errors":{"example":"test"}}'),
                AIMessage(content="An example error document."),
            ]
        },
        None,
    )
    assert policy.materials[0]["evidence_completeness"] == "complete"


def test_review_excerpt_does_not_mark_complete_source_evidence_as_missing():
    policy = _ReviewPolicy()
    middleware = EvidenceGateMiddleware(policy, "What caused the incident?")
    state = {
        "messages": [
            ToolMessage(
                name="github_search",
                tool_call_id="retrieval",
                content="Incident details. " * 300 + "INC-42 was caused by migration 0018.",
            ),
            AIMessage(content="INC-42 was caused by migration 0018."),
        ]
    }

    middleware.after_model(state, None)

    assert policy.materials[0]["evidence_completeness"] == "complete"
    assert policy.materials[0]["review_completeness"] == "incomplete"
    review_input = _post_run_state("What caused the incident?", state["messages"][-1].content, policy.materials[0])
    arguments = _gate_arguments("answer_supported", review_input, None, None, 4000)
    assert "incomplete" in arguments["state"].lower()


def test_review_compression_preserves_real_source_gaps_and_does_not_mutate_evidence():
    """Review size limits must preserve source coverage independently of review coverage."""

    evidence = {"evidence_completeness": "incomplete", "source": "Evidence. " * 1000}
    original = json.dumps(evidence)
    payload = json.loads(review_material("Question", "Answer", evidence, 1000))

    assert payload["evidence_completeness"] == "incomplete"
    assert payload["review_completeness"] == "incomplete"
    assert json.dumps(evidence) == original
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 1000


def test_incomplete_review_never_accepts_a_claim_or_triggers_futile_rechecks():
    """A complete source with a short review excerpt is still an unverified answer."""

    policy = _ReviewPolicy(verdict="supported")
    events = []
    middleware = EvidenceGateMiddleware(policy, "What happened?", emit_event=lambda name, detail: events.append(detail))
    state = {
        "messages": [
            ToolMessage(name="search", tool_call_id="source", content="Evidence. " * 1000),
            AIMessage(content="An incident happened."),
        ]
    }

    assert middleware.after_model(state, None) is None
    assert middleware.answer_nudges == 0
    assert events[-1]["action"] == "inconclusive"
    assert events[-1]["fallback_reason"] == "review_material_truncated"


def test_incomplete_review_material_does_not_regenerate_an_unsupported_answer():
    policy = _ReviewPolicy(verdict="unsupported", completeness="incomplete")
    middleware = EvidenceGateMiddleware(policy, "What caused the incident?")

    assert middleware.after_model({"messages": [AIMessage(content="The migration failed.")]}, None) is None
    assert middleware.answer_nudges == 0


def test_offloaded_preview_and_compacted_evidence_remain_inconclusive():
    """A short preview must not masquerade as the full source evidence."""

    policy = _ReviewPolicy(verdict="unsupported")
    middleware = EvidenceGateMiddleware(policy, "What failed?", evidence={"evidence_completeness": "incomplete"})
    state = {
        "messages": [
            ToolMessage(name="github_search", tool_call_id="source", content="INC-42 remains open."),
            AIMessage(content="INC-42 is resolved."),
        ]
    }
    assert middleware.after_model(state, None) is None
    assert policy.materials[-1]["evidence_completeness"] == "incomplete"

    middleware = EvidenceGateMiddleware(policy, "What failed?")
    state["messages"][0] = ToolMessage(
        name="github_search",
        tool_call_id="source",
        content="Tool result too large, the result was saved in the filesystem. Preview: INC-42 remains open.",
    )
    assert middleware.after_model(state, None) is None
    assert policy.materials[-1]["retrieved_evidence"][0]["truncated"] is True


def test_complete_retrieval_still_rechecks_unsupported_claims():
    policy = _ReviewPolicy(verdict="unsupported", completeness="complete")
    middleware = EvidenceGateMiddleware(policy, "Was the incident resolved?")
    state = {
        "messages": [
            ToolMessage(name="github_search", tool_call_id="issue", content="INC-42 remains open."),
            AIMessage(content="INC-42 has been resolved."),
        ]
    }

    result = middleware.after_model(state, None)

    assert result["jump_to"] == "model"
    assert middleware.answer_nudges == 1


def test_resume_retains_the_recheck_limit_and_internal_state_does_not_invalidate_cache():
    """Checkpoint restoration must not introduce another regeneration cycle."""

    policy = _ReviewPolicy(verdict="unsupported", completeness="complete")
    review_state = {}
    evidence = {"source": "INC-42 remains open.", "_answer_review": review_state}
    middleware = EvidenceGateMiddleware(policy, "Was it resolved?", evidence=evidence, review_state=review_state)
    state = {"messages": [AIMessage(content="It was resolved.")]}
    assert middleware.after_model(state, None)["jump_to"] == "model"
    assert middleware.cached_verdicts("It was resolved.", evidence)["answer_supported"] == "unsupported"

    events = []
    restored = EvidenceGateMiddleware(
        policy,
        "Was it resolved?",
        evidence=evidence,
        review_state=review_state,
        emit_event=lambda event, detail: events.append(detail),
    )
    assert restored.after_model(state, None) is None
    assert events[-1]["action"] == "accept_best_effort"
    assert restored.answer_nudges == 1


def test_directory_and_write_results_do_not_claim_retrieval_support():
    policy = _ReviewPolicy()
    middleware = EvidenceGateMiddleware(policy, "What caused the incident?")
    state = {
        "messages": [
            ToolMessage(name="ls", tool_call_id="list", content="/artifacts/incident.md"),
            ToolMessage(name="write_file", tool_call_id="write", content="Wrote incident.md."),
            AIMessage(content="The available material does not establish the cause."),
        ]
    }

    assert middleware.after_model(state, None) is None
    assert not (policy.materials[0] or {}).get("retrieved_evidence")


@pytest.mark.parametrize("retry_arguments", [{"project": "SL"}, {"project": "OPS"}, None])
def test_only_the_same_retried_operation_restores_substantive_answer_review(retry_arguments):
    policy = _ReviewPolicy(verdict="unsupported")
    middleware = EvidenceGateMiddleware(policy, "Was the incident resolved?")
    messages = [
        AIMessage(content="", tool_calls=[{"id": "failed", "name": "jira_search", "args": {"project": "SL"}}]),
        ToolMessage(name="jira_search", tool_call_id="failed", status="error", content="Request failed."),
    ]
    if retry_arguments is not None:
        messages.append(
            AIMessage(content="", tool_calls=[{"id": "retry", "name": "jira_search", "args": retry_arguments}])
        )
    messages.extend(
        [
            ToolMessage(name="jira_search", tool_call_id="retry", content='{"ok":true,"items":[{"status":"open"}]}'),
            AIMessage(content="The incident was resolved."),
        ]
    )
    result = middleware.after_model({"messages": messages}, None)
    if retry_arguments == {"project": "SL"}:
        assert result["jump_to"] == "model"
        assert policy.materials[-1]["evidence_completeness"] == "complete"
        assert policy.materials[-1]["retrieved_evidence"][0]["recovered"] is True
    else:
        assert result is None
        assert policy.materials[-1]["evidence_completeness"] == "incomplete"
    assert messages[1].status == "error"
