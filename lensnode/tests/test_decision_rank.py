"""Decision ranking seam: aggregation, bounds, and the decision_rank tool."""

import json
import threading
from types import SimpleNamespace

import pytest

from lensnode import decision_contract
from lensnode.agent_runtime import decision_policy
from lensnode.decision_contract import DecisionResult


def _score_result(probabilities, confidence=0.8, legend=None):
    return DecisionResult(
        kind="score",
        value=probabilities,
        confidence=confidence,
        legend=legend,
        usage={"input_tokens": 2, "output_tokens": 1},
    )


def _command(analysis="plan_quality", **config):
    return {
        "run_uuid": "run-1",
        "decision_analyses": [
            {
                "plugin_key": "typesafe",
                "plugin_version": "1.4.0",
                "connection_uuid": "connection-1",
                "analyses": {
                    analysis: {
                        "tool_key": "typesafe_score",
                        "kind": "score",
                        "rubric": ["weak", "acceptable", "strong"],
                        **config,
                    }
                },
            }
        ],
    }


def _score_payload(probabilities):
    return json.dumps(
        {
            "ok": True,
            "model": "jev-1",
            "answers": {
                "decision": {
                    "type": "score",
                    "score": 1,
                    "confidence": 0.8,
                    "probabilities": probabilities,
                    "legend": {"0": "weak", "1": "acceptable", "2": "strong"},
                }
            },
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }
    )


def test_rank_score_weights_probabilities_by_rubric_index():
    assert decision_contract.rank_score(
        _score_result({"0": 0.1, "1": 0.2, "2": 0.7})
    ) == pytest.approx(1.6)


@pytest.mark.parametrize(
    "probabilities,expected",
    [
        ({"a": 0.5}, None),
        ({"-1": 0.5}, None),
        ({"0": 0.5}, 0.0),
    ],
)
def test_rank_score_rejects_unorderable_results(probabilities, expected):
    assert (
        decision_contract.rank_score(_score_result(probabilities)) == expected
    )


def test_aggregate_ranked_is_deterministic():
    entries = [
        (0, "b", _score_result({"0": 0.2, "1": 0.8})),
        (1, "a", _score_result({"0": 0.1, "1": 0.9})),
        (2, "c", _score_result({"0": 0.1, "1": 0.9})),
    ]

    ranked = decision_contract.aggregate_ranked(entries)

    assert [item.label for item in ranked] == ["a", "c", "b"]


def test_null_ranker_is_inert():
    ranker = decision_policy.NullDecisionRanker()

    assert ranker.rank("plan_quality", [{"label": "a", "content": "x"}]) is None
    assert ranker.as_tools() == []


def test_build_decision_ranker_selects_the_implementation():
    assert isinstance(
        decision_policy.build_decision_ranker(
            {"decision_analyses": []},
            SimpleNamespace(),
            SimpleNamespace(),
        ),
        decision_policy.NullDecisionRanker,
    )
    assert isinstance(
        decision_policy.build_decision_ranker(
            _command(),
            SimpleNamespace(),
            SimpleNamespace(),
        ),
        decision_policy.DecisionRanker,
    )


def test_rank_orders_candidates_by_weighted_score(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        state = args[9]["state"]
        calls.append(args[9])
        if "strong" in state:
            return _score_payload({"0": 0.05, "1": 0.15, "2": 0.8}), ""
        return _score_payload({"0": 0.6, "1": 0.3, "2": 0.1}), ""

    monkeypatch.setattr(
        decision_policy,
        "run_decision_tool",
        fake_run,
    )
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    result = ranker.rank(
        "plan_quality",
        [
            {"label": "weak-plan", "content": "a weak plan"},
            {"label": "strong-plan", "content": "a strong plan"},
        ],
        "Prefer safer plans.",
    )

    assert result["ok"] is True
    assert [item["label"] for item in result["ranked"]] == [
        "strong-plan",
        "weak-plan",
    ]
    assert result["ranked"][0]["score"] == pytest.approx(1.75)
    assert result["ranked"][1]["score"] == pytest.approx(0.5)
    assert result["failed"] == []
    assert calls[0]["criteria"] == json.dumps(
        ["weak", "acceptable", "strong"],
        ensure_ascii=False,
    )
    assert calls[0]["instructions"] == "Prefer safer plans."


def test_rank_reports_partial_failures(monkeypatch):
    def fake_run(*args, **kwargs):
        if args[9]["state"] == "bad":
            return None, "timeout"
        return _score_payload({"0": 0.2, "1": 0.8}), ""

    monkeypatch.setattr(decision_policy, "run_decision_tool", fake_run)
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    result = ranker.rank(
        "plan_quality",
        [
            {"label": "bad", "content": "bad"},
            {"label": "good", "content": "good"},
        ],
    )

    assert [item["label"] for item in result["ranked"]] == ["good"]
    assert result["failed"] == [{"label": "bad", "reason": "timeout"}]


def test_rank_returns_none_when_every_candidate_fails(monkeypatch):
    monkeypatch.setattr(
        decision_policy,
        "run_decision_tool",
        lambda *args, **kwargs: (None, "error"),
    )
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    assert ranker.rank("plan_quality", [{"label": "a", "content": "x"}]) is None


def test_rank_is_bounded_by_candidate_cap(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[9]["state"])
        return _score_payload({"0": 0.2, "1": 0.8}), ""

    monkeypatch.setattr(decision_policy, "run_decision_tool", fake_run)
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    result = ranker.rank(
        "plan_quality",
        [
            {"label": f"c{index}", "content": f"content-{index}"}
            for index in range(12)
        ],
    )

    assert len(result["ranked"]) == decision_policy.RANK_MAX_CANDIDATES
    assert len(calls) == decision_policy.RANK_MAX_CANDIDATES


def test_rank_is_bounded_per_run(monkeypatch):
    monkeypatch.setattr(
        decision_policy,
        "run_decision_tool",
        lambda *args, **kwargs: (_score_payload({"0": 0.2, "1": 0.8}), ""),
    )
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    candidates = [{"label": "a", "content": "x"}]

    assert ranker.rank("plan_quality", candidates) is not None
    assert ranker.rank("plan_quality", candidates) is not None
    assert ranker.rank("plan_quality", candidates) is None


def test_rank_caches_identical_candidates_across_batches(monkeypatch):
    """The per-Run cache dedupes repeat content across rank calls.

    Within one batch candidates are scored concurrently, so duplicates in the
    same batch may each be scored once; the cache still prevents paying again
    on a later call.
    """

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[9]["state"])
        return _score_payload({"0": 0.2, "1": 0.8}), ""

    monkeypatch.setattr(decision_policy, "run_decision_tool", fake_run)
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    ranker.rank("plan_quality", [{"label": "a", "content": "same"}])
    ranker.rank("plan_quality", [{"label": "b", "content": "same"}])

    assert calls == ["same"]


def test_rank_ignores_unknown_or_unrankable_decisions():
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    assert ranker.rank("missing", [{"label": "a", "content": "x"}]) is None
    assert (
        ranker.rank("plan_quality", [{"label": "a", "content": ""}]) is None
    )


def test_rank_tool_is_exposed_without_an_evidence_capability():
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    tools = ranker.as_tools()

    assert [tool.name for tool in tools] == [decision_policy.RANK_TOOL_NAME]
    metadata = tools[0].metadata
    assert "capability_family" not in metadata
    assert metadata["capability"] == "decision.rank"


def test_rank_tool_returns_a_structured_error_when_unavailable(monkeypatch):
    monkeypatch.setattr(
        decision_policy,
        "run_decision_tool",
        lambda *args, **kwargs: (None, "error"),
    )
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    tool = ranker.as_tools()[0]

    payload = json.loads(
        tool.invoke(
            {
                "decision": "plan_quality",
                "candidates": [{"label": "a", "content": "x"}],
            }
        )
    )

    assert payload == {"ok": False, "error": "DECISION_RANK_UNAVAILABLE"}


def _choice_payload(probabilities):
    return json.dumps(
        {
            "ok": True,
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": max(probabilities, key=probabilities.get),
                    "probabilities": probabilities,
                    "confidence": 0.9,
                }
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def _choice_command(**config):
    return {
        "run_uuid": "run-1",
        "decision_analyses": [
            {
                "plugin_key": "typesafe",
                "plugin_version": "1.4.0",
                "connection_uuid": "connection-1",
                "analyses": {
                    "pick_best": {
                        "tool_key": "typesafe_choice",
                        "kind": "choice",
                        "rubric": ["weak", "strong"],
                        "target_option": "strong",
                        "summary": "Prefer the stronger option.",
                        **config,
                    }
                },
            }
        ],
    }


def test_rank_score_uses_the_target_option_for_choice():
    result = DecisionResult(
        kind="choice",
        value={"weak": 0.2, "strong": 0.8},
        confidence=0.8,
        legend=None,
        usage={},
    )

    assert decision_contract.rank_score(result, "strong") == pytest.approx(0.8)
    assert decision_contract.rank_score(result) is None
    assert decision_contract.rank_score(result, "missing") is None


def test_rank_orders_choice_candidates_by_target_probability(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        state = args[9]["state"]
        calls.append(args[9])
        if "strong" in state:
            return _choice_payload({"weak": 0.1, "strong": 0.9}), ""
        return _choice_payload({"weak": 0.7, "strong": 0.3}), ""

    monkeypatch.setattr(decision_policy, "run_decision_tool", fake_run)
    ranker = decision_policy.DecisionRanker(
        _choice_command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    result = ranker.rank(
        "pick_best",
        [
            {"label": "weak-plan", "content": "a weak plan"},
            {"label": "strong-plan", "content": "a strong plan"},
        ],
    )

    assert [item["label"] for item in result["ranked"]] == [
        "strong-plan",
        "weak-plan",
    ]
    assert result["ranked"][0]["score"] == pytest.approx(0.9)
    assert calls[0]["criteria"] == json.dumps(
        {"weak": None, "strong": None},
        ensure_ascii=False,
    )


def test_rank_refuses_a_choice_analysis_without_a_target(monkeypatch):
    monkeypatch.setattr(
        decision_policy,
        "run_decision_tool",
        lambda *args, **kwargs: (_choice_payload({"weak": 0.9}), ""),
    )
    command = _choice_command()
    command["decision_analyses"][0]["analyses"]["pick_best"].pop(
        "target_option"
    )
    ranker = decision_policy.DecisionRanker(
        command,
        SimpleNamespace(),
        SimpleNamespace(),
    )

    assert ranker.rank("pick_best", [{"label": "a", "content": "x"}]) is None


def test_rank_scores_candidates_concurrently(monkeypatch):
    barrier = threading.Barrier(2, timeout=5)

    def fake_run(*args, **kwargs):
        barrier.wait()
        return _score_payload({"0": 0.2, "1": 0.8}), ""

    monkeypatch.setattr(decision_policy, "run_decision_tool", fake_run)
    ranker = decision_policy.DecisionRanker(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    result = ranker.rank(
        "plan_quality",
        [
            {"label": "a", "content": "a"},
            {"label": "b", "content": "b"},
        ],
    )

    assert result is not None
    assert len(result["ranked"]) == 2


def test_rank_evidence_uses_the_reserved_key(monkeypatch):
    def fake_run(*args, **kwargs):
        state = args[9]["state"]
        if "strong" in state:
            return _score_payload({"0": 0.05, "1": 0.15, "2": 0.8}), ""
        return _score_payload({"0": 0.6, "1": 0.3, "2": 0.1}), ""

    monkeypatch.setattr(decision_policy, "run_decision_tool", fake_run)
    ranker = decision_policy.DecisionRanker(
        _command(analysis="evidence_relevance"),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    order = ranker.rank_evidence(
        [
            {"label": "0", "content": "a weak chunk"},
            {"label": "1", "content": "a strong chunk"},
        ],
        "answer the question",
    )

    assert order == ["1", "0"]


def test_rank_evidence_returns_none_without_the_reserved_binding(monkeypatch):
    monkeypatch.setattr(
        decision_policy,
        "run_decision_tool",
        lambda *args, **kwargs: (_score_payload({"0": 0.2, "1": 0.8}), ""),
    )
    ranker = decision_policy.DecisionRanker(
        _command(analysis="plan_quality"),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    assert ranker.rank_evidence([{"label": "0", "content": "chunk"}]) is None


def test_null_ranker_has_no_evidence_order():
    ranker = decision_policy.NullDecisionRanker()

    assert ranker.rank_evidence([{"label": "0", "content": "chunk"}]) is None
