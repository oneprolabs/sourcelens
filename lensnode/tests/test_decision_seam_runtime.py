"""Decision seam wiring: assembly order and dual-mode equivalence.

These lock the two guarantees §13 relies on and that no other test covers:

- the control policy is assembled *before* the gate call sites consult it;
- a bound gate that falls back changes nothing except its own event pair.
"""

import json
from types import SimpleNamespace

from lensnode import agent_runtime
from lensnode.agent_runtime import decision_gates, decision_policy

RUN_UUID = "00000000-0000-0000-0000-000000000021"


def _noul_payload(value):
    return json.dumps(
        {
            "ok": True,
            "model": "jev-1",
            "answers": {"decision": {"type": "noul", "noul": value}},
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
    )


def _gates(tool_key="typesafe_noul", **config):
    return [
        {
            "plugin_key": "typesafe",
            "plugin_version": "1.0.0",
            "connection_uuid": "connection-1",
            "gates": {
                "search_needed": {
                    "tool_key": tool_key,
                    "kind": "noul",
                    "threshold": 0.5,
                    "margin": 0.1,
                    "max_state_chars": 4000,
                    **config,
                }
            },
        }
    ]


def _analyses(analysis="plan_quality", **config):
    return [
        {
            "plugin_key": "typesafe",
            "plugin_version": "1.0.0",
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
    ]


def _run_answer(
    monkeypatch,
    tmp_path,
    *,
    gates=None,
    analyses=None,
    routing_mode=None,
):
    """Run one knowledge_qa answer through the real prepare and seam."""

    invocations = []
    events = []

    class Model:
        stop_reason = None
        token_usage = {"total_tokens": 0}

        def __init__(self, **_kwargs):
            pass

        def restore_runtime_state(self, *_args):
            pass

        def export_runtime_state(self):
            return {}

        def invoke(self, messages, **_kwargs):
            invocations.append(messages)
            return SimpleNamespace(content='{"needs_retrieval": true}')

    resources = SimpleNamespace(
        root=tmp_path,
        context_skill_contents=[],
        mcp_configs=[],
        skill_paths=[],
    )
    config = SimpleNamespace(
        workspace_path=str(tmp_path),
        ai_gateway_url="http://gateway/ai/",
        token="token",
        request_timeout_s=30,
        offload_tool_tokens=5000,
        offload_human_tokens=None,
    )
    monkeypatch.setattr(agent_runtime, "LensGatewayChatModel", Model)
    monkeypatch.setattr(
        agent_runtime,
        "prepare_runtime_resources",
        lambda *_args, **_kwargs: resources,
    )
    monkeypatch.setattr(
        agent_runtime,
        "cleanup_runtime_resources",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_runtime,
        "build_agent_tools",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        agent_runtime,
        "load_mcp_tools",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        agent_runtime,
        "build_deferred_mcp_tools",
        lambda *_args, **_kwargs: ([], None),
    )

    runtime = agent_runtime.LensDeepAgentRuntime(config)
    captured = {}

    def prepare(*args, **kwargs):
        state = original_prepare(*args, **kwargs)
        captured["state"] = state
        return state

    original_prepare = runtime._prepare_runtime
    monkeypatch.setattr(runtime, "_prepare_runtime", prepare)
    monkeypatch.setattr(
        runtime,
        "_build_agent",
        lambda state: captured.setdefault(
            "tool_names",
            [getattr(tool, "name", "") for tool in state.tools],
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_execute_agent",
        lambda _state: {"answer": "done"},
    )

    command = {
        "run_uuid": RUN_UUID,
        "task": "knowledge_qa",
        "question": "Deploy failed, why?",
        "agent_model_ref": "model-ref",
        "history": [],
    }
    if gates is not None:
        command["decision_gates"] = gates
    if analyses is not None:
        command["decision_analyses"] = analyses
    if routing_mode is not None:
        command["routing_mode"] = routing_mode

    def collect(_message, detail):
        events.append((detail.get("agent_event"), detail))

    result = runtime._answer_sync(command, emit_progress=collect)

    return SimpleNamespace(
        events=[name for name, _ in events],
        payloads=events,
        model_calls=len(invocations),
        tool_names=captured.get("tool_names", []),
        state=captured["state"],
        result=result,
    )


def test_decision_policy_is_assembled_before_the_gate_call_sites(
    monkeypatch,
    tmp_path,
):
    seen = []
    original = agent_runtime.LensDeepAgentRuntime._decision_policy

    def spy(self, state):
        seen.append(getattr(state, "decision_policy", None) is not None)
        return original(self, state)

    monkeypatch.setattr(
        agent_runtime.LensDeepAgentRuntime,
        "_decision_policy",
        spy,
    )

    run = _run_answer(
        monkeypatch,
        tmp_path,
        gates=_gates(),
    )

    assert seen, "the retrieval gate must consult the control seam"
    assert all(seen), "the policy must be assembled before its first use"
    assert isinstance(
        run.state.decision_policy,
        decision_policy.GateDecisionPolicy,
    )


def test_unbound_runs_keep_the_model_policy(monkeypatch, tmp_path):
    run = _run_answer(monkeypatch, tmp_path)

    assert isinstance(
        run.state.decision_policy,
        decision_policy.ModelDecisionPolicy,
    )
    assert not [
        name
        for name in run.events
        if name.startswith("deepagents.decision.")
    ]


def test_bound_but_falling_back_gates_match_the_baseline(monkeypatch, tmp_path):
    baseline = _run_answer(monkeypatch, tmp_path)
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *_args, **_kwargs: _noul_payload(0.52),
    )
    falling_back = _run_answer(
        monkeypatch,
        tmp_path,
        gates=_gates(),
    )

    baseline_events = [
        name
        for name in baseline.events
        if not name.startswith("deepagents.decision.")
    ]
    fallback_events = [
        name
        for name in falling_back.events
        if not name.startswith("deepagents.decision.")
    ]
    assert fallback_events == baseline_events
    assert falling_back.model_calls == baseline.model_calls == 1
    assert falling_back.tool_names == baseline.tool_names
    assert [
        name
        for name in falling_back.events
        if name.startswith("deepagents.decision.")
    ] == [
        "deepagents.decision.gate.start",
        "deepagents.decision.gate.done",
    ]
    done = falling_back.payloads[-1][1]
    assert done["verdict"] == "fallback"
    assert done["fallback_reason"] == "low_confidence"


def test_accepting_gate_skips_the_inner_classifier(monkeypatch, tmp_path):
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *_args, **_kwargs: _noul_payload(0.95),
    )

    accepted = _run_answer(
        monkeypatch,
        tmp_path,
        gates=_gates(),
    )

    assert accepted.model_calls == 0
    assert accepted.result == {"answer": "done"}
    done = accepted.payloads[-1][1]
    assert done["verdict"] == "accept"
    assert done["value"] == 0.95


def test_bound_analysis_exposes_the_decision_rank_tool(monkeypatch, tmp_path):
    baseline = _run_answer(monkeypatch, tmp_path)
    bound = _run_answer(monkeypatch, tmp_path, analyses=_analyses())

    assert "decision_rank" not in baseline.tool_names
    assert "decision_rank" in bound.tool_names
    assert isinstance(
        bound.state.decision_ranker,
        decision_policy.DecisionRanker,
    )
    assert isinstance(
        baseline.state.decision_ranker,
        decision_policy.NullDecisionRanker,
    )


def _post_run_command():
    return {
        "run_uuid": RUN_UUID,
        "decision_gates": [
            {
                "plugin_key": "typesafe",
                "plugin_version": "1.0.0",
                "connection_uuid": "connection-1",
                "gates": {
                    "evidence_sufficient": {
                        "tool_key": "typesafe_noul",
                        "kind": "noul",
                        "threshold": 0.5,
                        "margin": 0.1,
                        "max_state_chars": 4000,
                    }
                },
            }
        ],
    }


def _post_run_state(policy, resume_state=None):
    return SimpleNamespace(
        resume_state=resume_state,
        decision_policy=policy,
        question="Why did it fail?",
        runtime_evidence=None,
        checkpoint_ready=False,
        run_uuid=RUN_UUID,
        config=SimpleNamespace(workspace_path="/tmp"),
    )


def test_post_run_decision_gates_records_the_gate_verdict(monkeypatch):
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: _noul_payload(0.9),
    )
    runner = decision_gates.DecisionRunner(
        _post_run_command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    policy = decision_policy.GateDecisionPolicy(
        decision_policy.ModelDecisionPolicy(
            needs_retrieval=lambda *args, **kwargs: True,
            select_route=lambda *args, **kwargs: {},
        ),
        runner,
        {"evidence_sufficient": {"threshold": 0.5, "margin": 0.1}},
    )

    verdicts = agent_runtime._post_run_decision_gates(
        _post_run_state(policy),
        "It failed because of the deploy.",
    )

    assert verdicts == {"evidence_sufficient": True}


def test_post_run_decision_gates_replay_persisted_verdicts():
    stale = {
        "evidence_sufficient": False,
        "answer_supported": "unsupported",
    }
    state = _post_run_state(
        None,
        resume_state=SimpleNamespace(decision_gates=stale),
    )

    assert agent_runtime._post_run_decision_gates(state, "answer") == stale


def test_post_run_decision_gates_are_empty_without_a_policy():
    state = _post_run_state(None)

    assert agent_runtime._post_run_decision_gates(state, "answer") == {}


def test_smart_collaboration_still_exposes_the_decision_rank_tool(
    monkeypatch,
    tmp_path,
):
    """The coordinator is model-driven, so rank must reach it as a tool."""

    run = _run_answer(
        monkeypatch,
        tmp_path,
        analyses=_analyses(),
        routing_mode="smart",
    )

    assert run.state.command["routing_mode"] == "smart"
    assert agent_runtime.LensDeepAgentRuntime._is_smart_collaboration(
        run.state.command
    )
    assert "decision_rank" in run.tool_names


def test_decision_call_ids_are_scoped_per_attempt():
    """A resumed attempt must not reuse the previous attempt's call id."""

    command = {"run_uuid": RUN_UUID, "decision_gates": _gates()}
    first = decision_gates.DecisionRunner(
        command,
        SimpleNamespace(),
        SimpleNamespace(),
        attempt=1,
    )
    resumed = decision_gates.DecisionRunner(
        command,
        SimpleNamespace(),
        SimpleNamespace(),
        attempt=2,
    )

    assert first._next_call_id("search_needed") == "gate:search_needed:1"
    assert (
        resumed._next_call_id("search_needed")
        == "gate:search_needed:2:1"
    )


def test_decision_attempt_reads_the_resume_state():
    assert (
        agent_runtime._decision_attempt(
            SimpleNamespace(
                resume_state=SimpleNamespace(current_attempt=3)
            )
        )
        == 3
    )
    assert (
        agent_runtime._decision_attempt(
            SimpleNamespace(resume_state=None)
        )
        == 1
    )


def test_decision_seams_capture_the_resume_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_runtime, "_decision_attempt", lambda _state: 2)
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *_args, **_kwargs: _noul_payload(0.52),
    )

    run = _run_answer(
        monkeypatch,
        tmp_path,
        gates=_gates(),
        analyses=_analyses(),
    )

    assert run.state.decision_policy.runner._attempt == 2
    assert run.state.decision_ranker._attempt == 2
