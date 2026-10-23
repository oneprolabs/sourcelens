"""Automatic evidence-gate verification inside the agent loop."""

import json

from langchain_core.messages import AIMessage, ToolMessage

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
    assert nudge["messages"][0].additional_kwargs["lensnode_source"] == "runtime"
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


def test_no_gates_disables_review_but_not_loop_control():
    policy = _Policy(active=False)
    middleware = EvidenceGateMiddleware(policy, "q", interval=2, max_convergence_nudges=1)

    assert middleware.after_model(_answer_state(), None) is None
    assert policy.calls == []
    # "When to stop" is loop control, not answer review: the guard must
    # still fire when no evidence gate is bound.
    assert middleware.before_model(_tool_state(), None) is None
    assert middleware.before_model(_tool_state(), None)["messages"]
    assert middleware.before_model(_tool_state(), None) == {"jump_to": "end"}


def _search_state(query, content, call_id="call-1"):
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_workspace", "args": {"query": query}, "id": call_id}
                ],
            ),
            ToolMessage(
                name="search_workspace", tool_call_id=call_id, content=content
            ),
        ]
    }


def _evidence_page_state(offset, call_id="call-1", snapshot="abc", total_records=None):
    progress = {
        "snapshot_sha256": snapshot,
        "next_offset": offset,
        "complete": False,
    }
    if total_records is not None:
        progress["total_records"] = total_records
    payload = json.dumps({"read_progress": progress})
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": "read_tool_evidence", "args": {}, "id": call_id}],
            ),
            ToolMessage(
                name="read_tool_evidence", tool_call_id=call_id, content=payload
            ),
        ]
    }


def test_repeated_empty_results_end_the_run_without_gates():
    policy = _Policy(active=False)
    middleware = EvidenceGateMiddleware(policy, "q", interval=8, max_convergence_nudges=2)
    empty = '{"items":[],"ok":true,"project":"p","query":"%s"}'
    queries = ["130002", "13000", "ENOENT", "errno"]

    results = [
        middleware.before_model(_search_state(query, empty % query, call_id=f"c{index}"), None)
        for index, query in enumerate(queries)
    ]

    assert results[:3] == [None, None, None]
    assert results[-1] == {"jump_to": "end"}


def test_fresh_results_keep_the_run_alive():
    policy = _Policy(active=False)
    middleware = EvidenceGateMiddleware(policy, "q", interval=8, max_convergence_nudges=2)

    for index in range(6):
        state = _search_state(
            f"query-{index}",
            f"docs/file{index}.md: finding {index} is documented.",
            call_id=f"c{index}",
        )
        assert middleware.before_model(state, None) is None


def test_evidence_paging_beyond_grace_stops_counting_as_progress():
    policy = _Policy(active=False)
    middleware = EvidenceGateMiddleware(
        policy,
        "q",
        interval=1,
        max_convergence_nudges=1,
        evidence_page_progress_grace=2,
        max_evidence_pages=12,
    )

    for offset in range(2):
        assert middleware.before_model(
            _evidence_page_state(offset, total_records=5000), None
        ) is None
    assert middleware.before_model(
        _evidence_page_state(2, total_records=5000), None
    )["messages"]
    assert middleware.before_model(
        _evidence_page_state(3, total_records=5000), None
    ) == {"jump_to": "end"}


def test_feasible_corpus_paging_is_not_capped():
    policy = _Policy(active=False)
    middleware = EvidenceGateMiddleware(policy, "q", interval=2, max_convergence_nudges=1)

    for offset in range(20):
        assert middleware.before_model(
            _evidence_page_state(offset, total_records=120), None
        ) is None


def test_evidence_page_limit_ends_the_run():
    policy = _Policy(active=False)
    middleware = EvidenceGateMiddleware(
        policy, "q", max_evidence_pages=3, max_convergence_nudges=2
    )

    for offset in range(3):
        assert middleware.before_model(
            _evidence_page_state(offset, total_records=5000), None
        ) is None
    assert middleware.before_model(
        _evidence_page_state(3, total_records=5000), None
    ) == {"jump_to": "end"}


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


def test_recheck_keeps_the_convergence_budget_active():
    """A rejected draft must not disable the guard for its correction loop."""

    policy = _Policy({"answer_supported": "unsupported"})
    middleware = EvidenceGateMiddleware(policy, "q", interval=2, max_convergence_nudges=2)
    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None)["messages"]
    assert middleware.after_model(_answer_state(), None)["jump_to"] == "model"

    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None)["messages"]
    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None)["messages"]
    assert middleware.before_model({}, None) == {"jump_to": "end"}


def test_convergence_budget_survives_checkpoint_resume():
    """Resuming the correction stage does not grant a fresh search budget."""

    review_state = {}
    policy = _Policy({"answer_supported": "unsupported"})
    first = EvidenceGateMiddleware(policy, "q", interval=1, max_convergence_nudges=1, review_state=review_state)
    assert first.before_model({}, None)["messages"]
    assert first.after_model(_answer_state(), None)["jump_to"] == "model"

    resumed = EvidenceGateMiddleware(policy, "q", interval=1, max_convergence_nudges=1, review_state=review_state)
    assert resumed.before_model({}, None) is None
    resumed = EvidenceGateMiddleware(policy, "q", interval=1, max_convergence_nudges=1, review_state=review_state)
    assert resumed.before_model({}, None)["messages"]
    resumed = EvidenceGateMiddleware(policy, "q", interval=1, max_convergence_nudges=1, review_state=review_state)
    assert resumed.before_model({}, None) == {"jump_to": "end"}
    assert resumed.answer_nudges == 1
    assert resumed.after_model(_answer_state(), None) is None
    assert resumed.answer_nudges == 1


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


def test_evidence_review_scope_wraps_verification():
    entered = []

    class _Scope:
        def __enter__(self):
            entered.append("enter")
            return "evidence-span"

        def __exit__(self, *exc):
            entered.append("exit")
            return False

    policy = _Policy(
        {"answer_supported": "supported", "evidence_sufficient": True}
    )
    middleware = EvidenceGateMiddleware(
        policy,
        "q",
        review_scope=lambda: _Scope(),
    )

    assert middleware.after_model(_answer_state(), None) is None
    assert entered == ["enter", "exit"]


def test_exhausted_convergence_budget_allows_bounded_verification_and_restage():
    policy = _Policy({"answer_supported": "unsupported"})
    candidates = {"report.html": "old"}
    middleware = EvidenceGateMiddleware(
        policy,
        "q",
        interval=2,
        max_convergence_nudges=1,
        on_recheck=candidates.clear,
    )
    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None)["messages"]
    assert middleware.after_model(_answer_state(), None)["jump_to"] == "model"
    assert not candidates
    assert middleware.before_model({}, None) is None
    candidates["report.html"] = "corrected"
    policy._verdicts = {"answer_supported": "supported"}
    assert middleware.after_model(_answer_state("Corrected answer"), None) is None
    assert candidates == {"report.html": "corrected"}
    assert middleware.before_model({}, None) is None


def test_verification_grace_expires_when_the_agent_keeps_searching():
    policy = _Policy({"answer_supported": "unsupported"})
    middleware = EvidenceGateMiddleware(policy, "q", interval=2, max_convergence_nudges=1)
    middleware.before_model({}, None)
    middleware.before_model({}, None)
    middleware.after_model(_answer_state(), None)
    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None) is None
    assert middleware.before_model({}, None)["messages"]
    assert middleware.before_model({}, None) == {"jump_to": "end"}


def test_completion_issues_are_repaired_together_without_repeated_model_rounds():
    issues = [
        {'code': 'missing-evidence', 'recovery': 'Read saved evidence range 2.'},
        {'code': 'missing-file', 'recovery': 'Create the requested attachment.'},
    ]
    reviews = []
    policy = _Policy({'answer_supported': 'supported'})
    middleware = EvidenceGateMiddleware(
        policy, 'q', completion_check=lambda answer: list(issues), on_recheck=lambda: reviews.append(True)
    )
    correction = middleware.after_model(_answer_state(), None)
    assert correction['jump_to'] == 'model'
    assert correction['messages'][0].content.splitlines() == [item['recovery'] for item in issues]
    assert set(middleware.review_state['completion_check_codes']) == {item['code'] for item in issues}
    assert reviews == [True]
    issues.clear()
    assert middleware.after_model(_answer_state('Corrected answer'), None) is None
    assert len(policy.calls) == 1
    assert policy.calls[0][1] == 'Corrected answer'


def test_all_attachment_problems_are_included_in_one_bounded_repair_phase():
    issues = [{'code': str(index), 'recovery': f'Repair output file {index}.'} for index in range(6)]
    policy = _Policy({'answer_supported': 'supported'})
    middleware = EvidenceGateMiddleware(policy, 'q', prepare_deliverables=lambda: issues)
    result = middleware.after_model(_answer_state(), None)
    assert result['messages'][0].content.splitlines() == [item['recovery'] for item in issues]
    assert middleware.review_state['automatic_delivery_corrections'] == 1
    assert not policy.calls


def test_repeated_reads_of_an_oversized_corpus_count_toward_the_cap():
    policy = _Policy(active=False)
    middleware = EvidenceGateMiddleware(
        policy, "q", max_evidence_pages=3, max_convergence_nudges=2
    )
    page = _evidence_page_state(0, total_records=5000)

    # Re-reading the same offset never advances the cursor, but the corpus is
    # still too large to finish within one run.
    for _ in range(3):
        assert middleware.before_model(page, None) is None
    assert middleware.before_model(page, None) == {"jump_to": "end"}
