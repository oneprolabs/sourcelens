# SourceLens Q&A distribution

SourceLens Q&A is delivered to external coding agents as a shared Skill plus a
read-only REST contract. The client package lives in the sibling
`../sourcelens-agent-kit` repository; this document remains the server
integration contract.

## Boundary

The client exposes only an ask operation. Workspace, assistant, connection,
datasource, user, and plugin management are not part of this interface;
assistant listing is exposed read-only so the client can choose one. The
platform enforces user identity, workspace/assistant bindings, connection
scopes, quotas, and audit logging on every request.

There is no dedicated Q&A gateway. The client drives the ordinary session and
run endpoints:

```
POST /api/lens/sessions/                 { assistant_uuid }
POST /api/lens/sessions/<uuid>/runs/     { question, request_source }
GET  /api/lens/runs/<uuid>/              -> status, answer, citations
GET  /api/lens/assistants/               -> assistant catalog
```

`Run.answer` is the completed answer text (empty until the run finishes), so a
single run poll yields status, answer, and citations.

## Assistant discovery and routing

Every run requires an `assistant_uuid`, so the client has to choose an
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
`datasource_bindings`, picks one assistant, then creates the run. Selection
stays client-side; the server only authorizes the assistant it is handed.

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

Codex and Claude use the same `SKILL.md`, while their host configuration files
remain client-specific. The installer copies the package to the conventional
local Skill directory and adds the `sourcelens` CLI to `PATH`; there is no MCP
server to register. The CLI reads the service base URL from
`SOURCELENS_BASE_URL` and talks to the REST endpoints above.

A future hosted flow could support browser/device authentication and a
short-lived session. See Authentication for what ships today.

## Authentication

The service reuses the platform JWT authentication. Coding agents cannot run
the interactive refresh flow, so the user mints a long-lived access token once
from the web app (User settings -> Agent Integration) through
`POST /api/v1/auth/agent/token`. The request body may carry `lifetime_months`
(one of 1, 3, 6); omitting it falls back to `AGENT_TOKEN_LIFETIME_DAYS` days
(default 30, non-positive disables the endpoint). The token carries a
`scope=agent` claim and is sent as `Authorization: Bearer <token>`.

The token resolves to the owning user, so assistant access, quotas, and
per-user run isolation are unchanged. It is shown only once and must never be
embedded in the package.

### Scope enforcement

`scope=agent` is enforced by
`accounts.authentication.AgentRestrictedJWTAuthentication`, which runs on every
DRF request. A token carrying the claim may reach only the routes in
`AGENT_TOKEN_ALLOWED_ROUTES`:

```
POST /api/lens/sessions/                    POST /api/lens/sessions/<uuid>/runs/
GET  /api/lens/runs/<uuid>/                 GET  /api/lens/assistants/[<uuid>/]
```

Everything else is refused with `403 AGENT_TOKEN_SCOPE_RESTRICTED`, including
reads of admin endpoints and any write. Tokens without the claim (web sessions,
ordinary access tokens) are unaffected. Update the setting when the client
needs another read-only route.

SimpleJWT blacklisting is not enabled, so an issued token cannot be revoked
individually before it expires. A dedicated, revocable API key is the planned
follow-up, and device/browser authorization issuing a short-lived session
remains the target for the hosted flow.
