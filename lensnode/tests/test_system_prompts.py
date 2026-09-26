from lensnode.agent_runtime.system_prompts import (
    _general_chat_system_prompt,
    _knowledge_system_prompt,
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
