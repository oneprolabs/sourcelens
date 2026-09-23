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
from lens.plugins.decisions import (
    resolve_decision_gates,
    validate_decision_gates,
)
from lens.plugins.registry import PluginRegistryError, installed_plugin
from lens.services import (
    build_decision_analyses,
    build_decision_gates,
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


class DecisionGateBindingTests(TestCase):
    """Verify Assistant bindings for Decision Plugin control gates."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="decision-admin",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.lensnode = LensNode.objects.create(
            name="Decision node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            available_dirs=[{"path": "/workspace/repo"}],
            tasks=[
                {"name": "general_chat"},
                {"name": "knowledge_qa"},
            ],
        )
        material = SecretMaterial.objects.create(name="TypeSafe token")
        version = SecretVersion(material=material)
        version.set_value("typesafe-runtime-secret")
        version.save()
        self.connection = Connection.objects.create(
            name="TypeSafe",
            plugin_key="typesafe",
            endpoint="https://api.typesafe.ai",
            allowed_scope={},
            secret_version=version,
        )

    @contextmanager
    def decision_plugin_root(self, decisions=None, tool_exposure="model"):
        """Install one trusted Decision Plugin manifest for a test."""

        tool = {
            "key": "typesafe_noul",
            "description": "Return one bounded probability.",
            "capability": "decision.evaluate",
            "side_effect": "none",
            "input_schema": {
                "type": "object",
                "properties": {
                    "state": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["state", "instructions"],
            },
        }
        if tool_exposure != "model":
            tool["exposure"] = tool_exposure
        manifest = {
            "key": "typesafe",
            "version": "1.4.0",
            "protocol_version": 1,
            "plugin_type": "decision",
            "handlers": {
                "runtime": "python_v1",
                "control": "python_v1",
            },
            "tools": [tool],
            "decisions": (
                decisions
                if decisions is not None
                else [
                    {
                        "key": "search_needed",
                        "mode": "control",
                        "kind": "noul",
                        "applies_to": ["knowledge_qa", "code_analysis"],
                        "tool_keys": ["typesafe_noul"],
                    },
                    {
                        "key": "evidence_requirement",
                        "mode": "control",
                        "kind": "noul",
                        "applies_to": ["general_chat"],
                        "tool_keys": ["typesafe_noul"],
                    },
                ]
            ),
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "typesafe"
            path.mkdir(parents=True)
            (path / "plugin.json").write_text(json.dumps(manifest))
            (path / "control.py").write_text("PLUGIN_API_VERSION = 1\n")
            (path / "runtime.py").write_text("PLUGIN_API_VERSION = 1\n")
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                yield

    def _create(self, slug, task, bindings):
        return self.client.post(
            "/api/lens/assistants/",
            {
                "name": slug,
                "slug": slug,
                "lensnode_uuid": str(self.lensnode.uuid),
                "selected_task": task,
                "selected_dirs": (
                    [{"path": "/workspace/repo"}]
                    if task != "general_chat"
                    else []
                ),
                "plugin_bindings": bindings,
            },
            format="json",
        )

    def test_knowledge_qa_accepts_the_search_needed_gate(self):
        with self.decision_plugin_root():
            response = self._create(
                "decision-knowledge",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {
                            "search_needed": {
                                "threshold": 0.6,
                                "margin": 0.2,
                            }
                        },
                    }
                ],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-knowledge")
            loaded = build_loaded_plugins(assistant)

        gates = loaded[0]["decision_gates"]["search_needed"]
        self.assertEqual(gates["tool_key"], "typesafe_noul")
        self.assertEqual(gates["kind"], "noul")
        self.assertEqual(gates["threshold"], 0.6)
        self.assertEqual(gates["margin"], 0.2)
        self.assertEqual(gates["max_state_chars"], 4000)

    def test_command_carries_frozen_decision_gates(self):
        with self.decision_plugin_root():
            response = self._create(
                "decision-command",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {
                            "search_needed": {"threshold": 0.7}
                        },
                    }
                ],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-command")
            loaded = build_loaded_plugins(assistant)
            gates = build_decision_gates(loaded)

        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["plugin_key"], "typesafe")
        self.assertEqual(gates[0]["plugin_version"], "1.4.0")
        self.assertEqual(
            gates[0]["connection_uuid"],
            str(self.connection.uuid),
        )
        self.assertEqual(
            gates[0]["gates"]["search_needed"]["tool_key"],
            "typesafe_noul",
        )

    def test_internal_decision_tool_still_freezes_gates(self):
        with self.decision_plugin_root(tool_exposure="internal"):
            response = self._create(
                "decision-internal",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {"search_needed": {}},
                    }
                ],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-internal")
            loaded = build_loaded_plugins(assistant)
            gates = build_decision_gates(loaded)

        self.assertEqual(loaded[0]["tools"][0]["exposure"], "internal")
        self.assertEqual(
            gates[0]["gates"]["search_needed"]["tool_key"],
            "typesafe_noul",
        )

    def test_pure_decision_plugin_has_no_model_tools_or_virtual_skill(self):
        with self.decision_plugin_root(tool_exposure="internal"):
            response = self._create(
                "decision-pure",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {"search_needed": {}},
                    }
                ],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-pure")
            loaded = build_loaded_plugins(assistant)
            skills = build_loaded_plugin_skills(
                assistant,
                loaded_plugins=loaded,
            )

        self.assertEqual(
            [tool["exposure"] for tool in loaded[0]["tools"]],
            ["internal"],
        )
        self.assertEqual(skills, [])

    def test_stale_gate_survives_unrelated_assistant_edits(self):
        with self.decision_plugin_root():
            response = self._create(
                "decision-stale",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {"search_needed": {}},
                    }
                ],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-stale")

        with self.decision_plugin_root(decisions=[]):
            response = self.client.patch(
                f"/api/lens/assistants/{assistant.uuid}/",
                {"name": "Renamed"},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        assistant.refresh_from_db()
        self.assertEqual(assistant.name, "Renamed")
        gates = assistant.plugin_bindings.get().decision_gates
        self.assertEqual(set(gates), {"search_needed"})
        self.assertEqual(gates["search_needed"]["threshold"], 0.5)

    def test_binding_without_gates_auto_derives_the_manifest_defaults(self):
        with self.decision_plugin_root():
            response = self._create(
                "decision-plain",
                "knowledge_qa",
                [{"connection_uuid": str(self.connection.uuid)}],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-plain")
            loaded = build_loaded_plugins(assistant)

        # Auto mode enables only the gates whose applies_to matches the
        # Assistant capability, filled from the manifest defaults.
        gates = loaded[0]["decision_gates"]
        self.assertEqual(set(gates), {"search_needed"})
        self.assertEqual(gates["search_needed"]["threshold"], 0.5)
        self.assertEqual(gates["search_needed"]["margin"], 0.1)

    def test_manual_mode_can_disable_every_decision(self):
        with self.decision_plugin_root():
            response = self._create(
                "decision-manual-empty",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_auto": False,
                    }
                ],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-manual-empty")
            loaded = build_loaded_plugins(assistant)

        self.assertNotIn("decision_gates", loaded[0])

    def test_rejects_a_second_decision_plugin_binding(self):
        second = Connection.objects.create(
            name="TypeSafe 2",
            plugin_key="typesafe",
            endpoint="https://api.typesafe.ai",
            allowed_scope={},
            secret_version=self.connection.secret_version,
        )
        with self.decision_plugin_root():
            response = self._create(
                "decision-two",
                "knowledge_qa",
                [
                    {"connection_uuid": str(self.connection.uuid)},
                    {"connection_uuid": str(second.uuid)},
                ],
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("plugin_bindings", response.data)

    def test_decision_plugin_binds_without_a_secret(self):
        secretless = Connection.objects.create(
            name="Laya",
            plugin_key="typesafe",
            endpoint="http://laya:8000",
            allowed_scope={},
        )
        with self.decision_plugin_root():
            response = self._create(
                "decision-secretless",
                "knowledge_qa",
                [{"connection_uuid": str(secretless.uuid)}],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-secretless")
            loaded = build_loaded_plugins(assistant)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["connection_uuid"], str(secretless.uuid))

    def test_general_chat_rejects_the_search_needed_gate(self):
        with self.decision_plugin_root():
            response = self._create(
                "decision-general",
                "general_chat",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {"search_needed": {}},
                    }
                ],
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("plugin_bindings", response.data)

    def test_rejects_an_undeclared_gate(self):
        with self.decision_plugin_root():
            response = self._create(
                "decision-unknown",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {"answer_supported": {}},
                    }
                ],
            )

        self.assertEqual(response.status_code, 400, response.data)

    def test_rejects_gates_from_two_bindings_of_one_plugin(self):
        second = Connection.objects.create(
            name="TypeSafe two",
            plugin_key="typesafe",
            endpoint="https://api.typesafe.ai",
            allowed_scope={},
            secret_version=self.connection.secret_version,
        )
        with self.decision_plugin_root():
            response = self._create(
                "decision-double",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {"search_needed": {}},
                    },
                    {
                        "connection_uuid": str(second.uuid),
                        "decision_gates": {"search_needed": {}},
                    },
                ],
            )

        self.assertEqual(response.status_code, 400, response.data)

    def test_rejects_gates_for_an_integration_plugin(self):
        manifest = {
            "key": "typesafe",
            "version": "1.4.0",
            "protocol_version": 1,
            "handlers": {
                "runtime": "python_v1",
                "control": "python_v1",
            },
            "tools": [],
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "typesafe"
            path.mkdir(parents=True)
            (path / "plugin.json").write_text(json.dumps(manifest))
            (path / "control.py").write_text("PLUGIN_API_VERSION = 1\n")
            (path / "runtime.py").write_text("PLUGIN_API_VERSION = 1\n")
            with override_settings(LENS_PLUGIN_ROOTS=[root]):
                plugin = installed_plugin("typesafe")

        self.assertEqual(plugin.plugin_type, "integration")
        with self.assertRaises(PluginRegistryError):
            validate_decision_gates(plugin, {"search_needed": {}})

    def test_analysis_binding_is_frozen_into_the_command(self):
        decisions = [
            {
                "key": "plan_quality",
                "mode": "analysis",
                "kind": "score",
                "summary": "Score one candidate plan.",
                "rubric": ["weak", "acceptable", "strong"],
                "tool_keys": ["typesafe_noul"],
            }
        ]
        with self.decision_plugin_root(decisions=decisions):
            response = self._create(
                "decision-analysis",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_analyses": {"plan_quality": {}},
                    }
                ],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(slug="decision-analysis")
            loaded = build_loaded_plugins(assistant)
            analyses = build_decision_analyses(loaded)

        frozen = loaded[0]["decision_analyses"]["plan_quality"]
        self.assertEqual(frozen["tool_key"], "typesafe_noul")
        self.assertEqual(frozen["kind"], "score")
        self.assertEqual(
            frozen["rubric"],
            ["weak", "acceptable", "strong"],
        )
        self.assertEqual(len(analyses), 1)
        self.assertEqual(analyses[0]["plugin_version"], "1.4.0")
        self.assertEqual(
            analyses[0]["analyses"]["plan_quality"]["kind"],
            "score",
        )

    def test_removed_gate_is_frozen_as_not_declared(self):
        decisions = [
            {
                "key": "search_needed",
                "mode": "control",
                "kind": "noul",
                "applies_to": ["knowledge_qa"],
                "tool_keys": ["typesafe_noul"],
            }
        ]
        with self.decision_plugin_root(decisions=decisions):
            plugin = installed_plugin("typesafe")
            resolved = resolve_decision_gates(
                plugin,
                {
                    "search_needed": {"threshold": 0.5},
                    "evidence_requirement": {"threshold": 0.5},
                },
            )

        self.assertTrue(resolved["search_needed"]["declared"])
        self.assertFalse(resolved["evidence_requirement"]["declared"])
        self.assertEqual(resolved["evidence_requirement"]["tool_key"], "")

    def test_binding_inherits_control_gate_defaults(self):
        decisions = [
            {
                "key": "search_needed",
                "mode": "control",
                "kind": "noul",
                "applies_to": ["knowledge_qa"],
                "tool_keys": ["typesafe_noul"],
                "defaults": {
                    "threshold": 0.7,
                    "margin": 0.2,
                    "max_state_chars": 2000,
                },
            }
        ]
        with self.decision_plugin_root(decisions=decisions):
            plugin = installed_plugin("typesafe")
            inherited = validate_decision_gates(
                plugin, {"search_needed": {}}
            )
            overridden = validate_decision_gates(
                plugin,
                {"search_needed": {"threshold": 0.9}},
            )

        self.assertEqual(inherited["search_needed"]["threshold"], 0.7)
        self.assertEqual(inherited["search_needed"]["margin"], 0.2)
        self.assertEqual(inherited["search_needed"]["max_state_chars"], 2000)
        self.assertEqual(overridden["search_needed"]["threshold"], 0.9)
        self.assertEqual(overridden["search_needed"]["margin"], 0.2)

    def test_rejects_an_unrankable_analysis(self):
        decisions = [
            {
                "key": "plan_quality",
                "mode": "analysis",
                "kind": "choice",
                "target_option": "good",
                "tool_keys": ["typesafe_noul"],
            }
        ]
        with self.decision_plugin_root(decisions=decisions):
            response = self._create(
                "decision-unrankable",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_analyses": {"plan_quality": {}},
                    }
                ],
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("plugin_bindings", response.data)

    def test_rejects_an_undeclared_analysis(self):
        with self.decision_plugin_root():
            response = self._create(
                "decision-missing-analysis",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_analyses": {"plan_quality": {}},
                    }
                ],
            )

        self.assertEqual(response.status_code, 400, response.data)

    def test_rejects_gates_and_analyses_from_two_bindings(self):
        second = Connection.objects.create(
            name="TypeSafe three",
            plugin_key="typesafe",
            endpoint="https://api.typesafe.ai",
            allowed_scope={},
            secret_version=self.connection.secret_version,
        )
        decisions = [
            {
                "key": "search_needed",
                "mode": "control",
                "kind": "noul",
                "applies_to": ["knowledge_qa"],
                "tool_keys": ["typesafe_noul"],
            },
            {
                "key": "plan_quality",
                "mode": "analysis",
                "kind": "score",
                "rubric": ["weak", "strong"],
                "tool_keys": ["typesafe_noul"],
            },
        ]
        with self.decision_plugin_root(decisions=decisions):
            response = self._create(
                "decision-mixed",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_gates": {"search_needed": {}},
                    },
                    {
                        "connection_uuid": str(second.uuid),
                        "decision_analyses": {"plan_quality": {}},
                    },
                ],
            )

        self.assertEqual(response.status_code, 400, response.data)

    def test_choice_analysis_binding_is_frozen_with_its_target(self):
        decisions = [
            {
                "key": "plan_quality",
                "mode": "analysis",
                "kind": "choice",
                "rubric": ["weak", "strong"],
                "target_option": "strong",
                "tool_keys": ["typesafe_noul"],
            }
        ]
        with self.decision_plugin_root(decisions=decisions):
            response = self._create(
                "decision-choice-analysis",
                "knowledge_qa",
                [
                    {
                        "connection_uuid": str(self.connection.uuid),
                        "decision_analyses": {"plan_quality": {}},
                    }
                ],
            )
            self.assertEqual(response.status_code, 201, response.data)
            assistant = Assistant.objects.get(
                slug="decision-choice-analysis"
            )
            loaded = build_loaded_plugins(assistant)

        frozen = loaded[0]["decision_analyses"]["plan_quality"]
        self.assertEqual(frozen["tool_key"], "typesafe_noul")
        self.assertEqual(frozen["kind"], "choice")
        self.assertEqual(frozen["target_option"], "strong")
        self.assertEqual(frozen["rubric"], ["weak", "strong"])
