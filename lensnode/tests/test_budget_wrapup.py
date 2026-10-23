"""Regression coverage for budget boundaries and final synthesis context."""

import json
import threading
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from lensnode.agent_runtime.execution import (
    _compact_wrapup_messages,
    _run_agent_with_turn_limit,
    _synthesize_wrapup_answer,
)


class BudgetedAgent:
    """Reach the reserve while yielding scripted graph states."""

    def __init__(self, event, states):
        self.event = event
        self.states = states
        self.exhausted = False

    def stream(self, _input, **_kwargs):
        self.event.set()
        for messages in self.states:
            yield {"messages": messages}
        self.exhausted = True


class SynthesisModel:
    """Capture an unnecessary rewrite or a necessary recovery request."""

    stop_reason = "token_budget_wrapup"

    def __init__(self):
        self.messages = None

    def invoke(self, messages, **_kwargs):
        self.messages = messages
        return AIMessage(content="Recovered answer")


def test_budget_reserve_preserves_naturally_completed_answer():
    event = threading.Event()
    original = "Run egisplus-cli agent check to check the agent."
    agent = BudgetedAgent(event, [[HumanMessage(content="Check the agent"), AIMessage(content=original)]])
    model = SynthesisModel()

    answer, truncated, reason = _run_agent_with_turn_limit(
        agent,
        [{"role": "user", "content": "Check the agent"}],
        max_turns=26,
        model=model,
        answer_language="Simplified Chinese",
        token_budget_wrapup_event=event,
    )

    assert (answer, truncated, reason) == (original, False, None)
    assert model.messages is None
    assert agent.exhausted


def test_repeated_compaction_failure_stops_the_graph_and_synthesizes_once():
    """An invalid summarizer cannot leave the growing agent loop running."""

    from lensnode.agent_runtime import summarization

    class FailingAgent:
        def stream(self, _input, **_kwargs):
            yield {"messages": [HumanMessage(content="Summarize work"), AIMessage(content="Observed INC-42")]}
            raise summarization.ContextCompactionError("Invalid summary after recovery")

    events = []
    model = SynthesisModel()
    answer, truncated, reason = _run_agent_with_turn_limit(
        FailingAgent(), [{"role": "user", "content": "Summarize work"}], max_turns=100,
        model=model, emit_event=lambda name, detail: events.append(name),
    )
    assert (answer, truncated, reason) == ("Recovered answer", True, "context_compaction_failed")
    assert "Observed INC-42" in str(model.messages)
    assert "deepagents.agent.context_compaction_failed" in events


def test_budget_reserve_does_not_skip_after_model_correction():
    event = threading.Event()
    draft = [HumanMessage(content="Check the agent"), AIMessage(content="Unverified draft")]
    agent = BudgetedAgent(event, [draft, [*draft, HumanMessage(content="Correct the unsupported claim")]])
    model = SynthesisModel()

    answer, truncated, reason = _run_agent_with_turn_limit(
        agent,
        [{"role": "user", "content": "Check the agent"}],
        max_turns=26,
        model=model,
        token_budget_wrapup_event=event,
    )

    assert (answer, truncated, reason) == ("Recovered answer", True, "token_budget_wrapup")


@pytest.mark.parametrize("artifact_required,status,content", [
    (False, "success", "Updated file /report.html"),
    (True, "error", "Error: permission denied"),
])
def test_budget_publication_stops_after_unrelated_or_failed_write(artifact_required, status, content):
    event = threading.Event()
    write = AIMessage(content="", tool_calls=[{"name": "write_file", "args": {}, "id": "write"}])
    agent = BudgetedAgent(event, [[write], [write, ToolMessage(
        name="write_file", tool_call_id="write", content=content, status=status,
    )]])
    result = _run_agent_with_turn_limit(
        agent, [{"role": "user", "content": "report"}], 26, model=SynthesisModel(),
        token_budget_wrapup_event=event, execution_evidence={"artifact_required": artifact_required},
    )
    assert not agent.exhausted
    assert result == ("Recovered answer", True, "token_budget_wrapup")


@pytest.mark.parametrize("needs_correction", [False, True])
def test_real_graph_runs_verification_before_budget_completion(needs_correction):
    event = threading.Event()
    calls = []
    reviews = []
    question = "How do I check the Linux agent?"
    original = "Run egisplus-cli agent check."

    class BudgetModel(FakeMessagesListChatModel):
        def _generate(self, *args, **kwargs):
            """Reach the reserve during the first model response."""

            calls.append("model")
            event.set()
            return super()._generate(*args, **kwargs)

    class ReviewMiddleware(AgentMiddleware):
        @hook_config(can_jump_to=["model"])
        def after_model(self, state, runtime):
            """Require a correction only when the draft is unsupported."""

            reviews.append(state["messages"][-1].content)
            if needs_correction:
                return {
                    "messages": [HumanMessage(content="Correct the unsupported claim.")],
                    "jump_to": "model",
                }
            return None

    graph = create_agent(
        BudgetModel(responses=[AIMessage(content=original)]),
        middleware=[ReviewMiddleware()],
    )
    model = SynthesisModel()

    result = _run_agent_with_turn_limit(
        graph,
        [{"role": "user", "content": question}],
        26,
        model=model,
        token_budget_wrapup_event=event,
        answer_language="Simplified Chinese",
    )

    assert reviews == [original]
    assert calls == ["model"]
    if needs_correction:
        assert result == ("Recovered answer", True, "token_budget_wrapup")
        assert model.messages[-1].content == question
    else:
        assert result == (original, False, None)
        assert model.messages is None


@pytest.mark.parametrize("stop_reason", ["token_capped", "model_length_capped", "safety_terminated"])
def test_budget_reserve_keeps_abnormal_model_termination(stop_reason):
    event = threading.Event()
    agent = BudgetedAgent(event, [[AIMessage(content="Incomplete answer")]])
    model = SynthesisModel()
    model.stop_reason = stop_reason

    _answer, truncated, reason = _run_agent_with_turn_limit(
        agent, [{"role": "user", "content": "question"}], 26,
        model=model, token_budget_wrapup_event=event,
    )

    assert truncated
    assert reason is not None


@pytest.mark.parametrize("message", [
    AIMessage(content="Checking now", tool_calls=[{"name": "grep", "args": {}, "id": "read"}]),
    AIMessage(content="Incomplete", invalid_tool_calls=[{"name": "grep", "args": "{", "id": "read"}]),
    AIMessage(content="Incomplete", response_metadata={"finish_reason": "length"}),
    AIMessage(content="Incomplete", response_metadata={"finish_reason": "content_filter"}),
])
def test_budget_reserve_does_not_accept_pending_or_incomplete_text(message):
    event = threading.Event()
    agent = BudgetedAgent(event, [[message]])
    model = SynthesisModel()

    result = _run_agent_with_turn_limit(
        agent, [{"role": "user", "content": "question"}], 26,
        model=model, token_budget_wrapup_event=event,
    )

    assert result == ("Recovered answer", True, "token_budget_wrapup")
    assert model.messages is not None


@pytest.mark.parametrize("question", [
    "How do I check the Linux agent?",
    "请用英文回答：如何检查 Agent？",
    "Responde en español: ¿cómo compruebo el agente?",
])
def test_wrapup_keeps_original_request_as_language_authority(question):
    model = SynthesisModel()
    _synthesize_wrapup_answer(
        model, [HumanMessage(content=question)], "Simplified Chinese", None,
        current_question=question, reason="token_budget_wrapup",
    )

    assert model.messages[-1].type == "human"
    assert model.messages[-1].content == question
    control = model.messages[-2]
    assert control.type == "system"
    assert "internal" in control.content.lower()
    assert "language" in control.content.lower()
    assert "你已经达到" not in control.content


def test_wrapup_digest_preserves_relevant_evidence_beyond_message_prefix():
    evidence = "preamble " * 180 + "\nHeartbeat: 305\nsystemctl start egisplus-agent.service"
    messages = [
        SystemMessage(content="Trusted rules"),
        ToolMessage(content="old " * 10000, tool_call_id="old"),
        ToolMessage(content=evidence, tool_call_id="read"),
        AIMessage(content="Answer introduction " * 100 + "\nThe final verified detail."),
    ]

    compact = _compact_wrapup_messages(messages)
    text = "\n".join(str(message.content) for message in compact)

    assert "Heartbeat: 305" in text
    assert "systemctl start egisplus-agent.service" in text
    assert "The final verified detail." in text
    assert len(text) < 40000
    assert "omitted" in text.lower()


def test_required_wrapup_retains_runtime_rules_and_original_question():
    question = "How do I check the Linux agent?"
    rules = "Platform rules " * 900 + "\nNever expose credentials."
    event = threading.Event()
    agent = BudgetedAgent(event, [[
        HumanMessage(content="Internal correction from a checkpoint"),
        AIMessage(content="", tool_calls=[{"name": "grep", "args": {}, "id": "read"}]),
    ]])
    model = SynthesisModel()

    _run_agent_with_turn_limit(
        agent,
        [{"role": "user", "content": "Internal correction from a checkpoint"}],
        26,
        model=model,
        answer_language="Simplified Chinese",
        token_budget_wrapup_event=event,
        current_question=question,
        system_prompt=rules,
    )

    assert model.messages[0].type == "system"
    assert model.messages[0].content == rules
    assert model.messages[-1].content == question


def test_wrapup_retains_early_paginated_coverage_and_conversation_constraints():
    """Large late evidence must not erase an earlier dataset or platform constraint."""

    messages = [
        HumanMessage(content="Use VMware agentless backups; compare quotes with invoices."),
    ]
    for offset in range(0, 127, 20):
        messages.extend([
            AIMessage(content="", tool_calls=[{
                "id": f"invoice-{offset}", "name": "query_records",
                "args": {"table": "invoices", "offset": offset},
            }]),
            ToolMessage(content=json.dumps({"result": [{"type": "text", "text": json.dumps({
                "results": [{"id": n, "value": "row " * 100} for n in range(offset, min(offset + 20, 127))],
                "total": 127, "offset": offset, "has_more": offset + 20 < 127,
            })}]}), tool_call_id=f"invoice-{offset}"),
        ])
    messages.extend([
        ToolMessage(content=json.dumps({"total": 260, "offset": 240, "has_more": False,
                                       "results": [{"id": n, "value": "quote " * 500} for n in range(20)]}),
                    tool_call_id="quotes"),
        HumanMessage(content="Does the same window restriction still apply?"),
        AIMessage(content="Latest provisional answer; verify against the sources."),
    ])
    compact = _compact_wrapup_messages(messages)
    text = "\n".join(str(message.content) for message in compact)
    assert "VMware agentless" in text
    assert '"table": "invoices"' in text
    assert '"total": 127' in text
    assert '"offset": 120' in text
    assert '"has_more": false' in text
    assert '"total": 260' in text
    assert "Latest provisional answer" in text
    assert len(text) < 40000


def test_wrapup_recovers_evidence_removed_by_graph_summarization():
    """Keep observed tool metadata outside graph messages that middleware replaces."""

    class SummarizingAgent:
        def stream(self, _input, **_kwargs):
            yield {"messages": [
                HumanMessage(content="VMware agentless; compare invoice and quote totals."),
                AIMessage(content="", tool_calls=[{
                    "id": "invoices", "name": "query_records", "args": {"table": "invoices"},
                }]),
                ToolMessage(content=json.dumps({"total": 127, "offset": 120, "has_more": False}),
                            tool_call_id="invoices"),
            ]}
            event.set()
            yield {"messages": [
                HumanMessage(content="Conversation summarized; continue comparison."),
                ToolMessage(content=json.dumps({"total": 260, "offset": 240, "has_more": False}),
                            tool_call_id="quotes"),
            ]}

    event = threading.Event()
    model = SynthesisModel()
    _run_agent_with_turn_limit(
        SummarizingAgent(), [{"role": "user", "content": "VMware agentless; compare invoice and quote totals."}],
        26, model=model, token_budget_wrapup_event=event,
    )
    text = "\n".join(str(m.content) for m in model.messages)
    assert '"total": 127' in text
    assert '"table": "invoices"' in text
    assert '"total": 260' in text
    assert "VMware agentless" in text


def test_publication_grace_stops_on_new_response_after_history_compaction():
    """A smaller replacement history must not grant extra model calls or unrelated tools."""

    event = threading.Event()
    history = [AIMessage(content="Earlier response", id=f"old-{index}") for index in range(5)]
    write = AIMessage(content="", id="writer", tool_calls=[{"name": "write_file", "args": {}, "id": "write"}])
    written = ToolMessage(name="write_file", tool_call_id="write", content="Updated file /report.html")
    guidance = HumanMessage(content="Publish the report now")
    unrelated = AIMessage(content="", id="next", tool_calls=[{"name": "search", "args": {}, "id": "search"}])
    agent = BudgetedAgent(event, [
        [*history, write], [*history, write, written], [write, written, guidance],
        [write, written, guidance, unrelated],
        [write, written, guidance, unrelated, ToolMessage(content="Must not execute", tool_call_id="search")],
    ])
    result = _run_agent_with_turn_limit(
        agent, [{"role": "user", "content": "report"}], 26, model=SynthesisModel(),
        token_budget_wrapup_event=event, execution_evidence={"artifact_required": True},
    )
    assert not agent.exhausted
    assert result == ("Recovered answer", True, "token_budget_wrapup")


@pytest.mark.parametrize(
    "reason",
    ["evidence_convergence_limit", "no_information_gain", "evidence_corpus_limit", "convergence_budget"],
)
def test_evidence_gap_wrapup_requests_a_declared_gap(reason):
    """A retrieval dead end must end with an explicit, structured gap note."""

    model = SynthesisModel()
    _synthesize_wrapup_answer(
        model, [HumanMessage(content="Investigate INC-42")], "Simplified Chinese", None,
        current_question="Investigate INC-42", reason=reason,
    )

    control = model.messages[-2].content.lower()
    assert "could not be confirmed" in control
    assert "narrower query" in control


def test_convergence_wrapup_uses_the_specific_stop_reason():
    event = threading.Event()
    agent = BudgetedAgent(event, [[HumanMessage(content="Check the agent"), AIMessage(content="Draft")]])
    model = SynthesisModel()

    answer, truncated, reason = _run_agent_with_turn_limit(
        agent,
        [{"role": "user", "content": "Check the agent"}],
        max_turns=26,
        model=model,
        execution_evidence={
            "convergence_exhausted": True,
            "convergence_reason": "no_information_gain",
        },
    )

    assert (answer, truncated, reason) == ("Recovered answer", True, "evidence_convergence_limit")
    assert "could not be confirmed" in model.messages[-2].content.lower()
