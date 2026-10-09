"""Repository-scoped worktree discovery and Docker Compose lifecycle management."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
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
    records = [dict(r, name=r.get("display_name", r["name"])) for r in records]
    current_name = next(
        (r["name"] for r in records if r["path"] == current), Path(current).name if current else "(outside repository)"
    )
    if detailed and not verbose:
        print("Development status")
        print(f"  Current worktree  {current_name}")
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
    print(f"Worktrees: {len(records)}  |  Running: {running}  |  * current worktree  > active source")
    if not records:
        print("No local worktrees found.")
        return
    print()
    rows = []
    for index, record in enumerate(records, 1):
        containers = record["containers"]
        active = sum(c["State"]["Status"] == "running" for c in containers)
        marker = ("*" if record["path"] == current else " ") + (
            ">" if record.get("container") and record["status"] == "running" else " "
        )
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
        """Scope compatibility metadata and lifecycle locks to the repository's Git common directory."""
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
        """Serialize lifecycle mutations across local agents/processes."""
        with (self.repository / "devctl.lock").open("a") as handle:
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
        """Replace compatibility metadata atomically with private permissions; callers hold the lock."""
        with tempfile.NamedTemporaryFile(mode="w", dir=self.state_dir, delete=False) as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(self.registry)

    def run(self, args, capture=False, env=None):
        """Run a trusted argv command, retaining its failure status."""
        result = subprocess.run(args, check=True, text=True, capture_output=capture, env=env)
        return result.stdout.strip() if capture else ""

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
            if not apis and not project.startswith("sourcelens-wt-"):
                owner = members[0]["Config"]["Labels"].get("com.docker.compose.project.working_dir")
                if owner and host_path(owner) == self.root:
                    path = self.read_state().get("single", {}).get("path", str(self.root))
                    if path not in paths:
                        path = str(self.root)
                    found.append(
                        dict(
                            name=Path(path).name,
                            path=path,
                            project=project,
                            containers=members,
                            status="stopped",
                            port="-",
                            external=True,
                        )
                    )
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
                        external="redis_dbs" not in saved and not project.startswith("sourcelens-wt-"),
                    )
                )
        for tree in worktrees:
            if not any(r["path"] == tree["path"] for r in found):
                saved = next((r for r in metadata if r["path"] == tree["path"]), {})
                saved = {k: v for k, v in saved.items() if k in ("last_test",)}
                found.append(dict({**tree, **saved}, status="stopped", containers=[], external=True))
        primary = worktrees[0]["path"] if worktrees else None
        for record in found:
            record["display_name"] = "main" if record["path"] == primary else record["name"]
        return found

    def resolve(self, target, records):
        """Select by worktree path, directory name, branch, project or saved display alias."""
        path = str(Path(target).expanduser().resolve())
        matches = [
            r
            for r in records
            if target
            in (r.get("name"), r.get("display_name"), Path(r["path"]).name, branch(r["path"]), r.get("project"))
            or path == r["path"]
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

    def get(self, name):
        """Resolve live worktrees without requiring prior devctl startup."""
        return self.resolve(name, self.discover())

    def update(self, record, **changes):
        """Persist lifecycle/test results without exposing credentials."""
        record.update(changes, last_used_at=now(), branch=branch(record["path"]))
        state = self.read_state()
        state["environments"][record["name"]] = {
            k: v for k, v in record.items() if k not in ("containers", "container", "external")
        }
        self.save(state)

    def singleton(self, records=None):
        """Select the original shared-data stack, excluding retired isolated environments."""
        records = self.discover() if records is None else records
        stacks = [r for r in records if r.get("external") and r.get("containers")]
        if len(stacks) > 1:
            raise ValueError("Multiple single dev stacks found; cannot choose their shared database safely")
        return stacks[0] if stacks else None

    def retire_parallel(self, records):
        """Remove old parallel application containers and stop their dedicated servers; retain all data."""
        for record in records:
            if record.get("external") or not record.get("containers"):
                continue
            ids = self.application_containers(record)
            if ids:
                print(f"Retiring parallel application stack: {record['project']}", flush=True)
                self.run(["docker", "stop", *ids])
                self.run(["docker", "rm", *ids])
        ids = self.run(
            ["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=" + self.prefix + "-infra"],
            capture=True,
        ).split()
        if ids:
            self.run(["docker", "stop", *ids])

    def switch(self, path="."):
        """Retarget the single stack using its existing database and Redis."""
        self.up(path, require_existing=True)

    def up(self, path=".", build=False, require_existing=False, infrastructure_only=False):
        """Start or retarget one shared-data development stack, building only when requested."""
        trees = self.worktrees()
        trees[0]["display_name"] = "main"
        if Path(path).expanduser().is_dir():
            path = git(Path(path).expanduser(), "rev-parse", "--show-toplevel")
        target = self.resolve(path, trees)
        records = self.discover()
        stack = self.singleton(records)
        if require_existing and stack is None:
            raise ValueError("No existing single dev stack; run devctl up first")
        if stack is None:
            stack = dict(project=self.read_state().get("single", {}).get("project", "sourcelens-dev"), containers=[])
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
            DEV_BUILD_CONTEXT=target["path"],
            DEV_WORKTREE_ENTRYPOINT=str(self.tool_root / "docker/worktree-entrypoint.sh"),
        )
        remembered = self.read_state().get("single", {})
        if remembered.get("project") == stack["project"]:
            env.update(remembered.get("settings", {}))
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
            str(self.tool_root / "docker-compose.dev.yml"),
        ]
        rendered = json.loads(self.run([*command, "config", "--format", "json"], capture=True, env=env))
        infra = [
            c
            for c in stack["containers"]
            if c["Config"]["Labels"].get("com.docker.compose.service") in ("postgresql", "redis")
        ]

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
                if mount["Type"] == "volume":
                    spec_mount = next((m for m in spec.get("volumes", []) if m["target"] == mount["Destination"]), None)
                    volume_name = (
                        rendered.get("volumes", {}).get(spec_mount.get("source"), {}).get("name")
                        if spec_mount
                        else None
                    )
                    if volume_name != mount.get("Name"):
                        raise ValueError(
                            f"Shared config changes a retained volume for {service}; restore its Compose settings"
                        )
                    continue
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
        if build:
            self.run([*command, "build", "backend-api", "lensnode", "frontend"], env=env)
        elif not infrastructure_only:
            for service in ("backend-api", "lensnode", "frontend"):
                image = rendered["services"][service]["image"]
                try:
                    self.run(["docker", "image", "inspect", image], capture=True)
                except subprocess.CalledProcessError:
                    raise ValueError(f"Missing local image {image}; run devctl up --build first") from None
        self.retire_parallel(records)
        if len(infra) != 2 or any(c["State"]["Status"] != "running" for c in infra):
            self.run([*command, "up", "-d", "--no-deps", "--wait", "postgresql", "redis"], env=env)
        state = self.read_state()
        state["single"] = dict(
            project=stack["project"],
            path=target["path"],
            settings={
                key: env[key]
                for key in (
                    "DEV_BACKEND_IMAGE",
                    "DEV_LENSNODE_IMAGE",
                    "DEV_FRONTEND_IMAGE",
                    "DEV_HTTP_BIND",
                    "DEV_FLOWER_BIND",
                )
                if key in env
            },
        )
        self.save(state)
        if infrastructure_only:
            return
        self.stop(stack)
        startup = ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "600"]
        print(
            f"Using single dev stack: {target.get('display_name', target['name'])} ({branch(target['path'])})",
            flush=True,
        )
        self.run([*command, *startup, "backend-api", "frontend"], env=env)
        self.run([*command, *startup, "backend-worker", "backend-scheduler", "lensnode", "flower", "nginx"], env=env)
        current = next((r for r in self.discover() if r.get("project") == stack["project"] and r["containers"]), None)
        if current is None or current["path"] != target["path"]:
            raise ValueError("Switch failed: actual API source does not match the target worktree")
        display_environments([current], detailed=True)

    def stop(self, record, remove=False):
        """Stop application services without touching the shared infrastructure."""
        ids = self.application_containers(record)
        if ids:
            self.run(["docker", "stop", *ids], capture=True)
            if remove:
                self.run(["docker", "rm", *ids], capture=True)

    def test(self, record, command):
        """Verify actual mounted source before executing a suite, saving output and exit status."""
        container = record.get("container")
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
            wrapper = (
                ["sh", "/worktree-entrypoint.sh"]
                if any(m["Destination"] == "/worktree-entrypoint.sh" for m in details["Mounts"])
                else []
            )
            args = ["docker", "exec", "-w", "/opt/backend", container, *wrapper, *command]
            with subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
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
    up.add_argument("path", nargs="?", default=".")
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
        commands.add_parser(action).add_argument("name", nargs="?", default=None)
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
            record = manager.get(args.name) if args.name else manager.singleton()
            if record is None:
                raise ValueError("No single dev stack found")
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
                manager.up(infrastructure_only=True)
            elif args.action == "up":
                manager.up(args.path, build=args.build)
            elif args.action in ("down", "clean"):
                record = manager.get(args.name) if args.name else manager.singleton()
                if record is None:
                    raise ValueError("No single dev stack found")
                manager.stop(record, remove=args.action == "clean")
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
