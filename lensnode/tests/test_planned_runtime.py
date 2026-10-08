import json
from types import SimpleNamespace
from unittest.mock import Mock

from lensnode import agent_runtime
from lensnode.checkpoint import ResumeState
from lensnode.planned_evidence import build_evidence_bundle


def _workspace_tools():
    return [
        SimpleNamespace(
            name="search_workspace",
            invoke=lambda _args: "",
        )
    ]


def _runtime_state(tmp_path, resume_state=None):
    ready_events = []
    state = SimpleNamespace(
        exit_span=Mock(),
        runtime_mode=SimpleNamespace(
            name="code_analysis",
            execution_gates=False,
            general_chat=False,
        ),
        resume_state=resume_state,
        run_uuid="planned-run",
        model=SimpleNamespace(),
        command={
            "run_uuid": "planned-run",
            "task": "code_analysis",
            "question": "Analyze the runtime entry point.",
        },
        tools=[],
        mcp_tools=[],
        emit_agent_event=lambda *_args, **_kwargs: None,
        resources=SimpleNamespace(),
        initial_messages=[],
        history_assistant_turns=0,
        checkpoint_ready=resume_state is not None,
        initial_checkpoint_seeded=False,
        notify_checkpoint_ready=lambda: ready_events.append("ready"),
    )
    return state, ready_events


def test_resumed_code_analysis_uses_deep_agent_loop(
    monkeypatch,
    tmp_path,
):
    calls = []
    resume_state = ResumeState(
        messages=(),
        route_decision={"route": "planned_code_analysis"},
        history_assistant_turns=0,
        checkpoint_step=-1,
    )
    state, ready_events = _runtime_state(tmp_path, resume_state)
    runtime = agent_runtime.LensDeepAgentRuntime(
        SimpleNamespace(workspace_path=str(tmp_path))
    )

    monkeypatch.setattr(
        runtime,
        "_prepare_runtime",
        lambda *_args, **_kwargs: state,
    )
    monkeypatch.setattr(
        runtime,
        "_build_agent",
        lambda _state: calls.append("deep-agent"),
    )
    monkeypatch.setattr(
        runtime,
        "_execute_agent",
        lambda _state: calls.append("execute") or {"answer": "done"},
    )
    monkeypatch.setattr(
        agent_runtime,
        "cleanup_runtime_resources",
        lambda _resources: None,
    )

    result = runtime._answer_sync(state.command)

    assert result == {"answer": "done"}
    assert calls == ["deep-agent", "execute"]
    assert ready_events == []


def test_new_code_analysis_uses_deep_agent_loop_without_planner(
    monkeypatch,
    tmp_path,
):
    state, _ready_events = _runtime_state(tmp_path)
    runtime = agent_runtime.LensDeepAgentRuntime(
        SimpleNamespace(workspace_path=str(tmp_path))
    )
    calls = []

    monkeypatch.setattr(
        runtime,
        "_prepare_runtime",
        lambda *_args, **_kwargs: state,
    )
    monkeypatch.setattr(
        runtime,
        "_build_agent",
        lambda _state: calls.append("deep-agent"),
    )
    monkeypatch.setattr(
        runtime,
        "_execute_agent",
        lambda _state: calls.append("execute") or {"answer": "done"},
    )
    monkeypatch.setattr(
        agent_runtime,
        "cleanup_runtime_resources",
        lambda _resources: None,
    )

    result = runtime._answer_sync(state.command)

    assert result == {"answer": "done"}
    assert calls == ["deep-agent", "execute"]


def test_planned_code_analysis_blocks_before_model_when_capabilities_missing(
    tmp_path,
):
    class Model:
        stop_reason = "stop"
        token_usage = {}

        def __init__(self):
            self.calls = 0

        def invoke(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("capability precheck should run first")

    model = Model()
    result = agent_runtime._run_planned_code_analysis(
        model=model,
        command={"question": "Where is it implemented?"},
        tools=[],
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert model.calls == 0
    assert result["outcome"] == "blocked"
    assert result["termination_detail"]["reason"] == (
        "capability_unavailable"
    )
    assert result["planned_evidence"] == {
        "capability_status": "unavailable",
        "available_capabilities": [],
        "missing_capabilities": ["workspace", "codegraph"],
    }


def test_planned_code_analysis_returns_awaiting_user_input_for_clarification(
    tmp_path,
):
    clarification_plan = {
        "objective": "Inspect the deployment path",
        "clarification": {
            "question": "Which deployment environment should I inspect?",
            "reason": "ambiguous_scope",
            "answer_type": "text",
        },
    }

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def __init__(self):
            self.calls = []

        def invoke(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return SimpleNamespace(content=json.dumps(clarification_plan))

    model = Model()
    result = agent_runtime._run_planned_code_analysis(
        model=model,
        command={
            "run_uuid": "run-123",
            "question": "Why did deployment fail?",
        },
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert len(model.calls) == 1
    assert result["status"] == "awaiting_user_input"
    assert result["outcome"] == "blocked"
    assert result["termination_detail"]["reason"] == "needs_user_input"
    request = result["termination_detail"]["request"]
    assert request["request_id"].startswith("clarification-")
    assert request["answer_type"] == "text"
    assert request["question"] == (
        "Which deployment environment should I inspect?"
    )


def test_planned_planner_emits_planning_phase_and_reasoning_pulse(
    tmp_path,
):
    clarification_plan = {
        "objective": "Inspect the deployment path",
        "clarification": {
            "question": "Which deployment environment should I inspect?",
            "reason": "ambiguous_scope",
            "answer_type": "text",
        },
    }
    events = []
    invoke_kwargs = []

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def invoke(self, _messages, **kwargs):
            invoke_kwargs.append(kwargs)
            return SimpleNamespace(content=json.dumps(clarification_plan))

    model = Model()
    agent_runtime._run_planned_code_analysis(
        model=model,
        command={"question": "Why did deployment fail?"},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *args: events.append(args),
        workspace_root=tmp_path,
    )

    # The planner call went streaming with a reasoning pulse callback.
    assert invoke_kwargs[0]["runtime_structured_output"] is True
    assert callable(invoke_kwargs[0]["on_reasoning_delta"])
    # A planning phase event was emitted before the planner call.
    planning_events = [
        detail
        for (_event, detail) in events
        if detail.get("event_type") == "phase.changed"
        and detail.get("payload", {}).get("phase") == "planning"
    ]
    assert planning_events, "expected a planning phase event"


def test_planned_reasoning_pulse_throttles_phase_events(tmp_path):
    events = []

    def emit(*args):
        events.append(args)

    callback = agent_runtime._planned_reasoning_pulse(emit, throttle_s=60.0)
    # Two reasoning deltas inside the throttle window -> one pulse.
    callback("token-a")
    callback("token-b")
    assert len(events) == 1
    _event, detail = events[0]
    assert detail["event_type"] == "phase.changed"
    assert detail["payload"] == {"phase": "planning"}


def test_code_analysis_resume_does_not_enter_planner_pipeline(
    monkeypatch,
    tmp_path,
):
    resume_state = ResumeState(
        messages=(),
        route_decision={"route": "planned_code_analysis"},
        history_assistant_turns=0,
        checkpoint_step=-1,
    )
    state, _ready_events = _runtime_state(tmp_path, resume_state)
    runtime = agent_runtime.LensDeepAgentRuntime(
        SimpleNamespace(workspace_path=str(tmp_path))
    )
    calls = []

    monkeypatch.setattr(
        runtime,
        "_prepare_runtime",
        lambda *_args, **_kwargs: state,
    )
    monkeypatch.setattr(
        runtime,
        "_build_agent",
        lambda _state: calls.append("deep-agent"),
    )
    monkeypatch.setattr(
        runtime,
        "_execute_agent",
        lambda _state: calls.append("execute") or {"answer": "recovered"},
    )
    monkeypatch.setattr(
        agent_runtime,
        "cleanup_runtime_resources",
        lambda _resources: None,
    )

    result = runtime._answer_sync({**state.command, "resume": True})

    assert result == {"answer": "recovered"}
    assert calls == ["deep-agent", "execute"]


def test_planned_answer_extracts_json_after_explanatory_prefix(tmp_path):
    bundle = build_evidence_bundle([], max_tokens=100)
    response = SimpleNamespace(
        content=(
            "Here is the structured result:\n"
            "```json\n"
            '{"answer":"Readable answer","citations":[],'
            '"unsupported_claims":[]}\n'
            "```"
        )
    )

    answer, citations, unsupported = agent_runtime._validated_planned_answer(
        response,
        bundle,
        tmp_path,
    )

    assert answer == "Readable answer"
    assert citations == ()
    assert unsupported == 1
    assert "structured result" not in answer
    assert '"citations"' not in answer


def test_invalid_planned_envelope_is_not_exposed(tmp_path):
    bundle = build_evidence_bundle([], max_tokens=100)
    response = SimpleNamespace(
        content=(
            '{"answer":"Internal answer","citations":['
            '{"path":"private.py","supports":"internal rationale"}]'
        )
    )

    answer, citations, unsupported = agent_runtime._validated_planned_answer(
        response,
        bundle,
        tmp_path,
    )

    assert "Internal answer" not in answer
    assert "private.py" not in answer
    assert "supports" not in answer
    assert answer == "The code analysis result could not be validated."
    assert citations == ()
    assert unsupported == 1


def test_planned_answer_leads_with_conclusion_and_limits_evidence(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("\n".join(f"line {index}" for index in range(1, 8)))
    bundle = build_evidence_bundle(
        [
            {
                "evidence_type": "source",
                "path": "app.py",
                "symbol": "analyze",
                "start_line": 1,
                "end_line": 7,
                "content": source.read_text(),
            }
        ],
        max_tokens=100,
    )
    evidence_id = bundle.items[0].evidence_id
    citations = [
        {
            "evidence_id": evidence_id,
            "supports": f"finding {index}",
        }
        for index in range(1, 8)
    ]
    response = SimpleNamespace(
        content=json.dumps(
            {
                "answer": "The implementation has one root cause.",
                "citations": citations,
                "unsupported_claims": [],
            }
        )
    )

    answer, valid, unsupported = agent_runtime._validated_planned_answer(
        response,
        bundle,
        tmp_path,
        citation_context={
            "project": "SourceLens",
            "repository": "SourceLens",
            "revision": "abc123",
        },
    )

    assert answer == "The implementation has one root cause."
    assert len(valid) == 1
    assert valid[0]["path"] == "app.py"
    assert valid[0]["revision"] == "abc123"
    assert unsupported == 0


def test_planned_code_analysis_retries_truncated_final_concisely(tmp_path):
    plan = {
        "objective": "Find the root cause",
        "project": "SourceLens",
        "repository": "SourceLens",
        "revision": "workspace",
        "question_type": "implementation",
        "evidence_requirements": [],
        "codegraph_queries": [],
        "literal_queries": [],
        "source_windows": [],
        "max_files": 3,
        "max_fallback_rounds": 0,
        "budgets": {"max_evidence_tokens": 100},
    }
    responses = [
        SimpleNamespace(content=json.dumps(plan)),
        SimpleNamespace(
            content='{"answer":"An incomplete result",',
            response_metadata={"model_length_capped": True},
        ),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "The retry produced a complete conclusion.",
                    "citations": [],
                    "unsupported_claims": [],
                }
            ),
            response_metadata={},
        ),
    ]

    class Model:
        stop_reason = "model_length_capped"
        token_usage = {}

        def __init__(self):
            self.calls = []

        def invoke(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return responses[len(self.calls) - 1]

    model = Model()
    result = agent_runtime._run_planned_code_analysis(
        model=model,
        command={"question": "Why is the output incomplete?"},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["answer"] == (
        "The available code evidence is insufficient for a "
        "reliable conclusion.\n\n"
        "I searched the current scope but could not find reliable evidence "
        "linking the issue to a specific code path.\n"
        "Please provide any of these clues: a file path, class or function "
        "name, full traceback, or the relevant business call chain.\n"
        'If you only want a conceptual explanation, reply "explain the '
        'concept only".'
    )
    assert result["outcome"] == "blocked"
    assert result["planned_evidence"]["final_retry_count"] == 1
    assert result["planned_evidence"]["model_call_count"] == 3
    assert len(model.calls) == 3
    assert model.calls[1][1]["runtime_structured_output"] is True
    assert model.calls[2][1]["runtime_structured_output"] is True


def test_planned_code_analysis_retries_citation_missing_supports(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def load():\n    return 1\n", encoding="utf-8")
    source_payload = {
        "evidence_type": "source",
        "path": "app.py",
        "symbol": "load",
        "start_line": 1,
        "end_line": 2,
        "content": "def load():\n    return 1",
    }
    evidence_id = build_evidence_bundle(
        [source_payload],
        max_tokens=100,
    ).items[0].evidence_id
    plan = {
        "objective": "Explain load",
        "question_type": "implementation",
        "evidence_requirements": ["source lines"],
        "codegraph_queries": [],
        "literal_queries": [],
        "source_windows": [
            {"path": "app.py", "start_line": 1, "end_line": 2}
        ],
        "max_fallback_rounds": 0,
    }
    responses = [
        SimpleNamespace(content=json.dumps(plan)),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "load returns one.",
                    "citations": [{"evidence_id": evidence_id}],
                    "unsupported_claims": [],
                }
            )
        ),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "load returns one.",
                    "citations": [
                        {
                            "evidence_id": evidence_id,
                            "supports": "load returns one",
                        }
                    ],
                    "unsupported_claims": [],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def __init__(self):
            self.calls = []

        def invoke(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return responses[len(self.calls) - 1]

    class Reader:
        name = "read_workspace_file"

        def invoke(self, _args):
            return json.dumps(source_payload)

    model = Model()
    result = agent_runtime._run_planned_code_analysis(
        model=model,
        command={"question": "What does load return?"},
        tools=[Reader()],
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["answer"] == "load returns one."
    assert result["outcome"] == "completed"
    assert result["planned_evidence"]["final_retry_count"] == 1
    assert result["planned_evidence"]["citation_count"] == 1
    assert len(model.calls) == 3


def test_planned_code_analysis_uses_bounded_fallback_for_invalid_plan(
    tmp_path,
):
    responses = [
        SimpleNamespace(
            content=json.dumps(
                {
                    "objective": "Find the implementation",
                    "codegraph_queries": ["invalid schema"],
                }
            )
        ),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "Fallback planner answer.",
                    "citations": [],
                    "unsupported_claims": [],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def __init__(self):
            self.calls = []

        def invoke(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return responses[len(self.calls) - 1]

    model = Model()
    result = agent_runtime._run_planned_code_analysis(
        model=model,
        command={"question": "Where is it implemented?"},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["planned_evidence"]["planner_status"] == "fallback"
    assert result["planned_evidence"]["planner_retry_count"] == 0
    assert result["planned_evidence"]["model_call_count"] == 2
    assert "codegraph query must be an object" in result["planned_evidence"][
        "planner_rejection_reason"
    ]
    assert len(model.calls) == 2


def test_planner_repairs_invalid_plan_once_before_fallback(tmp_path):
    repaired_plan = {
        "objective": "Find the implementation",
        "question_type": "implementation",
        "evidence_requirements": ["source lines"],
        "codegraph_queries": [],
        "literal_queries": ["load"],
        "source_windows": [],
        "max_fallback_rounds": 0,
    }
    responses = [
        SimpleNamespace(
            content='{"objective":"invalid",'
            '"codegraph_queries":["bad"]}'
        ),
        SimpleNamespace(content=json.dumps(repaired_plan)),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "The implementation is in app.py.",
                    "citations": [],
                    "unsupported_claims": [],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def __init__(self):
            self.calls = []

        def invoke(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return responses[len(self.calls) - 1]

    model = Model()
    result = agent_runtime._run_planned_code_analysis(
        model=model,
        command={"question": "Where is it implemented?"},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
        planner_repair_enabled=True,
    )

    assert result["planned_evidence"]["planner_status"] == "repaired"
    assert result["planned_evidence"]["planner_retry_count"] == 1
    assert result["planned_evidence"]["model_call_count"] == 3
    assert len(model.calls) == 3


def test_planned_code_analysis_falls_back_without_repair_when_disabled(
    tmp_path,
):
    responses = [
        SimpleNamespace(
            content='{"objective":"still invalid",'
            '"codegraph_queries":["bad"]}'
        ),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "No repair was attempted.",
                    "citations": [],
                    "unsupported_claims": [],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def __init__(self):
            self.calls = []

        def invoke(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return responses[len(self.calls) - 1]

    model = Model()
    result = agent_runtime._run_planned_code_analysis(
        model=model,
        command={"question": "Where is it implemented?"},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["planned_evidence"]["planner_status"] == "fallback"
    assert result["planned_evidence"]["planner_retry_count"] == 0
    assert len(model.calls) == 2


def test_invalid_plan_fallback_uses_decomposed_queries():
    plan = agent_runtime._parse_planner_response(
        '{"objective":"invalid","codegraph_queries":["bad"]}',
        "为什么会证据不足，是否需要先做能力分析？",
        {},
    )

    queries = [item.query for item in plan.codegraph_queries]
    assert queries == ["证据不足"]
    assert plan.literal_queries == ("证据不足", "能力分析")
    assert "为什么会证据不足，是否需要先做能力分析？" not in (
        *queries,
        *plan.literal_queries,
    )


def test_insufficient_evidence_is_not_completed_or_added_to_answer(tmp_path):
    plan = {
        "objective": "Find the implementation",
        "project": "SourceLens",
        "repository": "SourceLens",
        "revision": "abc123",
        "question_type": "implementation",
        "evidence_requirements": ["source lines"],
        "codegraph_queries": [],
        "literal_queries": [],
        "source_windows": [],
        "max_files": 3,
        "max_fallback_rounds": 0,
        "budgets": {"max_evidence_tokens": 100},
    }
    responses = [
        SimpleNamespace(content=json.dumps(plan)),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "The implementation appears to be here.",
                    "citations": [],
                    "unsupported_claims": ["implementation location"],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def invoke(self, _messages, **_kwargs):
            return responses.pop(0)

    result = agent_runtime._run_planned_code_analysis(
        model=Model(),
        command={"question": "Where is it implemented?"},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["outcome"] == "blocked"
    assert result["termination_detail"] == {
        "reason": "evidence_insufficient"
    }
    assert result["planned_evidence"]["sufficient"] is False
    assert result["planned_evidence"]["gap_categories"] == ["source"]
    assert "Evidence gap" not in result["answer"]
    assert "unverified" not in result["answer"].lower()
    assert "file path" in result["answer"]
    assert "full traceback" in result["answer"]
    assert "explain the concept only" in result["answer"]


def test_unsupported_claims_prevent_completed_outcome(tmp_path):
    plan = {
        "objective": "Find the implementation",
        "project": "SourceLens",
        "repository": "SourceLens",
        "revision": "abc123",
        "question_type": "implementation",
        "evidence_requirements": [],
        "codegraph_queries": [],
        "literal_queries": [],
        "source_windows": [],
        "max_files": 3,
        "max_fallback_rounds": 0,
        "budgets": {"max_evidence_tokens": 100},
    }
    responses = [
        SimpleNamespace(content=json.dumps(plan)),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "The handler may be in services.py.",
                    "citations": [],
                    "unsupported_claims": ["handler location"],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def invoke(self, _messages, **_kwargs):
            return responses.pop(0)

    result = agent_runtime._run_planned_code_analysis(
        model=Model(),
        command={"question": "Where is the handler?"},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["outcome"] == "blocked"
    assert result["termination_detail"] == {
        "reason": "evidence_insufficient"
    }
    assert result["planned_evidence"]["sufficient"] is False
    assert result["planned_evidence"]["gap_categories"] == ["source"]
    assert "reliable conclusion" in result["answer"]


def test_structural_evidence_without_source_citation_returns_answer(tmp_path):
    plan = {
        "objective": "Trace the load flow",
        "question_type": "implementation",
        "evidence_requirements": ["structural flow"],
        "codegraph_queries": [
            {"operation": "explore", "query": "load"},
        ],
        "literal_queries": [],
        "source_windows": [],
        "max_fallback_rounds": 0,
    }
    responses = [
        SimpleNamespace(content=json.dumps(plan)),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "load delegates to the driver.",
                    "citations": [],
                    "unsupported_claims": [],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def invoke(self, _messages, **_kwargs):
            return responses.pop(0)

    class CodeGraphTool:
        name = "mcp__codegraph__codegraph_explore"

        def invoke(self, _args):
            return json.dumps(
                {
                    "ok": True,
                    "result": "load delegates to the driver",
                }
            )

    result = agent_runtime._run_planned_code_analysis(
        model=Model(),
        command={"question": "How does load work?"},
        tools=_workspace_tools(),
        mcp_tools=[CodeGraphTool()],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["answer"] == "load delegates to the driver."
    assert result["outcome"] == "completed"
    assert result["planned_evidence"]["citation_count"] == 0


def test_incomplete_final_protocol_prevents_completed_outcome(tmp_path):
    plan = {
        "objective": "Find the implementation",
        "project": "SourceLens",
        "repository": "SourceLens",
        "revision": "abc123",
        "question_type": "implementation",
        "evidence_requirements": [],
        "codegraph_queries": [],
        "literal_queries": [],
        "source_windows": [],
        "max_files": 3,
        "max_fallback_rounds": 0,
        "budgets": {"max_evidence_tokens": 100},
    }
    responses = [
        SimpleNamespace(content=json.dumps(plan)),
        SimpleNamespace(
            content=json.dumps(
                {"answer": "The handler is implemented in missing.py."}
            )
        ),
        SimpleNamespace(
            content=json.dumps(
                {"answer": "The handler is implemented in missing.py."}
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def invoke(self, _messages, **_kwargs):
            return responses.pop(0)

    result = agent_runtime._run_planned_code_analysis(
        model=Model(),
        command={"question": "Where is the handler?"},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["outcome"] == "blocked"
    assert result["termination_detail"] == {
        "reason": "evidence_insufficient"
    }
    assert "missing.py" not in result["answer"]


def test_planner_fallback_keeps_question_and_workspace_guidance(tmp_path):
    responses = [
        SimpleNamespace(
            content='{"objective":"Find it","codegraph_queries":['
        ),
        SimpleNamespace(
            content='{"objective":"Still invalid",'
            '"codegraph_queries":["bad"]}'
        ),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "Fallback answer.",
                    "citations": [],
                    "unsupported_claims": [],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def __init__(self):
            self.calls = []

        def invoke(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return responses[len(self.calls) - 1]

    model = Model()
    question = "Where is the request handler implemented?"
    guidance = "Search the API package before infrastructure modules."
    agent_runtime._run_planned_code_analysis(
        model=model,
        command={"question": question},
        tools=_workspace_tools(),
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
        context_skill_contents=[guidance],
    )

    planner_messages = model.calls[0][0]
    planner_text = "\n".join(message.content for message in planner_messages)
    assert question in planner_text
    assert guidance in planner_text
    assert len(model.calls) == 2


def test_fallback_query_terms_remove_conversational_chinese():
    assert agent_runtime._fallback_query_terms(
        "描述一下 代码分析的工作原理"
    ) == ("代码分析", "工作原理")


def test_citations_use_trusted_workspace_provenance(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def load():\n    return 1\n", encoding="utf-8")
    source_payload = {
        "evidence_type": "source",
        "path": "app.py",
        "symbol": "load",
        "start_line": 1,
        "end_line": 2,
        "content": "def load():\n    return 1",
    }
    evidence_id = build_evidence_bundle(
        [source_payload],
        max_tokens=100,
    ).items[0].evidence_id
    plan = {
        "objective": "Find the implementation",
        "project": "Invented Project",
        "repository": "invented/repository",
        "revision": "invented-revision",
        "question_type": "implementation",
        "evidence_requirements": ["source lines"],
        "codegraph_queries": [],
        "literal_queries": [],
        "source_windows": [
            {"path": "app.py", "start_line": 1, "end_line": 2}
        ],
        "max_fallback_rounds": 0,
    }
    responses = [
        SimpleNamespace(content=json.dumps(plan)),
        SimpleNamespace(
            content=json.dumps(
                {
                    "answer": "load returns one.",
                    "citations": [
                        {
                            "evidence_id": evidence_id,
                            "supports": "load returns one",
                        }
                    ],
                    "unsupported_claims": [],
                }
            )
        ),
    ]

    class Model:
        stop_reason = "stop"
        token_usage = {}

        def invoke(self, _messages, **_kwargs):
            return responses.pop(0)

    class Reader:
        name = "read_workspace_file"

        def invoke(self, _args):
            return json.dumps(source_payload)

    result = agent_runtime._run_planned_code_analysis(
        model=Model(),
        command={"question": "What does load return?"},
        tools=[Reader()],
        mcp_tools=[],
        emit_agent_event=lambda *_args: None,
        workspace_root=tmp_path,
    )

    assert result["citations"][0]["project"] == "workspace"
    assert result["citations"][0]["repository"] == "workspace"
    assert result["citations"][0]["revision"] == "working-tree"


def test_planned_prompts_include_bound_workspace_guidance():
    guidance = "Search porter before checking infrastructure modules."

    planner_prompt = agent_runtime._planned_planner_prompt(
        {"workspace_path": "/workspace"},
        context_skill_contents=[guidance],
    )
    final_prompt = agent_runtime._planned_final_prompt(
        context_skill_contents=[guidance]
    )

    assert guidance in planner_prompt
    assert "Leave source_windows empty unless exact file paths" in (
        planner_prompt
    )
    assert guidance in final_prompt


def test_planner_prompt_enumerates_allowed_operations_and_json_contract():
    planner_prompt = agent_runtime._planned_planner_prompt(
        {"workspace_path": "/workspace"},
    )

    assert '"callers"' in planner_prompt
    assert '"explore"' in planner_prompt
    assert '"search"' in planner_prompt
    assert '"operation": "search"' in planner_prompt
    assert "literal_queries must be an array of plain strings" in (
        planner_prompt
    )


def test_planner_prompt_honors_available_codegraph_operations():
    planner_prompt = agent_runtime._planned_planner_prompt(
        {"workspace_path": "/workspace"},
        codegraph_operations=["explore"],
    )

    assert '"explore"' in planner_prompt
    assert '"callers"' not in planner_prompt

    unavailable_prompt = agent_runtime._planned_planner_prompt(
        {"workspace_path": "/workspace"},
        codegraph_operations=[],
    )
    assert "CodeGraph is not available" in unavailable_prompt


def test_repair_prompt_includes_validation_error_and_allowed_operations():
    repair_prompt = agent_runtime._planned_planner_repair_prompt(
        validation_error="unsupported CodeGraph operation: symbol_search",
        codegraph_operations=["explore", "search"],
    )

    assert "unsupported CodeGraph operation: symbol_search" in repair_prompt
    assert '"explore"' in repair_prompt
    assert '"search"' in repair_prompt
