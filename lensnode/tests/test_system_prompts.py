from lensnode.agent_runtime.system_prompts import (
    _general_chat_system_prompt,
    _knowledge_system_prompt,
    _system_prompt,
)


def test_general_chat_describes_interactive_mindmap_format():
    prompt = _general_chat_system_prompt({"task": "general_chat"})

    assert "fenced `mindmap` code block" in prompt
    assert "indented Markdown bullets" in prompt


def test_knowledge_prompt_describes_interactive_mindmap_format():
    prompt = _knowledge_system_prompt(
        {"prompt": "Answer from the selected workspace."},
        {"task": "knowledge"},
    )

    assert "fenced `mindmap` code block" in prompt
    assert "Do not use Mermaid" in prompt


def test_knowledge_qa_prompt_keeps_research_process_out_of_final_answer():
    prompt = _knowledge_system_prompt(
        {"prompt": "Answer from the selected workspace."},
        {"task": "knowledge_qa"},
    )

    assert "lead with the direct conclusion" in prompt
    assert "Do not include a search log" in prompt
    assert "documents still to read" in prompt


def test_mcp_failure_is_visible_in_both_chat_modes():
    for task in ("general_chat", "knowledge_qa"):
        prompt = _system_prompt(
            {"prompt": "Answer from available evidence."},
            {
                "task": task,
                "unavailable_mcp_services": ["Records service"],
            },
        )

        assert "Records service" in prompt
        assert "Their data has not been queried" in prompt
        assert "do not claim an empty result" in prompt
        assert "Ordinary conversation can continue normally" in prompt
        assert "Follow pagination" in prompt


def test_record_fields_and_counts_are_preserved_in_both_chat_modes():
    for task in ("general_chat", "knowledge_qa"):
        prompt = _system_prompt(
            {"prompt": "Answer from available evidence."},
            {"task": task},
        )

        assert "Copy record identifiers and customer names verbatim" in prompt
        assert "Do not shorten" in prompt
        assert "parenthetical text" in prompt
        assert "other requested text fields" in prompt
        assert "Business record fields" in prompt
        assert "do not redact them as runtime identifiers" in prompt
        assert "Record counts are integers" in prompt
        assert "never label a monetary amount as a record count" in prompt
