"""Regression coverage for worktree identity, resource isolation, and lifecycle safety."""

import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.devctl import DevManager


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

    def register(self, name, path=None, port=None):
        """Apply the same registry lock used by the CLI."""
        with self.manager.lock():
            return self.manager.register(name, path or self.root, port)

    def test_parallel_environments_have_disjoint_resources(self):
        """Database, broker/cache/Channels, port, images, and runtime files are isolated."""
        a = self.register("branch-a")
        b = self.register("branch-b", self.other)
        for key in ("project", "port", "database", "data_dir", "backend_image", "lensnode_image"):
            self.assertNotEqual(a[key], b[key], key)
        self.assertTrue(set(a["redis_dbs"]).isdisjoint(b["redis_dbs"]))
        self.assertNotIn("password", json.dumps(self.manager.read_state()).lower())

    def test_existing_registration_cannot_change_identity_or_port(self):
        """A name cannot silently retarget a different branch or endpoint."""
        original = self.register("branch-a")
        self.assertEqual(original["project"], self.register("branch-a")["project"])
        with self.assertRaisesRegex(ValueError, "already points"):
            self.register("branch-a", self.other)
        with self.assertRaisesRegex(ValueError, "already uses port"):
            self.register("branch-a", port=original["port"] + 1)

    def test_name_and_port_validation(self):
        """Reject project-name ambiguity, traversal, and invalid ports before Docker."""
        for name in ("../escape", "Feature-A", "a_b", "-flag", "x" * 41):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.register(name)
        for port in (80, 65536):
            with self.assertRaises(ValueError):
                self.register("branch-a", port=port)

    def test_registered_and_live_ports_are_rejected(self):
        """Check both managed reservations and other processes' loopback listeners."""
        record = self.register("branch-a")
        with self.assertRaisesRegex(ValueError, "registered"):
            self.register("branch-b", self.other, record["port"])
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            with self.assertRaisesRegex(ValueError, "in use"):
                self.register("branch-b", self.other, listener.getsockname()[1])

    def test_unrelated_repository_is_rejected(self):
        """Never attach unrelated code to this repository's shared infrastructure."""
        unrelated = Path(self.temp.name) / "unrelated"
        subprocess.run(["git", "init", "-q", str(unrelated)], check=True)
        with self.assertRaisesRegex(ValueError, "same repository"):
            self.register("branch-a", unrelated)

    def test_cleanup_preserves_data_and_infra_and_reserves_redis_slots(self):
        """Cleanup must not delete databases, volumes, or another environment's broker state."""
        a = self.register("branch-a")
        Path(a["data_dir"]).mkdir(parents=True)
        calls = []
        with self.manager.lock(), patch.object(
            self.manager, "compose", side_effect=lambda *args, **kw: calls.append(args)
        ), patch.object(self.manager, "network") as network:
            self.manager.clean("branch-a")
        self.assertEqual(calls, [(a, ["down", "--remove-orphans"])])
        network.assert_called_once_with(a, remove=True)
        self.assertTrue(Path(a["data_dir"]).exists())
        self.assertNotIn("branch-a", self.manager.read_state()["environments"])
        b = self.register("branch-b", self.other)
        self.assertTrue(set(a["redis_dbs"]).isdisjoint(b["redis_dbs"]))

    def test_environment_overrides_shared_config_and_shell_values(self):
        """Inherited production endpoints/project names cannot override generated identities."""
        record = self.register("branch-a")
        with patch.dict(os.environ, {"DEV_HTTP_BIND": "80", "COMPOSE_PROJECT_NAME": "sourcelens"}):
            env = self.manager.compose_env(record)
        self.assertEqual(env["DEV_HTTP_BIND"], f"127.0.0.1:{record['port']}")
        self.assertNotIn("COMPOSE_PROJECT_NAME", env)
        self.assertEqual(env["DEV_CONFIG"], str(self.config.resolve()))

    def test_every_mode_uses_existing_compose_and_selects_only_its_services(self):
        """Shared infrastructure and apps must reuse the dev file without launching each other."""
        record = self.register("branch-a")
        for item in (None, record):
            argv = self.manager.compose_command(item, ["ps"])
            self.assertEqual(argv[argv.index("-f") + 1], str(self.root.resolve() / "docker-compose.dev.yml"))
        with patch.object(self.manager, "compose") as compose:
            self.manager.infra_up()
        self.assertEqual(compose.call_args.args[1][-2:], ["postgresql", "redis"])

    def test_runtime_overrides_are_private_and_do_not_copy_credentials(self):
        """The generated config only isolates endpoints; credentials remain in the shared config."""
        record = self.register("branch-a")
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
        record = self.register("branch-a")
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
        self.register("branch-a")
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
            "\nwith m.lock(): m.register(sys.argv[4], Path(sys.argv[5]))"
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
        record = self.register("branch-a")
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
        record = self.register("branch-a")
        with patch.object(self.manager, "run", return_value='[{"Labels": {}}]') as run:
            with self.assertRaisesRegex(ValueError, "not owned"):
                self.manager.network(record, remove=True)
        self.assertEqual(run.call_count, 1)

    def test_test_refuses_wrong_actual_mount(self):
        """An incorrect container mount must never produce a green suite for the registered branch."""
        record = self.register("branch-a")
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
        record = self.register("branch-a")
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
