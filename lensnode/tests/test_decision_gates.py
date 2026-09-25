"""Decision seam, gate runner, and neutral contract regression tests."""

import json
import time
from types import SimpleNamespace

import pytest

from lensnode import decision_contract
from lensnode.agent_runtime import decision_gates, decision_policy
from lensnode.plugin_package_loader import load_runtime_contract


def _noul_payload(value, *, confidence=None):
    answer = {"type": "noul", "noul": value}
    if confidence is not None:
        answer["confidence"] = confidence
    return json.dumps(
        {
            "ok": True,
            "model": "jev-1",
            "answers": {"decision": answer},
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
    )


def _command(gate="search_needed", **config):
    return {
        "run_uuid": "run-1",
        "decision_gates": [
            {
                "plugin_key": "typesafe",
                "plugin_version": "1.0.0",
                "connection_uuid": "connection-1",
                "gates": {
                    gate: {
                        "tool_key": "typesafe_noul",
                        "kind": "noul",
                        "threshold": 0.5,
                        "margin": 0.1,
                        "max_state_chars": 4000,
                        **config,
                    }
                },
            }
        ],
    }


def _runner(command, events=None, **overrides):
    sink = events if events is not None else []

    def emit(event, payload):
        sink.append((event, payload))

    runner = decision_gates.DecisionRunner(
        command,
        SimpleNamespace(),
        SimpleNamespace(),
        emit_event=emit,
        run_uuid="run-1",
    )
    for name, value in overrides.items():
        setattr(runner, name, value)
    return runner


def test_validates_a_neutral_noul_result():
    result = decision_contract.validate_decision_result(
        {
            "kind": "noul",
            "value": 0.8,
            "confidence": None,
            "legend": None,
            "usage": {"input_tokens": 3, "output_tokens": 1},
        },
        "noul",
    )

    assert result.kind == "noul"
    assert result.value == pytest.approx(0.8)
    assert result.confidence is None
    assert result.usage == {"input_tokens": 3, "output_tokens": 1}


def test_validates_neutral_probabilities():
    result = decision_contract.validate_decision_result(
        {
            "kind": "choice",
            "value": {"a": 0.7, "b": 0.3},
            "confidence": 0.7,
            "legend": {"a": "first"},
            "usage": {},
        },
        "choice",
    )

    assert result.value == {"a": pytest.approx(0.7), "b": pytest.approx(0.3)}
    assert result.confidence == pytest.approx(0.7)
    assert result.legend == {"a": "first"}


@pytest.mark.parametrize(
    "payload,kind",
    [
        ("not a dict", "noul"),
        ({"kind": "score", "value": 0.5}, "noul"),
        ({"kind": "noul", "value": 1.5}, "noul"),
        ({"kind": "noul", "value": "high"}, "noul"),
        ({"kind": "noul"}, "noul"),
        ({"kind": "choice", "value": {"a": 1.5}}, "choice"),
        ({"kind": "choice", "value": {}}, "choice"),
        ({"kind": "choice", "value": "x"}, "choice"),
    ],
)
def test_rejects_invalid_neutral_results(payload, kind):
    assert decision_contract.validate_decision_result(payload, kind) is None


def test_typesafe_runtime_projects_its_vendor_response():
    runtime = load_runtime_contract("typesafe", "1.0.0")

    neutral = runtime.project_decision(
        "typesafe_noul",
        json.loads(_noul_payload(0.72)),
    )
    result = decision_contract.validate_decision_result(neutral, "noul")

    assert result is not None
    assert result.value == pytest.approx(0.72)
    assert result.confidence is None


def test_typesafe_runtime_declares_its_post_path():
    runtime = load_runtime_contract("typesafe", "1.0.0")

    assert runtime.http_post_paths("https://api.typesafe.ai") == (
        "/v1/systemone",
    )
    assert runtime.http_post_paths(
        "https://ai-gateway.vercel.sh/typesafe"
    ) == ("/typesafe/v1/systemone",)


def test_unknown_gate_falls_back_and_emits_a_pair(monkeypatch):
    events = []
    runner = _runner(_command(), events)

    assert runner.evaluate("unknown_gate") is None

    kinds = [event for event, _ in events]
    assert kinds == [
        "deepagents.decision.gate.start",
        "deepagents.decision.gate.done",
    ]
    assert events[1][1]["fallback_reason"] == "unknown_gate"
    assert events[1][1]["source"] == "decision_gate"


def test_undeclared_gate_falls_back_as_not_declared():
    events = []
    runner = _runner(_command(declared=False), events)

    assert runner.evaluate("search_needed") is None

    kinds = [event for event, _ in events]
    assert kinds == [
        "deepagents.decision.gate.start",
        "deepagents.decision.gate.done",
    ]
    assert events[1][1]["fallback_reason"] == "not_declared"


def test_gate_above_the_active_phase_is_unbound(monkeypatch):
    events = []
    monkeypatch.setattr(decision_gates, "GATE_PHASE", "P1")
    runner = _runner(_command("evidence_requirement"), events)

    assert runner.evaluate("evidence_requirement") is None

    assert events[1][1]["fallback_reason"] == "not_bound"


def test_evidence_requirement_is_active_in_p3(monkeypatch):
    events = []
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: _noul_payload(0.9),
    )
    runner = _runner(_command("evidence_requirement"), events)

    assert (
        runner.evaluate("evidence_requirement", question="Deploy failed")
        == 0.9
    )
    assert events[1][1]["verdict"] == "accept"


def test_choice_gate_returns_the_winning_option(monkeypatch):
    events = []
    choice = json.dumps(
        {
            "ok": True,
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": "supported",
                    "probabilities": {"supported": 0.9, "unsupported": 0.1},
                    "confidence": 0.9,
                }
            },
            "usage": {},
        }
    )
    command = _command("answer_supported")
    command["decision_gates"][0]["gates"]["answer_supported"]["kind"] = "choice"
    command["decision_gates"][0]["gates"]["answer_supported"].pop(
        "threshold"
    )
    command["decision_gates"][0]["gates"]["answer_supported"].pop("margin")
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: choice,
    )
    runner = _runner(command, events)

    assert (
        runner.evaluate_choice(
            "answer_supported",
            question="Answer: x",
        )
        == "supported"
    )
    assert events[1][1]["verdict"] == "accept"
    assert events[1][1]["value"] == "supported"


def test_unknown_choice_gate_falls_back_to_the_fixed_verdict():
    runner = _runner(_command("answer_supported"))

    assert runner.evaluate_choice("answer_supported") is None
    assert runner.default_verdict("answer_supported") == "supported"
    assert runner.default_verdict("evidence_sufficient") is True


def test_evidence_strength_uses_unknown_as_the_unavailable_verdict():
    runner = _runner(_command("evidence_strength"))

    assert runner.default_verdict("evidence_strength") == "unknown"


def test_bound_gate_without_a_tool_is_unknown():
    events = []
    command = _command()
    command["decision_gates"][0]["gates"]["search_needed"]["tool_key"] = ""
    runner = _runner(command, events)

    assert runner.evaluate("search_needed") is None

    assert events[1][1]["fallback_reason"] == "unknown_gate"


def test_accepts_a_bound_gate_value(monkeypatch):
    events = []
    calls = []

    def fake_execute(*args, **kwargs):
        calls.append(kwargs)
        return _noul_payload(0.9)

    monkeypatch.setattr(decision_gates, "_execute_plugin_tool", fake_execute)
    runner = _runner(_command(), events)

    assert runner.evaluate("search_needed", question="Deploy failed") == (
        pytest.approx(0.9)
    )

    assert events[1][1]["verdict"] == "accept"
    assert events[1][1]["fallback_reason"] == ""
    assert calls[0]["source"] == "decision_gate"


def test_low_confidence_value_falls_back(monkeypatch):
    events = []
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: _noul_payload(0.52),
    )
    runner = _runner(_command(), events)

    assert runner.evaluate("search_needed", question="Deploy failed") is None
    assert events[1][1]["fallback_reason"] == "low_confidence"


def test_invalid_response_falls_back(monkeypatch):
    events = []
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: _noul_payload(2.0),
    )
    runner = _runner(_command(), events)

    assert runner.evaluate("search_needed", question="Deploy failed") is None
    assert events[1][1]["fallback_reason"] == "invalid_response"


def test_timeout_falls_back(monkeypatch):
    events = []

    def slow_execute(*args, **kwargs):
        time.sleep(0.3)
        return _noul_payload(0.9)

    monkeypatch.setattr(decision_gates, "_execute_plugin_tool", slow_execute)
    monkeypatch.setattr(decision_gates, "GATE_TIMEOUT_S", 0.05)
    runner = _runner(_command(), events)

    assert runner.evaluate("search_needed", question="Deploy failed") is None
    assert events[1][1]["fallback_reason"] == "timeout"


def test_run_budget_falls_back_without_calling_out(monkeypatch):
    events = []
    calls = []

    def fake_execute(*args, **kwargs):
        calls.append(1)
        return _noul_payload(0.9)

    monkeypatch.setattr(decision_gates, "_execute_plugin_tool", fake_execute)
    monkeypatch.setattr(decision_gates, "GATE_RUN_BUDGET", 1)
    runner = _runner(_command(), events)

    assert runner.evaluate("search_needed", question="one") == pytest.approx(
        0.9
    )
    assert runner.evaluate("search_needed", question="two") is None
    assert len(calls) == 1
    assert events[3][1]["fallback_reason"] == "budget_exceeded"


def test_call_ids_increase_per_gate(monkeypatch):
    events = []
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: _noul_payload(0.9),
    )
    runner = _runner(_command(), events)

    runner.evaluate("search_needed", question="one")
    runner.evaluate("search_needed", question="two")

    call_ids = [
        payload["call_id"]
        for event, payload in events
        if event.endswith(".start")
    ]
    assert call_ids == ["gate:search_needed:1", "gate:search_needed:2"]


def test_state_text_keeps_recent_turns_within_the_limit():
    history = [
        {"role": "user", "content": f"turn-{index}"}
        for index in range(10)
    ]

    state = decision_gates._state_text("question", history, 4000)

    assert "turn-9" in state
    assert "turn-0" not in state
    assert decision_gates._state_text("question", history, 100).startswith(
        "question"
    )


def test_build_decision_policy_defaults_to_the_model_policy():
    policy = decision_policy.build_decision_policy(
        {"decision_gates": []},
        SimpleNamespace(),
        SimpleNamespace(),
    )

    assert isinstance(policy, decision_policy.ModelDecisionPolicy)


def test_build_decision_policy_wraps_bound_gates():
    policy = decision_policy.build_decision_policy(
        _command(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    assert isinstance(policy, decision_policy.GateDecisionPolicy)


def test_gate_policy_uses_the_bound_threshold(monkeypatch):
    events = []
    runner = _runner(_command(), events)
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: _noul_payload(0.55),
    )
    monkeypatch.setattr(decision_gates, "GATE_RUN_BUDGET", 1)
    policy = decision_policy.GateDecisionPolicy(
        decision_policy.ModelDecisionPolicy(
            needs_retrieval=lambda *args, **kwargs: True,
            select_route=lambda *args, **kwargs: {},
        ),
        runner,
        {"search_needed": {"threshold": 0.5, "margin": 0.01}},
    )

    assert policy.needs_retrieval(None, "Deploy failed") is True


def test_gate_policy_falls_back_to_the_inner_classifier(monkeypatch):
    events = []
    inner_calls = []
    runner = _runner(_command(), events)

    def inner_needs_retrieval(model, question, history=None):
        inner_calls.append(question)
        return True

    policy = decision_policy.GateDecisionPolicy(
        decision_policy.ModelDecisionPolicy(
            needs_retrieval=inner_needs_retrieval,
            select_route=lambda *args, **kwargs: {},
        ),
        runner,
        {"search_needed": {"threshold": 0.5, "margin": 0.1}},
    )

    assert policy.needs_retrieval(None, "Hi") is True
    assert inner_calls == ["Hi"]


def test_select_route_corrects_evidence_requirement(monkeypatch):
    events = []
    monkeypatch.setattr(decision_gates, "GATE_PHASE", "P3")
    runner = _runner(_command("evidence_requirement"), events)
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: _noul_payload(0.9),
    )
    inner_route = {
        "intent": "informational",
        "complexity": "simple",
        "route": "direct_answer",
        "required_capabilities": [],
        "evidence_requirement": "none",
    }
    policy = decision_policy.GateDecisionPolicy(
        decision_policy.ModelDecisionPolicy(
            needs_retrieval=lambda *args, **kwargs: True,
            select_route=lambda *args, **kwargs: dict(inner_route),
        ),
        runner,
        {"evidence_requirement": {"threshold": 0.5, "margin": 0.1}},
    )

    route = policy.select_route(None, "Deploy failed", available_tools=[])

    assert route["evidence_requirement"] == "tool_result"
    assert route["route"] == "direct_execute"


def _choice_payload(probabilities, choice="supported"):
    return json.dumps(
        {
            "ok": True,
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": probabilities,
                    "confidence": 0.9,
                }
            },
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }
    )


def _post_run_command():
    return {
        "run_uuid": "run-1",
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
                    },
                    "answer_supported": {
                        "tool_key": "typesafe_choice",
                        "kind": "choice",
                        "max_state_chars": 4000,
                    },
                    "evidence_strength": {
                        "tool_key": "typesafe_choice",
                        "kind": "choice",
                        "max_state_chars": 6000,
                    },
                },
            }
        ],
    }


def _post_run_policy(runner, gate_keys):
    return decision_policy.GateDecisionPolicy(
        decision_policy.ModelDecisionPolicy(
            needs_retrieval=lambda *args, **kwargs: True,
            select_route=lambda *args, **kwargs: {},
        ),
        runner,
        {
            key: (
                {"threshold": 0.5, "margin": 0.1}
                if key != "answer_supported"
                else {"kind": "choice"}
            )
            for key in gate_keys
        },
    )


def test_post_run_checks_are_empty_for_the_model_policy():
    policy = decision_policy.ModelDecisionPolicy(
        needs_retrieval=lambda *args, **kwargs: True,
        select_route=lambda *args, **kwargs: {},
    )

    assert policy.post_run_checks("question", "answer") == {}


def test_post_run_checks_skip_unbound_gates():
    events = []
    policy = _post_run_policy(_runner(_command(), events), ["search_needed"])

    assert policy.post_run_checks("question", "answer") == {}
    assert events == []


def test_post_run_checks_report_sufficiency_and_support(monkeypatch):
    events = []

    def fake_execute(*args, **kwargs):
        if args[6] == "typesafe_choice":
            return _choice_payload({"supported": 0.9, "unsupported": 0.1})
        return _noul_payload(0.9)

    monkeypatch.setattr(decision_gates, "_execute_plugin_tool", fake_execute)
    policy = _post_run_policy(
        _runner(_post_run_command(), events),
        ["evidence_sufficient", "answer_supported"],
    )

    verdicts = policy.post_run_checks("question", "answer")

    assert verdicts == {
        "evidence_sufficient": True,
        "answer_supported": "supported",
    }


def test_post_run_checks_report_evidence_strength(monkeypatch):
    events = []

    def fake_execute(*args, **kwargs):
        if args[6] == "typesafe_choice":
            return _choice_payload(
                {
                    "direct": 0.1,
                    "derived": 0.1,
                    "adapted_only": 0.7,
                    "example_only": 0.02,
                    "planned": 0.02,
                    "unsupported": 0.03,
                    "contradicted": 0.03,
                },
                choice="adapted_only",
            )
        return _noul_payload(0.9)

    monkeypatch.setattr(decision_gates, "_execute_plugin_tool", fake_execute)
    policy = _post_run_policy(
        _runner(_post_run_command(), events),
        ["evidence_strength"],
    )

    assert policy.post_run_checks("question", "answer") == {
        "evidence_strength": "adapted_only",
    }


def test_post_run_checks_use_fixed_defaults_on_fallback(monkeypatch):
    events = []
    monkeypatch.setattr(
        decision_gates,
        "_execute_plugin_tool",
        lambda *args, **kwargs: json.dumps(
            {"ok": False, "error": "PLUGIN_REQUEST_FAILED"}
        ),
    )
    policy = _post_run_policy(
        _runner(_post_run_command(), events),
        ["evidence_sufficient", "answer_supported"],
    )

    assert policy.post_run_checks("question", "answer") == {
        "evidence_sufficient": True,
        "answer_supported": "supported",
    }


def test_post_run_state_includes_the_answer_and_evidence():
    state = decision_policy._post_run_state(
        "Why did it fail?",
        "Because of the deploy.",
        {"record_validation": {"valid": True}},
    )

    assert "Why did it fail?" in state
    assert "Because of the deploy." in state
    assert "Runtime evidence:" in state


def test_gate_not_declared_by_the_manifest_falls_back():
    events = []
    command = _command()
    command["decision_gates"][0]["gates"]["search_needed"] = {
        "declared": False,
        "tool_key": "",
        "kind": "",
        "threshold": 0.5,
        "margin": 0.1,
        "max_state_chars": 4000,
    }
    runner = _runner(command, events)

    assert runner.evaluate("search_needed", question="Why?") is None
    assert events[1][1]["fallback_reason"] == "not_declared"


def test_evidence_requirement_state_stays_within_the_limit():
    limit = 200
    tools = [
        SimpleNamespace(name=f"tool_{index}") for index in range(200)
    ]

    arguments = decision_gates._gate_arguments(
        "evidence_requirement",
        "x" * 5000,
        None,
        tools,
        limit,
    )

    assert len(arguments["state"]) <= limit
    assert "Available tools:" in arguments["state"]
