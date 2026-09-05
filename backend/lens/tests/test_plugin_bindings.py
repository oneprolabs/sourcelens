import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from lens.execution import execute_answer_run
from lens.models import (
    Assistant,
    AssistantMCP,
    AssistantPluginBinding,
    Connection,
    LensNode,
    MCPServer,
    SecretMaterial,
    SecretVersion,
    Session,
    Skill,
)
from lens.services import (
    build_loaded_mcps,
    build_loaded_plugin_skills,
    build_loaded_plugins,
    create_execution_run,
    dispatch_run_to_lensnode,
    validate_run_dispatch,
)


class AssistantPluginBindingTests(TestCase):
    """Verify Assistant access to reusable Plugin connections."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="plugin-assistant-admin",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.lensnode = LensNode.objects.create(
            name="Plugin node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            available_dirs=[{"path": "/workspace/repo"}],
            tasks=[
                {"name": "general_chat"},
                {"name": "knowledge_qa"},
            ],
        )
        material = SecretMaterial.objects.create(name="GitHub PAT")
        version = SecretVersion(material=material)
        version.set_value("ghp-runtime-secret")
        version.save()
        self.connection = Connection.objects.create(
            name="GitHub readonly",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["HyperBDR/sourcelens"]},
            secret_version=version,
        )
        self.assistant = Assistant.objects.create(
            name="Knowledge Assistant",
            slug="plugin-knowledge-assistant",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[{"path": "/workspace/repo"}],
        )

    @contextmanager
    def plugin_root(self):
        """Install one trusted read-only Plugin manifest for a test."""

        manifest = {
            "key": "github",
            "version": "1.0.0",
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
                            "ref": {"type": "string"},
                        },
                        "required": ["repository", "path"],
                    },
                },
                {
                    "key": "github_search_code",
                    "description": (
                        "Search code in an authorized repository."
                    ),
                    "capability": "repository.read",
                    "side_effect": "none",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "repository": {"type": "string"},
                            "query": {"type": "string"},
                            "max_results": {"type": "integer"},
                        },
                        "required": ["repository", "query"],
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "github"
            path.mkdir(parents=True)
            (path / "plugin.json").write_text(json.dumps(manifest))
            (path / "control.py").write_text("PLUGIN_API_VERSION = 1\n")
            (path / "runtime.py").write_text("PLUGIN_API_VERSION = 1\n")
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                yield

    def test_general_chat_can_use_plugin_tools_without_a_skill(self):
        with self.plugin_root():
            response = self.client.post(
                "/api/lens/assistants/",
                {
                    "name": "GitHub Assistant",
                    "slug": "github-assistant",
                    "lensnode_uuid": str(self.lensnode.uuid),
                    "selected_task": "general_chat",
                    "selected_dirs": [],
                    "plugin_bindings": [
                        {
                            "connection_uuid": str(self.connection.uuid),
                            "tools": ["github_read_file"],
                        }
                    ],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201, response.data)
        assistant = Assistant.objects.get(slug="github-assistant")
        binding = assistant.plugin_bindings.get()
        self.assertEqual(binding.connection, self.connection)
        self.assertEqual(binding.tools, ["github_read_file"])
        self.assertTrue(response.data["plugin_bindings"][0]["all_tools"])
        self.assertNotIn("ghp-runtime-secret", str(response.data))
        self.assertNotIn("encrypted_value", str(response.data))

    def test_direct_binding_uses_all_manifest_tools_when_tools_are_omitted(self):
        with self.plugin_root():
            response = self.client.post(
                "/api/lens/assistants/",
                {
                    "name": "All GitHub Tools Assistant",
                    "slug": "all-github-tools-assistant",
                    "lensnode_uuid": str(self.lensnode.uuid),
                    "selected_task": "general_chat",
                    "selected_dirs": [],
                    "plugin_bindings": [
                        {"connection_uuid": str(self.connection.uuid)}
                    ],
                },
                format="json",
            )

            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(
                slug="all-github-tools-assistant"
            )
            loaded = build_loaded_plugins(assistant)

        self.assertEqual(
            [tool["key"] for tool in loaded[0]["tools"]],
            ["github_read_file", "github_search_code"],
        )

    def test_skill_plugin_requirement_rejects_missing_capability_binding(self):
        skill = Skill.objects.create(
            name="GitHub workflow",
            definition={
                "required_plugins": [
                    {
                        "plugin": "github",
                        "capabilities": ["repository.read"],
                    }
                ]
            },
        )

        with self.plugin_root():
            response = self.client.post(
                "/api/lens/assistants/",
                {
                    "name": "Incomplete GitHub Assistant",
                    "slug": "incomplete-github-assistant",
                    "lensnode_uuid": str(self.lensnode.uuid),
                    "selected_task": "general_chat",
                    "selected_dirs": [],
                    "skill_bindings": [
                        {"skill_uuid": str(skill.uuid), "enabled": True}
                    ],
                    "plugin_bindings": [],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("required_plugins", str(response.data))

    def test_skill_plugin_requirement_accepts_matching_capability_binding(self):
        skill = Skill.objects.create(
            name="GitHub workflow",
            definition={
                "required_plugins": [
                    {
                        "plugin": "github",
                        "capabilities": ["repository.read"],
                    }
                ]
            },
        )

        with self.plugin_root():
            response = self.client.post(
                "/api/lens/assistants/",
                {
                    "name": "Complete GitHub Assistant",
                    "slug": "complete-github-assistant",
                    "lensnode_uuid": str(self.lensnode.uuid),
                    "selected_task": "general_chat",
                    "selected_dirs": [],
                    "skill_bindings": [
                        {"skill_uuid": str(skill.uuid), "enabled": True}
                    ],
                    "plugin_bindings": [
                        {
                            "connection_uuid": str(self.connection.uuid),
                            "tools": ["github_read_file"],
                            "enabled": True,
                        }
                    ],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201, response.data)

    def test_plugin_mcp_adapter_uses_native_plugin_runtime(self):
        with self.plugin_root():
            response = self.client.post(
                "/api/lens/admin/mcp-servers/",
                {
                    "name": "GitHub MCP Adapter",
                    "transport": "plugin",
                    "connection_uuid": str(self.connection.uuid),
                    "tools": ["github_read_file"],
                    "endpoint": "",
                    "config": {},
                    "environment": [],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            response.data["connection_uuid"],
            str(self.connection.uuid),
        )
        self.assertNotIn("ghp-runtime-secret", str(response.data))
        adapter = MCPServer.objects.get(uuid=response.data["uuid"])
        AssistantMCP.objects.create(assistant=self.assistant, mcp=adapter)

        with self.plugin_root():
            loaded_plugins = build_loaded_plugins(self.assistant)

        self.assertEqual(build_loaded_mcps(self.assistant), [])
        self.assertEqual(len(loaded_plugins), 1)
        self.assertEqual(
            loaded_plugins[0]["connection_uuid"],
            str(self.connection.uuid),
        )
        self.assertEqual(
            [tool["key"] for tool in loaded_plugins[0]["tools"]],
            ["github_read_file"],
        )

    def test_plugin_mcp_adapter_rejects_arbitrary_mcp_configuration(self):
        with self.plugin_root():
            response = self.client.post(
                "/api/lens/admin/mcp-servers/",
                {
                    "name": "Unsafe Plugin Adapter",
                    "transport": "plugin",
                    "connection_uuid": str(self.connection.uuid),
                    "tools": ["github_read_file"],
                    "endpoint": "https://mcp.example.com",
                    "config": {
                        "headers": {"Authorization": "Bearer bypass"}
                    },
                    "environment": [],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("config", str(response.data).lower())

    def test_general_chat_accepts_plugin_mcp_adapter_without_skill(self):
        adapter = MCPServer.objects.create(
            name="GitHub MCP Adapter",
            transport=MCPServer.Transport.PLUGIN,
            connection=self.connection,
            tools=["github_read_file"],
        )

        with self.plugin_root():
            response = self.client.post(
                "/api/lens/assistants/",
                {
                    "name": "GitHub MCP Assistant",
                    "slug": "github-mcp-assistant",
                    "lensnode_uuid": str(self.lensnode.uuid),
                    "selected_task": "general_chat",
                    "selected_dirs": [],
                    "mcp_bindings": [
                        {"mcp_uuid": str(adapter.uuid), "enabled": True}
                    ],
                    "plugin_bindings": [],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201, response.data)

    def test_skill_plugin_requirement_accepts_plugin_mcp_adapter(self):
        skill = Skill.objects.create(
            name="GitHub workflow",
            definition={
                "required_plugins": [
                    {
                        "plugin": "github",
                        "capabilities": ["repository.read"],
                    }
                ]
            },
        )
        adapter = MCPServer.objects.create(
            name="GitHub MCP Adapter",
            transport=MCPServer.Transport.PLUGIN,
            connection=self.connection,
            tools=["github_read_file"],
        )

        with self.plugin_root():
            response = self.client.post(
                "/api/lens/assistants/",
                {
                    "name": "GitHub Skill MCP Assistant",
                    "slug": "github-skill-mcp-assistant",
                    "lensnode_uuid": str(self.lensnode.uuid),
                    "selected_task": "general_chat",
                    "selected_dirs": [],
                    "skill_bindings": [
                        {"skill_uuid": str(skill.uuid), "enabled": True}
                    ],
                    "mcp_bindings": [
                        {"mcp_uuid": str(adapter.uuid), "enabled": True}
                    ],
                    "plugin_bindings": [],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201, response.data)

    def test_plugin_mcp_adapter_rejects_duplicate_native_tool_binding(self):
        adapter = MCPServer.objects.create(
            name="GitHub MCP Adapter",
            transport=MCPServer.Transport.PLUGIN,
            connection=self.connection,
            tools=["github_read_file"],
        )

        with self.plugin_root():
            response = self.client.patch(
                f"/api/lens/assistants/{self.assistant.uuid}/",
                {
                    "mcp_bindings": [
                        {"mcp_uuid": str(adapter.uuid), "enabled": True}
                    ],
                    "plugin_bindings": [
                        {
                            "connection_uuid": str(self.connection.uuid),
                            "tools": ["github_read_file"],
                            "enabled": True,
                        }
                    ],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unique", str(response.data).lower())

    def test_binding_rejects_tools_outside_the_manifest(self):
        with self.plugin_root():
            response = self.client.patch(
                f"/api/lens/assistants/{self.assistant.uuid}/",
                {
                    "plugin_bindings": [
                        {
                            "connection_uuid": str(self.connection.uuid),
                            "tools": ["github_delete_repository"],
                        }
                    ]
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("plugin_bindings", response.data)
        self.assertFalse(self.assistant.plugin_bindings.exists())

    def test_binding_rejects_duplicate_tool_names_across_connections(self):
        other = Connection.objects.create(
            name="Other GitHub",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["other/repository"]},
            secret_version=self.connection.secret_version,
        )

        with self.plugin_root():
            response = self.client.patch(
                f"/api/lens/assistants/{self.assistant.uuid}/",
                {
                    "plugin_bindings": [
                        {
                            "connection_uuid": str(self.connection.uuid),
                            "tools": ["github_read_file"],
                        },
                        {
                            "connection_uuid": str(other.uuid),
                            "tools": ["github_read_file"],
                        },
                    ]
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unique", str(response.data).lower())
        self.assertFalse(self.assistant.plugin_bindings.exists())

    def test_binding_rejects_a_disabled_connection(self):
        self.connection.status = Connection.Status.DISABLED
        self.connection.save(update_fields=["status"])

        with self.plugin_root():
            response = self.client.patch(
                f"/api/lens/assistants/{self.assistant.uuid}/",
                {
                    "plugin_bindings": [
                        {
                            "connection_uuid": str(self.connection.uuid),
                            "tools": ["github_read_file"],
                        }
                    ]
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("disabled", str(response.data).lower())

    def test_binding_rejects_inactive_secret_material(self):
        material = self.connection.secret_version.material
        material.status = "disabled"
        material.save(update_fields=["status"])

        with self.plugin_root():
            response = self.client.patch(
                f"/api/lens/assistants/{self.assistant.uuid}/",
                {
                    "plugin_bindings": [
                        {
                            "connection_uuid": str(self.connection.uuid),
                            "tools": ["github_read_file"],
                        }
                    ]
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("secret", str(response.data).lower())

    def test_binding_rejects_an_invalid_connection_uuid(self):
        with self.plugin_root():
            response = self.client.patch(
                f"/api/lens/assistants/{self.assistant.uuid}/",
                {
                    "plugin_bindings": [
                        {
                            "connection_uuid": "not-a-uuid",
                            "tools": ["github_read_file"],
                        }
                    ]
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("plugin_bindings", response.data)

    def test_connection_bound_to_an_assistant_cannot_be_deleted(self):
        AssistantPluginBinding.objects.create(
            assistant=self.assistant,
            connection=self.connection,
            tools=["github_read_file"],
        )

        response = self.client.delete(
            f"/api/lens/admin/connections/{self.connection.uuid}/"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "CONNECTION_IN_USE")
        self.assertTrue(Connection.objects.filter(pk=self.connection.pk).exists())
        detail = self.client.get(
            f"/api/lens/admin/connections/{self.connection.uuid}/"
        )
        self.assertEqual(detail.data["assistant_count"], 1)

    def test_general_chat_plugin_tools_pass_dispatch_validation(self):
        assistant = Assistant.objects.create(
            name="GitHub Chat",
            slug="github-chat",
            lensnode=self.lensnode,
            selected_task="general_chat",
            selected_dirs=[],
        )
        AssistantPluginBinding.objects.create(
            assistant=assistant,
            connection=self.connection,
            tools=["github_read_file"],
        )
        session = Session.objects.create(
            assistant=assistant,
            user=self.user,
        )

        with self.plugin_root():
            run = create_execution_run(
                session,
                "Read the project README",
                enqueue=False,
            )

        validate_run_dispatch(run)

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_run_snapshots_and_dispatches_non_sensitive_tool_definitions(
        self,
        get_channel_layer,
        mock_async_to_sync,
    ):
        del get_channel_layer
        AssistantPluginBinding.objects.create(
            assistant=self.assistant,
            connection=self.connection,
            tools=["github_read_file"],
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )

        with self.plugin_root():
            loaded = build_loaded_plugins(self.assistant)
            run = create_execution_run(
                session,
                "Read the project README",
                enqueue=False,
            )
            dispatch_run_to_lensnode(run, "Read the project README")

        self.assertEqual(run.execution.loaded_plugins, loaded)
        self.assertEqual(loaded[0]["plugin_key"], "github")
        self.assertEqual(loaded[0]["plugin_version"], "1.0.0")
        self.assertEqual(
            loaded[0]["connection_uuid"],
            str(self.connection.uuid),
        )
        self.assertEqual(loaded[0]["tools"][0]["side_effect"], "none")
        self.assertEqual(
            loaded[0]["tools"][0]["capability_family"],
            "plugin",
        )
        self.assertNotIn("ghp-runtime-secret", json.dumps(loaded))
        payload = mock_async_to_sync.return_value.call_args.args[1]["payload"]
        self.assertEqual(payload["loaded_plugins"], loaded)
        self.assertNotIn("ghp-runtime-secret", json.dumps(payload))

    def test_virtual_plugin_skill_has_safe_scope_references(self):
        AssistantPluginBinding.objects.create(
            assistant=self.assistant,
            connection=self.connection,
            tools=["github_read_file"],
        )

        with self.plugin_root():
            skills = build_loaded_plugin_skills(self.assistant)

        self.assertEqual(len(skills), 1)
        skill = skills[0]
        self.assertEqual(skill["skill_kind"], "plugin_virtual")
        self.assertEqual(skill["version"], "1.0.0")
        self.assertEqual(
            skill["definition"]["plugin_version"],
            "1.0.0",
        )
        self.assertEqual(
            skill["definition"]["allowed_scope"]["repositories"],
            ["HyperBDR/sourcelens"],
        )
        self.assertNotIn("ghp-runtime-secret", json.dumps(skill))
        self.assertNotIn("connection_uuid", skill["definition"])

    def test_inactive_secret_material_is_excluded_from_run_tools(self):
        AssistantPluginBinding.objects.create(
            assistant=self.assistant,
            connection=self.connection,
            tools=["github_read_file"],
        )
        material = self.connection.secret_version.material
        material.status = "disabled"
        material.save(update_fields=["status"])

        with self.plugin_root():
            loaded = build_loaded_plugins(self.assistant)

        self.assertEqual(loaded, [])

    @override_settings(
        CHANNEL_LAYERS={
            "default": {
                "BACKEND": "channels.layers.InMemoryChannelLayer",
            }
        }
    )
    def test_worker_refreshes_plugin_snapshot_when_execution_starts(self):
        binding = AssistantPluginBinding.objects.create(
            assistant=self.assistant,
            connection=self.connection,
            tools=["github_read_file"],
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )

        with self.plugin_root():
            run = create_execution_run(
                session,
                "Search the repository",
                enqueue=False,
            )
            self.assertEqual(
                run.execution.loaded_plugins[0]["tools"][0]["key"],
                "github_read_file",
            )
            binding.tools = ["github_search_code"]
            binding.save(update_fields=["tools"])
            execute_answer_run(run, dispatch=False)

        run.execution.refresh_from_db()
        self.assertEqual(
            [tool["key"] for tool in run.execution.loaded_plugins[0]["tools"]],
            ["github_read_file", "github_search_code"],
        )
