import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from lens.plugins.registry import (
    PluginRegistryError,
    discover_plugins,
    installed_plugin,
    latest_plugin,
)
from rest_framework.test import APIClient

User = get_user_model()


class PluginRegistryTests(TestCase):
    """Verify trusted plugin manifest discovery."""

    def test_discovers_plugin_from_flat_key_directory(self):
        manifest = {
            "key": "github",
            "version": "1.0.0",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
        }
        with tempfile.TemporaryDirectory() as root:
            plugin_dir = Path(root) / "github"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "plugin.json").write_text(json.dumps(manifest))
            (plugin_dir / "control.py").write_text("# test entrypoint\n")
            (plugin_dir / "runtime.py").write_text("# test entrypoint\n")
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                plugins = discover_plugins()

        self.assertEqual([plugin.key for plugin in plugins], ["github"])
        self.assertEqual(plugins[0].version, "1.0.0")

    def test_duplicate_configured_root_is_scanned_once(self):
        with tempfile.TemporaryDirectory() as root:
            plugin_dir = Path(root) / "github"
            plugin_dir.mkdir(parents=True)
            with override_settings(LENS_PLUGIN_ROOTS=[root, root]):
                with patch(
                    "lens.plugins.registry._load_plugin",
                    return_value=SimpleNamespace(
                        key="github",
                        version="1.0.0",
                    ),
                ) as load_plugin:
                    plugins = discover_plugins()

        self.assertEqual(len(plugins), 1)
        load_plugin.assert_called_once()

    def _write_manifest(self, root, manifest):
        manifest.setdefault("version", "1.0.0")
        path = Path(root) / "github"
        path.mkdir(parents=True)
        (path / "plugin.json").write_text(json.dumps(manifest))
        (path / "control.py").write_text("# test entrypoint\n")
        (path / "runtime.py").write_text("# test entrypoint\n")

    def test_resolves_the_installed_version(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(
                root,
                {
                    "key": "github",
                    "version": "1.0.0",
                    "protocol_version": 1,
                    "handlers": {
                        "runtime": "python_v1",
                        "datasource": "python_v1",
                    },
                },
            )
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                plugins = discover_plugins()
                exact = installed_plugin("github", "1.0.0")
                latest = latest_plugin("github")

        self.assertEqual(
            [(plugin.key, plugin.version) for plugin in plugins],
            [("github", "1.0.0")],
        )
        self.assertEqual(exact.version, "1.0.0")
        self.assertEqual(latest.version, "1.0.0")

    def test_accepts_modified_plugin_without_persisted_release_digest(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(
                root,
                {
                    "key": "github",
                    "version": "1.0.0",
                    "protocol_version": 1,
                    "display_name": "Original",
                    "handlers": {
                        "runtime": "python_v1",
                        "datasource": "python_v1",
                    },
                },
            )
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                self.assertEqual(
                    installed_plugin("github").display_name,
                    "Original",
                )
                manifest_path = Path(root) / "github" / "plugin.json"
                manifest = json.loads(manifest_path.read_text())
                manifest["display_name"] = "Updated"
                manifest_path.write_text(json.dumps(manifest))

                plugin = installed_plugin("github")

        self.assertEqual(plugin.display_name, "Updated")

    def test_rejects_manifest_key_that_differs_from_directory(self):
        manifest = {
            "key": "gitlab",
            "version": "1.0.0",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "github"
            path.mkdir(parents=True)
            (path / "plugin.json").write_text(json.dumps(manifest))
            (path / "control.py").write_text("# test entrypoint\n")
            (path / "runtime.py").write_text("# test entrypoint\n")
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(
                    PluginRegistryError,
                    "directory identity",
                ):
                    discover_plugins()

    def test_bundled_github_plugin_is_discoverable(self):
        plugin = installed_plugin("github")

        self.assertEqual(plugin.key, "github")
        self.assertEqual(plugin.version, "1.0.0")
        self.assertEqual(plugin.display_name, "GitHub")
        self.assertEqual(plugin.capability_family, "plugin")
        self.assertEqual(plugin.icon, "assets/icon.svg")
        self.assertEqual(plugin.datasource_source_type, "git")
        self.assertEqual(plugin.connection_schema["type"], "object")
        self.assertEqual(plugin.datasource_schema["type"], "object")
        self.assertEqual(
            plugin.datasource_schema["properties"]["repositories"]["resource"],
            "repositories",
        )
        self.assertIn(
            "shared by all repositories",
            plugin.datasource_schema["properties"]["branch"]["description"],
        )
        self.assertEqual(
            plugin.connection_schema["properties"]["repositories"]["write_to"],
            "allowed_scope.repositories",
        )
        self.assertEqual(
            plugin.assistant_guidance["topics"][0]["key"],
            "repository",
        )
        self.assertEqual(plugin.tools[0].capability_family, "plugin")

    def test_rejects_guidance_topic_tool_not_in_manifest(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
            "tools": [
                {
                    "key": "github_read_file",
                    "description": "Read one file.",
                    "capability": "repository.read",
                    "side_effect": "none",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                }
            ],
            "assistant_guidance": {
                "topics": [
                    {
                        "key": "repository",
                        "summary": "Read repository files.",
                        "tool_keys": ["github_missing_tool"],
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(
                    PluginRegistryError,
                    "guidance topic tools",
                ):
                    discover_plugins()

    def test_rejects_unknown_plugin_capability_family(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "capability_family": "mcp",
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(
                    PluginRegistryError,
                    "capability family",
                ):
                    discover_plugins()

    def test_accepts_bounded_resource_ids_and_field_dependencies(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
            "datasource_schema": {
                "type": "object",
                "properties": {
                    "repository": {
                        "type": "string",
                        "format": "provider-resource",
                        "resource": "repositories",
                    },
                    "branch": {
                        "type": "string",
                        "format": "provider-resource-option",
                        "resource": "branches",
                        "depends_on": "repository",
                    },
                },
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                plugin = discover_plugins()[0]

        self.assertEqual(
            plugin.datasource_schema["properties"]["branch"],
            {
                "type": "string",
                "title": "branch",
                "format": "provider-resource-option",
                "resource": "branches",
                "depends_on": "repository",
            },
        )

    def test_rejects_unsafe_resource_identifier(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
            "datasource_schema": {
                "type": "object",
                "properties": {
                    "repository": {
                        "type": "string",
                        "format": "provider-resource",
                        "resource": "../repositories",
                    }
                },
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(
                    PluginRegistryError,
                    "resource",
                ):
                    discover_plugins()

    def test_rejects_unknown_resource_field_dependency(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
            "datasource_schema": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "format": "provider-resource-option",
                        "resource": "branches",
                        "depends_on": "repository",
                    }
                },
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(
                    PluginRegistryError,
                    "dependency",
                ):
                    discover_plugins()

    def test_discovers_a_supported_installed_plugin(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                plugins = discover_plugins()

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].key, "github")
        self.assertEqual(plugins[0].runtime_handler, "python_v1")

    def test_rejects_a_manifest_without_a_semantic_version(self):
        manifest = {
            "key": "github",
            "version": "latest",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(
                    PluginRegistryError,
                    "plugin version",
                ):
                    discover_plugins()

    def test_rejects_a_manifest_with_an_unapproved_handler(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "os.system",
                "datasource": "python_v1",
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(PluginRegistryError, "handler"):
                    discover_plugins()

    def test_rejects_a_manifest_outside_its_directory_identity(self):
        manifest = {
            "key": "gitlab",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(
                    PluginRegistryError, "directory"
                ):
                    discover_plugins()

    def test_admin_can_list_installed_plugins_without_handlers_or_paths(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
        }
        admin = User.objects.create_user("plugin-admin", is_staff=True)
        client = APIClient()
        client.force_authenticate(admin)
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                response = client.get("/api/lens/admin/plugins/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    "key": "github",
                    "version": "1.0.0",
                    "protocol_version": 1,
                    "capability_family": "plugin",
                    "display_name": "github",
                    "description": "",
                    "assistant_guidance": {
                        "summary": "",
                        "when_to_use": [],
                        "topics": [],
                    },
                    "icon_url": None,
                    "datasource_source_type": "git",
                    "datasource": {
                        "key": "default",
                        "display_name": "Datasource",
                        "description": "",
                        "source_type": "git",
                        "config_schema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False,
                        },
                        "resources": [],
                        "runtime": {
                            "supports_incremental": False,
                            "supports_cancel": True,
                            "output": "workspace",
                        },
                    },
                }
            ],
        )

    def test_admin_can_list_read_only_plugin_tools(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
            "tools": [
                {
                    "key": "github_read_file",
                    "description": (
                        "Read a file from an authorized repository."
                    ),
                    "capability": "repository.read",
                    "side_effect": "none",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "repository": {"type": "string"},
                            "path": {"type": "string"},
                        },
                        "required": ["repository", "path"],
                    },
                }
            ],
        }
        admin = User.objects.create_user("tools-admin", is_staff=True)
        client = APIClient()
        client.force_authenticate(admin)
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                response = client.get("/api/lens/admin/plugins/github/tools/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["key"], "github_read_file")
        self.assertEqual(response.data[0]["capability_family"], "plugin")
        self.assertEqual(response.data[0]["side_effect"], "none")

    def test_admin_can_read_safe_manifest_configuration_schema(self):
        admin = User.objects.create_user("manifest-admin", is_staff=True)
        client = APIClient()
        client.force_authenticate(admin)

        response = client.get("/api/lens/admin/plugins/github/manifest/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["key"], "github")
        self.assertEqual(response.data["capability_family"], "plugin")
        self.assertEqual(response.data["display_name"], "GitHub")
        self.assertIn("assistant_guidance", response.data)
        self.assertEqual(
            response.data["icon_url"],
            "/api/lens/admin/plugins/github/icon/",
        )
        self.assertIn("connection_schema", response.data)
        self.assertIn("datasource_schema", response.data)
        self.assertEqual(
            response.data["datasource"]["key"],
            "github_repository",
        )
        self.assertTrue(
            response.data["datasource"]["runtime"]["supports_incremental"]
        )
        self.assertNotIn("handlers", response.data)
        self.assertNotIn("path", response.data)

    def test_admin_can_read_the_bundled_plugin_icon(self):
        admin = User.objects.create_user("icon-admin", is_staff=True)
        client = APIClient()
        client.force_authenticate(admin)

        response = client.get("/api/lens/admin/plugins/github/icon/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_rejects_an_icon_outside_the_plugin_package(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "icon": "../icon.svg",
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(PluginRegistryError, "icon"):
                    discover_plugins()

    def test_rejects_a_mutating_or_unknown_plugin_tool(self):
        manifest = {
            "key": "github",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "datasource": "python_v1",
            },
            "tools": [
                {
                    "key": "github_raw_exec",
                    "description": "Execute a command.",
                    "capability": "repository.write",
                    "side_effect": "write",
                    "input_schema": {"type": "object"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            self._write_manifest(root, manifest)
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                with self.assertRaisesMessage(PluginRegistryError, "tool"):
                    discover_plugins()
