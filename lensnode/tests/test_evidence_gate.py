"""Automatic evidence-gate verification inside the agent loop."""

from langchain_core.messages import AIMessage, ToolMessage

from lensnode.agent_runtime.evidence_gate import (
    EvidenceGateMiddleware,
    _CONVERGENCE_GUIDANCE,
    _RECHECK_GUIDANCE,
    _verdicts_supported,
)


class _Policy:
    """Stub control policy returning a fixed set of gate verdicts."""

    def __init__(self, verdicts=None, active=True):
        self._verdicts = verdicts if verdicts is not None else {}
        self._active = active
        self.calls = []

    def has_evidence_gates(self):
        return self._active

    def post_run_checks(self, question, answer, evidence=None):
        self.calls.append((question, answer, evidence))
        return dict(self._verdicts)


def _answer_state(text="final answer"):
    return {"messages": [AIMessage(content=text)]}


def _tool_state(call_id="call-1"):
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_workspace",
                        "args": {"query": "alpha"},
                        "id": call_id,
                    }
                ],
            )
        ]
    }


def test_supported_answer_is_accepted():
    policy = _Policy(
        {"answer_supported": "supported", "evidence_sufficient": True}
    )
    middleware = EvidenceGateMiddleware(policy, "q")

    assert middleware.after_model(_answer_state(), None) is None
    assert policy.calls == [("q", "final answer", None)]


def test_tool_results_are_available_to_evidence_review():
    policy = _Policy({"answer_supported": "supported"})
    middleware = EvidenceGateMiddleware(policy, "q")
    state = {
        "messages": [
            ToolMessage(
                name="search_workspace",
                tool_call_id="call-1",
                content="docs/ascend.md: vLLM-Ascend is supported.",
            ),
            AIMessage(content="It is supported."),
        ]
    }

    assert middleware.after_model(state, None) is None
    assert policy.calls[0][2]["retrieved_evidence"][0]["tool"] == (
        "search_workspace"
    )


def test_unsupported_answer_is_bounced_back_then_accepted():
    policy = _Policy({"answer_supported": "unsupported"})
    middleware = EvidenceGateMiddleware(policy, "q", max_nudges=1)

    nudge = middleware.after_model(_answer_state(), None)
    assert nudge["jump_to"] == "model"
    assert nudge["messages"]
    assert len(policy.calls) == 1

    # The nudge budget is spent: the best-effort answer is accepted.
    assert middleware.after_model(_answer_state("revised answer"), None) is None
    assert len(policy.calls) == 2


def test_pending_tool_calls_are_never_nudged():
    policy = _Policy()
    middleware = EvidenceGateMiddleware(policy, "q")

    # An AI message with unanswered tool calls must not jump back to the
    # model (that would send dangling tool_calls to the gateway).
    assert middleware.after_model(_tool_state(), None) is None
    assert policy.calls == []


def test_no_gates_is_a_noop():
    policy = _Policy(active=False)
    middleware = EvidenceGateMiddleware(policy, "q")

    assert middleware.after_model(_answer_state(), None) is None
    assert middleware.before_model(_tool_state(), None) is None
    assert policy.calls == []


def test_convergence_nudge_then_end():
    policy = _Policy()
    middleware = EvidenceGateMiddleware(
        policy, "q", interval=2, max_convergence_nudges=1
    )

    assert middleware.before_model(_tool_state(), None) is None
    nudge = middleware.before_model(_tool_state(), None)
    assert nudge["messages"]
    assert "jump_to" not in nudge
    assert middleware.before_model(_tool_state(), None) == {"jump_to": "end"}


def test_convergence_stops_after_final_answer():
    policy = _Policy({"answer_supported": "supported"})
    middleware = EvidenceGateMiddleware(
        policy, "q", interval=1, max_convergence_nudges=2
    )

    assert middleware.after_model(_answer_state(), None) is None
    for _ in range(5):
        assert middleware.before_model(_tool_state(), None) is None


def test_convergence_brake_can_be_disabled():
    policy = _Policy()
    middleware = EvidenceGateMiddleware(
        policy, "q", interval=1, max_convergence_nudges=0
    )

    for _ in range(5):
        assert middleware.before_model(_tool_state(), None) is None


def test_empty_answer_text_is_ignored():
    policy = _Policy({"answer_supported": "unsupported"})
    middleware = EvidenceGateMiddleware(policy, "q")

    assert (
        middleware.after_model({"messages": [AIMessage(content="")]}, None)
        is None
    )
    assert policy.calls == []


def test_verdicts_supported_helper():
    assert _verdicts_supported({}) is True
    assert _verdicts_supported({"answer_supported": "supported"}) is True
    assert _verdicts_supported({"evidence_sufficient": True}) is True
    assert _verdicts_supported({"answer_supported": "unsupported"}) is False
    assert _verdicts_supported({"evidence_sufficient": False}) is False


def test_weak_evidence_strength_requires_a_qualified_answer():
    assert _verdicts_supported({"evidence_strength": "direct"}) is True
    assert _verdicts_supported({"evidence_strength": "derived"}) is True
    assert _verdicts_supported({"evidence_strength": "qualified_weak"}) is True
    assert _verdicts_supported({"evidence_strength": "adapted_only"}) is False
    assert _verdicts_supported({"evidence_strength": "example_only"}) is False
    assert _verdicts_supported({"evidence_strength": "unknown"}) is False


def test_qualified_weak_answer_is_accepted_without_recheck():
    policy = _Policy(
        {
            "evidence_sufficient": True,
            "answer_supported": "supported",
            "evidence_strength": "qualified_weak",
        }
    )
    middleware = EvidenceGateMiddleware(policy, "Which models are running on Ascend?")

    answer = "The available examples do not confirm any model running on Ascend."
    assert middleware.after_model(_answer_state(answer), None) is None
    assert len(policy.calls) == 1


def test_default_recheck_budget_is_one():
    policy = _Policy({"answer_supported": "unsupported"})
    middleware = EvidenceGateMiddleware(policy, "q")

    assert middleware.after_model(_answer_state("Unsupported claim"), None)[
        "jump_to"
    ] == "model"
    assert middleware.after_model(_answer_state("Still unsupported"), None) is None
    assert middleware.answer_nudges == 1


def test_gate_guidance_preserves_user_facing_answer_shape():
    assert "not a search log" in _RECHECK_GUIDANCE
    assert "evidence inventory" in _RECHECK_GUIDANCE
    assert "lead with the direct conclusion" in _CONVERGENCE_GUIDANCE
    assert "list documents still to read" in _CONVERGENCE_GUIDANCE


def test_verification_reuses_only_identical_answer_and_evidence():
    policy = _Policy({"answer_supported": "supported"})
    evidence = {"record": "first"}
    middleware = EvidenceGateMiddleware(policy, "q", evidence=evidence)
    state = _answer_state("First answer")

    assert middleware.after_model(state, None) is None
    assert middleware.after_model(state, None) is None
    assert len(policy.calls) == 1
    assert middleware.cached_verdicts("First answer", evidence) == {
        "answer_supported": "supported"
    }
    assert middleware.cached_verdicts("Different answer", evidence) is None

    evidence["record"] = "new evidence"
    assert middleware.cached_verdicts("First answer", evidence) is None
    assert middleware.after_model(state, None) is None
    assert len(policy.calls) == 2
