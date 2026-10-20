from lensnode.agent_runtime.system_prompts import _general_chat_system_prompt


def test_smart_general_chat_prompt_keeps_assistant_workspace_guide():
    prompt = _general_chat_system_prompt(
        {
            "task": "general_chat",
            "routing_mode": "smart",
            "subagents": [],
        },
        workspace_guide="GitHub scope is limited to oneprolabs.",
    )

    assert "<workspace_context>" in prompt
    assert "GitHub scope is limited to oneprolabs." in prompt
