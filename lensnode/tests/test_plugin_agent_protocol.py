"""Generic Plugin extensions remain bounded by the host's admitted capabilities."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from lensnode import plugin_agent
from lensnode.plugin_agent import AgentExecutionPolicy, AgentExtension, prepare_agent_extensions
from lensnode.plugin_package_loader import PluginPackageLoadError


def _binding(plugin_key="custom_source", key="collect_events"):
    """Create a frozen model tool and a source-private internal tool."""
    return {
        "plugin_key": plugin_key,
        "plugin_version": "1.0.0",
        "plugin_display_name": "Custom Source",
        "protocol_version": 1,
        "connection_uuid": f"connection-{plugin_key}",
        "allowed_scope": {"repositories": ["private/repository"]},
        "tools": [
            {"key": key, "capability": "repository.read", "side_effect": "none"},
            {"key": f"{key}_internal", "exposure": "internal", "capability": "repository.read"},
        ],
    }


def _tool(name, plugin_key="custom_source"):
    """Represent an already admitted tool without requiring a model or transport."""
    return SimpleNamespace(name=name, metadata={"plugin_key": plugin_key, "capability_family": "plugin"})


def _prepare(monkeypatch, tmp_path, declarations, tools=(), bindings=None):
    """Provide only installed runtime declarations at the protocol's package boundary."""
    contracts = {
        key: SimpleNamespace(
            plugin_key=key,
            plugin_version="1.0.0",
            agent_extensions=lambda entries=entries: entries,
            tool_metadata={"collect_events": {"domain": "custom", "role": "batch"}},
        )
        for key, entries in declarations.items()
    }
    monkeypatch.setattr(plugin_agent, "load_runtime_contract", lambda key, version: contracts[key])
    command = {
        "run_uuid": "run-1",
        "question": "Collect yesterday's events.",
        "task": "general_chat",
        "runtime_route": "fast",
        "token": "sensitive-token",
        "config": {"secret": "sensitive-config"},
        "loaded_plugins": bindings if bindings is not None else [_binding(key) for key in declarations],
    }
    return prepare_agent_extensions(command, tmp_path, tools=tools)


def test_no_frozen_bindings_means_no_extension_factory_runs(monkeypatch, tmp_path):
    """Installing a runtime alone cannot grant its Agent behavior to a Run."""
    calls = []

    def load(*args):
        calls.append(args)
        raise AssertionError("An unbound runtime must not be loaded")

    monkeypatch.setattr(plugin_agent, "load_runtime_contract", load)
    extensions = prepare_agent_extensions({"question": "Collect events."}, tmp_path)

    assert extensions.preferred_route is None
    assert not extensions.guidance
    assert not extensions.tools
    assert calls == []


def test_arbitrary_domain_factory_gets_safe_context_and_only_model_bound_metadata(monkeypatch, tmp_path):
    """Domain registration does not require a source prefix or expose secrets/internal tools."""
    contexts = []

    class CustomExtension(AgentExtension):
        guidance = "Follow the source's event schema."
        preferred_route = {"route": "plan_execute", "confidence": 1.0, "reason": "plugin_domain"}

    def factory(context):
        contexts.append(context)
        return CustomExtension()

    extensions = _prepare(monkeypatch, tmp_path, {"custom_source": [("custom_domain", factory)]})

    context = contexts[0]
    assert context.command["question"] == "Collect yesterday's events."
    assert "token" not in context.command
    assert "config" not in context.command
    assert "loaded_plugins" not in context.command
    assert context.workspace == tmp_path
    assert context.state_root.is_relative_to(tmp_path)
    assert len(context.bound_tools) == 1
    bound = context.bound_tools[0]
    assert bound.key == "collect_events"
    assert bound.plugin_key == "custom_source"
    assert bound.plugin_version == "1.0.0"
    assert bound.connection_uuid == "connection-custom_source"
    assert bound.metadata["domain"] == "custom"
    assert bound.metadata["role"] == "batch"
    assert "Follow the source's event schema." in extensions.guidance
    assert extensions.preferred_route == CustomExtension.preferred_route


def test_shared_factory_is_initialized_once_with_all_bound_sources(monkeypatch, tmp_path):
    """Sources sharing an extension key and factory share one state owner and wrapper."""
    contexts = []

    def factory(context):
        contexts.append(context)
        return AgentExtension()

    bindings = [_binding("first_source", "collect_first"), _binding("second_source", "collect_second")]
    _prepare(
        monkeypatch,
        tmp_path,
        {"first_source": [("shared_domain", factory)], "second_source": [("shared_domain", factory)]},
        bindings=bindings,
    )

    assert len(contexts) == 1
    assert {tool.key for tool in contexts[0].bound_tools} == {"collect_first", "collect_second"}


def test_internal_only_binding_does_not_initialize_a_model_extension(monkeypatch, tmp_path):
    """An installed declaration does not expose internal-only source capabilities to the Agent."""
    calls = []
    binding = _binding()
    binding["tools"] = binding["tools"][1:]

    def factory(context):
        calls.append(context)
        return AgentExtension()

    extensions = _prepare(
        monkeypatch,
        tmp_path,
        {"custom_source": [("internal", factory)]},
        bindings=[binding],
    )

    assert calls == []
    assert not extensions.tools
    assert not extensions.guidance


def test_conflicting_factories_cannot_silently_claim_the_same_extension_key(monkeypatch, tmp_path):
    """A key collision is an admission error instead of choosing an arbitrary implementation."""
    with pytest.raises(PluginPackageLoadError):
        _prepare(
            monkeypatch,
            tmp_path,
            {
                "first_source": [("shared_domain", lambda context: AgentExtension())],
                "second_source": [("shared_domain", lambda context: AgentExtension())],
            },
        )


def test_policy_filters_only_the_tools_that_the_host_already_provided(monkeypatch, tmp_path):
    """A Plugin may narrow the executor's tools while preserving their original instances."""
    selected, removed = _tool("collect_events"), _tool("get_details")

    class NarrowExtension(AgentExtension):
        def execution_policy(self, route, tools):
            return AgentExecutionPolicy(route=route, allowed_tool_names=frozenset({"collect_events"}))

    extensions = _prepare(
        monkeypatch,
        tmp_path,
        {"custom_source": [("narrow", lambda context: NarrowExtension())]},
        tools=(selected, removed),
    )
    policy = extensions.apply_policy({"route": "fast"}, (selected, removed))

    assert policy.allowed_tool_names == frozenset({"collect_events"})
    assert extensions.filter_tools((selected, removed)) == [selected]


def test_execution_policy_cannot_restore_an_unbound_tool(monkeypatch, tmp_path):
    """Naming a remote tool in a policy cannot expand the host's admitted tool set."""
    selected = _tool("collect_events")

    class ExpandingExtension(AgentExtension):
        def execution_policy(self, route, tools):
            return AgentExecutionPolicy(route=route, allowed_tool_names=frozenset({"unbound_remote_tool"}))

    extensions = _prepare(
        monkeypatch,
        tmp_path,
        {"custom_source": [("expanding", lambda context: ExpandingExtension())]},
        tools=(selected,),
    )

    with pytest.raises(PluginPackageLoadError):
        extensions.apply_policy({"route": "fast"}, (selected,))


def test_multiple_extension_tool_filters_are_intersected(monkeypatch, tmp_path):
    """A later policy cannot restore a tool removed by an earlier Plugin policy."""
    first, second = _tool("collect_first"), _tool("collect_second")

    class FirstExtension(AgentExtension):
        def execution_policy(self, route, tools):
            return AgentExecutionPolicy(route=route, allowed_tool_names=frozenset({"collect_first"}))

    class SecondExtension(AgentExtension):
        def execution_policy(self, route, tools):
            return AgentExecutionPolicy(route=route, allowed_tool_names=frozenset({"collect_second"}))

    extensions = _prepare(
        monkeypatch,
        tmp_path,
        {"custom_source": [("first", lambda context: FirstExtension()), ("second", lambda context: SecondExtension())]},
        tools=(first, second),
    )
    policy = extensions.apply_policy({"route": "fast"}, (first, second))

    assert policy.allowed_tool_names == frozenset()
    assert extensions.filter_tools((first, second)) == []


@pytest.mark.parametrize("name", ["collect_events", "write_todos", "execute", "task"])
def test_extension_helpers_cannot_shadow_host_or_registered_tool_names(monkeypatch, tmp_path, name):
    """Helpers cannot replace core capabilities or source tools by reusing their names."""

    class HelperExtension(AgentExtension):
        tools = (_tool(name),)

    with pytest.raises(PluginPackageLoadError):
        _prepare(
            monkeypatch,
            tmp_path,
            {"custom_source": [("helper", lambda context: HelperExtension())]},
            tools=(_tool("collect_events"),),
        )


@pytest.mark.parametrize("error", [RuntimeError("provider failed"), asyncio.CancelledError("cancelled")])
def test_executor_wrapper_preserves_provider_errors_and_cancellation(monkeypatch, tmp_path, error):
    """Extension execution must preserve the original failure object and cancellation signal."""
    calls = []

    class WrappingExtension(AgentExtension):
        def wrap_executor(self, binding, definition, executor):
            def wrapped(*args, **kwargs):
                calls.append(definition["key"])
                return executor(*args, **kwargs)

            return wrapped

    def factory(context):
        return WrappingExtension()

    extensions = _prepare(monkeypatch, tmp_path, {"custom_source": [("wrapper", factory)]})

    def execute():
        raise error

    binding = _binding()
    wrapped = extensions.wrap_executor(binding, binding["tools"][0], execute)

    with pytest.raises(type(error)) as raised:
        wrapped()

    assert raised.value is error
    assert calls == ["collect_events"]


def test_extension_evidence_cannot_override_host_delivery_facts(monkeypatch, tmp_path):
    """Plugin evidence is confined to its documented namespace."""

    class InvalidEvidenceExtension(AgentExtension):
        def evidence(self):
            return {"_deliverables": [{"delivered": True}], "plugin_evidence": {"custom": []}}

    extensions = _prepare(
        monkeypatch,
        tmp_path,
        {"custom_source": [("evidence", lambda context: InvalidEvidenceExtension())]},
    )

    with pytest.raises(PluginPackageLoadError):
        extensions.evidence()


def test_cleanup_rejects_a_symlink_state_root_without_deleting_its_target(tmp_path):
    """A replaced Run directory cannot direct cleanup outside the workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    root = plugin_agent.plugin_state_root(workspace, "run-1")
    root.parent.mkdir(parents=True, exist_ok=True)
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PluginPackageLoadError):
        plugin_agent.cleanup_plugin_state(workspace, "run-1")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert root.is_symlink()


def test_state_cleanup_rejects_a_symlink_parent_escape(tmp_path):
    """A symlink in an ancestor cannot redirect a hashed Run path outside the workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (workspace / ".sourcelens").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PluginPackageLoadError):
        plugin_agent.cleanup_plugin_state(workspace, "run-1")

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_agent_runtime_has_no_source_specific_activity_report_contract():
    """Core Agent code communicates through Plugin hooks, without report business rules."""
    root = Path(__file__).resolve().parents[1] / "lensnode" / "agent_runtime"
    forbidden_names = {
        "ActivityLedger",
        "ACTIVITY_TOOLS",
        "activity_tool_bindings",
        "build_activity_evidence_tool",
        "_is_activity_report",
        "_is_github_activity_report",
        "_activity_report_batch_tools",
        "_activity_report_expected_tools",
        "_report_execution_guidance",
        "_report_scoped_tools",
        "_high_confidence_report_route",
    }
    violations = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "activity_reports" in (node.module or ""):
                violations.append(f"{path.name}:{node.lineno}: activity report import")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in forbidden_names:
                violations.append(f"{path.name}:{node.lineno}: business helper {node.name}")
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                violations.append(f"{path.name}:{node.lineno}: business symbol {node.id}")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and any(
                    key in node.value
                    for key in ("github_activity_summary", "gitlab_activity_summary", "jira_activity_summary")
                )
            ):
                violations.append(f"{path.name}:{node.lineno}: source-specific activity tool")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {
                    "startswith",
                    "endswith",
                }
                and any(
                    isinstance(argument, ast.Constant) and argument.value == "activity_summary"
                    for argument in node.args
                )
            ):
                violations.append(f"{path.name}:{node.lineno}: tool-name business inference")

    assert violations == []


@pytest.mark.parametrize("task,routing_mode", [("code_analysis", None), ("document", None), ("general_chat", "smart")])
def test_extension_constraints_apply_in_modes_without_general_chat_classification(
    tmp_path, monkeypatch, task, routing_mode
):
    """Runtime mode shortcuts cannot bypass an admitted extension's constraints."""

    from lensnode.agent_runtime.runtime import LensDeepAgentRuntime

    class ConstrainedExtension(AgentExtension):
        def execution_policy(self, route, tools):
            return AgentExecutionPolicy(route, frozenset({"allowed"}), False, ("plugin:allowed",))

    monkeypatch.setenv("LENSNODE_CHECKPOINT_ENABLED", "false")
    tools = [_tool("allowed"), _tool("removed")]
    state = SimpleNamespace(
        command={"task": task, "routing_mode": routing_mode},
        runtime_mode=SimpleNamespace(execution_gates=False, general_chat=task == "general_chat"),
        agent_extensions=plugin_agent.AgentExtensions((ConstrainedExtension(),)),
        tools=tools,
        plugin_tools=tools,
        mcp_tools=[],
        runtime_evidence={},
        resume_state=None,
        run_uuid="run-1",
        resources=SimpleNamespace(skill_paths=[], context_skill_contents=[]),
        emit_agent_event=lambda *args: None,
        emit_user_event=lambda *args: None,
        persist_execution_state=lambda: None,
        checkpoint_ready=False,
        new_span=lambda *args: "span",
        enter_span=lambda *args: None,
        exit_span=lambda *args: None,
    )
    runtime = LensDeepAgentRuntime(SimpleNamespace())
    assert runtime._route_runtime(state) is None
    assert state.tools == [tools[0]]
    assert state.plugin_tools == [tools[0]]
    assert state.runtime_evidence["required_sources"] == ["plugin:allowed"]
    assert not runtime._subagents_enabled(state)
    assert state.capability_middleware is not None
    assert state.capability_middleware.require_initial_plan == (task == "general_chat")

    from langchain_core.messages import ToolMessage
    from lensnode.agent_runtime.assembly import _agent_middleware
    from lensnode.agent_runtime.outcomes import _finalize_runtime_outcome

    assert state.capability_middleware in _agent_middleware(
        state.command,
        None,
        capability_middleware=state.capability_middleware,
    )
    state.capability_middleware._record_result(
        SimpleNamespace(tool=tools[0], tool_call={"name": "allowed", "args": {}}),
        ToolMessage(content='{"ok":true,"_evidence":{"complete":true}}', tool_call_id="call-1"),
    )
    outcome, _ = _finalize_runtime_outcome(
        capability_middleware=state.capability_middleware,
        evidence_requirement=state.evidence_requirement,
        required_capabilities=[],
        truncated=False,
        stop_reason=None,
        execution_gate_enabled=False,
        runtime_evidence=state.runtime_evidence,
    )
    assert outcome == "completed"


def test_helper_collisions_with_late_discovered_tools_are_rejected():
    """A helper admitted before MCP discovery cannot replace a remote Tool."""

    helper = _tool("remote_search")

    class LocalExtension(AgentExtension):
        tools = (helper,)

    extensions = plugin_agent.AgentExtensions((LocalExtension(),))
    with pytest.raises(PluginPackageLoadError, match="conflict"):
        extensions.validate_tool_names((helper, _tool("remote_search", "remote")))


def test_policy_removed_mcp_tools_are_not_discoverable_through_tool_search():
    """Deferred discovery respects the same shrinking execution boundary."""

    import json

    from lensnode.mcp_tools import DeferredMCPToolMiddleware

    allowed = SimpleNamespace(name="mcp__server__allowed", description="Retrieve remote events")
    removed = SimpleNamespace(name="mcp__server__removed", description="Retrieve remote events")
    middleware = DeferredMCPToolMiddleware((allowed, removed))
    middleware.restrict_tools({allowed.name, "tool_search"})
    found = json.loads(middleware.search_tool.invoke({"query": "remote"}))
    assert [item["name"] for item in found["tools"]] == [allowed.name]


def test_active_extensions_cannot_be_bypassed_by_a_no_retrieval_shortcut():
    """Installed execution constraints must run before a mode can finish directly."""

    from lensnode.agent_runtime.runtime import LensDeepAgentRuntime

    state = SimpleNamespace(
        agent_extensions=plugin_agent.AgentExtensions((AgentExtension(),)),
        runtime_mode=SimpleNamespace(general_chat=False),
        decision_policy=SimpleNamespace(needs_retrieval=lambda *args: pytest.fail("classifier bypassed the extension")),
    )
    assert LensDeepAgentRuntime(SimpleNamespace())._maybe_answer_without_retrieval(state) is None


@pytest.mark.parametrize("task", ["document", "general_chat"])
def test_extension_subagent_prohibition_reaches_graph_assembly(monkeypatch, tmp_path, task):
    """Configured subagents cannot bypass a Plugin policy when the graph is assembled."""

    from lensnode.agent_runtime import runtime as runtime_module

    class NoDelegationExtension(AgentExtension):
        def execution_policy(self, route, tools):
            return AgentExecutionPolicy(route, subagents_allowed=False)

    extensions = plugin_agent.AgentExtensions((NoDelegationExtension(),))
    extensions.apply_policy({"route": "plan_execute"}, ())
    captured = {}
    configured_calls = []
    runtime = runtime_module.LensDeepAgentRuntime(SimpleNamespace(workspace_path=str(tmp_path)))

    def configured(state):
        configured_calls.append(state)
        return [{"name": "configured-worker", "description": "A configured subagent"}]

    def create(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(runtime, "_build_configured_subagents", configured)
    monkeypatch.setattr(runtime_module, "_build_execution_backend", lambda *args: object())
    monkeypatch.setattr(runtime_module, "_build_summarization_middleware", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime_module, "checkpoint_enabled", lambda: False)
    monkeypatch.setattr(runtime_module, "create_deep_agent", create)
    state = SimpleNamespace(
        command={"task": task, "question": "Collect events."},
        scenario={"prompt": "Collect events."},
        runtime_evidence={},
        runtime_mode=SimpleNamespace(general_chat=task == "general_chat"),
        agent_extensions=extensions,
        route_decision={"route": "plan_execute"},
        resources=SimpleNamespace(root=tmp_path, skill_paths=[], context_skill_contents=[], mcp_config_path=tmp_path),
        model=object(),
        model_ref="model-ref",
        tools=[],
        mcp_tools=[],
        plugin_tools=[],
        mcp_middleware=None,
        capability_middleware=None,
        subagent_middleware=(),
        runtime_middleware=(),
        runtime_guidance=(),
        root_observation_id=None,
        trajectory=None,
        cancel_event=None,
        trace_context=None,
        shared_token_budget=None,
        token_budget={"max_tokens": 0, "final_reserve_tokens": 0},
        token_budget_wrapup_event=None,
        run_uuid="run-1",
        run_span="run-span",
        emit_agent_event=lambda *args: None,
        emit_trace_observation=lambda *args: None,
        new_span=lambda *args: "span",
    )

    runtime._build_agent(state)

    assert configured_calls == []
    assert captured["subagents"] == []
