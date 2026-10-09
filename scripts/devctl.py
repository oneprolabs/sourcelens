"""Repository-scoped worktree registry and Docker Compose lifecycle management."""

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
    """Return a registry timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def git(path, *args):
    """Read local Git metadata without interpreting command strings."""
    return subprocess.check_output(["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL).strip()


def common_dir(path):
    """Identify the repository shared by all of its worktrees."""
    return (Path(path) / git(path, "rev-parse", "--git-common-dir")).resolve()


def branch(path):
    """Describe the current branch or detached revision, including deleted worktrees."""
    try:
        return git(path, "symbolic-ref", "--short", "HEAD")
    except subprocess.CalledProcessError:
        try:
            return "detached at " + git(path, "rev-parse", "--short", "HEAD")
        except (subprocess.CalledProcessError, FileNotFoundError):
            return "unavailable"


class DevManager:
    """Manage application projects while retaining shared infrastructure and data."""

    def __init__(self, root, state_home, config):
        """Scope Docker resources and registry locks to the repository's Git common directory."""
        self.root = Path(root).resolve()
        self.config = Path(config).resolve()
        self.repository = common_dir(self.root)
        scope = hashlib.sha256(str(self.repository).encode()).hexdigest()[:12]
        state_home = Path(state_home).expanduser().resolve()
        # Separate registries must not allocate overlapping namespaces on the same servers.
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
        """Read an atomic registry snapshot; retired allocations remain reserved."""
        if not self.registry.exists():
            return {
                "manager": {"root": str(self.root), "config": str(self.config)},
                "environments": {},
                "allocations": {},
            }
        return json.loads(self.registry.read_text())

    def save(self, state):
        """Replace the registry atomically with private permissions; callers hold the lock."""
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
            str(self.root / "docker-compose.dev.yml"),
            *args,
        ]

    def compose(self, record, args, capture=False):
        """Operate only the selected application or the explicitly requested infrastructure."""
        return self.run(self.compose_command(record, args), capture=capture, env=self.compose_env(record))

    def register(self, name, path, port=None):
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
        existing = state["environments"].get(name)
        if existing:
            if existing["path"] != str(path):
                raise ValueError(f"{name} already points to {existing['path']}; clean it first")
            if port is not None and existing["port"] != port:
                raise ValueError(f"{name} already uses port {existing['port']}; clean it first")
            return existing
        if port is not None and not 1024 <= port <= 65535:
            raise ValueError("Host port must be between 1024 and 65535")
        registered = {item["port"] for item in state["environments"].values()}
        for candidate in ([port] if port is not None else range(18081, 19081)):
            if candidate in registered:
                if port is not None:
                    raise ValueError(f"Port {port} is already registered")
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
        identity = hashlib.sha256(f"{name}:{path}".encode()).hexdigest()[:12]
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
            status="registered",
        )
        state["environments"][name] = record
        self.save(state)
        return record

    def get(self, name):
        """Find a registered environment without requiring its worktree to still exist."""
        record = self.read_state()["environments"].get(name)
        if record is None:
            raise ValueError(f"Unknown environment: {name}")
        return record

    def update(self, record, **changes):
        """Persist lifecycle/test results without exposing credentials."""
        record.update(changes, last_used_at=now(), branch=branch(record["path"]))
        state = self.read_state()
        state["environments"][record["name"]] = record
        self.save(state)

    def infra_up(self):
        """Start only the dedicated shared services, waiting for health."""
        self.compose(None, ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "postgresql", "redis"])

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

    def up(self, record, build=True):
        """Create the environment's database and start its complete stack with health gates."""
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
        """Remove app containers/registration while retaining DBs, files, volumes and Redis allocations."""
        record = self.get(name)
        self.compose(record, ["down", "--remove-orphans"])
        self.network(record, remove=True)
        state = self.read_state()
        del state["environments"][name]
        self.save(state)
        print(f"Removed {name}; retained database {record['database']} and data at {record['data_dir']}")

    def test(self, record, command):
        """Verify actual mounted source before executing a suite, saving output and exit status."""
        container = self.compose(record, ["ps", "-q", "backend-api"], capture=True)
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
        if Path(source).resolve() != (Path(record["path"]) / "backend").resolve():
            raise ValueError("Actual mounted source differs from registered worktree; tests were not run")
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
            args = self.compose_command(
                record, ["exec", "-T", "-w", "/opt/backend", "backend-api", "sh", "/worktree-entrypoint.sh", *command]
            )
            with subprocess.Popen(
                args, env=self.compose_env(record), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
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
    up = commands.add_parser("up", help="register and start a worktree")
    up.add_argument("name")
    up.add_argument("path", nargs="?", default=".")
    up.add_argument("--port", type=int)
    up.add_argument("--no-build", action="store_true", help="reuse this environment's existing images")
    for action in ("infra-up", "list", "status"):
        commands.add_parser(action)
    for action in ("logs", "down", "clean"):
        commands.add_parser(action).add_argument("name")
    test = commands.add_parser("test", help="inspect source and run a backend test command")
    test.add_argument("name")
    test.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(os.environ.get("DEVCTL_ROOT", Path(__file__).resolve().parents[1])).resolve()
    config = Path(os.environ.get("DEVCTL_CONFIG", root / ".env.dev"))
    state = Path(
        os.environ.get(
            "DEVCTL_STATE", Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "sourcelens-dev"
        )
    )
    os.umask(0o077)
    try:
        manager = DevManager(root, state, config)
        if args.action == "list":
            for record in manager.read_state()["environments"].values():
                last_test = record.get("last_test", {}).get("exit_code", "-")
                print(
                    f"{record['name']}  :{record['port']}  {record['status']}  "
                    f"branch={branch(record['path'])}  test={last_test}  {record['path']}"
                )
            return
        if not config.is_file():
            raise ValueError(f"Missing shared config: {config}; copy env.sample to .env.dev in DEVCTL_ROOT")
        if args.action == "logs":
            manager.compose(manager.get(args.name), ["logs", "-f", "--tail", "200"])
            return
        if args.action == "status":
            manager.compose(None, ["ps"])
            for record in manager.read_state()["environments"].values():
                print(f"=== {record['name']} ({record['path']}) ===", flush=True)
                manager.compose(record, ["ps"])
            return
        with manager.lock():
            if args.action == "infra-up":
                manager.infra_up()
            elif args.action == "up":
                manager.up(manager.register(args.name, args.path, args.port), build=not args.no_build)
            elif args.action == "down":
                record = manager.get(args.name)
                manager.compose(record, ["down"])
                manager.update(record, status="stopped")
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
