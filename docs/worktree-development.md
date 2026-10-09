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

## Run multiple worktrees with devctl

Keep the manager (`devctl`, its Python module, `docker-compose.dev.yml` and routing
configuration) in the primary Git checkout. This directory owns the shared
`.env.dev`. `devctl` automatically finds that checkout from Git's worktree list,
even when invoked from a linked worktree. "Primary" means the original checkout,
not whichever worktree currently has the `main` branch checked out. Python 3,
Git, Docker and Compose V2 are required (macOS/Linux).
The Python helper uses only the standard library.

`devctl` selects services from `docker-compose.dev.yml` explicitly. Its
infrastructure project starts only `postgresql` and `redis`; application
projects start API/frontend first and wait for health, then start the other
application services. `--no-deps` prevents Compose from creating a second
database or Redis server inside an application project. Standalone defaults
still support `docker compose -f docker-compose.dev.yml up -d` directly.

```bash
cp env.sample .env.dev
# Configure local development credentials and optional AI model settings.
# Create this file only in the primary checkout; linked worktrees do not need it.

# Each up registers the worktree, allocates an unused loopback HTTP port,
# starts the shared infrastructure, creates its database, builds private
# backend/LensNode images, and waits for application health.
./devctl up feature-a /absolute/path/to/worktree-a
./devctl up feature-b /absolute/path/to/worktree-b --port 18082

./devctl list
./devctl status
./devctl logs feature-a

./devctl test feature-a
./devctl test feature-a -- python manage.py test lens.tests.test_api --noinput

# Stop application containers, keeping the registration and all data.
./devctl down feature-a

# Resume; --no-build reuses this environment's existing private image tags.
./devctl up feature-a /absolute/path/to/worktree-a --no-build

# Remove application containers, private network and registration. Data stays.
./devctl clean feature-a
```

`infra-up` starts the shared servers without registering an application. There
is deliberately no infrastructure `down`, database deletion or volume deletion
command. The legacy `sourcelens-dev` stack and production projects are separate
from these managed projects and remain untouched.

From a linked worktree, use its `devctl` entry point after this tooling is present
there, or invoke the primary checkout's entry point. Both automatically use the
primary checkout's configuration; there is no need to copy `.env.dev` or set
`DEVCTL_ROOT` each time:

```bash
cd /absolute/path/to/worktree-c
./devctl up feature-c
./devctl list
./devctl test feature-c
```

For an explicitly chosen manager directory, override the automatic discovery:

```bash
DEVCTL_ROOT=/absolute/path/to/manager-checkout \
  /absolute/path/to/manager-checkout/devctl up feature-c /absolute/path/to/worktree-c
```

`DEVCTL_ROOT` optionally replaces the primary checkout as the manager directory.
`DEVCTL_CONFIG` optionally selects a shared config file outside the manager
checkout. `DEVCTL_STATE` selects the state parent (default:
`${XDG_STATE_HOME:-~/.local/state}/sourcelens-dev`). A repository hash derived
from Git's common directory separates repositories within that parent.
Docker projects also include the state parent in their identity, so an
independent acceptance registry does not reuse daily development resources. Once
registered, the configuration home cannot silently change. Do not move/delete
the manager or discard its registry while environments still exist.

## Isolation and lifecycle guarantees

| Resource | Scope |
|---|---|
| PostgreSQL/Redis processes | One infrastructure project per repository |
| Business and Django test databases | One database per environment; Django derives its own test DB |
| Redis broker, cache and Channels | Three distinct logical Redis DBs per environment |
| Application containers and image tags | One Compose project per environment; no pinned container names |
| Application DNS | Private network per environment; only the shared servers also join it |
| HTTP port | Loopback only; allocated from 18081–19080, or explicitly selected |
| Uploads, workspaces, logs, checkpoints, static files | Environment directory outside the repository |
| Frontend dependencies and npm download cache | Environment-specific named volumes |
| Credentials and routing configuration | Manager directory; no secrets copied to the registry |

Redis DB isolation covers hard-coded task queues, raw Redis clients, cache
clears and Channels keys without changing application code or relying only on
key prefixes. A maximum of 85 identities can reserve namespaces. Cleanup keeps
Redis allocations reserved, so a new identity cannot accidentally consume
another environment's retained messages. Re-registering the same name/path
resumes the same database, namespace, runtime files and dependency volumes.
Changing names creates a new identity, even for the same worktree.

A repository-level file lock serializes lifecycle mutations, port allocation
and registry writes between agents. JSON writes are atomic and private. The
registry records source path, creation/last-use time, last observed branch,
lifecycle status and latest test exit code/log path. `status` queries Docker
for live state; `list` summarizes recorded state and the current branch. An
allocated port is checked against both the registry and local listeners;
another process can still claim it before Docker binds it, in which case
startup fails and prints logs.

Alongside the JSON registry, the manager writes private, generated `.env`
files containing only environment-specific database names, Redis endpoints
and local runtime settings. These override the shared configuration without
copying credentials or requiring that it be shell-compatible. Database URL
credentials are read and encoded inside the container by the entrypoint.

`devctl test` reads the running API container's actual mount and refuses to
run if it differs from the registered worktree. Running a named environment
from another checkout prints a notice. Output is streamed and saved to a
private log; a failed suite returns its failure status. The default suite is
Django's `manage.py test --noinput`; pass a different backend command after
`--` when needed. Test tools must exist in the environment's image. Use distinct
environment names for concurrent suites; operations on one repository are
serialized while a lifecycle command or suite holds the registry lock.

API and Vite reload edited source. Restart workers/scheduler/LensNode by
running `devctl up NAME PATH --no-build`, which recreates that environment's
containers. For Python dependency or image changes, omit `--no-build` to
rebuild from the selected worktree. Vite installs dependencies from that
worktree's lockfile on startup into its own volume. Each API startup applies
migrations only to its environment's database.

`down` and `clean` never call `down -v`, flush Redis, drop a database or remove
runtime files. Cleanup still works after an application worktree is deleted,
provided the manager/config remain available. Retained databases, namespace
allocations, data directories and dependency volumes require separate manual
maintenance after all owners have been identified. Do not run `down -v` on
the shared infrastructure as part of normal worktree cleanup.

## Verification

```bash
python3 -m unittest scripts.test_devctl
bash scripts/test_test_dev.sh
```

These regressions cover resource/port isolation, two-process allocation,
configuration ownership, cleanup retention, private network attachments,
source mismatch rejection, detached branches, missing mounts, argument
forwarding and suite failure propagation. For a runtime acceptance check,
start two environments, check both `/health` URLs, run each test entry point,
then stop/clean one and confirm the other remains healthy.
