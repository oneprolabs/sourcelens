"""Preserve evidence and user input across real context compaction."""

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from lensnode.agent_runtime.summarization import LensSummarizationMiddleware


def _conversation():
    """Return tool evidence followed by a recent working set."""

    return [
        HumanMessage(content="Summarize yesterday's work", id="user"),
        AIMessage(content="", id="request", tool_calls=[{"id": "fetch", "name": "fetch", "args": {}}]),
        ToolMessage(content="INC-42 was resolved. " * 40, name="fetch", tool_call_id="fetch", id="evidence"),
        AIMessage(content="Draft report", id="draft"),
        HumanMessage(content="Verify the report", id="review"),
        AIMessage(content="Recent working context", id="recent"),
    ]


def _middleware(response):
    """Use the upstream compaction path with a deterministic summary model."""

    return LensSummarizationMiddleware(
        model=FakeListChatModel(responses=[response]),
        token_counter=lambda messages: sum(len(str(message.content)) for message in messages),
        trigger=("tokens", 100),
        keep=("messages", 2),
        trim_tokens_to_summarize=32000,
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_compaction_summarizes_tool_evidence_without_dropping_human_anchors(asynchronous):
    middleware = _middleware("Evidence: INC-42 was resolved. Remaining work: deliver report.")
    messages = _conversation()
    old, preserved = middleware._partition_messages(messages, 4)

    assert any(isinstance(message, ToolMessage) for message in middleware._trim_messages_for_summary(old))
    assert messages[0] in preserved
    result = (
        asyncio.run(middleware.abefore_model({"messages": messages}, None))
        if asynchronous
        else middleware.before_model({"messages": messages}, None)
    )

    assert result is not None
    text = " ".join(str(message.content) for message in result["messages"])
    assert "Evidence: INC-42 was resolved" in text
    assert "Summarize yesterday's work" in text
    assert "Verify the report" in text
    assert "Previous conversation was too long" not in text


@pytest.mark.parametrize("response", [
    "", "Previous conversation was too long to summarize.",
    'I will collect more evidence. <｜DSML｜calls><｜DSML｜invoke name="read_file">',
    'Evidence summary. <||DSML||invoke name="execute">',
    'Remaining work. <｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="read_file">',
])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_invalid_summary_retains_original_history_and_emits_diagnostic(response, asynchronous):
    middleware = _middleware(response)
    events = []
    middleware._emit_event = lambda name, payload: events.append((name, payload))
    messages = _conversation()
    state = {"messages": messages}

    result = (
        asyncio.run(middleware.abefore_model(state, None)) if asynchronous else middleware.before_model(state, None)
    )

    assert result is None
    assert state["messages"] == messages
    assert events[-1][0] == "deepagents.summarization.failed"
    assert events[-1][1]["reason"] == "invalid_summary"
