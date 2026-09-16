# SourceLens Q&A distribution

SourceLens Q&A is delivered to external coding agents as a shared Skill plus
a read-only MCP contract. The client package now lives in the sibling
`../sourcelens-client` repository; this document remains the server integration
contract.

## Boundary

The client package exposes only `sourcelens_ask` and `sourcelens_search`.
Workspace, assistant, connection, datasource, user, and plugin management are
not part of this interface. The MCP gateway enforces user identity,
workspace/assistant bindings, connection scopes, quotas, and audit logging on
every request.

## Host integration

Codex and Claude use the same `SKILL.md` and MCP descriptor, while their host
configuration files remain client-specific. The installer copies the package
to the conventional local Skill directory; the host setup registers the HTTPS
endpoint from `SOURCELENS_MCP_URL`.

The gateway must support browser/device authentication and issue a short-lived
session for the MCP connection. Personal access tokens are an optional local
development fallback and must never be embedded in the package.
