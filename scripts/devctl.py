"""Repository-scoped worktree discovery and Docker Compose lifecycle management."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def now():
    """Return a metadata timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def git(path, *args):
    """Read local Git metadata without interpreting command strings."""
    return subprocess.check_output(["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL).strip()


def common_dir(path):
    """Identify the repository shared by all of its worktrees."""
    return (Path(path) / git(path, "rev-parse", "--git-common-dir")).resolve()


def host_path(path):
    """Normalize Docker Desktop's VM mount prefix when its host path exists locally."""
    if sys.platform == "darwin" and str(path).startswith("/host_mnt/"):
        candidate = Path(str(path).removeprefix("/host_mnt"))
        if candidate.exists():
            return candidate.resolve()
    return Path(path).resolve()


def manager_paths(tool_root, environment=None):
    """Keep the manager and shared config in Git's primary checkout when switching worktrees."""
    environment = os.environ if environment is None else environment
    if environment.get("DEVCTL_ROOT"):
        root = Path(environment["DEVCTL_ROOT"]).expanduser().resolve()
    else:
        # Git lists the primary checkout first, regardless of its current branch.
        fields = git(tool_root, "worktree", "list", "--porcelain", "-z").split("\0")
        if "bare" in fields[: fields.index("")]:
            raise ValueError("Bare repository has no primary checkout; set DEVCTL_ROOT to a manager worktree")
        root = Path(fields[0].removeprefix("worktree ")).resolve()
    config = Path(environment.get("DEVCTL_CONFIG", root / ".env.dev")).expanduser().resolve()
    return root, config


def branch(path):
    """Describe the current branch or detached revision, including deleted worktrees."""
    try:
        return git(path, "symbolic-ref", "--short", "HEAD")
    except subprocess.CalledProcessError:
        try:
            return "detached at " + git(path, "rev-parse", "--short", "HEAD")
        except (subprocess.CalledProcessError, FileNotFoundError):
            return "unavailable"


def print_table(headers, rows, indent=""):
    """Align plain-text columns without terminal escapes or extra dependencies."""
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    for row in [headers, ["-" * width for width in widths], *rows]:
        print(indent + "  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip())


def display_environments(records, detailed=False, verbose=False):
    """Show a worktree overview or service details, keeping complete paths available."""
    try:
        current = str(Path(git(Path.cwd(), "rev-parse", "--show-toplevel")).resolve())
    except subprocess.CalledProcessError:
        current = None
    if detailed and not verbose:
        print("Development status")
        print(f"  Current worktree  {Path(current).name if current else '(outside repository)'}")
        environments = [r for r in records if r["containers"]]
        if not environments:
            print("\n  No development service containers found.")
        elif current and not any(r["path"] == current for r in environments):
            print("  Current services  not started")
        for record in environments:
            active = sum(c["State"]["Status"] == "running" for c in record["containers"])
            location = "current worktree" if record["path"] == current else "other worktree"
            print(f"\n  {record['name']}  ({location})")
            print(f"  {active}/{len(record['containers'])} services running")
            print()
            services = []
            for container in sorted(
                record["containers"], key=lambda c: c["Config"]["Labels"]["com.docker.compose.service"]
            ):
                state = container["State"]
                services.append(
                    [
                        container["Config"]["Labels"]["com.docker.compose.service"],
                        state["Status"],
                        state.get("Health", {}).get("Status", "-"),
                    ]
                )
            print_table(["SERVICE", "STATE", "HEALTH"], services, indent="    ")
        return
    running = sum(r["status"] == "running" for r in records)
    print(f"Worktrees: {len(records)}  |  Running: {running}  |  * current worktree")
    if not records:
        print("No local worktrees found.")
        return
    print()
    rows = []
    for index, record in enumerate(records, 1):
        containers = record["containers"]
        active = sum(c["State"]["Status"] == "running" for c in containers)
        marker = "*" if record["path"] == current else " "
        url = f"http://localhost:{record['port']}" if record.get("port", "-") != "-" else "-"
        rows.append([f"{index}{marker}", record["status"], f"{active}/{len(containers)}", url, record["name"]])
    print_table(["#", "STATE", "SERVICES", "URL", "WORKTREE"], rows)
    if not detailed and not verbose:
        return
    for index, record in enumerate(records, 1):
        print(f"\n[{index}] {record['name']}")
        if verbose:
            print(f"    Branch:  {branch(record['path'])}")
            print(f"    Path:    {record['path']}")
        if not detailed:
            continue
        print(f"    Project: {record.get('project', '-')}")
        if not record["containers"]:
            print("    No application containers.")
            continue
        services = []
        for container in sorted(
            record["containers"], key=lambda c: c["Config"]["Labels"]["com.docker.compose.service"]
        ):
            state = container["State"]
            service = container["Config"]["Labels"]["com.docker.compose.service"]
            health = state.get("Health", {}).get("Status", "-")
            ports = sorted(
                {
                    f"{'[' + p['HostIp'] + ']' if ':' in p['HostIp'] else p['HostIp']}:{p['HostPort']} -> {port}"
                    for port, values in (container.get("NetworkSettings", {}).get("Ports") or {}).items()
                    for p in (values or [])
                }
            )
            services.append([service, state["Status"], health, ", ".join(ports) or "-"])
        print()
        print_table(["SERVICE", "STATE", "HEALTH", "PUBLISHED PORTS"], services)


class DevManager:
    """Manage application projects while retaining shared infrastructure and data."""

    def __init__(self, root, state_home, config):
        """Scope Docker resources and registry locks to the repository's Git common directory."""
        self.root = Path(root).resolve()
        self.tool_root = Path(__file__).resolve().parents[1]
        self.config = Path(config).resolve()
        self.repository = common_dir(self.root)
        scope = hashlib.sha256(str(self.repository).encode()).hexdigest()[:12]
        state_home = Path(state_home).expanduser().resolve()
        # Separate state roots must not allocate overlapping namespaces on the same servers.
        docker_scope = hashlib.sha256(f"{self.repository}:{state_home}".encode()).hexdigest()[:12]
        self.prefix = "sourcelens-wt-" + docker_scope
        self.state_dir = state_home / scope
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.registry = self.state_dir / "registry.json"
        manager = self.read_state().get("manager")
        if manager and manager != {"root": str(self.root), "config": str(self.config)}:
            raise ValueError(
                f"Registry uses a different configuration home; set DEVCTL_ROOT={manager['root']} "
                f"and DEVCTL_CONFIG={manager['config']}"
            )

    @contextlib.contextmanager
    def lock(self):
        """Serialize allocation and lifecycle mutations across local agents/processes."""
        with (self.state_dir / "lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            manager = self.read_state()["manager"]
            if manager != {"root": str(self.root), "config": str(self.config)}:
                raise ValueError("Registry uses a different configuration home; use its recorded DEVCTL_ROOT/config")
            yield

    def read_state(self):
        """Read an atomic metadata snapshot; retired allocations remain reserved."""
        if not self.registry.exists():
            return {
                "manager": {"root": str(self.root), "config": str(self.config)},
                "environments": {},
                "allocations": {},
            }
        return json.loads(self.registry.read_text())

    def save(self, state):
        """Replace allocation metadata atomically with private permissions; callers hold the lock."""
        with tempfile.NamedTemporaryFile(mode="w", dir=self.state_dir, delete=False) as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(self.registry)

    def run(self, args, capture=False, env=None):
        """Run a trusted argv command, retaining its failure status."""
        result = subprocess.run(args, check=True, text=True, capture_output=capture, env=env)
        return result.stdout.strip() if capture else ""

    def compose_env(self, record=None):
        """Supply generated non-secret interpolation values; credentials stay in the config file."""
        env = dict(os.environ)
        for key in ("COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"):
            env.pop(key, None)
        env.update(
            DEV_ROOT=str(self.root),
            DEV_WORKTREE_ENTRYPOINT=str(self.tool_root / "docker/worktree-entrypoint.sh"),
            DEV_CONFIG=str(self.config),
            DEV_OVERRIDE_CONFIG=str(self.config),
            DEV_NETWORK_NAME=self.prefix + "-infra",
            DEV_NETWORK_EXTERNAL="false",
            DEV_DATA_DIR=str(self.state_dir / "infra-data"),
            DEV_POSTGRES_DATA="postgres-data",
            DEV_POSTGRES_VOLUME_NAME=self.prefix + "-infra_postgres-data",
            DEV_REDIS_VOLUME_NAME=self.prefix + "-infra_redis-data",
            DEV_SHARED_VOLUMES_EXTERNAL="false",
            DEV_POSTGRES_BIND="127.0.0.1:",
            DEV_REDIS_IMAGE="redis:7-alpine",
            DEV_REDIS_DATABASES="256",
            DEV_REDIS_APPENDONLY="yes",
        )
        project = record["project"] if record else self.prefix + "-infra"
        for service, key in (
            ("backend-api", "API"),
            ("backend-worker", "WORKER"),
            ("backend-scheduler", "SCHEDULER"),
            ("postgresql", "POSTGRES"),
            ("redis", "REDIS"),
            ("flower", "FLOWER"),
            ("lensnode", "LENSNODE"),
            ("frontend", "FRONTEND"),
            ("nginx", "NGINX"),
        ):
            env[f"DEV_{key}_CONTAINER"] = f"{project}-{service}-1"
        if record:
            broker, cache, channel = record["redis_dbs"]
            overrides = {
                "DB_ENGINE": "postgresql",
                "POSTGRES_HOST": "postgresql",
                "POSTGRES_PORT": "5432",
                "POSTGRES_DB": record["database"],
                "CELERY_BROKER_URL": f"redis://redis:6379/{broker}",
                "REDIS_URL": f"redis://redis:6379/{broker}",
                "CACHE_BACKEND": "redis",
                "CACHE_REDIS_URL": f"redis://redis:6379/{cache}",
                "CHANNEL_LAYER_REDIS_URL": f"redis://redis:6379/{channel}",
                "CELERY_TASK_DEFAULT_QUEUE": "backend",
                "CELERY_TASK_QUEUES": "backend,lens",
                "CELERY_REQUIRED_QUEUES": "backend,lens",
                "CELERY_CONCURRENCY": "2",
                "DJANGO_DEBUG": "true",
                "CSRF_TRUSTED_ORIGINS": f"http://localhost:{record['port']},http://127.0.0.1:{record['port']}",
                "SITE_DOMAIN": f"localhost:{record['port']}",
            }
            override_file = self.state_dir / (record["project"] + ".env")
            with tempfile.NamedTemporaryFile(mode="w", dir=self.state_dir, delete=False) as handle:
                handle.write("".join(f"{key}={value}\n" for key, value in overrides.items()))
                temporary = Path(handle.name)
            temporary.replace(override_file)
            env.update(
                WORKTREE_DIR=record["path"],
                DEV_DATA_DIR=record["data_dir"],
                DEV_BACKEND_IMAGE=record["backend_image"],
                DEV_LENSNODE_IMAGE=record["lensnode_image"],
                DEV_OVERRIDE_CONFIG=str(override_file),
                DEV_NETWORK_NAME=record["project"] + "-net",
                DEV_NETWORK_EXTERNAL="true",
                DEV_SHARED_VOLUMES_EXTERNAL="true",
                DEV_STATICFILES_DIR=str(Path(record["data_dir"]) / "staticfiles"),
                DEV_BUILD_CONTEXT=record["path"],
                DEV_BACKEND_ENTRYPOINT="/worktree-entrypoint.sh",
                DEV_FRONTEND_DIR=str(Path(record["path"]) / "frontend"),
                DEV_FRONTEND_IMAGE="node:22-alpine",
                DEV_NODE_MODULES="node-modules",
                DEV_FRONTEND_COMMAND="npm ci --cache /npm-cache --no-audit --no-fund && exec npm run dev -- --host 0.0.0.0",
                DEV_HTTP_BIND=f"127.0.0.1:{record['port']}",
                DEV_FLOWER_BIND="127.0.0.1:",
                DEV_NGINX_HEALTH_COMMAND="curl -f http://127.0.0.1/health",
                LENSNODE_NAME=record["project"],
                LENSNODE_TOKEN="dev-" + record["project"],
                LENSNODE_MAX_CONCURRENT_RUNS="2",
            )
        return env

    def compose_command(self, record, args):
        """Build an explicit project/config invocation independent of the caller's directory."""
        project = record["project"] if record else self.prefix + "-infra"
        return [
            "docker",
            "compose",
            "--project-directory",
            str(self.root),
            "-p",
            project,
            "--env-file",
            str(self.config),
            "-f",
            str(self.tool_root / "docker-compose.dev.yml"),
            *args,
        ]

    def compose(self, record, args, capture=False):
        """Operate only the selected application or the explicitly requested infrastructure."""
        return self.run(self.compose_command(record, args), capture=capture, env=self.compose_env(record))

    def worktrees(self):
        """Read Git's worktree inventory, including detached checkouts."""
        records = []
        for block in git(self.root, "worktree", "list", "--porcelain", "-z").split("\0\0"):
            fields = block.split("\0")
            if fields[0].startswith("worktree ") and "bare" not in fields:
                path = str(Path(fields[0][9:]).resolve())
                records.append(dict(path=path, name=Path(path).name, branch=branch(path)))
        return records

    def discover(self):
        """Join Git worktrees with actual dev Compose mounts; state is only launch metadata."""
        worktrees = self.worktrees()
        paths = {item["path"] for item in worktrees}
        ids = self.run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"], capture=True).split()
        containers = json.loads(self.run(["docker", "inspect", *ids], capture=True)) if ids else []
        projects = {}
        for container in containers:
            labels = container.get("Config", {}).get("Labels") or {}
            files = labels.get("com.docker.compose.project.config_files", "").split(",")
            if not any(Path(file).name == "docker-compose.dev.yml" for file in files):
                continue
            project = labels.get("com.docker.compose.project")
            projects.setdefault(project, []).append(container)
        metadata = list(self.read_state()["environments"].values())
        found = []
        for project, members in projects.items():
            apis = [c for c in members if c["Config"]["Labels"].get("com.docker.compose.service") == "backend-api"]
            for api in apis:
                source = next(
                    (
                        m["Source"]
                        for m in api.get("Mounts", [])
                        if m["Destination"] == "/opt/backend" and m["Type"] == "bind"
                    ),
                    None,
                )
                path = str(host_path(source).parent) if source else None
                if path not in paths:
                    continue
                saved = next((r for r in metadata if r["project"] == project and r["path"] == path), {})
                ports = [
                    p["HostPort"]
                    for c in members
                    for values in (c.get("NetworkSettings", {}).get("Ports") or {}).values()
                    for p in (values or [])
                    if c["Config"]["Labels"].get("com.docker.compose.service") == "nginx"
                ]
                found.append(
                    dict(
                        saved,
                        name=saved.get("name", Path(path).name),
                        path=path,
                        project=project,
                        port=int(ports[0]) if ports else saved.get("port", "-"),
                        status=api["State"]["Status"],
                        containers=members,
                        container=api["Id"],
                        external="redis_dbs" not in saved,
                    )
                )
        for tree in worktrees:
            if not any(r["path"] == tree["path"] for r in found):
                saved = next((r for r in metadata if r["path"] == tree["path"]), {})
                if "redis_dbs" not in saved:
                    saved = {k: v for k, v in saved.items() if k not in ("project", "port")}
                found.append(
                    dict({**tree, **saved}, status="stopped", containers=[], external="redis_dbs" not in saved)
                )
        return found

    def resolve(self, target, records):
        """Select by worktree path, directory name, branch, project or saved display alias."""
        path = str(Path(target).expanduser().resolve())
        matches = [
            r
            for r in records
            if target in (r.get("name"), Path(r["path"]).name, branch(r["path"]), r.get("project")) or path == r["path"]
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Worktree target is {'ambiguous' if matches else 'unknown'}: {target}; use an absolute path/project"
            )
        return matches[0]

    def application_containers(self, record):
        """Exclude shared database/Redis even when discovered inside a legacy single stack."""
        return [
            c["Id"]
            for c in record.get("containers", [])
            if c["Config"]["Labels"].get("com.docker.compose.service")
            in ("backend-api", "backend-worker", "backend-scheduler", "frontend", "lensnode", "flower", "nginx")
        ]

    def prepare(self, name, path, port=None):
        """Reserve an identity, loopback port, database and three Redis DBs under the lock."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name):
            raise ValueError("Names must be lowercase letters/digits/hyphens, at most 40 characters")
        path = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
        if common_dir(path) != self.repository:
            raise ValueError("Worktree must belong to the same repository as devctl")
        for directory in ("backend", "frontend", "plugins", "lensnode/lensnode"):
            if not (path / directory).is_dir():
                raise ValueError(f"Missing SourceLens source directory: {path / directory}")
        state = self.read_state()
        existing = next(
            (r for r in state["environments"].values() if r["path"] == str(path) and "redis_dbs" in r), None
        )
        alias = state["environments"].get(name)
        if alias and alias["path"] != str(path):
            raise ValueError(f"{name} already points to {alias['path']}; use another display name")
        if existing:
            if port is not None and existing["port"] != port:
                raise ValueError(f"{name} already uses port {existing['port']}; reuse its allocated port")
            return existing
        if port is not None and not 1024 <= port <= 65535:
            raise ValueError("Host port must be between 1024 and 65535")
        allocated = {item["port"] for item in state["environments"].values() if "redis_dbs" in item}
        for candidate in ([port] if port is not None else range(18081, 19081)):
            if candidate in allocated:
                if port is not None:
                    raise ValueError(f"Port {port} is already allocated")
                continue
            with socket.socket() as listener:
                try:
                    listener.bind(("127.0.0.1", candidate))
                except OSError:
                    if port is not None:
                        raise ValueError(f"Port {port} is in use") from None
                    continue
            break
        else:
            raise ValueError("No free port in 18081..19080")
        identity = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        allocations = state["allocations"]
        if identity not in allocations:
            used = {slot for slots in allocations.values() for slot in slots}
            for first in range(0, 253, 3):
                slots = [first, first + 1, first + 2]
                if used.isdisjoint(slots):
                    allocations[identity] = slots
                    break
            else:
                raise ValueError("All 85 Redis namespaces are reserved; use a new infrastructure/state root")
        project = self.prefix + "-" + identity
        record = dict(
            name=name,
            path=str(path),
            project=project,
            database="wt_" + identity,
            port=candidate,
            redis_dbs=allocations[identity],
            data_dir=str(self.state_dir / "data" / identity),
            backend_image=project + "-backend:dev",
            lensnode_image=project + "-lensnode:dev",
            created_at=now(),
            last_used_at=now(),
            branch=branch(path),
            status="stopped",
        )
        state["environments"][name] = record
        self.save(state)
        return record

    def get(self, name):
        """Resolve live worktrees without requiring prior devctl startup."""
        records = self.discover()
        paths = {r["path"] for r in records}
        retired = [r for r in self.read_state()["environments"].values() if r["path"] not in paths and "redis_dbs" in r]
        return self.resolve(name, records + retired)

    def update(self, record, **changes):
        """Persist lifecycle/test results without exposing credentials."""
        record.update(changes, last_used_at=now(), branch=branch(record["path"]))
        state = self.read_state()
        state["environments"][record["name"]] = {
            k: v for k, v in record.items() if k not in ("containers", "container", "external")
        }
        self.save(state)

    def infra_up(self):
        """Start only the dedicated shared services, waiting for health."""
        self.compose(None, ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "postgresql", "redis"])

    def switch(self, path):
        """Retarget the existing single development stack without recreating its infrastructure."""
        target = self.resolve(path, self.discover())
        stacks = [r for r in self.discover() if r.get("external") and r.get("containers")]
        if len(stacks) != 1:
            raise ValueError(
                "switch requires exactly one existing single Compose dev stack; start it with Compose first"
            )
        stack = stacks[0]
        if not self.config.is_file():
            raise ValueError(f"Missing shared config: {self.config}")
        for directory in ("backend", "frontend/src", "plugins", "lensnode/lensnode"):
            if not (Path(target["path"]) / directory).is_dir():
                raise ValueError(f"Missing source directory: {directory}")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DEV_", "COMPOSE_"))}
        env.update(
            WORKTREE_DIR=target["path"],
            DEV_ROOT=str(self.root),
            DEV_CONFIG=str(self.config),
            DEV_WORKTREE_ENTRYPOINT=str(Path(__file__).resolve().parents[1] / "docker/worktree-entrypoint.sh"),
        )
        for container in stack["containers"]:
            service = container["Config"]["Labels"].get("com.docker.compose.service")
            image_key = {
                "backend-api": "DEV_BACKEND_IMAGE",
                "lensnode": "DEV_LENSNODE_IMAGE",
                "frontend": "DEV_FRONTEND_IMAGE",
            }.get(service)
            if image_key:
                env[image_key] = container["Config"]["Image"]
            key, port = {"nginx": ("DEV_HTTP_BIND", "80/tcp"), "flower": ("DEV_FLOWER_BIND", "5555/tcp")}.get(
                service, (None, None)
            )
            bindings = (
                (container.get("NetworkSettings", {}).get("Ports") or {}).get(port)
                or (container.get("HostConfig", {}).get("PortBindings") or {}).get(port)
                or []
            )
            if key and len(bindings) == 1:
                host = bindings[0].get("HostIp") or "0.0.0.0"
                env[key] = f"{'[' + host + ']' if ':' in host else host}:{bindings[0]['HostPort']}"
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(self.root),
            "-p",
            stack["project"],
            "--env-file",
            str(self.config),
            "-f",
            str(Path(__file__).resolve().parents[1] / "docker-compose.dev.yml"),
        ]
        rendered = json.loads(self.run([*command, "config", "--format", "json"], capture=True, env=env))
        infra = [
            c
            for c in stack["containers"]
            if c["Config"]["Labels"].get("com.docker.compose.service") in ("postgresql", "redis")
        ]
        if len(infra) != 2 or any(c["State"]["Status"] != "running" for c in infra):
            raise ValueError("Existing PostgreSQL and Redis must be running before switch")
        for container in stack["containers"]:
            service = container["Config"]["Labels"]["com.docker.compose.service"]
            spec = rendered["services"][service]
            if spec.get("container_name") != container["Name"].lstrip("/"):
                raise ValueError("Shared config does not match existing container names; restore its Compose settings")
            if spec.get("image") != container["Config"]["Image"]:
                raise ValueError(f"Shared config image differs for {service}; rebuild/update the single stack first")
            mounts = {
                m["target"]: str(Path(m["source"]).resolve()) for m in spec.get("volumes", []) if m["type"] == "bind"
            }
            source_targets = {
                "/opt/backend",
                "/opt/plugins",
                "/opt/lensnode/lensnode",
                "/app/src",
                "/app/public",
                "/app/design",
                "/app/index.html",
                "/worktree-entrypoint.sh",
            }
            for mount in container["Mounts"]:
                if (
                    sys.platform == "darwin"
                    and mount["Destination"] == "/var/run/docker.sock"
                    and mount["Source"] == "/run/host-services/docker.proxy.sock"
                ):
                    continue
                if mount["Type"] == "bind" and mount["Destination"] not in source_targets:
                    if mounts.get(mount["Destination"]) != str(host_path(mount["Source"])):
                        raise ValueError(
                            f"Shared config changes data/config mount for {service}; restore its Compose settings"
                        )
            expected_ports = {(str(p["target"]), str(p.get("published", ""))) for p in spec.get("ports", [])}
            actual_ports = {
                (port.split("/")[0], p["HostPort"])
                for port, values in (
                    container.get("NetworkSettings", {}).get("Ports")
                    or container.get("HostConfig", {}).get("PortBindings")
                    or {}
                ).items()
                for p in (values or [])
            }
            if service not in ("postgresql", "redis") and expected_ports != actual_ports:
                raise ValueError(f"Shared config changes ports for {service}; restore its Compose settings")
        api_source = next(
            v["source"] for v in rendered["services"]["backend-api"]["volumes"] if v["target"] == "/opt/backend"
        )
        if Path(api_source).resolve() != Path(target["path"]) / "backend":
            raise ValueError("Compose configuration did not select the target source")
        startup = ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "600"]
        print(f"Switching single dev stack to {target['name']} ({branch(target['path'])})", flush=True)
        self.run([*command, *startup, "backend-api", "frontend"], env=env)
        self.run([*command, *startup, "backend-worker", "backend-scheduler", "lensnode", "flower", "nginx"], env=env)
        current = next((r for r in self.discover() if r.get("project") == stack["project"] and r["containers"]), None)
        if current is None or current["path"] != target["path"]:
            raise ValueError("Switch failed: actual API source does not match the target worktree")
        display_environments([current], detailed=True)

    def network(self, record, remove=False):
        """Attach shared servers to a private app network, avoiding shared service-name collisions."""
        name = record["project"] + "-net"
        label = "io.sourcelens.devctl.project"
        try:
            details = json.loads(self.run(["docker", "network", "inspect", name], capture=True))[0]
        except subprocess.CalledProcessError:
            if remove:
                return
            self.run(["docker", "network", "create", "--label", f"{label}={record['project']}", name], capture=True)
            details = {"Labels": {label: record["project"]}, "Containers": {}}
        if (details.get("Labels") or {}).get(label) != record["project"]:
            raise ValueError(f"Refusing to modify network not owned by this environment: {name}")
        for service in ("postgresql", "redis"):
            container = self.compose(None, ["ps", "-a", "-q", service], capture=True)
            if not container and not remove:
                raise ValueError(f"Shared infrastructure service is missing: {service}")
            attached = container in (details.get("Containers") or {})
            if remove and attached:
                self.run(["docker", "network", "disconnect", name, container])
            elif not remove and not attached:
                self.run(["docker", "network", "connect", "--alias", service, name, container])
        if remove:
            self.run(["docker", "network", "rm", name])

    def reuse_images(self, record):
        """Reuse local development images under private tags without building or downloading."""
        for key, shared in (
            ("backend_image", "sourcelens-api:latest"),
            ("lensnode_image", "sourcelens-lensnode:latest"),
        ):
            image = record[key]
            try:
                self.run(["docker", "image", "inspect", image], capture=True)
                continue
            except subprocess.CalledProcessError:
                pass
            try:
                self.run(["docker", "image", "inspect", shared], capture=True)
            except subprocess.CalledProcessError:
                raise ValueError(
                    f"No local image {image} or {shared}; run devctl up {record['path']} --build first"
                ) from None
            self.run(["docker", "tag", shared, image])
            print(f"Reusing {shared} as {image}", flush=True)

    def up(self, record, build=False):
        """Create the environment's database and start its complete stack with health gates."""
        if not build:
            self.reuse_images(record)
        self.infra_up()
        database = record["database"]
        sql = f"SELECT 1 FROM pg_database WHERE datname='{database}'"
        exists = self.compose(
            None,
            ["exec", "-T", "postgresql", "sh", "-c", 'exec psql -U "$POSTGRES_USER" -d postgres -tAc "$1"', "sh", sql],
            capture=True,
        )
        if exists != "1":
            self.compose(
                None, ["exec", "-T", "postgresql", "sh", "-c", 'exec createdb -U "$POSTGRES_USER" "$1"', "sh", database]
            )
        for directory in (
            "staticfiles",
            "logs/api",
            "logs/worker",
            "logs/scheduler",
            "logs/nginx",
            "storage/media",
            "workspace",
            "runtime",
            "document-attachments",
            "deliverables",
        ):
            runtime_path = Path(record["data_dir"]) / directory
            runtime_path.mkdir(parents=True, exist_ok=True)
            runtime_path.chmod(0o755)
        try:
            if build:
                self.compose(record, ["build", "backend-api", "lensnode"])
            self.network(record)
            startup = ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "600"]
            self.compose(record, [*startup, "backend-api", "frontend"])
            self.compose(record, [*startup, "backend-worker", "backend-scheduler", "lensnode", "flower", "nginx"])
        except subprocess.CalledProcessError:
            self.update(record, status="failed")
            with contextlib.suppress(subprocess.CalledProcessError):
                self.compose(record, ["logs", "--no-color", "--tail", "80"])
            raise
        self.update(record, status="running")
        print(f"Started {record['name']}: http://127.0.0.1:{record['port']}", flush=True)

    def clean(self, name):
        """Remove application containers while retaining worktrees, metadata and all data."""
        record = self.get(name)
        self.stop(record, remove=True)
        print(f"Cleaned {name}; retained data and launch/test metadata")

    def stop(self, record, remove=False):
        """Stop application services without touching the shared infrastructure."""
        if record.get("external"):
            ids = self.application_containers(record)
            if ids:
                self.run(["docker", "stop", *ids])
                if remove:
                    self.run(["docker", "rm", *ids])
        else:
            self.compose(record, ["down", "--remove-orphans"] if remove else ["down"])
            if remove:
                self.network(record, remove=True)
            self.update(record, status="stopped")

    def test(self, record, command):
        """Verify actual mounted source before executing a suite, saving output and exit status."""
        container = record.get("container") or (
            self.compose(record, ["ps", "-q", "backend-api"], capture=True) if not record.get("external") else None
        )
        if not container:
            raise ValueError("Environment API is not running; use devctl up first")
        details = json.loads(self.run(["docker", "inspect", container], capture=True))[0]
        source = next(
            (
                item["Source"]
                for item in details["Mounts"]
                if item["Destination"] == "/opt/backend" and item["Type"] == "bind"
            ),
            None,
        )
        if source is None:
            raise ValueError("API container has no /opt/backend bind mount; tests were not run")
        print(f"Source under test: {source}  (branch: {branch(source)})", flush=True)
        if host_path(source) != (Path(record["path"]) / "backend").resolve():
            raise ValueError("Actual mounted source differs from selected worktree; tests were not run")
        try:
            current = Path(git(Path.cwd(), "rev-parse", "--show-toplevel")).resolve()
            if current != Path(record["path"]):
                print(f"NOTE: that is not this worktree ({current}); explicitly testing {record['name']}.", flush=True)
        except subprocess.CalledProcessError:
            pass
        logs = self.state_dir / "test-logs"
        logs.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", prefix=record["project"] + "-", suffix=".log", dir=logs, delete=False
        ) as log:
            log.write(f"Source under test: {source}  (branch: {branch(source)})\n")
            if record.get("external"):
                wrapper = (
                    ["sh", "/worktree-entrypoint.sh"]
                    if any(m["Destination"] == "/worktree-entrypoint.sh" for m in details["Mounts"])
                    else []
                )
                args = ["docker", "exec", "-w", "/opt/backend", container, *wrapper, *command]
                env = None
            else:
                args = self.compose_command(
                    record,
                    ["exec", "-T", "-w", "/opt/backend", "backend-api", "sh", "/worktree-entrypoint.sh", *command],
                )
                env = self.compose_env(record)
            with subprocess.Popen(
                args, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            ) as process:
                for line in process.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    log.write(line)
                status = process.wait()
        self.update(record, last_test=dict(at=now(), exit_code=status, log=log.name))
        return status


def main():
    """Dispatch the local development CLI; no command destroys shared infrastructure."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    up = commands.add_parser("up", help="start a Git worktree (defaults to the current checkout)")
    up.add_argument("name", nargs="?", default=".")
    up.add_argument("path", nargs="?", default=".")
    up.add_argument("--port", type=int)
    build_options = up.add_mutually_exclusive_group()
    build_options.add_argument("--build", action="store_true", help="build backend and LensNode images before startup")
    build_options.add_argument("--no-build", action="store_true", help=argparse.SUPPRESS)
    commands.add_parser("switch", help="retarget the existing single dev stack").add_argument(
        "path", nargs="?", default="."
    )
    for action in ("infra-up", "list", "status"):
        command = commands.add_parser(action)
        if action in ("list", "status"):
            command.add_argument("-v", "--verbose", action="store_true", help="show full worktree paths")
    for action in ("logs", "down", "clean"):
        commands.add_parser(action).add_argument("name")
    test = commands.add_parser("test", help="inspect source and run a backend test command")
    test.add_argument("name")
    test.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    state = Path(
        os.environ.get(
            "DEVCTL_STATE", Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "sourcelens-dev"
        )
    )
    os.umask(0o077)
    try:
        root, config = manager_paths(Path(__file__).resolve().parents[1])
        manager = DevManager(root, state, config)
        if args.action in ("list", "status"):
            display_environments(manager.discover(), detailed=args.action == "status", verbose=args.verbose)
            return
        if args.action == "logs":
            record = manager.get(args.name)
            ids = manager.application_containers(record)
            if not ids:
                raise ValueError("No application containers for this worktree")
            for container in ids:
                manager.run(["docker", "logs", "--tail", "50", container])
            manager.run(["docker", "logs", "-f", "--tail", "0", record["container"]])
            return
        with manager.lock():
            if args.action == "switch":
                manager.switch(args.path)
            elif args.action == "infra-up":
                if not config.is_file():
                    raise ValueError(f"Missing shared config: {config}")
                manager.infra_up()
            elif args.action == "up":
                records = manager.discover()
                if args.path != ".":
                    selected = manager.resolve(args.path, records)
                    name = args.name
                else:
                    try:
                        selected = manager.resolve(args.name, records)
                        name = selected["name"]
                    except ValueError as exc:
                        if Path(args.name).exists() or "ambiguous" in str(exc):
                            raise
                        selected = manager.resolve(".", records)
                        name = args.name
                if selected.get("external") and selected.get("containers"):
                    if args.build:
                        raise ValueError("Build a directly launched stack with its original Docker Compose command")
                    if args.port is not None:
                        raise ValueError("Existing Compose stack keeps its published ports")
                    manager.run(["docker", "restart", *manager.application_containers(selected)])
                else:
                    if not config.is_file():
                        raise ValueError(f"Missing shared config: {config}")
                    name = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")[:40] or "worktree"
                    manager.up(manager.prepare(name, selected["path"], args.port), build=args.build)
            elif args.action == "down":
                record = manager.get(args.name)
                manager.stop(record)
            elif args.action == "clean":
                manager.clean(args.name)
            elif args.action == "test":
                command = args.command
                if command and command[0] == "--":
                    command = command[1:]
                sys.exit(manager.test(manager.get(args.name), command or ["python", "manage.py", "test", "--noinput"]))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
