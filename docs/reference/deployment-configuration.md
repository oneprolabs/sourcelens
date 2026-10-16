# Deployment configuration

Copy `env.sample` to `.env` for production or `.env.dev` for development.
The sample contains deployment inputs; runtime tuning is optional. Existing
variables in your environment file remain supported unless listed as removed.

## Configuration ownership

- Application code owns runtime defaults. Set overrides in the service's
  `env_file` (`.env`, `.env.dev`, or the configuration home selected by devctl).
  Shell-only tuning exports no longer override these file values; put persistent
  tuning in the environment file. Compose interpolation still controls published
  ports, images, identities, and the deployment's concurrency default.
  This cleanup does not rewrite existing environment files automatically.
- Compose owns service addresses, mounted paths, and deployment-specific defaults.
  Root Compose stacks derive all LensNode endpoints from one base address and
  clear old per-endpoint addresses: blue/green uses `http://nginx:80`; development
  and single-instance production use `http://backend-api:8000`.
- The backend and worker receive the matching AI Gateway address for datasource
  conversion; removing the endpoint variables from `.env` does not disable it.
- A separately deployed worker (`lensnode/docker-compose.yml`) only needs
  `LENSNODE_SERVER_URL` plus its name/token. Legacy explicit endpoint overrides
  are still supported there and when running the Python worker directly.
- Root Compose stacks default to eight concurrent runs. A Python worker started
  directly defaults to one. Development retains its 600-second request timeout;
  production defaults to 240 seconds. Initial planning defaults to `medium` in
  every stack; an explicit existing override remains effective.
- LensNode's default drain timeout is 240 seconds and production Docker grace is
  270 seconds. If you increase the drain timeout, also increase
  `stop_grace_period` in a Compose override so Docker does not interrupt draining.
- PostgreSQL database/credentials remain explicit in the template. Existing
  database names are not migrated by this cleanup. Frontend `VITE_*` options
  still require the normal frontend build or supported runtime injection.
- New installations use Celery's `backend` default queue, matching the worker's
  subscribed queues (`backend,lens`). The old sample's `sourcelens` queue is no
  longer emitted; existing custom queues require matching worker subscriptions.

## Redis

`REDIS_URL` is the shared broker address. `CELERY_BROKER_URL` can still override
it for a separate broker. Cache defaults to database 1 on that Redis host
(`CACHE_REDIS_URL` takes precedence; legacy `CELERY_BROKER_URL` remains a
fallback). Authentication, TLS scheme, and URL query options are preserved.
If the separate broker uses RabbitMQ, set `CACHE_REDIS_URL` explicitly.
`CHANNEL_LAYER_REDIS_URL` remains explicit in the template, keeping channels
on database 2 and avoiding a change to existing channel routing.

## Removed ineffective option

`LENSNODE_TOKEN_BUDGET_HARD_MAX_TOKENS` no longer participates in budget
resolution and has been removed. Existing environment entries are harmless and
ignored. The Assistant's per-run budget is authoritative, including unlimited
profiles; `LENSNODE_TOKEN_BUDGET_MAX_TOKENS` and the final reserve apply when
no run budget is supplied.

## Optional LensNode overrides

Uncomment only the settings you need in your environment file. Values below
are examples; the dev request timeout and direct-worker concurrency defaults
are described above.

```dotenv
# Execute shell commands in the LensNode container. This does not add a
# sandbox; use "filesystem" only to disable the general execute capability.
# LENSNODE_EXECUTION_BACKEND=filesystem
# Compose sets the deployment-specific base URL (nginx for blue/green,
# backend-api for standalone). WebSocket, AI Gateway, upload, and delegation
# endpoints are derived from it. The explicit endpoint variables remain
# supported for legacy deployments that need a non-standard route.
# Optional outbound proxy used only by the development LensNode container.
# Put these values in the root .env file used for Compose interpolation.
# Docker Desktop exposes a proxy on the host through host.docker.internal.
# LENSNODE_HTTP_PROXY=http://host.docker.internal:7890
# LENSNODE_HTTPS_PROXY=http://host.docker.internal:7890
# LENSNODE_ALL_PROXY=http://host.docker.internal:7890
# LENSNODE_NO_PROXY=localhost,127.0.0.1,::1,backend-api,nginx,postgresql,redis
# Keep certificate and hostname verification enabled by default. For a private
# CA, mount its certificate into LensNode and set the container path below.
# LENSNODE_TLS_SKIP_VERIFY=false
# LENSNODE_TLS_CA_FILE=/etc/sourcelens/ca.crt
# LENSNODE_HEARTBEAT_INTERVAL_S=15
# HTTP transport timeout for AI Gateway calls, deliverable uploads, and Skill
# downloads. This does not limit the complete Agent Run.
# LENSNODE_REQUEST_TIMEOUT_S=240
# Run watchdog: abort when NO gateway activity (heartbeats, tokens,
# progress) arrives for this long. The gateway heartbeats every ~10s, so
# this only trips on a dead pipe, never on a quiet-but-alive model call.
# LENSNODE_RUN_IDLE_TIMEOUT_S=180
# Number of checkpoint stream recovery retries and exponential backoff.
# LENSNODE_STREAM_RECOVERY_ATTEMPTS=3
# LENSNODE_STREAM_RECOVERY_BACKOFF_S=1
# LENSNODE_STREAM_RECOVERY_BACKOFF_MAX_S=8
# Graceful-drain budget on shutdown/upgrade: a node receiving SIGTERM stops
# taking new runs and lets in-flight runs finish for up to this long before
# exiting (only runs still active past it are cancelled). The compose
# lensnode service's stop_grace_period must stay larger than this.
# LENSNODE_DRAIN_TIMEOUT_S=240
# Max concurrent runs a single lensnode executes. Each run holds a slot for
# the full answer duration; raise for more parallelism (avoids long Queued).
# LENSNODE_MAX_CONCURRENT_RUNS=8
# LENSNODE_MAX_CONCURRENT_DATASOURCE_SYNCS=1
# Git datasource working-tree size ceiling in bytes (default 1 GiB). A repo
# over this fails with LENS_SOURCE_RESOURCE_LIMIT_EXCEEDED; raise it for large
# repositories.
# LENSNODE_GIT_MAX_BYTES=1073741824
# Optional LensNode cgroup budget used to derive a V8 heap ceiling for
# CodeGraph. LENSNODE_NODE_MAX_OLD_SPACE_MB or LENSNODE_NODE_OPTIONS can
# override the derived value.
# LENSNODE_MEMORY_LIMIT_MB=2048
# LENSNODE_NODE_MAX_OLD_SPACE_MB=1024
# LENSNODE_NODE_OPTIONS=--max-old-space-size=1024
# Fallback budget for commands without a control-plane budget profile.
# LENSNODE_TOKEN_BUDGET_MAX_TOKENS=200000
# The warning ratio asks the model to converge; the fallback reserve forces a
# tool-free synthesis before the ceiling.
# LENSNODE_TOKEN_BUDGET_FINAL_RESERVE_TOKENS=40000
# LENSNODE_TOKEN_BUDGET_WARN_RATIO=0.8
# Maximum actual Tool calls per Run; 0 disables this safety limit.
# LENSNODE_TOOL_BUDGET_MAX_CALLS=64
# Initial plan generation is coordination work; restore the Assistant's normal
# reasoning effort immediately after write_todos succeeds.
# LENSNODE_PLANNING_REASONING_EFFORT=medium
# URL MCP connection/discovery and per-call limits.
# LENSNODE_MCP_DISCOVERY_TIMEOUT_S=30
# LENSNODE_MCP_TOOL_TIMEOUT_S=60
# Defer MCP schemas behind tool_search when the loaded tool count exceeds this.
# LENSNODE_MCP_DEFER_THRESHOLD=12
# Comma-separated allowlist of stdio MCP commands that may be spawned.
# Empty means no stdio servers run (fail closed).
# LENSNODE_MCP_STDIO_ALLOWLIST=codegraph
# Built-in CodeGraph plugin: index the workspace and serve it over stdio MCP.
# LENSNODE_MCP_ENABLE_CODEGRAPH=true
# LENSNODE_CODEGRAPH_COMMAND=codegraph
# LENSNODE_CODEGRAPH_INIT_TIMEOUT_S=300
# Re-run the planner with a repair prompt after an invalid retrieval plan
# instead of falling back to bounded keyword search immediately. The repair
# costs one extra model call per invalid plan; the fallback is fast.
# LENSNODE_PLANNER_REPAIR_ENABLED=false
```
