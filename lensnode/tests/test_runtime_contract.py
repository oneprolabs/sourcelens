"""Cross-version contract tests for the deepagents runtime dependency.

deepagents is pinned exactly in ``pyproject.toml``, but upstream minor
releases have silently removed default middleware, prompt text, and
tool-usage prose (0.7 dropped the default ``TodoListMiddleware`` and the
authored base prompt). These tests build the same graph the runtime builds
and fail loudly when an upgrade drops a tool, a middleware, or required
prompt guidance, instead of degrading at runtime.
"""

from importlib import metadata
from types import SimpleNamespace

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import deepagents.graph
from deepagents import create_deep_agent

from lensnode.agent_runtime.assembly import _agent_middleware, _fast_subagent
from lensnode.agent_runtime.runtime import _build_execution_backend
from lensnode.agent_runtime.system_prompts import _system_prompt


MIN_DEEPAGENTS_VERSION = (0, 7, 15)


class _SilentModel(BaseChatModel):
    """Chat model that satisfies graph construction without calling out."""

    @property
    def _llm_type(self):
        return "runtime-contract-test"

    def bind_tools(self, _tools, **_kwargs):
        return self

    def _generate(self, messages, **_kwargs):
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="ok"))]
        )


def _middleware_named(middleware, name):
    matches = [
        item for item in middleware if getattr(item, "name", None) == name
    ]
    assert matches, f"{name} missing from the assembled middleware stack"
    return matches[0]


def _tool_names(middleware):
    return {
        getattr(tool, "name", None)
        for tool in getattr(middleware, "tools", [])
    }


@pytest.fixture
def built_agent(monkeypatch, tmp_path):
    """Build the real graph and capture what reaches ``create_agent``."""

    captured = {}
    original = deepagents.graph.create_agent

    def capture(model, **kwargs):
        captured["middleware"] = list(kwargs.get("middleware") or [])
        captured["system_prompt"] = kwargs.get("system_prompt")
        return original(model, **kwargs)

    monkeypatch.setattr(deepagents.graph, "create_agent", capture)
    command = {"task": "code_analysis", "target_dirs": [{"path": "/w"}]}
    backend = _build_execution_backend(
        SimpleNamespace(execution_backend="trusted_container"),
        tmp_path,
    )
    create_deep_agent(
        model=_SilentModel(),
        tools=[],
        system_prompt=_system_prompt({"prompt": "Analyze."}, command),
        backend=backend,
        subagents=[_fast_subagent()],
        middleware=_agent_middleware(command, None),
        name="contract",
    )
    return captured


def test_installed_deepagents_meets_contract_floor():
    version = metadata.version("deepagents")
    parsed = tuple(int(part) for part in version.split(".")[:3])
    assert parsed >= MIN_DEEPAGENTS_VERSION, (
        f"deepagents {version} is below the contract floor "
        f"{MIN_DEEPAGENTS_VERSION}"
    )


def test_main_stack_keeps_planning_and_filesystem_contract(built_agent):
    middleware = built_agent["middleware"]
    todo = _middleware_named(middleware, "TodoListMiddleware")
    filesystem = _middleware_named(middleware, "FilesystemMiddleware")
    _middleware_named(middleware, "SubAgentMiddleware")

    assert "write_todos" in _tool_names(todo)
    assert {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
    } <= _tool_names(filesystem)


def test_general_purpose_subagent_keeps_planning_tool():
    subagent = _fast_subagent()
    todo = _middleware_named(subagent["middleware"], "TodoListMiddleware")

    assert "write_todos" in _tool_names(todo)


def test_final_prompt_keeps_runtime_owned_guidance(built_agent):
    prompt = built_agent["system_prompt"]

    assert "Operating contract:" in prompt
    assert "plan tool" in prompt
    assert "shell/execute tool" in prompt
