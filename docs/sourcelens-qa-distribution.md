# SourceLens Q&A distribution

SourceLens Q&A is delivered to external coding agents as a shared Skill plus
a read-only MCP contract. The client package now lives in the sibling
`../sourcelens-client` repository; this document remains the server integration
contract.

## Boundary

The client package exposes only `sourcelens_ask` and `sourcelens_search`.
Workspace, assistant, connection, datasource, user, and plugin management are
not part of this interface; assistant listing is exposed read-only so the
client can choose one. The MCP gateway enforces user identity,
workspace/assistant bindings, connection scopes, quotas, and audit logging on
every request.

## Assistant discovery and routing

Both MCP tools require an `assistant_uuid`, so the client has to choose an
assistant before it can ask anything. Two read-only endpoints support that
choice:

- `GET /api/lens/assistants/` — the compact catalog. Each row carries `uuid`,
  `slug`, `name`, `description`, `routing_description`, `capability`, `mode`,
  and `datasource_bindings`.
- `GET /api/lens/assistants/<uuid>/` — the same data plus the full bindings.

`routing_description` is the localized, non-sensitive synopsis built by
`lens/routing_descriptions.py`. It states the capability, the requests the
assistant suits, its enabled Skills and MCPs, and whether its workspace scope
is limited to configured directories; it carries no credentials and no
workspace paths. Localize it with `Accept-Language` (`en`, `zh`, `es`).

The local agent reads every candidate's `routing_description` together with
`datasource_bindings`, picks one assistant, then calls the MCP tool. Selection
stays client-side; the gateway only authorizes the assistant it is handed.

## Citations

`code_analysis` runs cite through the planned-evidence pipeline: the planner
collects evidence, the model selects evidence IDs, and `validate_citations`
maps them to trusted workspace-relative paths.

Knowledge Q&A tool loops never select evidence IDs, so they report the
workspace sources the run actually inspected. `ConsultedSources` records
each `search_workspace` match and `read_workspace_file` window, reduces it to a
public `<mount name>/<relative path>`, and emits at most five citations. The
answer text is unchanged: the knowledge scenario still forbids forcing source
markers into the reply, and citations travel as run metadata.

Paths outside the mounted data sources are dropped, so internal runtime
locations and host absolute paths never reach the client. `run.citations` is
validated by `lens/citations.py` on write and rendered without captured source
text by `public_run_citations`.

Two layers own the citation path:

- `lensnode/consulted_sources.py` produces the reader-facing prefix. A mount
  name the operator chose is kept. A generated `ds_<uuid>` mount (including the
  `_<item hex>` form for item-scoped bindings) is replaced by the datasource
  name, indexed (`<name>-1`, `<name>-2`) when one datasource is mounted more
  than once so the prefixes stay distinct.
- `lens/citations.py` guarantees that a generated `ds_*` segment never reaches
  a reader. It is a safety net for runs where the datasource name is missing,
  not the normal path — the fallback drops the segment, so that path carries no
  source attribution.

Citations describe *consultation*, never support: the `supports` label reads
"Read while answering" / "回答过程中查阅", so an answer that reports the
workspace lacks the information cannot read as if a searched file backed it.
Detecting such answers from their text was tried and dropped — a partial "not
found" about one term suppressed the citations of a fully grounded reply.

## Host integration

Codex and Claude use the same `SKILL.md` and MCP descriptor, while their host
configuration files remain client-specific. The installer copies the package
to the conventional local Skill directory; the host setup registers the HTTPS
endpoint from `SOURCELENS_MCP_URL`.

The gateway must eventually support browser/device authentication and issue a
short-lived session for the MCP connection. See Authentication for what ships
today.

## Authentication

The gateway reuses the platform JWT authentication. Coding agents cannot run
the interactive refresh flow, so the user mints a long-lived access token once
from the web app (User settings -> MCP Clients) through
`POST /api/v1/auth/mcp/token`. The token carries a `scope=mcp` claim and lives
for `MCP_TOKEN_LIFETIME_DAYS` days (default 30, non-positive disables the
endpoint); the client sends it as `Authorization: Bearer <token>`.

The token resolves to the owning user, so assistant access, quotas, and
per-user run isolation are unchanged. It is shown only once and must never be
embedded in the package.

### Scope enforcement

`scope=mcp` is enforced by `accounts.authentication.MCPRestrictedJWTAuthentication`,
which runs on every DRF request. A token carrying the claim may reach only the
routes in `MCP_TOKEN_ALLOWED_ROUTES`:

```
POST /api/lens/mcp/            POST /api/lens/mcp/qa/
GET  /api/lens/mcp/qa/<uuid>/  GET  /api/lens/assistants/[<uuid>/]
```

Everything else is refused with `403 MCP_TOKEN_SCOPE_RESTRICTED`, including
reads of admin endpoints and any write. Tokens without the claim (web sessions,
ordinary access tokens) are unaffected. Update the setting when the client
needs another read-only route.

The MCP transport (`/api/lens/mcp/`) answers with a bare JSON-RPC envelope, not
the platform's `code`/`message`/`data` wrapper: MCP clients reject the wrapper
during the `initialize` handshake. `SourceLensQAMCPRPCView` therefore sets
`renderer_classes = [JSONRenderer]`. `/api/lens/mcp/qa/<uuid>/` stays on the
platform envelope — it is polled by the client CLI, not by an MCP client.

SimpleJWT blacklisting is not enabled, so an issued token cannot be revoked
individually before it expires. A dedicated, revocable API key is the planned
follow-up, and device/browser authorization issuing a short-lived session
remains the target for the hosted flow.
