# Worktree development with Docker

SourceLens runs one local development stack. `devctl up` starts it with source
from the current worktree, or switches that same stack when it already exists.
All worktrees use the original PostgreSQL database, Redis, runtime data and ports.
No worktree registration, private database, port allocation or image tag is needed.
The existing `docker-compose.dev.yml` is the only Compose template.

## Commands

```bash
./devctl up                 # Start/switch the single stack to this worktree.
./devctl up main            # Use the primary Git checkout.
./devctl up /path/to/tree
./devctl up --build         # Explicitly rebuild images; default is no build.
./devctl switch             # Explicit switch; requires an existing stack.
./devctl switch main
./devctl list               # All local worktrees and actual service ownership.
./devctl status             # Current worktree and running service states.
./devctl list -v            # Also show branches and full paths.
./devctl status -v
./devctl logs               # Recent app logs, then follow the API log.
./devctl test main -- python manage.py test accounts.tests --noinput
./devctl down               # Stop application services; keep PostgreSQL/Redis.
./devctl clean              # Remove application containers; retain all data.
./devctl up                 # Resume after down or clean.
./devctl infra-up           # Start only the original PostgreSQL/Redis services.
```

`test` requires a worktree target and checks the actual API source before running.
Its default suite is `python manage.py test --noinput`. Explicit targets for
`logs`, `down` and `clean` remain supported. Worktree targets can be paths,
directory names or branches; `main` always means the original Git checkout,
regardless of its checked-out branch. Ambiguous targets must use an absolute path.

`list` scans Git worktrees and joins Docker's actual API bind mount. The current
shell directory is marked `*`; only the worktree whose code is mounted owns the
single stack. `status` distinguishes the current shell worktree from the services'
source worktree. It reports service state and health; `-v` adds full details.
Production stacks and unrelated repositories are excluded.

## Shared configuration and data

The primary checkout owns `.env.dev`, nginx routing/certificates, runtime data
and installed frontend dependencies. Linked worktrees automatically find it.
`DEVCTL_ROOT` and `DEVCTL_CONFIG` are explicit configuration-home overrides.
The Compose template and entrypoint come from the invoked tool's checkout, so an
older primary Compose file cannot ignore source-switching settings.

`up` and `switch` retain existing images and published ports. Ordinary source
changes are loaded through mounts without rebuilding. `--build` rebuilds backend,
LensNode and frontend images; backend/LensNode build contexts use the selected
worktree, while frontend manifests/dependencies remain in the configuration home.
Missing local images produce a build instruction instead of an implicit build.
The old `--no-build` flag is accepted for compatibility.

Before changing services, the tool validates source, container names, images,
ports and retained data/config mounts. It stops all old application processes,
then starts API/frontend with health gates followed by worker, scheduler,
LensNode, Flower and nginx. Healthy PostgreSQL/Redis are not restarted or recreated.
First startup or stopped infrastructure is started using the original project.
Application recreation briefly interrupts requests. A startup failure returns
nonzero; there is no automatic source or database rollback.

All branches share migration history. API startup applies migrations; target
code must remain compatible with the existing database. Containers switch source,
not installed Python/frontend dependencies. Use `--build` when dependencies change.
API/Vite reload ordinary source edits; run `up` to recreate workers/LensNode.

`down` stops only application containers. `clean` removes only application
containers, leaving database/Redis containers, volumes, files and Git worktrees.
Neither command calls `down -v`, deletes worktrees, drops databases or clears Redis.
The stack remains discoverable through its infrastructure after `clean`.

## Transition from older parallel environments

Before `up`/`switch` activates the single stack, old devctl parallel application
containers are stopped and removed. Their dedicated PostgreSQL/Redis instances
are stopped. Their databases, Redis volumes, files, networks, images and metadata
are retained for separate manual maintenance; no data is copied or deleted.
The original single-stack database remains the source of existing business data.
Multiple independently launched single stacks are rejected rather than choosing
an arbitrary database. Other projects and production containers are untouched.

The state parent is `DEVCTL_STATE` or
`${XDG_STATE_HOME:-~/.local/state}/sourcelens-dev`. The compatibility `registry.json`
now retains test results, active-source information and non-secret image/port
settings needed after cleanup. Old allocation records are preserved for recovery
but no longer drive worktree existence or startup. A lock in Git's common directory
serializes lifecycle operations across all linked worktrees and state parents.

## Direct Compose and verification

The equivalent direct source switch is:

```bash
WORKTREE_DIR=/absolute/path/to/tree \
  docker compose -f docker-compose.dev.yml up -d --force-recreate
./scripts/test-dev.sh accounts.tests --noinput
```

Run from the checkout owning configuration/data. `WORKTREE_DIR` only changes
source mounts; relative paths resolve against that checkout. Restart alone does
not change mounts. The test helper reports the actual API source mount.

```bash
python3 -m unittest scripts.test_devctl
bash scripts/test_test_dev.sh
```
