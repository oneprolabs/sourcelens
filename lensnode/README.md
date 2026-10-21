# LensNode

LensNode is the standalone execution worker for SourceLens. It connects to the
control plane over WebSocket, reports workspace directories and available tasks,
then executes dispatched runs inside its local workspace.

## Thinking modes

The assistant's `deep_thinking` default can be overridden with a boolean on each
Run creation request. Enabling it requests `reasoning_effort=high` for the main
agent; the provider determines how that setting is supported. Normal mode
explicitly requests `reasoning_effort=low`, including on nodes configured for
stronger reasoning. Summary and emergency wrap-up calls keep their own reasoning
settings.

Selecting a mode records a requested setting; it does not prove provider support
or certify answer quality. Run APIs and diagnostics expose the frozen
`deep_thinking` and `reasoning_effort_requested` fields. Legacy executions leave
these unknown rather than reconstructing them from current assistant settings.

Normal mode prioritizes speed and skips the independent answer-grounding model
review, including its in-loop correction and final review calls. Its review
assessment is `not_requested`. Deep thinking keeps the configured answer review.
If no reviewer is bound, its review is inconclusive with `review_unavailable`,
rather than verified.
Retrieval and routing decisions, convergence guards, tool execution checks, and
attachment validation still apply in both modes. Legacy snapshots without a
`deep_thinking` choice keep their original review behavior.

New runs use the same execution policy in both modes, without the former tier's
Token, model-turn, or default tool-call caps. The one-hour deadline, graph safety
limit, repeated-call detection, cancellation, and explicit administrator tool
budgets still apply. A run freezes its thinking choice; delegated and continued
work inherit it unless explicitly overridden. Legacy execution snapshots retain
their original limits when resumed. Existing assistants using `deep` or `max`
start with deep thinking enabled after the migration. Legacy clients explicitly
sending `agent_rounds` map deep/max to enabled and other tiers to disabled; an
explicit `deep_thinking` boolean takes precedence.

Administrators can configure `settings.execution_budget.max_tokens` and
`settings.execution_budget.max_agent_turns` independently of thinking mode. Both
accept nonnegative integers; zero preserves the absence of a configured cap.
Budgets freeze when a Run starts. A bounded token budget reserves 20% for
wrap-up; existing shared accounting and guards enforce it between model calls.
A single in-flight provider response can exceed the remaining allowance, so a
token budget is not a hard monetary ceiling. Existing
`settings.tool_budget.max_calls` continues to bound actual tool calls.

## Command execution

The default `LENSNODE_EXECUTION_BACKEND=trusted_container` enables the
DeepAgents `execute` tool. Commands run directly inside the LensNode container,
with the container's environment and the current Run workspace as the working
directory.
There is no command blacklist and this mode is not an additional sandbox; do
not expose the container to untrusted users. Set the backend to `filesystem`
to roll back to file operations only while a sandbox provider is prepared.

## Development

```bash
docker compose up --build
```

The standalone compose file only starts the LensNode service. Set
`LENSNODE_SERVER_URL` to the existing SourceLens backend; LensNode derives the
control WebSocket, AI Gateway, deliverable upload, and delegation endpoints.
The individual endpoint variables remain available for non-standard routes.
In the root blue/green stack, use `http://nginx:80`; in the development and
standalone stacks, use `http://backend-api:8000`. See [env.sample](../env.sample)
for deployment inputs and [config.py](lensnode/config.py) for runtime defaults.

## TLS verification

LensNode verifies SourceLens HTTPS and WSS certificates with the system trust
store by default. This applies to the control channel, AI Gateway requests,
Skill package downloads, and deliverable uploads.

For a SourceLens deployment that uses a private CA, mount the CA certificate
into the LensNode container and configure its container path:

```yaml
services:
  lensnode:
    environment:
      LENSNODE_TLS_CA_FILE: /etc/sourcelens/ca.crt
    volumes:
      - ./ca.crt:/etc/sourcelens/ca.crt:ro
```

For local development with a self-signed certificate, verification can be
disabled explicitly:

```env
LENSNODE_TLS_SKIP_VERIFY=true
```

This disables both certificate and hostname verification and must not be used
in production. `LENSNODE_TLS_SKIP_VERIFY` defaults to `false`. When it is
`true`, it takes precedence over `LENSNODE_TLS_CA_FILE` and LensNode logs a
warning at startup.
