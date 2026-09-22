import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from lensnode import plugin_tools
from lensnode.plugin_runtime import PluginRuntimeError
from lensnode.plugin_package_loader import load_runtime_contract
from lensnode.plugin_tools import (
    PluginToolError,
    _execute_plugin_tool,
    _json,
    build_plugin_tools,
)


AI_GATEWAY_URL = "http://gateway/api/lens/lensnode/ai-gateway/"
GITHUB_RUNTIME = load_runtime_contract("github", "1.0.0")


def _github_read_file(
    client,
    arguments,
    token,
    endpoint="https://github.com",
    config=None,
):
    config = config or {
        "__allowed_scope": {"repositories": ["owner/repository"]}
    }
    return GITHUB_RUNTIME.execute_tool(
        "github_read_file",
        client,
        arguments,
        token,
        endpoint,
        config,
    )


def _github_search_code(
    client,
    arguments,
    token,
    endpoint="https://github.com",
    config=None,
):
    config = config or {
        "__allowed_scope": {"repositories": ["owner/repository"]}
    }
    return GITHUB_RUNTIME.execute_tool(
        "github_search_code",
        client,
        arguments,
        token,
        endpoint,
        config,
    )


def _command(*tool_keys):
    definitions = {
        "github_read_file": {
            "key": "github_read_file",
            "description": "Read one authorized repository file.",
            "capability": "repository.read",
            "side_effect": "none",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repository": {"type": "string"},
                    "path": {"type": "string"},
                    "ref": {"type": "string"},
                },
                "required": ["repository", "path"],
            },
        },
        "github_search_code": {
            "key": "github_search_code",
            "description": "Search one authorized repository.",
            "capability": "repository.read",
            "side_effect": "none",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repository": {"type": "string"},
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["repository", "query"],
            },
        },
    }
    return {
        "run_uuid": "run-1",
        "loaded_plugins": [
            {
                "connection_uuid": "connection-1",
                "plugin_key": "github",
                "plugin_version": "1.0.0",
                "protocol_version": 1,
                "tools": [definitions[key] for key in tool_keys],
            }
        ],
    }


class GitHubRuntimeClient:
    def __init__(self, github_barrier=None):
        self.requests = []
        self.github_barrier = github_barrier

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        if url.endswith("/plugin-runtime/tool-snapshots/"):
            payload = kwargs["json"]
            return httpx.Response(
                201,
                json={
                    "snapshot_uuid": f"snapshot-{payload['call_id']}",
                    "run_uuid": payload["run_uuid"],
                    "connection_uuid": payload["connection_uuid"],
                    "tool_key": payload["tool_key"],
                    "invocation_id": payload["call_id"],
                    "plugin_key": "github",
                    "plugin_version": "1.0.0",
                    "protocol_version": 1,
                },
            )
        if url.endswith("/material/"):
            return httpx.Response(
                200,
                json={
                    "plugin_key": "github",
                    "endpoint": "https://github.com",
                    "value": "github-secret",
                },
            )
        if url.endswith("/plugin-runtime/leases/"):
            snapshot_uuid = kwargs["json"]["snapshot_uuid"]
            return httpx.Response(
                201,
                json={
                    "lease_uuid": f"lease-{snapshot_uuid}",
                    "snapshot_uuid": snapshot_uuid,
                },
            )
        raise AssertionError(url)

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        if "/plugin-runtime/snapshots/" in url:
            snapshot_uuid = url.rstrip("/").rsplit("/", 1)[-1]
            tool_key = (
                "github_search_code"
                if "search" in snapshot_uuid
                else "github_read_file"
            )
            arguments = {
                "repository": "owner/repository",
                "query": "ToolRuntime",
                "max_results": 2,
            } if tool_key == "github_search_code" else {
                "repository": "owner/repository",
                "path": "README.md",
                "ref": "main",
            }
            return httpx.Response(
                200,
                json={
                    "snapshot_uuid": snapshot_uuid,
                    "run_uuid": "run-1",
                    "tool_key": tool_key,
                    "invocation_id": snapshot_uuid.removeprefix("snapshot-"),
                    "plugin_key": "github",
                    "plugin_version": "1.0.0",
                    "resolved_config": {
                        "endpoint": "https://github.com",
                        "allowed_scope": {
                            "repositories": ["owner/repository"],
                        },
                        "arguments": arguments,
                    },
                },
            )
        if "/contents/" in url:
            assert kwargs["headers"]["Authorization"] == (
                "Bearer github-secret"
            )
            if self.github_barrier is not None:
                self.github_barrier.wait(timeout=2)
            return httpx.Response(200, text="# SourceLens\n")
        if url.endswith("/search/code"):
            assert kwargs["params"]["q"] == (
                "ToolRuntime repo:owner/repository"
            )
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "items": [
                        {
                            "name": "plugin_tools.py",
                            "path": "lensnode/plugin_tools.py",
                            "sha": "abc",
                        },
                        {
                            "name": "test_plugin_tools.py",
                            "path": "tests/test_plugin_tools.py",
                            "sha": "def",
                        },
                    ],
                },
            )
        raise AssertionError(url)

    @contextmanager
    def stream(self, method, url, **kwargs):
        assert method == "GET"
        yield self.get(url, **kwargs)


def _config():
    return SimpleNamespace(
        ai_gateway_url=AI_GATEWAY_URL,
        token="node-token",
    )


def test_build_plugin_tools_hides_runtime_and_connection_inputs():
    tools = build_plugin_tools(
        _command("github_read_file"),
        _config(),
        GitHubRuntimeClient(),
    )

    assert [item.name for item in tools] == ["github_read_file"]
    assert set(tools[0].args) == {"repository", "path", "ref"}
    assert "connection_uuid" not in tools[0].args
    assert "runtime" not in tools[0].args
    assert tools[0].metadata["capability_family"] == "plugin"
    assert tools[0].metadata["plugin_key"] == "github"
    assert tools[0].metadata["capability"] == "repository.read"


def test_plugin_tool_uses_connection_scoped_provider_http_client():
    control_client = GitHubRuntimeClient()

    class Pool:
        def __init__(self):
            self.bindings = []

        def bind(self, plugin_key, connection_uuid, origins, post_paths=()):
            self.bindings.append(
                (plugin_key, connection_uuid, origins, post_paths)
            )
            return control_client

    pool = Pool()
    tool = build_plugin_tools(
        _command("github_read_file"),
        _config(),
        control_client,
        plugin_http_pool=pool,
    )[0]

    result = json.loads(tool.func(
        repository="owner/repository",
        path="README.md",
        runtime=SimpleNamespace(tool_call_id="provider-http-1"),
    ))

    assert result["ok"] is True
    assert pool.bindings == [
        (
            "github",
            "connection-1",
            ("https://api.github.com",),
            (),
        )
    ]


def test_build_plugin_tools_rejects_unsupported_capability_family():
    command = _command("github_read_file")
    command["loaded_plugins"][0]["tools"][0]["capability_family"] = "mcp"

    with pytest.raises(PluginToolError) as exc_info:
        build_plugin_tools(command, _config(), GitHubRuntimeClient())

    assert str(exc_info.value) == "PLUGIN_TOOL_INVALID"


def test_github_read_file_uses_snapshot_lease_and_bounded_result():
    client = GitHubRuntimeClient()
    tool = build_plugin_tools(
        _command("github_read_file"),
        _config(),
        client,
    )[0]

    result = json.loads(tool.func(
        repository="owner/repository",
        path="README.md",
        ref="main",
        runtime=SimpleNamespace(tool_call_id="read-1"),
    ))

    assert result == {
        "ok": True,
        "repository": "owner/repository",
        "path": "README.md",
        "ref": "main",
        "content": "# SourceLens\n",
        "truncated": False,
    }
    assert "github-secret" not in json.dumps(result)


def test_github_search_code_returns_only_bounded_safe_fields():
    client = GitHubRuntimeClient()
    tool = build_plugin_tools(
        _command("github_search_code"),
        _config(),
        client,
    )[0]

    result = json.loads(tool.func(
        repository="owner/repository",
        query="ToolRuntime",
        path="",
        max_results=2,
        runtime=SimpleNamespace(tool_call_id="search-1"),
    ))

    assert result["ok"] is True
    assert result["total_count"] == 2
    assert result["items"] == [
        {
            "name": "plugin_tools.py",
            "path": "lensnode/plugin_tools.py",
            "sha": "abc",
        },
        {
            "name": "test_plugin_tools.py",
            "path": "tests/test_plugin_tools.py",
            "sha": "def",
        },
    ]
    assert "github-secret" not in json.dumps(result)


def test_plugin_tool_calls_can_overlap_github_requests():
    client = GitHubRuntimeClient(github_barrier=Barrier(2))
    tool = build_plugin_tools(
        _command("github_read_file"),
        _config(),
        client,
    )[0]

    def invoke(index):
        return tool.func(
            repository="owner/repository",
            path="README.md",
            ref="main",
            runtime=SimpleNamespace(tool_call_id=f"read-{index}"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, range(2)))

    assert all(json.loads(result)["ok"] for result in results)


class StreamingGitHubClient:
    """Provide controlled streaming responses for boundary tests."""

    def __init__(self, response):
        self.response = response

    @contextmanager
    def stream(self, method, _url, **_kwargs):
        assert method == "GET"
        yield self.response


def test_github_read_file_rejects_redirects():
    response = httpx.Response(302, headers={"Location": "https://evil"})

    with pytest.raises(PluginRuntimeError) as exc_info:
        _github_read_file(
            StreamingGitHubClient(response),
            {"repository": "owner/repository", "path": "README.md"},
            "github-secret",
        )

    assert str(exc_info.value) == "GITHUB_REDIRECT_REJECTED"


def test_github_read_file_truncates_oversized_text():
    response = httpx.Response(
        200,
        content=b"a" * 200_001,
    )

    result = _github_read_file(
        StreamingGitHubClient(response),
        {"repository": "owner/repository", "path": "README.md"},
        "github-secret",
    )

    assert result["truncated"] is True
    assert len(result["content"]) == 200_000


def test_github_search_code_rejects_oversized_response():
    response = httpx.Response(
        200,
        content=b"{" + b"a" * 1_000_000 + b"}",
    )

    with pytest.raises(PluginRuntimeError) as exc_info:
        _github_search_code(
            StreamingGitHubClient(response),
            {
                "repository": "owner/repository",
                "query": "needle",
                "max_results": 10,
            },
            "github-secret",
        )

    assert str(exc_info.value) == "GITHUB_RESPONSE_TOO_LARGE"


def test_plugin_events_never_include_material_value():
    events = []
    tool = build_plugin_tools(
        _command("github_read_file"),
        _config(),
        GitHubRuntimeClient(),
        emit_event=lambda name, detail: events.append((name, detail)),
    )[0]

    tool.func(
        repository="owner/repository",
        path="README.md",
        runtime=SimpleNamespace(tool_call_id="event-1"),
    )

    assert "github-secret" not in json.dumps(events)


def test_plugin_runtime_failure_is_returned_as_a_stable_error():
    """Unexpected Plugin exceptions must not escape into model execution."""

    def fail_handler(*_args):
        raise RuntimeError("secret detail")

    result = _execute_plugin_tool(
        _command("github_read_file"),
        _config(),
        GitHubRuntimeClient(),
        "connection-1",
        "github",
        "1.0.0",
        "github_read_file",
        SimpleNamespace(tool_call_id="failure-1"),
        {"repository": "owner/repository", "path": "README.md"},
        fail_handler,
        None,
    )

    payload = json.loads(result)
    assert payload == {"ok": False, "error": "PLUGIN_EXECUTION_FAILED"}


def test_plugin_result_has_a_total_model_context_limit():
    """Large Plugin responses must not consume an unbounded context window."""

    payload = json.loads(_json({"ok": True, "content": "x" * 900_001}))

    assert payload == {"ok": False, "error": "PLUGIN_RESULT_TOO_LARGE"}


class PluginToolCallingModel(BaseChatModel):
    """Issue one Plugin Tool call and then finish from its result."""

    @property
    def _llm_type(self):
        return "plugin-tool-test"

    def bind_tools(self, _tools, **_kwargs):
        return self

    def _generate(self, messages, **_kwargs):
        if not any(message.type == "tool" for message in messages):
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "github_read_file",
                        "args": {
                            "repository": "owner/repository",
                            "path": "README.md",
                            "ref": "main",
                        },
                        "id": "agent-call-1",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content="Tool completed.")
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_deep_agent_injects_model_tool_call_id_into_plugin_snapshot():
    client = GitHubRuntimeClient()
    tools = build_plugin_tools(
        _command("github_read_file"),
        _config(),
        client,
    )
    agent = create_deep_agent(
        model=PluginToolCallingModel(),
        tools=tools,
        system_prompt="Use the Plugin Tool.",
        subagents=[],
    )

    result = agent.invoke({
        "messages": [{"role": "user", "content": "Read the README."}]
    })

    tool_snapshot_requests = [
        kwargs["json"]
        for method, url, kwargs in client.requests
        if method == "POST" and url.endswith("/tool-snapshots/")
    ]
    assert tool_snapshot_requests[0]["call_id"] == "agent-call-1"
    assert result["messages"][-1].content == "Tool completed."


class ParallelPluginToolCallingModel(PluginToolCallingModel):
    """Issue two independent Plugin Tool calls in one model turn."""

    def _generate(self, messages, **_kwargs):
        if any(message.type == "tool" for message in messages):
            message = AIMessage(content="Both tools completed.")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "github_read_file",
                        "args": {
                            "repository": "owner/repository",
                            "path": "README.md",
                            "ref": "main",
                        },
                        "id": f"parallel-call-{index}",
                        "type": "tool_call",
                    }
                    for index in range(2)
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_deep_agent_executes_independent_plugin_calls_in_parallel():
    client = GitHubRuntimeClient(github_barrier=Barrier(2))
    tools = build_plugin_tools(
        _command("github_read_file"),
        _config(),
        client,
    )
    agent = create_deep_agent(
        model=ParallelPluginToolCallingModel(),
        tools=tools,
        system_prompt="Use the Plugin Tool in parallel.",
        subagents=[],
    )

    result = agent.invoke({
        "messages": [{"role": "user", "content": "Read twice."}]
    })

    github_requests = [
        url
        for method, url, _kwargs in client.requests
        if method == "GET" and "/contents/" in url
    ]
    tool_messages = [
        message for message in result["messages"] if message.type == "tool"
    ]
    assert len(github_requests) == 2
    assert len(tool_messages) == 2
    assert all(json.loads(message.content)["ok"] for message in tool_messages)


def test_runtime_contract_allows_a_decision_only_runtime(tmp_path):
    package = tmp_path / "decideonly"
    package.mkdir()
    (package / "runtime.py").write_text(
        "PLUGIN_API_VERSION = 1\n"
        "PLUGIN_KEY = 'decideonly'\n"
        "PLUGIN_VERSION = '1.0.0'\n"
        "def execute_tool(key, client, arguments, secret, endpoint, config):\n"
        "    return {'ok': True}\n"
    )

    contract = load_runtime_contract(
        "decideonly",
        "1.0.0",
        roots=[str(tmp_path)],
    )

    assert contract.build_tool is None
    assert callable(contract.execute_tool)


def test_build_plugin_tools_requires_build_tool_for_exposed_tools(monkeypatch):
    contract = SimpleNamespace(
        build_tool=None,
        execute_tool=lambda *args, **kwargs: {"ok": True},
        http_origins=None,
        http_post_paths=None,
    )
    monkeypatch.setattr(
        plugin_tools,
        "load_runtime_contract",
        lambda *args, **kwargs: contract,
    )

    with pytest.raises(PluginToolError):
        build_plugin_tools(
            _command("github_read_file"),
            _config(),
            GitHubRuntimeClient(),
        )


def test_build_plugin_tools_hides_internal_tools():
    command = _command("github_read_file")
    command["loaded_plugins"][0]["tools"][0]["exposure"] = "internal"

    tools = build_plugin_tools(command, _config(), GitHubRuntimeClient())

    assert tools == []


def test_build_plugin_tools_keeps_model_tools_alongside_internal_ones():
    command = _command("github_read_file", "github_search_code")
    command["loaded_plugins"][0]["tools"][0]["exposure"] = "internal"

    tools = build_plugin_tools(command, _config(), GitHubRuntimeClient())

    assert [item.name for item in tools] == ["github_search_code"]
