"""Regression coverage for worktree identity, resource isolation, and lifecycle safety."""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.devctl import DevManager, main, manager_paths


class DevManagerTests(unittest.TestCase):
    """Use real worktrees and filesystem state; replace only the Docker boundary."""

    def setUp(self):
        """Create two worktrees sharing the same Git common directory."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "main"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for directory in ("backend", "frontend", "plugins", "lensnode/lensnode"):
            (self.root / directory).mkdir(parents=True)
            (self.root / directory / "fixture").touch()
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Fixture",
            ],
            check=True,
        )
        self.other = Path(self.temp.name) / "other worktree"
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "-qb", "other", str(self.other)], check=True)
        self.config = self.root / ".env.dev"
        self.config.write_text("POSTGRES_USER=postgres\nPOSTGRES_PASSWORD='complex $ password'\n")
        self.manager = DevManager(self.root, Path(self.temp.name) / "state", self.config)

    def prepare(self, name, path=None, port=None):
        """Apply the same registry lock used by the CLI."""
        with self.manager.lock():
            return self.manager.prepare(name, path or self.root, port)

    def test_worktrees_exist_without_launch_metadata(self):
        """Git alone supplies the inventory before any environment has been started."""
        with patch.object(self.manager, "run", return_value=""):
            records = self.manager.discover()
        self.assertEqual({r["path"] for r in records}, {str(self.root.resolve()), str(self.other.resolve())})
        self.assertTrue(all(r["status"] == "stopped" for r in records))

    def test_linked_worktree_reuses_primary_checkout_configuration(self):
        """A worktree without .env.dev still resolves the main checkout's manager/config."""
        self.assertFalse((self.other / ".env.dev").exists())
        root, config = manager_paths(self.other, {})
        self.assertEqual(root, self.root.resolve())
        self.assertEqual(config, self.config.resolve())
        self.assertTrue(config.is_file())

    def test_linked_worktree_configuration_does_not_override_main(self):
        """Adding branch-local configuration must not silently switch shared credentials."""
        (self.other / ".env.dev").write_text("POSTGRES_DB=wrong_branch\n")
        root, config = manager_paths(self.other, {})
        self.assertEqual(root, self.root.resolve())
        self.assertEqual(config, self.config.resolve())

    def test_manager_resolution_is_independent_of_caller_directory(self):
        """The tool location identifies its repository even when invoked from outside it."""
        with patch("scripts.devctl.Path.cwd", return_value=Path(self.temp.name)):
            self.assertEqual(manager_paths(self.other, {}), (self.root.resolve(), self.config.resolve()))

    def test_explicit_manager_and_configuration_overrides_are_preserved(self):
        """Custom manager/config locations remain available as an explicit opt-in."""
        root, config = manager_paths(self.other, {"DEVCTL_ROOT": str(self.other)})
        self.assertEqual(root, self.other.resolve())
        self.assertEqual(config, (self.other / ".env.dev").resolve())
        root, config = manager_paths(self.other, {"DEVCTL_CONFIG": str(self.config)})
        self.assertEqual(root, self.root.resolve())
        self.assertEqual(config, self.config.resolve())

    def test_linked_worktree_cli_lists_and_queries_main_without_root_override(self):
        """Invoke the copied tool from a worktree that has no configuration file."""
        scripts = self.other / "scripts"
        scripts.mkdir()
        shutil.copyfile(Path(__file__), scripts / "test_devctl.py")
        shutil.copyfile(Path(__file__).with_name("devctl.py"), scripts / "devctl.py")
        source_root = Path(__file__).resolve().parents[1]
        shutil.copyfile(source_root / "devctl", self.other / "devctl")
        self.prepare("branch-a")
        env = dict(os.environ)
        env.pop("DEVCTL_ROOT", None)
        env.pop("DEVCTL_CONFIG", None)
        env["DEVCTL_STATE"] = str(Path(self.temp.name) / "state")
        fake_bin = Path(self.temp.name) / "bin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text("#!/bin/sh\nexit 0\n")
        docker.chmod(0o755)
        env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
        result = subprocess.run(
            [os.sys.executable, str(self.other / "devctl"), "list"],
            cwd=self.other,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn(str(self.other.resolve()), result.stdout)

    def compose_fixture(self, project="manual-dev", path=None, production=False):
        """Build Docker inspection data with Compose labels and an actual source mount."""
        return dict(
            Id="api-id",
            Name="/api",
            Config=dict(
                Labels={
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": "backend-api",
                    "com.docker.compose.project.config_files": str(
                        self.root / ("docker-compose.yml" if production else "docker-compose.dev.yml")
                    ),
                }
            ),
            Mounts=[dict(Type="bind", Destination="/opt/backend", Source=str((path or self.other) / "backend"))],
            State=dict(Status="running"),
            NetworkSettings=dict(Ports={}),
        )

    def test_manual_compose_is_discovered_by_actual_mount_not_launch_directory(self):
        """Retargeted legacy stacks belong to their mounted worktree with no metadata."""
        fixture = self.compose_fixture()
        with patch.object(self.manager, "run", side_effect=["api-id", json.dumps([fixture])]):
            records = self.manager.discover()
        linked = self.manager.resolve(str(self.other), records)
        self.assertEqual(linked["project"], "manual-dev")
        self.assertTrue(linked["external"])
        self.assertEqual(linked["status"], "running")
        self.assertEqual(self.manager.resolve(str(self.root), records)["status"], "stopped")
        self.assertFalse(self.manager.registry.exists())

    def test_production_and_unrelated_mounts_are_excluded(self):
        """Compose labels alone must not include production or another repository."""
        fixtures = [self.compose_fixture(production=True), self.compose_fixture(path=Path("/unrelated"))]
        with patch.object(self.manager, "run", side_effect=["api-id", json.dumps(fixtures)]):
            records = self.manager.discover()
        self.assertTrue(all(r["status"] == "stopped" for r in records))

    def test_alias_change_reuses_worktree_data_identity(self):
        """A worktree's identity does not change with its display name."""
        first = self.prepare("first")
        second = self.prepare("second")
        self.assertEqual(first["project"], second["project"])
        self.assertEqual(first["redis_dbs"], second["redis_dbs"])

    def test_clean_keeps_worktree_discoverable(self):
        """Container cleanup never removes a Git worktree from the inventory."""
        record = self.prepare("branch-a")
        with patch.object(self.manager, "get", return_value=record), patch.object(
            self.manager, "compose"
        ), patch.object(self.manager, "network"):
            self.manager.clean("branch-a")
        with patch.object(self.manager, "run", return_value=""):
            records = self.manager.discover()
        self.assertEqual(self.manager.resolve(str(self.root), records)["status"], "stopped")

    def test_manual_cleanup_never_stops_shared_services(self):
        """A legacy project includes infrastructure, but only app containers may be cleaned."""
        api = self.compose_fixture()
        database = self.compose_fixture()
        database["Id"] = "database-id"
        database["Config"]["Labels"]["com.docker.compose.service"] = "postgresql"
        record = dict(external=True, containers=[api, database])
        with patch.object(self.manager, "run") as run:
            self.manager.stop(record, remove=True)
        self.assertEqual(
            [c.args[0] for c in run.call_args_list], [["docker", "stop", "api-id"], ["docker", "rm", "api-id"]]
        )

    def test_parallel_environments_have_disjoint_resources(self):
        """Database, broker/cache/Channels, port, images, and runtime files are isolated."""
        a = self.prepare("branch-a")
        b = self.prepare("branch-b", self.other)
        for key in ("project", "port", "database", "data_dir", "backend_image", "lensnode_image"):
            self.assertNotEqual(a[key], b[key], key)
        self.assertTrue(set(a["redis_dbs"]).isdisjoint(b["redis_dbs"]))
        self.assertNotIn("password", json.dumps(self.manager.read_state()).lower())

    def test_existing_registration_cannot_change_identity_or_port(self):
        """A name cannot silently retarget a different branch or endpoint."""
        original = self.prepare("branch-a")
        self.assertEqual(original["project"], self.prepare("branch-a")["project"])
        with self.assertRaisesRegex(ValueError, "already points"):
            self.prepare("branch-a", self.other)
        with self.assertRaisesRegex(ValueError, "already uses port"):
            self.prepare("branch-a", port=original["port"] + 1)

    def test_name_and_port_validation(self):
        """Reject project-name ambiguity, traversal, and invalid ports before Docker."""
        for name in ("../escape", "Feature-A", "a_b", "-flag", "x" * 41):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare(name)
        for port in (80, 65536):
            with self.assertRaises(ValueError):
                self.prepare("branch-a", port=port)

    def test_registered_and_live_ports_are_rejected(self):
        """Check both managed reservations and other processes' loopback listeners."""
        record = self.prepare("branch-a")
        with self.assertRaisesRegex(ValueError, "allocated"):
            self.prepare("branch-b", self.other, record["port"])
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            with self.assertRaisesRegex(ValueError, "in use"):
                self.prepare("branch-b", self.other, listener.getsockname()[1])

    def test_unrelated_repository_is_rejected(self):
        """Never attach unrelated code to this repository's shared infrastructure."""
        unrelated = Path(self.temp.name) / "unrelated"
        subprocess.run(["git", "init", "-q", str(unrelated)], check=True)
        with self.assertRaisesRegex(ValueError, "same repository"):
            self.prepare("branch-a", unrelated)

    def test_cleanup_preserves_data_and_infra_and_reserves_redis_slots(self):
        """Cleanup must not delete databases, volumes, or another environment's broker state."""
        a = self.prepare("branch-a")
        Path(a["data_dir"]).mkdir(parents=True)
        calls = []
        with self.manager.lock(), patch.object(self.manager, "get", return_value=a), patch.object(
            self.manager, "compose", side_effect=lambda *args, **kw: calls.append(args)
        ), patch.object(self.manager, "network") as network:
            self.manager.clean("branch-a")
        self.assertEqual(calls, [(a, ["down", "--remove-orphans"])])
        network.assert_called_once_with(a, remove=True)
        self.assertTrue(Path(a["data_dir"]).exists())
        self.assertEqual(self.manager.read_state()["environments"]["branch-a"]["status"], "stopped")
        b = self.prepare("branch-b", self.other)
        self.assertTrue(set(a["redis_dbs"]).isdisjoint(b["redis_dbs"]))

    def test_environment_overrides_shared_config_and_shell_values(self):
        """Inherited production endpoints/project names cannot override generated identities."""
        record = self.prepare("branch-a")
        with patch.dict(os.environ, {"DEV_HTTP_BIND": "80", "COMPOSE_PROJECT_NAME": "sourcelens"}):
            env = self.manager.compose_env(record)
        self.assertEqual(env["DEV_HTTP_BIND"], f"127.0.0.1:{record['port']}")
        self.assertNotIn("COMPOSE_PROJECT_NAME", env)
        self.assertEqual(env["DEV_CONFIG"], str(self.config.resolve()))

    def test_every_mode_uses_existing_compose_and_selects_only_its_services(self):
        """Shared infrastructure and apps must reuse the dev file without launching each other."""
        record = self.prepare("branch-a")
        for item in (None, record):
            argv = self.manager.compose_command(item, ["ps"])
            self.assertEqual(argv[argv.index("-f") + 1], str(self.root.resolve() / "docker-compose.dev.yml"))
        with patch.object(self.manager, "compose") as compose:
            self.manager.infra_up()
        self.assertEqual(compose.call_args.args[1][-2:], ["postgresql", "redis"])

    def test_runtime_overrides_are_private_and_do_not_copy_credentials(self):
        """The generated config only isolates endpoints; credentials remain in the shared config."""
        record = self.prepare("branch-a")
        env = self.manager.compose_env(record)
        path = Path(env["DEV_OVERRIDE_CONFIG"])
        content = path.read_text()
        self.assertIn(f"POSTGRES_DB={record['database']}\n", content)
        self.assertIn(f"CELERY_BROKER_URL=redis://redis:6379/{record['redis_dbs'][0]}\n", content)
        self.assertNotIn("POSTGRES_PASSWORD", content)
        self.assertNotIn("complex", content)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_application_startup_never_starts_database_services(self):
        """Keep API/frontend health gating while reusing only the shared servers."""
        record = self.prepare("branch-a")
        with patch.object(self.manager, "infra_up"), patch.object(self.manager, "network"), patch.object(
            self.manager, "compose", return_value="1"
        ) as compose:
            self.manager.up(record, build=False)
        commands = [call.args[1] for call in compose.call_args_list if call.args[0] == record]
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][-2:], ["backend-api", "frontend"])
        self.assertEqual(commands[1][-5:], ["backend-worker", "backend-scheduler", "lensnode", "flower", "nginx"])
        for command in commands:
            self.assertIn("--no-deps", command)
            self.assertIn("--wait", command)
            self.assertNotIn("postgresql", command)
            self.assertNotIn("redis", command)

    def test_registry_has_one_configuration_home(self):
        """A tool copied into a different worktree cannot silently change shared credentials."""
        self.prepare("branch-a")
        with self.assertRaisesRegex(ValueError, "different configuration home"):
            DevManager(self.other, Path(self.temp.name) / "state", self.config)

    def test_independent_state_roots_do_not_share_docker_resources(self):
        """An acceptance registry must not reuse the daily development infrastructure/Redis slots."""
        other = DevManager(self.root, Path(self.temp.name) / "other-state", self.config)
        self.assertNotEqual(self.manager.prefix, other.prefix)

    def test_concurrent_agents_allocate_distinct_resources(self):
        """Exercise the real file lock in two independent interpreter processes."""
        script = (
            "from pathlib import Path; from scripts.devctl import DevManager; import sys; "
            "m=DevManager(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])); "
            "\nwith m.lock(): m.prepare(sys.argv[4], Path(sys.argv[5]))"
        )
        processes = [
            subprocess.Popen(
                [
                    os.sys.executable,
                    "-c",
                    script,
                    str(self.root),
                    str(Path(self.temp.name) / "state"),
                    str(self.config),
                    name,
                    str(path),
                ],
            )
            for name, path in (("branch-a", self.root), ("branch-b", self.other))
        ]
        for process in processes:
            self.assertEqual(process.wait(timeout=20), 0)
        records = list(self.manager.read_state()["environments"].values())
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0]["port"], records[1]["port"])
        self.assertTrue(set(records[0]["redis_dbs"]).isdisjoint(records[1]["redis_dbs"]))

    def test_network_attaches_only_shared_servers_to_environment_network(self):
        """API/frontend aliases remain on separate networks, while PostgreSQL/Redis are shared."""
        record = self.prepare("branch-a")
        name = record["project"] + "-net"
        details = [{"Labels": {"io.sourcelens.devctl.project": record["project"]}, "Containers": {}}]
        with patch.object(self.manager, "run", return_value=json.dumps(details)) as run, patch.object(
            self.manager, "compose", side_effect=["postgres-id", "redis-id"]
        ):
            self.manager.network(record)
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["docker", "network", "connect", "--alias", "postgresql", name, "postgres-id"],
        )
        self.assertEqual(
            run.call_args_list[2].args[0], ["docker", "network", "connect", "--alias", "redis", name, "redis-id"]
        )

    def test_foreign_network_cannot_be_modified(self):
        """Refuse an unrelated network even if its name happens to collide."""
        record = self.prepare("branch-a")
        with patch.object(self.manager, "run", return_value='[{"Labels": {}}]') as run:
            with self.assertRaisesRegex(ValueError, "not owned"):
                self.manager.network(record, remove=True)
        self.assertEqual(run.call_count, 1)

    def test_test_refuses_wrong_actual_mount(self):
        """An incorrect container mount must never produce a green suite for the registered branch."""
        record = self.prepare("branch-a")
        inspected = [
            {"Mounts": [{"Type": "bind", "Destination": "/opt/backend", "Source": str(self.other / "backend")}]}
        ]
        with patch.object(self.manager, "compose", return_value="container-id") as compose, patch.object(
            self.manager, "run", return_value=json.dumps(inspected)
        ):
            with self.assertRaisesRegex(ValueError, "mounted source differs"):
                self.manager.test(record, ["python", "manage.py", "test"])
        self.assertEqual(compose.call_count, 1)

    def test_suite_failure_is_saved_and_propagated(self):
        """Preserve a failing command's exit status and log instead of reporting success."""
        record = self.prepare("branch-a")
        inspected = [
            {"Mounts": [{"Type": "bind", "Destination": "/opt/backend", "Source": str(self.root / "backend")}]}
        ]
        real_popen = subprocess.Popen
        with patch.object(self.manager, "compose", return_value="container-id"), patch.object(
            self.manager, "run", return_value=json.dumps(inspected)
        ), patch("scripts.devctl.subprocess.Popen") as popen:
            process = popen.return_value
            process.__enter__.return_value.stdout = iter(["FAILED fixture\n"])
            process.__enter__.return_value.wait.return_value = 7
            popen.side_effect = lambda argv, **kwargs: real_popen(argv, **kwargs) if argv[0] == "git" else process
            self.assertEqual(self.manager.test(record, ["python", "manage.py", "test"]), 7)
        saved = self.manager.read_state()["environments"]["branch-a"]["last_test"]
        self.assertEqual(saved["exit_code"], 7)
        self.assertIn("FAILED fixture", Path(saved["log"]).read_text())


if __name__ == "__main__":
    unittest.main()
