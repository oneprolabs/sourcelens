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
