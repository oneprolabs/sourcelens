# Worktree development with Docker

SourceLens supports two local workflows. Use `WORKTREE_DIR` to retarget the
existing single dev stack, or `devctl` to run several worktrees at once.
Both test entry points report the actual source mount before running tests.
Both workflows use the existing `docker-compose.dev.yml`; no additional
Compose files or duplicate service definitions are needed.

## Retarget the existing stack

Run Compose from the directory that owns `.env.dev`, `data.dev`, and the
installed `frontend/node_modules`:

```bash
WORKTREE_DIR=/absolute/path/to/another-worktree \
  docker compose -f docker-compose.dev.yml up -d --force-recreate

# Run from the worktree you intend to test; test labels/options pass through.
./scripts/test-dev.sh lens.tests.test_api --noinput
```

Unset `WORKTREE_DIR` (or set it to `.`) to use the Compose directory's source.
Relative values are resolved against the Compose directory, not the shell's
current directory. Switching needs `--force-recreate`; `docker restart` keeps
the original mounts. The script reads `/opt/backend` from `docker inspect`,
prints its host path and branch, and warns if it differs from the caller's
worktree. An inspection failure or missing bind mount stops the test run.

Backend, plugins, LensNode and frontend source follow the variable. Database
data, runtime files, `.env.dev`, certificates, nginx configuration, build
contexts, frontend configuration/manifests and `node_modules` stay with the
Compose directory. A source switch does not update installed dependencies.
Rebuild/install there when dependencies change. All worktrees in this mode
share one database: switching back to code without an applied migration can
leave the schema ahead of that code.

## Switch the existing single stack with devctl

```bash
./devctl switch                        # Mount source from the current worktree.
./devctl switch /absolute/path/to/tree  # Mount source from another local worktree.
```

`switch` requires one existing single development Compose stack and running
PostgreSQL/Redis containers. It uses the primary checkout's `.env.dev`, runtime
data, routing configuration and installed dependencies. It validates container
names, images, ports and data mounts before recreating application containers
with `--no-deps --no-build`. It waits for API/frontend before recreating workers,
LensNode, Flower and nginx, then verifies the actual API source mount and reports
service states. Shared infrastructure is not restarted or recreated.

Unlike `up`, this command does not start an independent application environment.
There is a brief interruption while application containers are recreated. Build
or install dependencies in the configuration checkout separately when needed.
The API startup applies migrations to the existing shared database: target code
must be compatible with its migration history, including when switching back.
A startup failure returns nonzero; the command does not automatically roll back
source or database migrations. More than one single dev stack is rejected rather
than choosing an arbitrary stack.

## Worktree discovery and lifecycle with devctl

`devctl list` scans `git worktree list` and inspects Docker Compose containers.
Every local worktree is visible before startup. A running or stopped development
stack is associated with its API container's actual `/opt/backend` bind mount,
including stacks launched directly with `docker compose up` and retargeted with
`WORKTREE_DIR`. Production Compose files and unrelated repositories are excluded.
`status` also displays individual container states and health checks. Docker must
be available for these live inspections; failures are reported explicitly.

Targets can be a worktree path, directory name, branch, Compose project, or an
existing display alias. Use an absolute path or project when a target is ambiguous.
There is no registration or unregistration step.

```bash
./devctl list
./devctl status
./devctl up                         # Start the current worktree.
./devctl up /absolute/path/to/worktree --port 18082
./devctl logs /absolute/path/to/worktree
./devctl test /absolute/path/to/worktree -- python manage.py test lens.tests.test_api --noinput
./devctl down /absolute/path/to/worktree
./devctl clean /absolute/path/to/worktree
./devctl up /absolute/path/to/worktree --no-build
```

The previous `up NAME PATH` syntax remains supported as a display alias. A name
change does not create a new data identity: identities derive from the canonical
worktree path. Existing saved identities are reused to preserve their data.

The primary Git checkout owns `docker-compose.dev.yml`, routing files and
`.env.dev`. Linked worktrees automatically use it without copying configuration.
Primary means the original checkout, regardless of its checked-out branch.
`DEVCTL_ROOT` and `DEVCTL_CONFIG` remain explicit manager/config overrides.
Python 3, Git, Docker and Compose V2 are required on macOS/Linux.

For a new stack, `up` allocates a loopback port, starts shared PostgreSQL/Redis,
creates a private database and network, builds private backend/LensNode images,
and waits for API/frontend before starting remaining services. All services reuse
`docker-compose.dev.yml`; `--no-deps` prevents duplicate infrastructure.
`infra-up` starts only the shared services. `--no-build` reuses existing image tags.

For a discovered stack launched directly with Compose, `up` restarts its existing
application containers and retains its original configuration, ports and shared
data. Rebuild such a stack through its original Compose invocation when changing
dependencies. `logs` prints recent application logs and follows the API log.
`test` uses the discovered API container directly, verifies its source mount,
and streams/saves the result. It does not require a devctl-created environment.
The default command is `python manage.py test --noinput`.

`down` stops application containers; `clean` removes them and, for isolated stacks,
the private network. Both retain databases, Redis data, files, dependency volumes
and launch/test metadata. Git worktrees remain visible after either operation.
Even in a legacy project containing database services, only application containers
are stopped/removed. Neither command executes `down -v` or deletes a Git worktree.

## Isolation and retained metadata

New isolated stacks share PostgreSQL/Redis processes but use separate databases,
three Redis logical DBs for broker/cache/Channels, private application networks,
image tags, runtime directories and frontend dependency volumes. HTTP ports bind
only to loopback, automatically allocated from 18081–19080 or explicitly chosen.
Retained Redis allocations are not reused by different worktree identities.
Directly launched legacy stacks retain their original data isolation behavior.

The state parent is `DEVCTL_STATE` or
`${XDG_STATE_HOME:-~/.local/state}/sourcelens-dev`, scoped by Git's common directory.
Docker resource identities also include the state parent, so temporary acceptance
environments do not collide with daily development. The existing `registry.json`
file is retained for compatibility, but serves only as launch allocation and test
metadata: it does not decide which worktrees exist or whether containers run.
Do not discard allocation metadata while retaining shared Redis/database data.

A file lock serializes allocation and lifecycle changes. Writes are atomic and
private. Credentials stay in the main configuration; generated private env files
contain only per-environment endpoints and settings. Test results contain their
actual source path, exit code and log path. API/Vite reload source edits; isolated
workers/scheduler/LensNode can be recreated with `up --no-build`.

## Verification

```bash
python3 -m unittest scripts.test_devctl
bash scripts/test_test_dev.sh
```

Coverage includes Git-only discovery, direct Compose discovery, retargeted source
mounts, production exclusion, path identity, concurrent allocation, data retention,
private networking, source mismatch rejection and test failure propagation.
