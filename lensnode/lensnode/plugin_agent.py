"""Generic lifecycle protocol for installed Plugin agent extensions."""

import copy
import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .plugin_package_loader import PluginPackageLoadError, load_runtime_contract

EXTENSION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class BoundPluginTool:
    """One model-visible Tool admitted by the Run's frozen binding."""

    plugin_key: str
    plugin_version: str
    connection_uuid: str
    key: str
    metadata: dict = field(default_factory=dict)
    plugin_display_name: str = ""


@dataclass(frozen=True)
class AgentExtensionContext:
    """Task context without host credentials, mutable state, or service clients."""

    command: dict
    bound_tools: tuple
    workspace: Path
    state_root: Path
    skills: tuple = ()
    tool_names: tuple = ()
    emit_event: object = None


@dataclass(frozen=True)
class AgentExecutionPolicy:
    """Execution constraints returned by an installed extension."""

    route: dict
    allowed_tool_names: frozenset | None = None
    subagents_allowed: bool = True
    required_sources: tuple = ()


class AgentExtension:
    """Fixed prepare, policy, execution, and evidence lifecycle."""

    guidance = ""
    tools = ()
    preferred_route = None
    refresh_evidence = False

    def execution_policy(self, route, tools):
        """Contribute constraints after the host selects a route."""

        return AgentExecutionPolicy(route=route)

    def wrap_executor(self, binding, definition, executor):
        """Wrap an already authorized executor without obtaining its credentials."""

        return executor

    def evidence(self):
        """Return execution evidence using the generic host envelope."""

        return {}


def plugin_state_root(workspace, run_uuid):
    """Locate private per-Run state without accepting a caller-selected path."""

    root = Path(workspace).resolve()
    key = hashlib.sha256(str(run_uuid).encode()).hexdigest()
    path = root / ".sourcelens" / "plugin-state" / key
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise PluginPackageLoadError("plugin state path is invalid")
    return path


def cleanup_plugin_state(workspace, run_uuid):
    """Release private extension state when the Run lifecycle ends."""

    path = plugin_state_root(workspace, run_uuid)
    if path.exists():
        shutil.rmtree(path)


class AgentExtensions:
    """Compose Plugin contributions while preserving host authority boundaries."""

    def __init__(self, extensions=(), reserved_tool_names=()):
        self.extensions = tuple(extensions)
        self.allowed_tool_names = None
        self.subagents_allowed = True
        seen = set(reserved_tool_names) | {
            "write_todos",
            "task",
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "execute",
        }
        for extension in self.extensions:
            if not isinstance(extension, AgentExtension):
                raise PluginPackageLoadError("plugin agent extension is invalid")
            for tool in extension.tools:
                name = getattr(tool, "name", None)
                if not isinstance(name, str) or not name or name in seen:
                    raise PluginPackageLoadError("plugin agent tool name conflict")
                seen.add(name)

    def validate_tool_names(self, tools):
        """Reject helper collisions with capabilities discovered after preparation."""

        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise PluginPackageLoadError("plugin agent tool name conflict")

    @property
    def guidance(self):
        """Return Plugin-owned instructions for the agent prompt."""

        return tuple(extension.guidance for extension in self.extensions if extension.guidance)

    @property
    def tools(self):
        """Return local helper Tools contributed during preparation."""

        return tuple(tool for extension in self.extensions for tool in extension.tools)

    @property
    def preferred_route(self):
        """Return a single compatible route suggestion, if one exists."""

        routes = [extension.preferred_route for extension in self.extensions if extension.preferred_route is not None]
        if routes and any(route != routes[0] for route in routes[1:]):
            raise PluginPackageLoadError("plugin agent route conflict")
        return copy.deepcopy(routes[0]) if routes else None

    @property
    def refresh_evidence(self):
        """Invalidate cached reviews when extension evidence can change on resume."""

        return any(extension.refresh_evidence for extension in self.extensions)

    def apply_policy(self, route, tools):
        """Combine constraints; extensions may only shrink the available Tool set."""

        selected = copy.deepcopy(route)
        original = {tool.name for tool in tools}
        allowed = set(original)
        required_sources = set()
        self.subagents_allowed = True
        for extension in self.extensions:
            policy = extension.execution_policy(copy.deepcopy(selected), tuple(tools))
            if not isinstance(policy, AgentExecutionPolicy) or not isinstance(policy.route, dict):
                raise PluginPackageLoadError("plugin execution policy is invalid")
            selected = policy.route
            if policy.allowed_tool_names is not None:
                if not set(policy.allowed_tool_names) <= original:
                    raise PluginPackageLoadError("plugin execution policy cannot add tools")
                allowed.intersection_update(policy.allowed_tool_names)
            self.subagents_allowed = self.subagents_allowed and policy.subagents_allowed
            required_sources.update(policy.required_sources)
        self.allowed_tool_names = frozenset(allowed)
        return AgentExecutionPolicy(
            selected, self.allowed_tool_names, self.subagents_allowed, tuple(sorted(required_sources))
        )

    def filter_tools(self, tools):
        """Filter existing Tool objects without constructing remote capabilities."""

        if self.allowed_tool_names is None:
            return list(tools)
        return [tool for tool in tools if tool.name in self.allowed_tool_names]

    def wrap_executor(self, binding, definition, executor):
        """Compose wrappers around the host's frozen-binding executor."""

        for extension in reversed(self.extensions):
            safe_binding = {
                key: binding[key]
                for key in ("plugin_key", "plugin_version", "connection_uuid", "protocol_version")
                if key in binding
            }
            executor = extension.wrap_executor(copy.deepcopy(safe_binding), copy.deepcopy(definition), executor)
            if not callable(executor):
                raise PluginPackageLoadError("plugin executor wrapper is invalid")
        return executor

    def evidence(self):
        """Combine protocol evidence without interpreting any domain payload."""

        combined = {}
        for extension in self.extensions:
            evidence = extension.evidence()
            if not isinstance(evidence, dict):
                raise PluginPackageLoadError("plugin evidence is invalid")
            if set(evidence) - {"plugin_evidence", "evidence_completeness"}:
                raise PluginPackageLoadError("plugin evidence envelope is invalid")
            for key, value in evidence.items():
                if key == "evidence_completeness" and combined.get(key) == "incomplete":
                    continue
                if key == "plugin_evidence":
                    if not isinstance(value, dict):
                        raise PluginPackageLoadError("plugin evidence envelope is invalid")
                    payloads = combined.setdefault(key, {})
                    for identity, payload in value.items():
                        if identity in payloads and payloads[identity] != payload:
                            raise PluginPackageLoadError("plugin evidence conflict")
                        payloads[identity] = payload
                elif value not in {"complete", "incomplete", "unverified"}:
                    raise PluginPackageLoadError("plugin evidence completeness is invalid")
                else:
                    combined[key] = value
        return combined


def prepare_agent_extensions(command, workspace, skills=(), tools=(), emit_event=None):
    """Prepare extensions only from the assistant's model-visible bindings."""

    bound_tools = []
    factories = {}
    for binding in command.get("loaded_plugins") or []:
        definitions = [
            definition
            for definition in binding.get("tools") or []
            if isinstance(definition, dict) and definition.get("exposure", "model") == "model"
        ]
        if not definitions:
            continue
        contract = load_runtime_contract(binding.get("plugin_key"), binding.get("plugin_version"))
        for definition in definitions:
            bound_tools.append(
                BoundPluginTool(
                    plugin_key=contract.plugin_key,
                    plugin_version=contract.plugin_version,
                    connection_uuid=str(binding.get("connection_uuid") or ""),
                    key=definition["key"],
                    metadata=copy.deepcopy((contract.tool_metadata or {}).get(definition["key"], {})),
                    plugin_display_name=str(binding.get("plugin_display_name") or ""),
                )
            )
        if contract.agent_extensions is None:
            continue
        specs = contract.agent_extensions()
        if not isinstance(specs, (list, tuple)) or len(specs) > 16:
            raise PluginPackageLoadError("plugin agent extension declarations are invalid")
        for spec in specs:
            if not isinstance(spec, (list, tuple)) or len(spec) != 2:
                raise PluginPackageLoadError("plugin agent extension declaration is invalid")
            key, factory = spec
            if not isinstance(key, str) or not EXTENSION_KEY_PATTERN.fullmatch(key) or not callable(factory):
                raise PluginPackageLoadError("plugin agent extension declaration is invalid")
            if key in factories and factories[key] is not factory:
                raise PluginPackageLoadError("plugin agent extension identity conflict")
            factories[key] = factory
    safe_command = copy.deepcopy(
        {
            key: command[key]
            for key in ("question", "history", "task", "routing_mode", "run_uuid", "runtime_route")
            if key in command
        }
    )
    if not factories:
        return AgentExtensions()
    state_root = plugin_state_root(workspace, command.get("run_uuid"))
    tool_names = tuple([*(getattr(tool, "name", "") for tool in tools), *(tool.key for tool in bound_tools)])
    extensions = []
    for key, factory in factories.items():
        if (state_root / key).is_symlink():
            raise PluginPackageLoadError("plugin state path is invalid")
        context = AgentExtensionContext(
            copy.deepcopy(safe_command),
            tuple(copy.deepcopy(bound_tools)),
            Path(workspace),
            state_root / key,
            tuple(skills),
            tool_names,
            emit_event,
        )
        extension = factory(context)
        if extension is not None:
            extensions.append(extension)
    return AgentExtensions(extensions, tool_names)
