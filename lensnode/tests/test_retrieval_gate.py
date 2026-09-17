from types import SimpleNamespace

import pytest

from lensnode import agent_runtime


def _mode(general_chat=False):
    return SimpleNamespace(
        name="general_chat" if general_chat else "document_qa",
        general_chat=general_chat,
        execution_gates=general_chat,
        emit_model_round=lambda *_args, **_kwargs: None,
    )


def _model(content="", error=None):
    def invoke(*_args, **_kwargs):
        if error is not None:
            raise error
        return SimpleNamespace(content=content)

    return SimpleNamespace(
        invoke=invoke,
        emit_output=None,
        stop_reason="stop",
        token_usage={"total_tokens": 3},
    )


def _state(question, *, model=None, command_extra=None, general_chat=False):
    command = {
        "run_uuid": "gate-run",
        "task": "knowledge_qa",
        "question": question,
        "answer_language": "zh-CN",
    }
    command.update(command_extra or {})
    return SimpleNamespace(
        runtime_mode=_mode(general_chat),
        resume_state=None,
        run_uuid="gate-run",
        model=model or _model(),
        command=command,
        tools=[],
        mcp_tools=[],
        scenario={"title": "Knowledge Q&A", "prompt": "Answer from files."},
        question=question,
        resources=SimpleNamespace(context_skill_contents=[]),
        emit_agent_event=lambda *_args, **_kwargs: None,
        emit_user_event=lambda *_args, **_kwargs: None,
        emit_output=None,
    )


def _runtime(tmp_path):
    return agent_runtime.LensDeepAgentRuntime(
        SimpleNamespace(workspace_path=str(tmp_path))
    )


def _patch_loop(monkeypatch, runtime, state, calls):
    monkeypatch.setattr(
        runtime,
        "_prepare_runtime",
        lambda *_args, **_kwargs: state,
    )
    monkeypatch.setattr(
        runtime,
        "_build_agent",
        lambda _state: calls.append("build"),
    )
    monkeypatch.setattr(
        runtime,
        "_execute_agent",
        lambda _state: calls.append("execute") or {"answer": "retrieved"},
    )
    monkeypatch.setattr(
        agent_runtime,
        "cleanup_runtime_resources",
        lambda _resources: None,
    )


def test_needs_retrieval_true_only_on_explicit_false():
    assert (
        agent_runtime._message_needs_retrieval(
            _model('{"needs_retrieval": false}'),
            "在么",
        )
        is False
    )
    for content in (
        '{"needs_retrieval": true}',
        '{"needs_retrieval": "no"}',
        '{"something": false}',
        "not json",
        "",
        "```json\n{}\n```",
    ):
        assert (
            agent_runtime._message_needs_retrieval(_model(content), "在么")
            is True
        )


def test_needs_retrieval_fails_safe_on_error():
    model = _model(error=RuntimeError("gateway down"))
    assert agent_runtime._message_needs_retrieval(model, "在么") is True


@pytest.mark.parametrize(
    "text",
    ["Hi", "Helo", "在么", "helloooo", "Thanks a lot!", "你是谁呀"],
)
def test_gate_answers_non_retrieval_messages(
    monkeypatch,
    tmp_path,
    text,
):
    state = _state(
        text,
        model=_model('{"needs_retrieval": false}'),
    )
    runtime = _runtime(tmp_path)
    calls = []
    _patch_loop(monkeypatch, runtime, state, calls)
    monkeypatch.setattr(
        agent_runtime,
        "_answer_general_chat_directly",
        lambda *_args, **_kwargs: "你好，有什么可以帮你的？",
    )

    result = runtime._answer_sync(state.command)

    assert calls == []
    assert result["outcome"] == "completed"
    assert result["answer"] == "你好，有什么可以帮你的？"


@pytest.mark.parametrize(
    "text",
    [
        "介绍一下 AGIOne 在新加坡的案例",
        "Singtel",
        "合同里的付款条款是什么",
    ],
)
def test_gate_defers_retrieval_messages(monkeypatch, tmp_path, text):
    state = _state(
        text,
        model=_model('{"needs_retrieval": true}'),
    )
    runtime = _runtime(tmp_path)
    calls = []
    _patch_loop(monkeypatch, runtime, state, calls)

    result = runtime._answer_sync(state.command)

    assert calls == ["build", "execute"]
    assert result == {"answer": "retrieved"}


def test_gate_fails_safe_to_retrieval(monkeypatch, tmp_path):
    state = _state("Hi", model=_model(error=RuntimeError("down")))
    runtime = _runtime(tmp_path)
    calls = []
    _patch_loop(monkeypatch, runtime, state, calls)

    result = runtime._answer_sync(state.command)

    assert calls == ["build", "execute"]
    assert result == {"answer": "retrieved"}


def test_gate_defers_when_subject_document_present(monkeypatch, tmp_path):
    state = _state(
        "Hi",
        model=_model('{"needs_retrieval": false}'),
        command_extra={"subject_documents": [{"uuid": "doc-1"}]},
    )
    runtime = _runtime(tmp_path)
    calls = []
    _patch_loop(monkeypatch, runtime, state, calls)

    result = runtime._answer_sync(state.command)

    assert calls == ["build", "execute"]
    assert result == {"answer": "retrieved"}


def test_gate_ignores_general_chat_mode(tmp_path):
    state = _state("Hi", general_chat=True)
    runtime = _runtime(tmp_path)

    assert runtime._maybe_answer_without_retrieval(state) is None


def test_default_turn_limit_for_knowledge_qa():
    assert (
        agent_runtime._resolve_agent_turn_limit({"task": "knowledge_qa"}) == 26
    )
    assert (
        agent_runtime._resolve_agent_turn_limit(
            {"task": "knowledge_qa", "agent_rounds": "max"}
        )
        == 100
    )
    assert (
        agent_runtime._resolve_agent_turn_limit(
            {"task": "knowledge_qa", "max_agent_turns": 7}
        )
        == 7
    )
