"""Validation primitives for the read-only SourceLens Q&A MCP gateway."""

from dataclasses import dataclass


READ_ONLY_TOOLS = frozenset({"sourcelens_ask", "sourcelens_search"})
MAX_QUERY_LENGTH = 8_000
MAX_RESULTS = 50
REQUEST_CHANNELS = frozenset({"mcp", "web", "mobile", "api"})
REQUEST_CLIENTS = frozenset({
    "codex", "claude", "sourcelens-web", "sourcelens-ios",
    "sourcelens-android", "unknown",
})


def validate_request_source(value):
    """Return bounded client metadata for auditing, never authorization."""

    if not isinstance(value, dict):
        return {"channel": "api", "client": "unknown"}
    channel = value.get("channel", "api")
    client = value.get("client", "unknown")
    output = {
        "channel": channel if channel in REQUEST_CHANNELS else "api",
        "client": client if client in REQUEST_CLIENTS else "unknown",
    }
    for key in ("client_version", "skill", "tool", "request_id"):
        text = value.get(key, "")
        if isinstance(text, str) and text:
            output[key] = text[:128]
    return output


class QAMCPRequestError(ValueError):
    """Raised when an external Q&A tool request is invalid."""


@dataclass(frozen=True)
class QAMCPRequest:
    """Validated, authorization-neutral Q&A request."""

    tool: str
    query: str
    workspace: str = ""
    max_results: int = 10


def validate_request(tool, arguments):
    """Validate the public read-only Q&A tool contract."""

    if tool not in READ_ONLY_TOOLS:
        raise QAMCPRequestError("MCP_TOOL_UNSUPPORTED")
    if not isinstance(arguments, dict):
        raise QAMCPRequestError("MCP_ARGUMENTS_INVALID")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise QAMCPRequestError("MCP_QUERY_REQUIRED")
    query = query.strip()
    if len(query) > MAX_QUERY_LENGTH:
        raise QAMCPRequestError("MCP_QUERY_TOO_LONG")
    workspace = arguments.get("workspace", "")
    if not isinstance(workspace, str) or len(workspace) > 200:
        raise QAMCPRequestError("MCP_WORKSPACE_INVALID")
    max_results = arguments.get("max_results", 10)
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise QAMCPRequestError("MCP_MAX_RESULTS_INVALID")
    if not 1 <= max_results <= MAX_RESULTS:
        raise QAMCPRequestError("MCP_MAX_RESULTS_INVALID")
    return QAMCPRequest(
        tool=tool,
        query=query,
        workspace=workspace.strip(),
        max_results=max_results,
    )
