"""Regression coverage for worktree identity, resource isolation, and lifecycle safety."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.devctl import DevManager, manager_paths


class DevManagerTests(unittest.TestCase):
    """Use real worktrees and filesystem state; replace only the Docker boundary."""

    def setUp(self):
        """Create two worktrees sharing the same Git common directory."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "main"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for directory in ("backend", "frontend/src", "plugins", "lensnode/lensnode"):
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
            [os.sys.executable, str(self.other / "devctl"), "list", "--verbose"],
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

    def test_legacy_test_metadata_does_not_pin_a_stack_to_its_previous_source(self):
        """After a source switch, test history must not duplicate the live project identity."""
        self.manager.update(dict(name="other", path=str(self.other.resolve()), project="manual-dev", port=8000))
        fixture = self.compose_fixture(path=self.root)
        with patch.object(self.manager, "run", side_effect=["api-id", json.dumps([fixture])]):
            records = self.manager.discover()
        self.assertEqual(self.manager.resolve("manual-dev", records)["path"], str(self.root.resolve()))
        self.assertNotIn("project", self.manager.resolve(str(self.other), records))

    def test_production_and_unrelated_mounts_are_excluded(self):
        """Compose labels alone must not include production or another repository."""
        fixtures = [self.compose_fixture(production=True), self.compose_fixture(path=Path("/unrelated"))]
        with patch.object(self.manager, "run", side_effect=["api-id", json.dumps(fixtures)]):
            records = self.manager.discover()
        self.assertTrue(all(r["status"] == "stopped" for r in records))

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

    def switch_fixture(self):
        """Supply a rendered single stack and running infrastructure for lifecycle checks."""
        api = self.compose_fixture(path=self.root)
        api["Config"]["Image"] = "backend:dev"
        containers = [api]
        services = {
            "backend-api": dict(
                container_name="api",
                image="backend:dev",
                volumes=[dict(type="bind", source=str(self.other.resolve() / "backend"), target="/opt/backend")],
            )
        }
        for service in ("postgresql", "redis"):
            container = self.compose_fixture(path=self.root)
            container["Id"] = service
            container["Name"] = "/" + service
            container["Config"]["Labels"]["com.docker.compose.service"] = service
            container["Config"]["Image"] = service + ":dev"
            container["Mounts"] = []
            containers.append(container)
            services[service] = dict(container_name=service, image=service + ":dev")
        services["lensnode"] = dict(image="lensnode:dev")
        services["frontend"] = dict(image="frontend:dev")
        stack = dict(
            name="main", path=str(self.root.resolve()), project="manual-dev", external=True, containers=containers
        )
        target = dict(name="other", path=str(self.other.resolve()), containers=[])
        after = dict(stack, path=target["path"])
        return stack, target, after, dict(services=services)

    def test_switch_recreates_only_applications_and_selects_target_source(self):
        """Single-stack switching uses the config home, no build, and no infrastructure up."""
        stack, target, after, config = self.switch_fixture()
        with patch.object(self.manager, "discover", side_effect=[[stack, target], [after]]), patch.object(
            self.manager, "run", side_effect=[json.dumps(config), "image", "image", "image", "", ""]
        ) as run, patch.object(self.manager, "retire_parallel"), patch.object(self.manager, "stop"), patch(
            "scripts.devctl.display_environments"
        ):
            self.manager.switch(str(self.other))
        calls = run.call_args_list
        self.assertEqual(calls[0].kwargs["env"]["WORKTREE_DIR"], str(self.other.resolve()))
        self.assertEqual(calls[0].args[0][calls[0].args[0].index("--project-directory") + 1], str(self.root.resolve()))
        for call in calls[-2:]:
            self.assertIn("--no-deps", call.args[0])
            self.assertIn("--no-build", call.args[0])
            self.assertNotIn("postgresql", call.args[0])
            self.assertNotIn("redis", call.args[0])

    def test_switch_rejects_changed_ports_before_mutation(self):
        """A source switch must not silently replace the stack's published ports."""
        stack, target, after, config = self.switch_fixture()
        config["services"]["backend-api"]["ports"] = [dict(target=8000, published=18000)]
        with patch.object(self.manager, "discover", return_value=[stack, target]), patch.object(
            self.manager, "run", return_value=json.dumps(config)
        ) as run:
            with self.assertRaisesRegex(ValueError, "changes ports"):
                self.manager.switch(str(self.other))
        self.assertEqual(run.call_count, 1)

    def test_up_uses_the_single_stack_without_build_or_new_database(self):
        """Up and switch use the same project, shared configuration and application services."""
        stack, target, after, config = self.switch_fixture()
        with patch.object(self.manager, "discover", side_effect=[[stack, target], [after]]), patch.object(
            self.manager, "run", side_effect=[json.dumps(config), "image", "image", "image", "", ""]
        ) as run, patch.object(self.manager, "retire_parallel"), patch.object(self.manager, "stop") as stop, patch(
            "scripts.devctl.display_environments"
        ):
            self.manager.up(str(self.other))
        stop.assert_called_once_with(stack)
        commands = [c.args[0] for c in run.call_args_list]
        self.assertFalse(any("build" in c or "createdb" in c or "tag" in c for c in commands))
        self.assertTrue(all("manual-dev" in c for c in commands if "compose" in c))
        self.assertEqual(self.manager.read_state()["single"]["project"], "manual-dev")

    def test_build_is_explicit_and_happens_before_stopping_the_stack(self):
        """A failed build must leave existing services running."""
        stack, target, after, config = self.switch_fixture()
        with patch.object(self.manager, "discover", return_value=[stack, target]), patch.object(
            self.manager, "run", side_effect=[json.dumps(config), subprocess.CalledProcessError(1, ["build"])]
        ) as run, patch.object(self.manager, "retire_parallel") as retire, patch.object(self.manager, "stop") as stop:
            with self.assertRaises(subprocess.CalledProcessError):
                self.manager.up(str(self.other), build=True)
        self.assertEqual(run.call_args.args[0][-4:], ["build", "backend-api", "lensnode", "frontend"])
        retire.assert_not_called()
        stop.assert_not_called()

    def test_up_bootstraps_a_single_stack_without_worktree_allocations(self):
        """First startup uses the standard singleton project and creates no private namespace."""
        stack, target, after, config = self.switch_fixture()
        after["project"] = "sourcelens-dev"
        with patch.object(self.manager, "discover", side_effect=[[target], [after]]), patch.object(
            self.manager, "run", side_effect=[json.dumps(config), "image", "image", "image", "", "", ""]
        ) as run, patch.object(self.manager, "retire_parallel"), patch.object(self.manager, "stop"), patch(
            "scripts.devctl.display_environments"
        ):
            self.manager.up(str(self.other))
        commands = [c.args[0] for c in run.call_args_list]
        self.assertEqual(commands[-3][-2:], ["postgresql", "redis"])
        self.assertEqual(self.manager.read_state()["environments"], {})
        self.assertEqual(self.manager.read_state()["allocations"], {})

    def test_infrastructure_only_stack_is_discovered_after_clean(self):
        """Removing application containers must not hide the original database project."""
        fixture = self.compose_fixture(path=self.root)
        fixture["Config"]["Labels"]["com.docker.compose.service"] = "postgresql"
        fixture["Config"]["Labels"]["com.docker.compose.project.working_dir"] = str(self.root)
        fixture["Mounts"] = []
        with patch.object(self.manager, "run", side_effect=["db-id", json.dumps([fixture])]):
            records = self.manager.discover()
        self.assertEqual(self.manager.singleton(records)["project"], "manual-dev")
        self.assertEqual(self.manager.singleton(records)["status"], "stopped")

    def test_retire_parallel_stops_applications_and_old_infrastructure_but_keeps_data(self):
        """Migration to singleton mode never drops databases or removes volumes."""
        api = self.compose_fixture(project=self.manager.prefix + "-old")
        record = dict(project=self.manager.prefix + "-old", external=False, containers=[api])
        with patch.object(self.manager, "run", side_effect=["", "", "old-pg old-redis", ""]) as run:
            self.manager.retire_parallel([record])
        commands = [c.args[0] for c in run.call_args_list]
        self.assertEqual(commands[0], ["docker", "stop", "api-id"])
        self.assertEqual(commands[1], ["docker", "rm", "api-id"])
        self.assertEqual(commands[-1], ["docker", "stop", "old-pg", "old-redis"])
        self.assertFalse(any("-v" in c or "dropdb" in c for c in commands))

    def test_down_keeps_database_and_redis_containers(self):
        """Pausing a singleton never stops its shared infrastructure."""
        stack, target, after, config = self.switch_fixture()
        with patch.object(self.manager, "run") as run:
            self.manager.stop(stack)
        run.assert_called_once_with(["docker", "stop", "api-id"], capture=True)

    def test_stale_parallel_metadata_does_not_show_old_ports(self):
        """Unstarted worktrees are listed independently of obsolete allocation records."""
        self.manager.update(
            dict(name="old", path=str(self.other.resolve()), project="old-project", port=18081, redis_dbs=[0, 1, 2])
        )
        with patch.object(self.manager, "run", return_value=""):
            record = self.manager.resolve(str(self.other), self.manager.discover())
        self.assertNotIn("project", record)
        self.assertNotIn("port", record)
        self.assertEqual(record["name"], self.other.name)

    def test_missing_local_image_does_not_stop_the_running_stack(self):
        """Missing dependencies report --build before affecting services or data."""
        stack, target, after, config = self.switch_fixture()
        with patch.object(self.manager, "discover", return_value=[stack, target]), patch.object(
            self.manager, "run", side_effect=[json.dumps(config), subprocess.CalledProcessError(1, ["inspect"])]
        ), patch.object(self.manager, "retire_parallel") as retire, patch.object(self.manager, "stop") as stop:
            with self.assertRaisesRegex(ValueError, "--build first"):
                self.manager.up(str(self.other))
        stop.assert_not_called()
        retire.assert_not_called()

    def test_multiple_single_stacks_are_rejected_before_changes(self):
        """Never select an arbitrary database when more than one original stack exists."""
        stack, target, after, config = self.switch_fixture()
        second = dict(stack, project="second-dev")
        with patch.object(self.manager, "discover", return_value=[stack, second]), patch.object(
            self.manager, "run"
        ) as run:
            with self.assertRaisesRegex(ValueError, "Multiple single"):
                self.manager.up(str(self.other))
        run.assert_not_called()

    def test_changed_database_volume_is_rejected_before_stopping_services(self):
        """A shared-data stack must not silently open a different named database volume."""
        stack, target, after, config = self.switch_fixture()
        database = stack["containers"][1]
        database["Mounts"] = [dict(Type="volume", Destination="/var/lib/postgresql/data", Name="existing-db")]
        config["services"]["postgresql"]["volumes"] = [
            dict(type="volume", source="pg", target="/var/lib/postgresql/data")
        ]
        config["volumes"] = dict(pg=dict(name="new-empty-db"))
        with patch.object(self.manager, "discover", return_value=[stack, target]), patch.object(
            self.manager, "run", return_value=json.dumps(config)
        ), patch.object(self.manager, "stop") as stop:
            with self.assertRaisesRegex(ValueError, "retained volume"):
                self.manager.up(str(self.other))
        stop.assert_not_called()

    def test_switch_requires_an_existing_single_stack(self):
        """Switch never creates a second stack when none is present."""
        with patch.object(self.manager, "run", return_value=""):
            with self.assertRaisesRegex(ValueError, "No existing"):
                self.manager.switch(str(self.other))

    def test_test_refuses_wrong_actual_mount(self):
        """An incorrect container mount must never produce a green suite for the registered branch."""
        record = dict(
            name="main", path=str(self.root.resolve()), project="manual-dev", container="api-id", external=True
        )
        inspected = [
            {"Mounts": [{"Type": "bind", "Destination": "/opt/backend", "Source": str(self.other / "backend")}]}
        ]
        with patch.object(self.manager, "run", return_value=json.dumps(inspected)) as run:
            with self.assertRaisesRegex(ValueError, "mounted source differs"):
                self.manager.test(record, ["python", "manage.py", "test"])
        self.assertEqual(run.call_count, 1)

    def test_suite_failure_is_saved_and_propagated(self):
        """Preserve a failing command's exit status and log instead of reporting success."""
        record = dict(
            name="main", path=str(self.root.resolve()), project="manual-dev", container="api-id", external=True
        )
        inspected = [
            {"Mounts": [{"Type": "bind", "Destination": "/opt/backend", "Source": str(self.root / "backend")}]}
        ]
        real_popen = subprocess.Popen
        with patch.object(self.manager, "run", return_value=json.dumps(inspected)), patch(
            "scripts.devctl.subprocess.Popen"
        ) as popen:
            process = popen.return_value
            process.__enter__.return_value.stdout = iter(["FAILED fixture\n"])
            process.__enter__.return_value.wait.return_value = 7
            popen.side_effect = lambda argv, **kwargs: real_popen(argv, **kwargs) if argv[0] == "git" else process
            self.assertEqual(self.manager.test(record, ["python", "manage.py", "test"]), 7)
        saved = self.manager.read_state()["environments"]["main"]["last_test"]
        self.assertEqual(saved["exit_code"], 7)
        self.assertIn("FAILED fixture", Path(saved["log"]).read_text())


if __name__ == "__main__":
    unittest.main()
