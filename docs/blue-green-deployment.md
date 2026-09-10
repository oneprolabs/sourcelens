# Single-node Blue/Green Deployment — Reusable Playbook

A portable specification for zero-downtime deployment on a **single host with no
orchestrator** (no k8s / Swarm). It captures the architecture, the per-project
adaptation points, and — most importantly — a **correctness checklist** distilled
from bugs found in real ports of this pattern, so a new project can adopt it
without re-discovering them.

sourcelens is the reference implementation. Paths below in `code font` point at
sourcelens files; the `<app>` / `<service>` placeholders are what you rename per
project (see [Adaptation](#per-project-adaptation)).

---

## 1. When to use this

Use it when **all** of these hold:

- One production host, no orchestrator (a plain Docker + Docker Compose box).
- You need zero dropped requests during an upgrade, plus a fast rollback.
- Your user-facing services are stateless (state lives in Postgres/Redis/volumes).

Do **not** reach for it when you have k8s/Swarm (use their rolling updates), or
when a brief blip on deploy is acceptable — then the simpler *standalone* topology
below is enough.

### Scope: single host only — NOT multi-node

This pattern is **single-node by construction** and does **not** extend to a
multi-host fleet. Everything that makes the switch work is local to one machine:

- `.active_color` / `.rollback_version` / `upstream.conf` are **local files** on
  one host — there is no shared or coordinated "current color" across machines.
- `install.sh` runs on **one** host and drives that host's `docker compose`.
- nginx runs on the **same** host and proxies to colors by container name over
  the **local** Docker network; the switch is `docker exec <that-nginx> nginx -s
  reload`. There is no cross-host load balancer.

Multi-node zero-downtime is a **different architecture**, not an extension of
this one: it needs a fronting cross-host load balancer (or DNS/anycast), the
active color promoted to **shared/coordinated cluster state**, and something to
**orchestrate the switch across the whole fleet** (including that hosts briefly
serve mixed colors/versions during the roll — the same expand/contract
constraint, now fleet-wide). At that point you are rebuilding a slice of an
orchestrator — use k8s/Swarm/a service mesh instead. Running this playbook
independently per host behind an external LB is possible but is *N independent
single-node deploys*, not a coordinated fleet switch; don't mistake it for one.

---

## 2. Topology model — three compose files

| File | Role | Project name | Brought up by | Images | Zero-downtime |
|---|---|---|---|---|---|
| `docker-compose.dev.yml` | development | `<app>-dev` | `docker compose -f … up -d` | built from source, hot-reload mounts | no |
| `docker-compose.standalone.yml` | simple prod, single instance | `<app>` | `docker compose -f … up -d` | pulled release images, no source mounts | no |
| `docker-compose.yml` | zero-downtime prod | `<app>` | **`scripts/install.sh`** (blue/green) | pulled release images | **yes** |

> ### IRON RULE — dev and prod must use distinct compose project names
>
> **Every compose file MUST set an explicit top-level `name:`.** Dev gets its own
> project (`<app>-dev`); the production shapes share `<app>`. Without an explicit
> `name:` every file defaults to the **directory name** and they share one
> project namespace — and because they also share service keys (`postgresql`,
> `redis`, …), bringing up a production stack in that directory **recreates the
> dev containers** (and vice versa), silently taking down the other environment.
> This is not hypothetical: it happened during this project's own testing.
>
> Dev must be isolated from prod. Of the two **production** shapes, run only one
> per host — they intentionally share `<app>` (and the singleton
> postgres/redis data). `COMPOSE_PROJECT_NAME` / `-p` still override the file's
> `name:`, which is how you run an isolated *test* copy of a prod stack
> (`COMPOSE_PROJECT_NAME=<app>-verify`) without disturbing anything.

The blue/green file **cannot** be brought up with a bare `docker compose up -d`
(API/UI are `profiles`-gated, and nginx needs bootstrapped runtime state) — that
is by design; `install.sh` is its only entrypoint.

---

## 3. Architecture (blue/green file)

- **Two colors per stateless service.** `<app>-api-blue` / `<app>-api-green`,
  `<app>-ui-blue` / `<app>-ui-green`, defined once via a YAML anchor and split by
  compose `profiles: ["blue"|"green"]`. Only one color is ever in nginx's traffic
  path. (`docker-compose.yml`)
- **Stateful services stay singleton** — `postgresql`, `redis`, and any volume-
  backed service. Never blue/green'd.
- **Background workers roll normally.** Celery workers / schedulers / WS workers
  are recreated with a plain `up -d` on deploy (rely on graceful shutdown —
  `stop_grace_period`, late ack, `prefetch=1`), not blue/green'd.
- **nginx routing state** lives in a bind-mounted **directory**
  (`docker/nginx/conf.d/`), not a single file. `default.conf` proxies to a
  variable whose value is a `map` in `upstream.conf`; the deploy flips that map.
  (See [Correctness §5.2](#52-nginx).)
- **Runtime state files** (gitignored, bootstrapped once, never committed):
  `.active_color`, `.rollback_version`, `docker/nginx/conf.d/upstream.conf`, and
  the self-signed TLS cert.
- **Two scripts, shared lib.**
  - `scripts/install.sh` — install **and** upgrade (idempotent, one command).
  - `scripts/<app>ctl.sh` — day-2 ops: `status` / `restart-workers` /
    `restart` / `recreate` / `rollback`.
  - `scripts/lib/deploy-common.sh` — `current_color` / `other_color` /
    `wait_for_healthy` / `switch_traffic` shared by both.

### The deploy sequence (`install.sh`)

1. Fetch the small set of declarative assets from the target git ref (compose,
   nginx/postgres config, the scripts themselves — self-updating). `--local`
   skips this and builds from the working tree.
2. Bootstrap runtime state **only if missing** (never clobber).
3. Determine current color from `.active_color`; if its API container isn't
   running, this is a first install → deploy to that color directly (nothing to
   switch/retire). Otherwise deploy to the **other** color.
4. Pull/build images for the deploy color; migrate (or let the color's own
   startup migrate) — no traffic yet.
5. Bring up the deploy color, **health-gate** it (poll `/health`, bounded
   retries). Abort cleanly if it never turns healthy — current color stays live.
6. Roll queue and WS consumers (`up -d`) while the old API producer is still
   active. Consumers must accept both the outgoing and incoming producer's
   message shapes and capabilities during this step.
7. **Switch** nginx to the new color (`nginx -t` then `nginx -s reload`, no
   dropped connections). Write `.active_color` + `.rollback_version`
   **immediately**. Observe a fixed window, then retire the old color. Prune old
   image tags. Single-flight lock throughout.

---

## 4. Per-project adaptation

Rename these; everything else is mechanical:

| Thing | sourcelens value | Where |
|---|---|---|
| Service/container names | `sourcelens-api-{blue,green}`, `sourcelens-ui-…`, `sourcelens-{worker,scheduler,nginx,postgres,redis}` | compose, scripts, nginx |
| Image repos | `oneprolabs/sourcelens-backend`, `…-frontend` | compose, `install.sh` |
| Git repo (asset fetch) | `HyperBDR/sourcelens` | `install.sh` `REPO=` |
| Health endpoint | `GET /health` on `:8000` | `deploy-common.sh` `wait_for_healthy`, compose healthcheck |
| Version LABEL | `com.oneprolabs.sourcelens.version` | Dockerfiles + `deploy-common.sh` |
| nginx upstream var names | `$sourcelens_api_active` / `$sourcelens_ui_active` | `default.conf` + `upstream.conf` |
| WS worker (if any) | `lensnode` (reaches API via nginx) | compose env, backend grace-period |

---

## 5. Correctness checklist (MUST)

These are not style preferences — each is a bug that shipped in a real port and
was caught only in review. Treat them as acceptance criteria.

### 5.1 Deploy scripts

- [ ] **Every compose file sets an explicit distinct `name:`** (IRON RULE, §2):
      dev under `<app>-dev`, prod under `<app>`. Never rely on the directory-name
      default — a prod `up` in the repo dir otherwise recreates the dev
      containers (shared project namespace + `postgresql`/`redis` service keys).
- [ ] **Atomic single-flight lock.** Acquire with `set -o noclobber`
      (`while ! (set -o noclobber; echo $$ > "$LOCK"); do …`), never a
      `[ -f "$LOCK" ] && echo > "$LOCK"` check-then-write — that is a TOCTOU race
      two deploys can both pass. Keep a stale-lock takeover after a max wait.
- [ ] **Rollback pins the old image version.** After a switch, the retired
      color's container is removed, so `docker compose up -d <color>` recreates it
      from `image: …:${VERSION:-latest}` = **latest** = the *current* (bad)
      release. Record the outgoing color's version to `.rollback_version` right
      before retiring it, and have `rollback` export `APP_VERSION` from that file.
      Without this, "rollback" silently redeploys the broken version.
- [ ] **Write `.active_color` immediately after the switch**, before the
      observe/retire window — not at the end. An interrupt mid-window otherwise
      leaves nginx on the new color while `.active_color` names the old one,
      desyncing the next rollback.
- [ ] **Version-skip is `>=`, not `>`.** `sort -V | tail -1 != TARGET` never
      no-ops an *equal* re-deploy (max equals target). Add an explicit equality
      clause if you want an idempotent re-run to skip.
- [ ] **Guard `set -euo pipefail` pipelines that may legitimately not match.**
      e.g. the tag-prune `docker images … | grep -vE '^latest$' | …` aborts the
      whole script when a repo has only `latest` (grep exits 1). Append `|| true`.
- [ ] **CI: the image tag and the asset-fetch ref must not diverge.** If
      `install.sh` derives the image tag from the ref it fetches (`${ref#v}`),
      then a build tagged with a *short* SHA but fetched from the *full* SHA pulls
      a nonexistent tag. Use one consistent value.
- [ ] **First cutover cleans up the old single-container stack.** Migrating a
      host from a pre-blue/green single-container deploy: the old single `api`/`ui`
      containers are NOT part of the blue/green compose, so `up` leaves them
      running as orphans (holding DB connections / memory) — `--remove-orphans`
      won't catch them across different compose files either. Add a one-time
      `docker rm -f <app>-api <app>-ui || true` after the first install. The
      first cutover's only downtime is the nginx **container recreate** (single-
      file → directory mount), which is sub-second when the new color is
      health-gated up *before* nginx is recreated; steady-state deploys are a
      reload (zero-downtime).
      *(sourcelens `install.sh` does NOT do this cleanup yet — copy it from the
      devify implementation, which does `docker rm -f devify-api devify-ui`.)*
- [ ] **Roll queue consumers before switching to a new producer.** A release
      that changes a Celery task signature or a WS capability must first put
      consumers in place that accept both the outgoing and incoming API's
      messages. Switching API traffic first can enqueue new payloads to old
      workers or leave the new API unaware of required worker capabilities.

### 5.2 nginx

- [ ] **Variable `proxy_pass` + `resolver`, not a static `upstream` block.**
      A static `upstream { server <app>-api-blue:8000; }` is resolved once and
      cached forever: nginx **crash-loops on a host reboot** if the active color
      isn't up yet (`host not found in upstream`), and serves **stale-IP 502s**
      after a container is recreated without a reload. Instead put a `map` in
      `upstream.conf` and `proxy_pass http://$<app>_api_active;` with
      `resolver 127.0.0.11 valid=10s;` in `default.conf`. nginx then starts even
      when the color is down and self-heals within `valid` after a recreate.
      *(This is the one place sourcelens improves on the pattern it ported.)*
- [ ] **Mount `conf.d/` as a whole directory**, never the single `upstream.conf`
      file. The switch does a `sed -i` (rename → new inode); a single-file bind
      mount pins the original inode and nginx keeps serving the pre-switch color.

### 5.3 Migrations (expand/contract)

- [ ] During the observe window the **outgoing** color still serves traffic
      against the **post-migration** schema. A migration that drops/renames a
      column or tightens a constraint in the same release as the code that stops
      using it breaks the outgoing color mid-window. **Split such changes across
      two releases** (expand, then contract).

### 5.4 WebSocket worker survival (if you have one)

A worker connected over WebSocket drops its socket when the API color is
recycled. That is **not** proof its in-flight work failed.

- [ ] **Server: grace period before failing runs.** On disconnect, don't fail the
      node's runs immediately — stamp `disconnected_at` and schedule a **Celery
      countdown** task (not an in-process timer: the API process is what gets
      recycled). Fail the runs only if the node is still gone after the window.
- [ ] **Episode-pin the grace check with a tolerance, not exact timestamp
      strings.** Comparing `disconnected_at.isoformat()` to the scheduled string
      breaks on any DB that truncates sub-second precision (the check then always
      no-ops). Compare parsed datetimes within ~1s.
- [ ] **Handle the disconnect CAS miss.** If a health sweep already marked the
      node offline and cleared its `connection_id`, the disconnect's
      `connection_id`-scoped update matches 0 rows — still schedule the check (on
      the ownerless row) so a genuinely-dead node's runs are failed, not left
      hanging until the idle reaper.
- [ ] **Wrap the scheduling `apply_async` in try/except.** It's a broker call in
      the disconnect path; a broker hiccup must not raise out of `disconnect()`.
      The periodic idle reaper is the backstop.
- [ ] **Client: durable, bounded outbound buffer.** Frames a run emits *while
      disconnected* must survive to the next connection (buffer across reconnects,
      don't drop). But the buffer **must be bounded** — a prolonged outage with a
      producing run OOM-kills the worker otherwise; drop oldest past a cap with a
      warning.
- [ ] **Client: send `hello` directly on the socket, not through the durable
      buffer.** A buffered hello gets replayed on the next connection carrying a
      stale `active_runs` snapshot, triggering a spurious server-side reconcile.
      Send it from the connect path before the send loop starts.
- [ ] **Client: pop-before-send, re-queue-at-front on failure.** Pop a frame out
      of the buffer before awaiting the send (so a concurrent drop-oldest can't
      race the in-flight frame) and re-queue it at the front on a mid-send error.
      Accept that this is **at-least-once** (a frame whose bytes reached the
      server before the error is re-sent) — make it harmless by having the run's
      terminal/`final_content` frame reconcile the accumulated output.

---

## 6. Verification protocol

Prove it, don't assume it. On an isolated compose project
(`COMPOSE_PROJECT_NAME=<app>-verify`, so you never disturb a dev stack in the
same dir):

1. **First install:** `./scripts/install.sh --local` → blue built, health-gated,
   nginx serving it; `curl` `/health` = 200.
2. **Zero-downtime switch:** run a tight `/health` load loop, then
   `./scripts/install.sh --local` again (blue→green). **Expect zero non-200s**
   across the whole run (build, health-gate, `nginx -s reload`, observe, retire).
   sourcelens measured 2115/2115 = 200 end-to-end via `install.sh`, and 800/800
   across a bare `switch_traffic` reload.
3. **Resolver self-heal:** recreate the active color's container with no nginx
   reload → `/health` recovers within `valid=10s`.
4. **Day-2:** `<app>ctl.sh status` shows the active color healthy; `rollback`
   flips to the other color **without a rebuild** and lands on the *old* version;
   `restart` and `recreate` operate only on runtime services (`workers`,
   `scheduler`, `lensnode`, or `runtime`). They never directly restart or
   recreate blue/green API/UI, nginx, PostgreSQL, or Redis.
5. **WS worker (if any):** start a long run, switch mid-run → the run stays
   RUNNING, the worker reconnects, buffered frames flush, the run completes; keep
   the worker down past the grace window → the run correctly fails.

---

## 7. Adopting it in a new project

1. Copy `docker-compose.yml` (blue/green), `docker-compose.standalone.yml`,
   `docker/nginx/conf.d/default.conf`, `docker/nginx/upstream.conf.default`,
   `docker/nginx/default.standalone.conf`, and `scripts/{install,<app>ctl}.sh` +
   `scripts/lib/deploy-common.sh`.
2. Apply the [Adaptation](#per-project-adaptation) renames.
3. Add the version `LABEL` (fed by an `APP_VERSION` build arg) as the **last**
   Dockerfile layer, so a version bump doesn't bust the apt/pip/npm cache.
4. Gitignore the runtime-state files; commit `upstream.conf.default` only.
5. Walk the [Correctness checklist](#5-correctness-checklist-must) line by line.
6. Run the [Verification protocol](#6-verification-protocol) before trusting it.
7. If you have a WebSocket worker, implement §5.4 — it's the one genuinely new
   piece, not a copy.
