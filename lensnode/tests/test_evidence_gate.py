"""Automatic evidence-gate verification inside the agent loop."""

from langchain_core.messages import AIMessage

from lensnode.agent_runtime.evidence_gate import (
    EvidenceGateMiddleware,
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
        self.calls.append((question, answer))
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
    assert policy.calls == [("q", "final answer")]


def test_unsupported_answer_is_bounced_back_then_accepted():
    policy = _Policy({"answer_supported": "unsupported"})
    middleware = EvidenceGateMiddleware(policy, "q", max_nudges=1)

    nudge = middleware.after_model(_answer_state(), None)
    assert nudge["jump_to"] == "model"
    assert nudge["messages"]
    assert len(policy.calls) == 1

    # The nudge budget is spent: the best-effort answer is accepted.
    assert middleware.after_model(_answer_state(), None) is None
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
