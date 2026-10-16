"""In-process LensNode capability plugins.

Plugins contribute optional capabilities (MCP servers today, skills or
tools later) behind a stable internal boundary. Built-in adapters ship
inside this package. These in-process capability adapters are separate
from the installed integration packages documented in
docs/reference/plugin-protocol.md.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRuntimeContribution:
    """Optional agent-runtime capabilities contributed by one plugin."""

    prompt_guidance: str = ""
    middleware: tuple = ()
    subagent_middleware: tuple = ()
    always_visible_tool_prefixes: tuple = ()


class LensNodePlugin:
    """Base class for an in-process capability plugin.

    A plugin reports whether it may run for a given config and
    contributes MCP server configs; future capability types add their
    own contribute_* methods with default no-op implementations.
    """

    name = "base"

    def enabled(self, config):
        """Return whether this plugin may run for the given config."""

        return True

    def contribute_mcp_servers(
        self,
        config,
        emit_event=None,
        command=None,
    ):
        """Return MCP server configs this plugin contributes."""

        del command
        return []

    def contribute_agent_runtime(self, config, command, mcp_tools):
        """Return optional runtime behavior after MCP discovery."""

        return None


def _plugins():
    """Return the built-in plugin registry."""

    from .codegraph import CodeGraphPlugin

    return (CodeGraphPlugin(),)


def collect_mcp_servers(
    config,
    mcp_configs,
    emit_event=None,
    command=None,
):
    """Merge plugin-contributed MCP servers, deduplicating by name.

    Configured servers always win over plugin contributions sharing the
    same name.
    """

    plugins = _plugins()
    servers = list(mcp_configs)
    for plugin in plugins:
        if not plugin.enabled(config):
            continue
        for server in plugin.contribute_mcp_servers(
            config,
            emit_event=emit_event,
            command=command,
        ):
            name = str(server.get("name") or "").lower()
            if any(
                str(item.get("name") or "").lower() == name
                for item in servers
            ):
                continue
            servers.append(server)
    return servers


def collect_agent_runtime_contributions(config, command, mcp_tools):
    """Collect runtime behavior without exposing plugin identities upstream."""

    contributions = []
    for plugin in _plugins():
        if not plugin.enabled(config):
            continue
        contribution = plugin.contribute_agent_runtime(
            config,
            command,
            mcp_tools,
        )
        if contribution is not None:
            contributions.append(contribution)
    return contributions
