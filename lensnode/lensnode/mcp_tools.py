import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import StructuredTool

from .gateway_model import neutralize_untrusted_text

LOGGER = logging.getLogger("lensnode")

MCP_TOOL_PREFIX = "mcp__"
MCP_TOOL_NAME_MAX_CHARS = 64
MCP_SEARCH_LIMIT = 5
MCP_MAX_SERVERS = 20
MCP_MAX_TOOLS = 100
MCP_STDIO_MAX_ARGS = 32
MCP_STDIO_MAX_ENV = 32
MCP_RESULT_MAX_CHARS = 8000


class MCPToolFirstMiddleware(AgentMiddleware):
    """Expose one MCP tool family on the first model turn."""

    def __init__(self, tool_prefix):
        self._tool_prefix = str(tool_prefix or "")
        self._completed = False
        self._lock = threading.Lock()

    def _filter_tools(self, tools):
        """Keep only the configured tool family until its first call."""

        with self._lock:
            if self._completed:
                return list(tools)
        return [
            tool
            for tool in tools
            if str(getattr(tool, "name", "") or "").startswith(
                self._tool_prefix
            )
        ]

    def _tool_name(self, request):
        """Return the invoked tool name from a middleware request."""

        tool_call = getattr(request, "tool_call", None) or {}
        return str(
            tool_call.get("name")
            or getattr(getattr(request, "tool", None), "name", None)
            or ""
        )

    def _mark_completed(self):
        """Restore the normal tool set after the first tool call."""

        with self._lock:
            self._completed = True

    def wrap_model_call(self, request, handler):
        """Filter synchronous model requests."""

        request = request.override(tools=self._filter_tools(request.tools))
        return handler(request)

    async def awrap_model_call(self, request, handler):
        """Filter asynchronous model requests."""

        request = request.override(tools=self._filter_tools(request.tools))
        return await handler(request)

    def wrap_tool_call(self, request, handler):
        """Restore tools after a synchronous first-family call."""

        try:
            return handler(request)
        finally:
            if self._tool_name(request).startswith(self._tool_prefix):
                self._mark_completed()

    async def awrap_tool_call(self, request, handler):
        """Restore tools after an asynchronous first-family call."""

        try:
            return await handler(request)
        finally:
            if self._tool_name(request).startswith(self._tool_prefix):
                self._mark_completed()


class DeferredMCPToolMiddleware(AgentMiddleware):
    """Hide MCP schemas until tool_search promotes matching tools."""

    def __init__(self, mcp_tools, always_visible_prefixes=()):
        self._mcp_tools = {tool.name: tool for tool in mcp_tools}
        self._always_visible_prefixes = tuple(always_visible_prefixes)
        self._promoted = set()
        self._lock = threading.Lock()
        self.search_tool = StructuredTool.from_function(
            func=self._search,
            name="tool_search",
            description=(
                "Search available remote MCP tools by capability. Matching "
                "tools become available on the next model turn."
            ),
        )

    def _search(self, query: str):
        """Promote remote tools whose names or descriptions match a query."""

        terms = [term for term in re.split(r"\W+", query.lower()) if term]
        matches = []
        for tool in self._mcp_tools.values():
            haystack = f"{tool.name} {tool.description}".lower()
            score = sum(term in haystack for term in terms)
            if score:
                matches.append((score, tool.name, tool))
        matches.sort(key=lambda item: (-item[0], item[1]))
        selected = [item[2] for item in matches[:MCP_SEARCH_LIMIT]]
        with self._lock:
            self._promoted.update(tool.name for tool in selected)
        return json.dumps(
            {
                "ok": True,
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                    }
                    for tool in selected
                ],
            },
            ensure_ascii=False,
        )

    def _filter_tools(self, tools):
        """Return base tools, tool_search, and promoted MCP tools."""

        with self._lock:
            promoted = set(self._promoted)
        return [
            tool
            for tool in tools
            if _has_prefix(tool, self._always_visible_prefixes)
            or not _is_mcp_tool(tool)
            or tool.name in promoted
        ]

    def wrap_model_call(self, request, handler):
        """Filter synchronous model requests."""

        request = request.override(tools=self._filter_tools(request.tools))
        return handler(request)

    async def awrap_model_call(self, request, handler):
        """Filter asynchronous model requests."""

        request = request.override(tools=self._filter_tools(request.tools))
        return await handler(request)


def load_mcp_tools(
    server_configs,
    *,
    discovery_timeout_s=30,
    tool_timeout_s=60,
    emit_event=None,
    stdio_allowlist=None,
):
    """Discover safe URL and allowlisted stdio MCP tools.

    stdio servers only run a plain executable name found on PATH whose
    basename is in stdio_allowlist; a None/empty allowlist blocks all
    stdio servers (fail closed).
    """

    if not server_configs:
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        _emit(
            emit_event,
            "mcp.runtime.unavailable",
            {"reason": "langchain_mcp_adapters_missing"},
        )
        return []

    stdio_allowlist = set(tuple(stdio_allowlist or ()))
    server_configs = list(server_configs)
    if len(server_configs) > MCP_MAX_SERVERS:
        _emit(
            emit_event,
            "mcp.runtime.limited",
            {
                "reason": "server_limit",
                "configured": len(server_configs),
                "loaded": MCP_MAX_SERVERS,
            },
        )

    prepared = []
    used_server_names = set()
    for config in server_configs[:MCP_MAX_SERVERS]:
        display_name = str(config.get("name") or "MCP server")
        transport = str(config.get("transport") or "").lower()
        stdio_params = None
        if transport == "stdio":
            if not stdio_allowlist:
                _emit(
                    emit_event,
                    "mcp.server.skipped",
                    {
                        "server": display_name,
                        "reason": "stdio_disabled",
                    },
                )
                continue
            try:
                stdio_params = _stdio_server_params(config)
            except ValueError as exc:
                LOGGER.warning(
                    "Skipping MCP server %s with invalid config (%s)",
                    display_name,
                    exc,
                )
                _emit(
                    emit_event,
                    "mcp.server.skipped",
                    {
                        "server": display_name,
                        "reason": "invalid_config",
                    },
                )
                continue
            if Path(stdio_params["command"]).name not in stdio_allowlist:
                _emit(
                    emit_event,
                    "mcp.server.skipped",
                    {
                        "server": display_name,
                        "reason": "stdio_not_allowed",
                    },
                )
                continue
        elif transport != "url":
            _emit(
                emit_event,
                "mcp.server.skipped",
                {
                    "server": display_name,
                    "reason": "unsupported_transport",
                },
            )
            continue

        server_name = _unique_identifier(display_name, used_server_names)
        try:
            params = (
                stdio_params
                if stdio_params is not None
                else _url_server_params(config)
            )
            client = MultiServerMCPClient(
                {server_name: params},
                tool_name_prefix=True,
            )
        except Exception as exc:
            LOGGER.warning(
                "Skipping MCP server %s after discovery failed (%s)",
                display_name,
                type(exc).__name__,
            )
            _emit(
                emit_event,
                "mcp.server.failed",
                {
                    "server": display_name,
                    "reason": "discovery_failed",
                },
            )
            continue
        prepared.append(
            (config, display_name, server_name, client, stdio_params)
        )

    if not prepared:
        return []
    discovery_started_at = time.monotonic()
    discovery_timeout = max(float(discovery_timeout_s), 0.001)
    _emit(
        emit_event,
        "mcp.discovery.start",
        {
            "server_count": len(prepared),
            "timeout_s": discovery_timeout,
        },
    )
    discovered_servers = asyncio.run(
        _discover_mcp_servers(prepared, discovery_timeout_s)
    )
    _emit(
        emit_event,
        "mcp.discovery.done",
        {
            "server_count": len(prepared),
            "failed_count": sum(
                error is not None
                for _discovered, error in discovered_servers
            ),
            "duration_ms": int(
                (time.monotonic() - discovery_started_at) * 1000
            ),
        },
    )
    tools = []
    used_tool_names = set()
    for prepared_server, discovery in zip(
        prepared,
        discovered_servers,
        strict=True,
    ):
        config, display_name, server_name, _client, stdio_params = (
            prepared_server
        )
        try:
            discovered, error = discovery
            if error is not None:
                LOGGER.warning(
                    "Skipping MCP server %s after discovery failed (%s)",
                    display_name,
                    type(error).__name__,
                )
                _emit(
                    emit_event,
                    "mcp.server.failed",
                    {
                        "server": display_name,
                        "reason": "discovery_failed",
                    },
                )
                continue

            server_tool_timeout = _positive_float(
                (config.get("load_config") or {}).get("tool_timeout_s"),
                tool_timeout_s,
            )
            terminate_fn = None
            if stdio_params is not None:
                terminate_fn = (
                    lambda params=stdio_params: _terminate_stdio_servers(
                        params
                    )
                )
            loaded_tool_names = []
            for raw_tool in discovered:
                if len(tools) >= MCP_MAX_TOOLS:
                    _emit(
                        emit_event,
                        "mcp.runtime.limited",
                        {
                            "reason": "tool_limit",
                            "loaded": MCP_MAX_TOOLS,
                        },
                    )
                    return tools
                tool = _wrap_mcp_tool(
                    raw_tool,
                    server_name,
                    display_name,
                    server_tool_timeout,
                    used_tool_names,
                    terminate_fn=terminate_fn,
                )
                tools.append(tool)
                loaded_tool_names.append(tool.name)
            _emit(
                emit_event,
                "mcp.server.ready",
                {
                    "server": display_name,
                    "tool_count": len(discovered),
                    "tool_names": loaded_tool_names,
                },
            )
        finally:
            if stdio_params is not None:
                _terminate_stdio_servers(stdio_params)
    return tools


async def _discover_mcp_servers(prepared, timeout_s):
    """Discover all validated MCP servers concurrently."""

    timeout = max(float(timeout_s), 0.001)

    async def discover(server_name, client):
        try:
            tools = await asyncio.wait_for(
                client.get_tools(server_name=server_name),
                timeout=timeout,
            )
            return tools, None
        except Exception as exc:
            return [], exc

    return await asyncio.gather(
        *(
            discover(server_name, client)
            for _config, _display_name, server_name, client, _params in (
                prepared
            )
        )
    )


def build_deferred_mcp_tools(
    mcp_tools,
    *,
    threshold=12,
    always_visible_prefixes=(),
):
    """Return registered MCP tools and optional schema-filter middleware."""

    if len(mcp_tools) <= max(int(threshold), 0):
        return list(mcp_tools), None
    middleware = DeferredMCPToolMiddleware(
        mcp_tools,
        always_visible_prefixes=always_visible_prefixes,
    )
    return [*mcp_tools, middleware.search_tool], middleware


def _url_server_params(config):
    """Build validated adapter parameters for one URL MCP server."""

    endpoint = str(config.get("endpoint") or "").strip()
    parsed = urlparse(endpoint)
    raw_config = config.get("config") or {}
    load_config = config.get("load_config") or {}
    allowed_schemes = {"https"}
    allow_insecure_http = os.getenv(
        "LENSNODE_ALLOW_INSECURE_HTTP_MCP", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if load_config.get("allow_insecure_http") is True or allow_insecure_http:
        allowed_schemes.add("http")
    if parsed.scheme not in allowed_schemes or not parsed.netloc:
        raise ValueError(
            "URL MCP endpoint must use https; insecure http requires "
            "allow_insecure_http"
        )
    adapter_transport = str(
        load_config.get("transport")
        or raw_config.get("transport")
        or "streamable_http"
    ).lower()
    if adapter_transport == "http":
        adapter_transport = "streamable_http"
    if adapter_transport not in {"sse", "streamable_http"}:
        raise ValueError("URL MCP transport must be sse or streamable_http")
    params = {
        "transport": adapter_transport,
        "url": endpoint,
    }
    headers = raw_config.get("headers")
    if headers is not None and not isinstance(headers, dict):
        raise ValueError("MCP headers must be an object")
    if headers is not None:
        params["headers"] = {
            str(key): str(value)
            for key, value in headers.items()
        }
    return params


def _stdio_server_params(config):
    """Build validated adapter parameters for one stdio MCP server.

    The command must be a plain executable name (no path separators) so
    resolution always goes through PATH inside the sandbox, never an
    arbitrary filesystem location.
    """

    raw_config = config.get("config") or {}
    if not isinstance(raw_config, dict):
        raise ValueError("stdio MCP config must be an object")
    command = str(raw_config.get("command") or "").strip()
    if not command or "/" in command or "\\" in command:
        raise ValueError(
            "stdio MCP command must be a plain executable name"
        )
    args = raw_config.get("args")
    args = [str(arg) for arg in args] if isinstance(args, list) else []
    if len(args) > MCP_STDIO_MAX_ARGS:
        raise ValueError("stdio MCP args exceed the configured limit")
    params = {
        "transport": "stdio",
        "command": command,
        "args": args,
    }
    cwd = raw_config.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        params["cwd"] = cwd.strip()
    env = raw_config.get("env")
    if isinstance(env, dict):
        if len(env) > MCP_STDIO_MAX_ENV:
            raise ValueError("stdio MCP env exceeds the configured limit")
        params["env"] = {
            str(key): str(value)
            for key, value in env.items()
        }
    return params


def _terminate_stdio_servers(params):
    """Reap orphaned subprocesses of one stdio MCP server.

    Servers such as CodeGraph detach a background process when their
    stdin closes, so the MCP client reports a clean session while an
    orphaned server keeps running. Match the exact command and args and
    terminate the process group.
    """

    needle = [
        str(item)
        for item in (
            params.get("command"),
            *(params.get("args") or ()),
        )
        if str(item).strip()
    ]
    if not needle:
        return
    for pid, argv in _process_table():
        if all(item in argv for item in needle):
            _terminate_process_group(pid)


def _process_table():
    """Yield (pid, argv) pairs for live processes on this host."""

    if sys.platform == "darwin":
        cmd = ["ps", "-ww", "-axo", "pid=,command="]
    else:
        cmd = ["ps", "-eo", "pid=,args="]
    try:
        output = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return
    for line in output.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            yield parts[0], parts[1]


def _terminate_process_group(pid_str):
    """Terminate a matched server process, never LensNode's own group.

    The process group is only terminated when the matched process leads
    it; otherwise the single process is signalled. Groups shared with
    the running LensNode process are always skipped to avoid killing the
    host agent itself.
    """

    pid = int(pid_str)
    if pid == os.getpid():
        return
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError, ValueError):
        return
    if pgid == os.getpgrp():
        return
    if pgid == pid:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                return
            except OSError:
                break
    else:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
            except OSError:
                break


def _wrap_mcp_tool(
    raw_tool,
    server_name,
    display_name,
    timeout_s,
    used_names,
    terminate_fn=None,
):
    """Wrap an async adapter tool for the synchronous Deep Agent runtime."""

    raw_name = str(getattr(raw_tool, "name", "") or "tool")
    adapter_prefix = f"{server_name}_"
    if raw_name.startswith(adapter_prefix):
        raw_name = raw_name[len(adapter_prefix) :]
    base_name = f"{MCP_TOOL_PREFIX}{server_name}__{_identifier(raw_name)}"
    name = _unique_tool_name(base_name, used_names)
    seen_queries = set()
    dedupe_queries = server_name == "codegraph"

    async def call_async(**kwargs):
        query_key = json.dumps(
            kwargs,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if dedupe_queries and query_key in seen_queries:
            return _result_json(
                True,
                cached=True,
                message="Duplicate MCP query; use the previous result.",
            )
        try:
            result = await asyncio.wait_for(
                raw_tool.ainvoke(kwargs),
                timeout=max(float(timeout_s), 0.001),
            )
            bounded, truncated = _bound_mcp_result(result)
            if dedupe_queries:
                seen_queries.add(query_key)
            return _result_json(
                True,
                result=bounded,
                **({"truncated": True} if truncated else {}),
            )
        except TimeoutError:
            return _result_json(
                False,
                error="MCP_TOOL_TIMEOUT",
                detail="The remote MCP tool exceeded its call timeout.",
            )
        except Exception as exc:
            LOGGER.warning(
                "MCP tool %s from %s failed (%s)",
                raw_name,
                display_name,
                type(exc).__name__,
            )
            return _result_json(
                False,
                error="MCP_TOOL_FAILED",
                detail="The remote MCP tool failed.",
            )
        finally:
            if terminate_fn is not None:
                terminate_fn()

    def call_sync(**kwargs):
        return asyncio.run(call_async(**kwargs))

    metadata = dict(getattr(raw_tool, "metadata", None) or {})
    metadata.update(
        {
            "mcp_server": display_name,
            "mcp_remote_content": True,
            "mcp_tool_name": raw_name,
        }
    )
    return StructuredTool(
        name=name,
        description=neutralize_untrusted_text(
            str(getattr(raw_tool, "description", "") or raw_name),
            neutralize_boundaries=True,
        )[:4000],
        args_schema=getattr(raw_tool, "args_schema", None),
        func=call_sync,
        coroutine=call_async,
        metadata=metadata,
    )


def _result_json(ok, **payload):
    """Return a JSON-safe structured MCP tool result."""

    return json.dumps(
        {"ok": ok, **payload},
        ensure_ascii=False,
        default=str,
    )


def _bound_mcp_result(value, limit=MCP_RESULT_MAX_CHARS):
    """Bound remote MCP content before it enters the agent context."""

    limit = max(int(limit), 0)
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= limit:
        return value, False
    if isinstance(value, str):
        return value[: max(limit - 2, 0)], True
    if isinstance(value, list):
        bounded = []
        for item in value:
            remaining = limit - _json_size(bounded) - 2
            if bounded:
                remaining -= 1
            if remaining <= 0:
                break
            candidate, _ = _bound_mcp_result(item, remaining)
            if _json_size(bounded + [candidate]) > limit:
                break
            bounded.append(candidate)
        return bounded, True
    if isinstance(value, dict):
        bounded = {}
        for key, item in value.items():
            key_size = len(json.dumps(str(key), ensure_ascii=False)) + 2
            remaining = limit - _json_size(bounded) - key_size
            if bounded:
                remaining -= 1
            if remaining <= 0:
                break
            candidate, _ = _bound_mcp_result(item, remaining)
            if _json_size({**bounded, key: candidate}) > limit:
                if not isinstance(item, str):
                    break
                empty_size = _json_size({**bounded, key: ""})
                candidate = item[: max(limit - empty_size - 2, 0)]
                if _json_size({**bounded, key: candidate}) > limit:
                    break
            bounded[key] = candidate
        return bounded, True
    bounded = str(value)[:limit]
    return bounded, True


def _json_size(value):
    """Return the serialized size used for MCP result budgeting."""

    return len(json.dumps(value, ensure_ascii=False, default=str))


def _unique_identifier(value, used):
    """Return a stable unique identifier within one MCP load."""

    base = _identifier(value)[:24] or "server"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base[:20]}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _identifier(value):
    """Canonicalize an untrusted MCP name for model tool binding."""

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_-")
    return normalized.lower() or "tool"


def _unique_tool_name(value, used):
    """Return a unique tool name within provider identifier limits."""

    base = value[:MCP_TOOL_NAME_MAX_CHARS]
    candidate = base
    suffix = 2
    while candidate in used:
        suffix_text = f"_{suffix}"
        candidate = base[: MCP_TOOL_NAME_MAX_CHARS - len(suffix_text)]
        candidate += suffix_text
        suffix += 1
    used.add(candidate)
    return candidate


def _positive_float(value, default):
    """Return a positive float or the supplied default."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return parsed if parsed > 0 else float(default)


def _is_mcp_tool(tool):
    """Return whether a tool came from the MCP loader."""

    return str(getattr(tool, "name", "")).startswith(MCP_TOOL_PREFIX)


def _has_prefix(tool, prefixes):
    """Return whether a tool name starts with one of the prefixes."""

    name = str(getattr(tool, "name", "") or "")
    return any(name.startswith(prefix) for prefix in prefixes)


def _emit(emit_event, event, detail):
    """Emit an MCP runtime event when a callback is configured."""

    if emit_event is not None:
        emit_event(event, detail)
