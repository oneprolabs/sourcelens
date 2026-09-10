import hashlib
import io
import json
import tempfile
import threading
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from accounts.models import Role
from agentcore_metering.adapters.django.models import LLMConfig, LLMUsage
from agentcore_task.adapters.django.models import TaskExecution
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from lens.datasource_services import (
    DataSourceDispatchError,
)
from lens.datasource_services import (
    test_datasource_connection as run_datasource_connection_test,
)
from lens.lensnode_auth import hash_lensnode_token
from lens.models import (
    Assistant,
    AssistantAccess,
    AssistantMCP,
    AssistantSkill,
    Connection,
    CredentialLease,
    DataSource,
    DataSourceCredential,
    EnvironmentVariableSet,
    ExecutionSnapshot,
    GlobalSetting,
    LensNode,
    MCPServer,
    MessageAttachment,
    PluginInvocation,
    Run,
    RunExecution,
    RunStep,
    ScheduledTask,
    Session,
    SecretMaterial,
    SecretVersion,
    SharedQA,
    Skill,
)
from lens.serializers import (
    AssistantSerializer,
    RunCreateSerializer,
    SessionCreateSerializer,
    SessionSerializer,
    validate_retrieval_policy,
    validate_retrieval_scope,
)
from lens.routing_descriptions import build_routing_description
from lens.services import (
    LensNodeDispatchError,
    append_lensnode_output,
    build_loaded_mcps,
    build_loaded_skills,
    create_execution_run,
    resolve_loaded_mcp_environment,
    resolve_loaded_skill_environment,
    validate_run_dispatch,
)
from lens.skill_packages import package_zip_bytes
from lens.tasks import (
    SourceSyncBusy,
    acquire_datasource_lock,
    complete_datasource_sync_task,
    release_datasource_lock,
)
from lens.views.assistants import AssistantViewSet
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
}

TEST_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}


async def _collect_async_stream(streaming_content, limit=None):
    """Collect bytes from an async streaming response."""

    chunks = []
    count = 0
    async for chunk in streaming_content:
        chunks.append(chunk)
        count += 1
        if limit is not None and count >= limit:
            break
    return b"".join(chunks)


def collect_stream(streaming_content, limit=None):
    """Collect bytes from sync or async streaming response content."""

    if hasattr(streaming_content, "__aiter__"):
        return async_to_sync(_collect_async_stream)(streaming_content, limit)

    chunks = []
    for count, chunk in enumerate(streaming_content, start=1):
        chunks.append(chunk)
        if limit is not None and count >= limit:
            break
    return b"".join(chunks)


def bearer_header(user):
    """Return an Authorization header for native Django streaming views."""

    return f"Bearer {AccessToken.for_user(user)}"


def skill_zip_upload(
    name,
    body,
    environment=None,
    api=None,
    transforms=None,
    required_plugins=None,
    package_files=None,
):
    """Return an uploaded zip containing one SKILL.md."""

    buffer = io.BytesIO()
    skill_md = (
        "---\n"
        f"name: {name}\n"
        f"description: {name} description\n"
        "---\n"
        f"{body}\n"
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{name}/SKILL.md", skill_md)
        for path, content in (package_files or {}).items():
            archive.writestr(f"{name}/{path}", content)
        if any(
            value is not None
            for value in (environment, api, transforms, required_plugins)
        ):
            config = {}
            if environment is not None:
                config["environment"] = environment
            if api is not None:
                config["api"] = api
            if transforms is not None:
                config["transforms"] = transforms
            if required_plugins is not None:
                config["required_plugins"] = required_plugins
            archive.writestr(
                f"{name}/sourcelens.json",
                json.dumps(config),
            )
    buffer.seek(0)
    return SimpleUploadedFile(
        f"{name}.zip",
        buffer.read(),
        content_type="application/zip",
    )


def skill_zip_upload_with_file(
    name,
    file_size,
    compression=zipfile.ZIP_DEFLATED,
):
    """Return a Skill zip containing one generated package file."""

    buffer = io.BytesIO()
    skill_md = (
        "---\n"
        f"name: {name}\n"
        f"description: {name} description\n"
        "---\n"
        "Use the bundled executable.\n"
    )
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        archive.writestr(f"{name}/SKILL.md", skill_md)
        archive.writestr(f"{name}/bin/tool", b"\0" * file_size)
    buffer.seek(0)
    return SimpleUploadedFile(
        f"{name}.zip",
        buffer.read(),
        content_type="application/zip",
    )


def skill_zip_upload_with_member_names(member_names):
    """Return a Skill zip using the supplied archive member names."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in member_names.items():
            archive.writestr(name, content)
    buffer.seek(0)
    return SimpleUploadedFile(
        "winrar-skill.zip",
        buffer.read(),
        content_type="application/zip",
    )


class RetrievalPolicyValidationTests(SimpleTestCase):
    def test_hidden_file_retrieval_options_accept_booleans(self):
        self.assertEqual(
            validate_retrieval_scope({"include_hidden": True}),
            {"include_hidden": True},
        )
        self.assertEqual(
            validate_retrieval_policy({"include_hidden": False}),
            {"include_hidden": False},
        )

    def test_hidden_file_retrieval_options_reject_non_booleans(self):
        for value in ("true", None, 1):
            with self.subTest(scope_value=value):
                with self.assertRaisesRegex(
                    ValidationError,
                    "retrieval_scope.include_hidden must be a boolean",
                ):
                    validate_retrieval_scope({"include_hidden": value})

            with self.subTest(policy_value=value):
                with self.assertRaisesRegex(
                    ValidationError,
                    "settings.retrieval_policy.include_hidden must be " "a boolean",
                ):
                    validate_retrieval_policy({"include_hidden": value})


@override_settings(CACHES=TEST_CACHES, CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
class LensApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="lens-admin",
            email="lens-admin@example.com",
            password="pass12345",
            is_staff=True,
        )
        self.client.force_authenticate(self.user)
        self.lensnode = LensNode.objects.create(
            name="Local LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            available_dirs=[{"path": "/workspace/repo"}],
            tasks=[
                {
                    "name": "knowledge_qa",
                    "description": "Answer code questions",
                },
                {
                    "name": "general_chat",
                    "description": "Chat with bound Skills",
                },
            ],
        )
        self.assistant = Assistant.objects.create(
            name="Code Advisor",
            slug="code-advisor",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[
                {
                    "path": "/workspace/repo",
                    "retrieval_scope": {"include_paths": ["backend/**"]},
                }
            ],
        )
        self.datasource = DataSource.objects.create(
            name="Repo Cache",
            source_type="git",
            lensnode=self.lensnode,
            config={"repo_url": "https://example.com/repo.git"},
            sync_policy={"interval_seconds": 3600},
            target_path="/workspace/repo-cache",
        )
        self.skill = Skill.objects.create(
            name="Code Search",
            package_name="code-search",
            definition={"summary": "Search code"},
        )
        self.mcp = MCPServer.objects.create(
            name="GitHub MCP",
            transport="url",
            endpoint="https://mcp.example.com/github",
        )

    def test_mcp_api_masks_sensitive_config_and_preserves_masked_updates(self):
        self.mcp.config = {
            "api_key": "mcp-secret-key",
            "region": "us-east-1",
            "headers": {
                "Authorization": "Bearer mcp-secret-token",
                "X-Client": "sourcelens",
            },
        }
        self.mcp.save(update_fields=["config"])

        response = self.client.get(f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["config"]["api_key"], "********")
        self.assertEqual(
            response.data["config"]["headers"]["Authorization"],
            "********",
        )
        self.assertEqual(response.data["config"]["region"], "us-east-1")
        self.assertNotIn("mcp-secret", str(response.data))

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {
                "config": {
                    "api_key": "",
                    "region": "eu-west-1",
                    "headers": {
                        "Authorization": "********",
                        "X-Client": "updated-client",
                    },
                }
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.mcp.refresh_from_db()
        self.assertEqual(self.mcp.config["api_key"], "mcp-secret-key")
        self.assertEqual(
            self.mcp.config["headers"]["Authorization"],
            "Bearer mcp-secret-token",
        )
        self.assertEqual(self.mcp.config["region"], "eu-west-1")
        self.assertEqual(
            self.mcp.config["headers"]["X-Client"],
            "updated-client",
        )
        self.assertEqual(response.data["config"]["api_key"], "********")
        self.assertNotIn("mcp-secret", str(response.data))

    def test_mcp_api_preserves_non_secret_token_settings(self):
        self.mcp.config = {
            "github_token": "secret",
            "max_tokens": 100,
            "token_limit": 200,
            "tokenizer": "cl100k_base",
        }
        self.mcp.save(update_fields=["config"])

        response = self.client.get(f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["config"]["github_token"], "********")
        self.assertEqual(response.data["config"]["max_tokens"], 100)
        self.assertEqual(response.data["config"]["token_limit"], 200)
        self.assertEqual(response.data["config"]["tokenizer"], "cl100k_base")

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {"config": response.data["config"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.mcp.refresh_from_db()
        self.assertEqual(
            self.mcp.config,
            {
                "github_token": "secret",
                "max_tokens": 100,
                "token_limit": 200,
                "tokenizer": "cl100k_base",
            },
        )

    def test_mcp_api_allows_blank_sensitive_fields_in_lists(self):
        config = {"profiles": [{"name": "local", "token": ""}]}

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {"config": config},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["config"], config)
        self.mcp.refresh_from_db()
        self.assertEqual(self.mcp.config, config)

    def test_mcp_api_protects_masked_list_item_identity(self):
        self.mcp.config = {
            "region": "us-east-1",
            "profiles": [
                {"name": "production", "token": "production-secret"},
                {"name": "staging", "token": "staging-secret"},
            ],
        }
        self.mcp.save(update_fields=["config"])

        detail = self.client.get(f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/")
        unchanged = detail.data["config"]
        unchanged["region"] = "eu-west-1"
        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {"config": unchanged},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.mcp.refresh_from_db()
        self.assertEqual(self.mcp.config["region"], "eu-west-1")
        self.assertEqual(
            [item["token"] for item in self.mcp.config["profiles"]],
            ["production-secret", "staging-secret"],
        )

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {
                "config": {
                    "profiles": [
                        {"name": "staging", "token": "********"},
                        {"name": "production", "token": "********"},
                    ]
                }
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.mcp.refresh_from_db()
        self.assertEqual(
            self.mcp.config["profiles"],
            [
                {"name": "production", "token": "production-secret"},
                {"name": "staging", "token": "staging-secret"},
            ],
        )

        self.mcp.config = {
            "profiles": [
                {"name": "production", "api_key": "production-secret"},
                {"name": "staging", "api_key": "staging-secret"},
            ]
        }
        self.mcp.save(update_fields=["config"])
        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {
                "config": {
                    "profiles": [
                        {"name": "staging", "api-key": "********"},
                        {
                            "name": "production",
                            "api-key": "********",
                        },
                    ]
                }
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.mcp.refresh_from_db()
        self.assertEqual(
            self.mcp.config["profiles"],
            [
                {"name": "production", "api_key": "production-secret"},
                {"name": "staging", "api_key": "staging-secret"},
            ],
        )

    def test_mcp_api_preserves_masked_secrets_in_nested_lists(self):
        self.mcp.config = {"groups": [[{"name": "production", "token": "secret"}]]}
        self.mcp.save(update_fields=["config"])

        detail = self.client.get(f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/")
        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {"config": detail.data["config"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.mcp.refresh_from_db()
        self.assertEqual(
            self.mcp.config,
            {"groups": [[{"name": "production", "token": "secret"}]]},
        )

    def test_mcp_api_rejects_mask_for_renamed_sensitive_key(self):
        self.mcp.config = {"api_key": "secret"}
        self.mcp.save(update_fields=["config"])

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {"config": {"access_token": "********"}},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.mcp.refresh_from_db()
        self.assertEqual(self.mcp.config, {"api_key": "secret"})

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {"config": {"auth": "********"}},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.mcp.refresh_from_db()
        self.assertEqual(self.mcp.config, {"api_key": "secret"})

    def test_mcp_api_accepts_environment_declarations(self):
        environment = [
            {
                "name": "GITHUB_TOKEN",
                "description": "GitHub access token",
                "required": True,
                "secret": True,
            }
        ]

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {"environment": environment},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.mcp.refresh_from_db()
        self.assertEqual(self.mcp.environment, environment)
        self.assertEqual(response.data["environment"], environment)

    def test_mcp_api_rejects_undeclared_environment_references(self):
        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {
                "endpoint": "https://${MCP_HOST}/api",
                "environment": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("MCP_HOST", str(response.data))

    def test_mcp_api_preserves_legacy_literal_environment_references(self):
        self.mcp.endpoint = "https://mcp.example.com/${API_VERSION}"
        self.mcp.config = {"template": "${LITERAL}"}
        self.mcp.save(update_fields=["endpoint", "config"])

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {"name": "Legacy Template MCP"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["environment_references"], [])
        AssistantMCP.objects.create(
            assistant=self.assistant,
            mcp=self.mcp,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session, "Use legacy MCP", enqueue=False)

        validate_run_dispatch(run)
        runtime = resolve_loaded_mcp_environment(run.execution.loaded_mcps)

        self.assertEqual(
            runtime[0]["endpoint"],
            "https://mcp.example.com/${API_VERSION}",
        )
        self.assertEqual(runtime[0]["config"], {"template": "${LITERAL}"})
        self.assertTrue(runtime[0]["environment_resolved"])

    def test_mcp_api_validates_references_after_restoring_secrets(self):
        self.mcp.config = {"api_token": "${MCP_TOKEN}"}
        self.mcp.environment = [
            {
                "name": "MCP_TOKEN",
                "required": False,
                "secret": True,
            }
        ]
        self.mcp.save(update_fields=["config", "environment"])

        response = self.client.patch(
            f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/",
            {
                "config": {"api_token": "********"},
                "environment": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("MCP_TOKEN", str(response.data))
        self.mcp.refresh_from_db()
        self.assertEqual(self.mcp.config, {"api_token": "${MCP_TOKEN}"})
        self.assertEqual(len(self.mcp.environment), 1)

    def test_mcp_api_exposes_secret_safe_environment_references(self):
        self.mcp.config = {"headers": {"Authorization": "Bearer ${MCP_TOKEN}"}}
        self.mcp.environment = [
            {
                "name": "MCP_TOKEN",
                "required": False,
                "secret": True,
            }
        ]
        self.mcp.save(update_fields=["config", "environment"])

        response = self.client.get(f"/api/lens/admin/mcp-servers/{self.mcp.uuid}/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["config"]["headers"]["Authorization"],
            "********",
        )
        self.assertEqual(
            response.data["environment_references"],
            ["MCP_TOKEN"],
        )

    def test_assistant_mcp_resolves_environment_without_leaking(self):
        self.mcp.config = {"headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}}
        self.mcp.environment = [
            {
                "name": "GITHUB_TOKEN",
                "description": "GitHub access token",
                "required": True,
                "secret": True,
            }
        ]
        self.mcp.save(update_fields=["config", "environment"])

        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {
                "mcp_bindings": [
                    {
                        "mcp_uuid": str(self.mcp.uuid),
                        "environment_variable_set_name": "GitHub MCP",
                        "environment_values": [
                            {
                                "key": "GITHUB_TOKEN",
                                "value": "runtime-secret",
                            }
                        ],
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("runtime-secret", str(response.data))
        binding = self.assistant.mcp_bindings.get(mcp=self.mcp)
        self.assertEqual(
            binding.environment_variable_set.get_values(),
            {"GITHUB_TOKEN": "runtime-secret"},
        )

        loaded = build_loaded_mcps(self.assistant)
        runtime = resolve_loaded_mcp_environment(loaded)

        self.assertNotIn("runtime-secret", str(loaded))
        self.assertEqual(
            runtime[0]["environment"],
            {"GITHUB_TOKEN": "runtime-secret"},
        )
        self.assertEqual(
            runtime[0]["config"]["headers"]["Authorization"],
            "Bearer runtime-secret",
        )
        self.assertTrue(runtime[0]["environment_resolved"])

    def test_dispatch_preserves_references_inside_mcp_environment_values(self):
        self.mcp.config = {"headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}}
        self.mcp.environment = [
            {
                "name": "GITHUB_TOKEN",
                "required": True,
                "secret": True,
            }
        ]
        self.mcp.save(update_fields=["config", "environment"])
        variable_set = EnvironmentVariableSet.objects.create(
            name="GitHub token with literal reference"
        )
        variable_set.set_values({"GITHUB_TOKEN": "runtime-${HOME}-secret"})
        variable_set.save(update_fields=["encrypted_values"])
        AssistantMCP.objects.create(
            assistant=self.assistant,
            mcp=self.mcp,
            environment_variable_set=variable_set,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session, "Search GitHub", enqueue=False)

        validate_run_dispatch(run)
        runtime = resolve_loaded_mcp_environment(run.execution.loaded_mcps)

        self.assertEqual(
            runtime[0]["config"]["headers"]["Authorization"],
            "Bearer runtime-${HOME}-secret",
        )

    def test_assistant_requires_referenced_optional_mcp_environment(self):
        self.mcp.endpoint = "https://${MCP_TOKEN}/api"
        self.mcp.environment = [
            {
                "name": "MCP_TOKEN",
                "required": False,
                "secret": True,
            }
        ]
        self.mcp.save(update_fields=["endpoint", "environment"])

        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {"mcp_bindings": [{"mcp_uuid": str(self.mcp.uuid)}]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("MCP_TOKEN", str(response.data))

    def test_environment_variable_set_lists_skill_and_mcp_usages(self):
        variable_set = EnvironmentVariableSet.objects.create(
            name="Shared Integration Credentials"
        )
        variable_set.set_values({"TOKEN": "secret-value"})
        variable_set.save(update_fields=["encrypted_values"])
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
            environment_variable_set=variable_set,
        )
        AssistantMCP.objects.create(
            assistant=self.assistant,
            mcp=self.mcp,
            environment_variable_set=variable_set,
        )

        response = self.client.get("/api/lens/admin/environment-variable-sets/")

        self.assertEqual(response.status_code, 200, response.data)
        row = next(
            item
            for item in response.data["results"]
            if item["uuid"] == str(variable_set.uuid)
        )
        self.assertEqual(
            row["usages"],
            [
                {
                    "type": "mcp",
                    "resource_uuid": str(self.mcp.uuid),
                    "resource_name": self.mcp.name,
                    "assistant_uuid": str(self.assistant.uuid),
                    "assistant_name": self.assistant.name,
                },
                {
                    "type": "skill",
                    "resource_uuid": str(self.skill.uuid),
                    "resource_name": self.skill.name,
                    "assistant_uuid": str(self.assistant.uuid),
                    "assistant_name": self.assistant.name,
                },
            ],
        )
        self.assertNotIn("secret-value", str(response.data))

    def test_assistant_create_saves_lensnode_and_bindings(self):
        payload = {
            "name": "API Explorer",
            "description": "Explore API behavior and implementation.",
            "slug": "api-explorer",
            "lensnode_uuid": str(self.lensnode.uuid),
            "selected_task": "knowledge_qa",
            "selected_dirs": [{"path": "/workspace/repo"}],
            "token_budget_profile": "deep",
            "skill_bindings": [
                {
                    "skill_uuid": str(self.skill.uuid),
                    "enabled": True,
                    "load_config": {"mode": "read-only"},
                },
            ],
            "mcp_bindings": [
                {
                    "mcp_uuid": str(self.mcp.uuid),
                    "enabled": True,
                    "load_config": {"stream": True},
                },
            ],
        }

        response = self.client.post(
            "/api/lens/assistants/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant = Assistant.objects.get(slug="api-explorer")
        self.assertEqual(assistant.token_budget_profile, "standard")
        self.assertEqual(response.data["token_budget_profile"], "standard")
        self.assertEqual(assistant.lensnode, self.lensnode)
        self.assertEqual(
            assistant.description,
            "Explore API behavior and implementation.",
        )
        self.assertEqual(response.data["description"], assistant.description)
        self.assertEqual(assistant.selected_task, "knowledge_qa")
        self.assertEqual(assistant.skill_bindings.count(), 1)
        self.assertEqual(assistant.mcp_bindings.count(), 1)
        self.assertIn("Knowledge Q&A", assistant.routing_description)
        self.assertIn(
            "Explore API behavior and implementation.", assistant.routing_description
        )
        self.assertIn("Code Search", assistant.routing_description)
        self.assertIn("GitHub MCP", assistant.routing_description)
        self.assertNotIn("routing_description", response.data)
        self.assertEqual(
            assistant.settings["_model_check"]["agent_model_ref"]["status"],
            "skipped",
        )

    def test_assistant_create_derives_unlimited_budget_from_max_rounds(self):
        response = self.client.post(
            "/api/lens/assistants/",
            {
                "name": "Unlimited Budget",
                "slug": "unlimited-budget",
                "lensnode_uuid": str(self.lensnode.uuid),
                "selected_task": "knowledge_qa",
                "selected_dirs": [{"path": "/workspace/repo"}],
                "agent_rounds": "max",
                "token_budget_profile": "standard",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant = Assistant.objects.get(slug="unlimited-budget")
        self.assertEqual(assistant.token_budget_profile, "unlimited")
        self.assertEqual(response.data["token_budget_profile"], "unlimited")

    def test_assistant_update_derives_budget_from_agent_rounds(self):
        self.assistant.agent_rounds = "balanced"
        self.assistant.token_budget_profile = "standard"
        self.assistant.save(
            update_fields=["agent_rounds", "token_budget_profile"]
        )

        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {
                "agent_rounds": "max",
                "token_budget_profile": "standard",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.agent_rounds, "max")
        self.assertEqual(self.assistant.token_budget_profile, "unlimited")
        self.assertEqual(response.data["token_budget_profile"], "unlimited")

    def test_assistant_update_saves_description(self):
        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {"description": "Updated assistant description."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assistant.refresh_from_db()
        self.assertEqual(
            self.assistant.description,
            "Updated assistant description.",
        )
        self.assertEqual(
            response.data["description"],
            "Updated assistant description.",
        )
        self.assertIn(
            "Updated assistant description.",
            self.assistant.routing_description,
        )

    def test_assistant_model_save_refreshes_routing_description(self):
        """Direct model saves keep the routing synopsis current."""

        self.assistant.description = "Updated outside the API serializer."
        self.assistant.save(update_fields=["description"])
        self.assistant.refresh_from_db()

        self.assertIn(
            "Updated outside the API serializer.",
            self.assistant.routing_description,
        )

    def test_resource_changes_refresh_routing_description(self):
        """Routing descriptions follow bound resource availability."""

        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
        )
        AssistantMCP.objects.create(
            assistant=self.assistant,
            mcp=self.mcp,
        )
        self.assistant.refresh_from_db()
        self.assertIn(self.skill.name, self.assistant.routing_description)
        self.assertIn(self.mcp.name, self.assistant.routing_description)

        self.skill.name = "Renamed Code Search"
        self.skill.save(update_fields=["name"])
        self.mcp.enabled = False
        self.mcp.save(update_fields=["enabled"])

        self.assistant.refresh_from_db()
        self.assertIn("Renamed Code Search", self.assistant.routing_description)
        self.assertNotIn(self.mcp.name, self.assistant.routing_description)

    def test_routing_description_uses_the_run_answer_language(self):
        """Smart-routing metadata follows the current Run language."""

        spanish = build_routing_description(self.assistant, "es")
        chinese = build_routing_description(self.assistant, "zh-CN")

        self.assertIn("Capacidad: Preguntas y respuestas de conocimiento.", spanish)
        self.assertIn("能力：知识库问答。", chinese)

    def test_assistant_serializer_rejects_non_boolean_hidden_options(self):
        scope_serializer = AssistantSerializer(
            self.assistant,
            data={
                "selected_dirs": [
                    {
                        "path": "/workspace/repo",
                        "retrieval_scope": {"include_hidden": "true"},
                    }
                ]
            },
            partial=True,
        )
        policy_serializer = AssistantSerializer(
            self.assistant,
            data={
                "settings": {
                    "retrieval_policy": {"include_hidden": "false"},
                }
            },
            partial=True,
        )

        self.assertFalse(scope_serializer.is_valid())
        self.assertFalse(policy_serializer.is_valid())
        self.assertIn("include_hidden", str(scope_serializer.errors))
        self.assertIn("include_hidden", str(policy_serializer.errors))

    def test_assistant_archive_moves_it_to_archived_list(self):
        response = self.client.post(
            f"/api/lens/assistants/{self.assistant.uuid}/archive/",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "archived")
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.status, "archived")

        active_response = self.client.get("/api/lens/assistants/")
        active_slugs = [
            assistant["slug"] for assistant in active_response.data["results"]
        ]
        self.assertNotIn(self.assistant.slug, active_slugs)

        archived_response = self.client.get(
            "/api/lens/assistants/",
            {"archived": "true"},
        )
        archived_slugs = [
            assistant["slug"] for assistant in archived_response.data["results"]
        ]
        self.assertIn(self.assistant.slug, archived_slugs)

    def test_assistant_restore_returns_it_to_active_list(self):
        self.assistant.status = "archived"
        self.assistant.save(update_fields=["status"])

        response = self.client.post(
            f"/api/lens/assistants/{self.assistant.uuid}/restore/",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "active")
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.status, "active")
        active_slugs = [
            assistant["slug"]
            for assistant in self.client.get("/api/lens/assistants/").data["results"]
        ]
        self.assertIn(self.assistant.slug, active_slugs)

    def test_assistant_list_uses_compact_rows_but_retrieve_has_bindings(self):
        """Keep list payloads small while retaining the edit contract."""

        AssistantSkill.objects.create(assistant=self.assistant, skill=self.skill)
        AssistantMCP.objects.create(assistant=self.assistant, mcp=self.mcp)

        list_response = self.client.get("/api/lens/assistants/")
        row = list_response.data["results"][0]

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(row["skill_summary"], {"total": 1, "enabled": 1})
        self.assertEqual(row["mcp_summary"], {"total": 1, "enabled": 1})
        self.assertNotIn("skill_bindings", row)
        self.assertNotIn("mcp_bindings", row)
        self.assertNotIn("access_grants", row)
        self.assertNotIn("settings", row)

        detail_response = self.client.get(
            f"/api/lens/assistants/{self.assistant.uuid}/"
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(len(detail_response.data["skill_bindings"]), 1)
        self.assertEqual(len(detail_response.data["mcp_bindings"]), 1)

    def test_assistant_status_cannot_bypass_lifecycle_actions(self):
        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {"status": "archived"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.status, Assistant.Status.ACTIVE)

    def test_assistant_delete_is_not_available(self):
        response = self.client.delete(
            f"/api/lens/assistants/{self.assistant.uuid}/",
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Assistant.objects.filter(uuid=self.assistant.uuid).exists())

    def test_assistant_create_saves_workspace_guide_context(self):
        payload = {
            "name": "Workspace Aware",
            "slug": "workspace-aware",
            "lensnode_uuid": str(self.lensnode.uuid),
            "selected_task": "knowledge_qa",
            "selected_dirs": [{"path": "/workspace/repo"}],
            "workspace_guide": {
                "enabled": True,
                "content": "- repo is the primary application repository.",
            },
        }

        response = self.client.post(
            "/api/lens/assistants/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant = Assistant.objects.get(slug="workspace-aware")
        self.assertEqual(
            assistant.workspace_guide,
            "- repo is the primary application repository.",
        )
        self.assertFalse(
            assistant.skill_bindings.filter(
                skill__kind="workspace_guide",
            ).exists()
        )
        self.assertIn(
            "repo is the primary application repository",
            response.data["workspace_guide"]["content"],
        )
        self.assertTrue(response.data["workspace_guide"]["enabled"])

    def test_assistant_update_clears_workspace_guide_context(self):
        create_response = self.client.post(
            "/api/lens/assistants/",
            {
                "name": "Workspace Aware",
                "slug": "workspace-aware",
                "lensnode_uuid": str(self.lensnode.uuid),
                "selected_task": "knowledge_qa",
                "selected_dirs": [{"path": "/workspace/repo"}],
                "workspace_guide": {
                    "enabled": True,
                    "content": "- repo is primary.",
                },
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)

        response = self.client.patch(
            f"/api/lens/assistants/{create_response.data['uuid']}/",
            {
                "workspace_guide": {
                    "enabled": False,
                    "content": "",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        assistant = Assistant.objects.get(uuid=create_response.data["uuid"])
        self.assertEqual(assistant.workspace_guide, "")

    def test_assistant_saves_workspace_guide_as_assistant_context(self):
        response = self.client.post(
            "/api/lens/assistants/",
            {
                "name": "General Chat Guide",
                "slug": "general-chat-guide",
                "lensnode_uuid": str(self.lensnode.uuid),
                "selected_task": "general_chat",
                "skill_bindings": [
                    {"skill_uuid": str(self.skill.uuid)},
                ],
                "workspace_guide": {
                    "enabled": True,
                    "content": "Use the engineering workspace guide.",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant = Assistant.objects.get(slug="general-chat-guide")
        self.assertEqual(
            assistant.workspace_guide,
            "Use the engineering workspace guide.",
        )
        self.assertFalse(
            assistant.skill_bindings.filter(
                skill__kind="workspace_guide",
                enabled=True,
            ).exists()
        )
        self.assertEqual(
            response.data["workspace_guide"]["content"],
            "Use the engineering workspace guide.",
        )

    def test_global_setting_accepts_skill_generator_model_ref(self):
        response = self.client.post(
            "/api/lens/admin/global-settings/",
            {
                "key": "lens.skills.generator_model_ref",
                "value": "016d5cf7-2245-4015-b242-d6323e795b58",
                "description": "Skill generator model",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        setting = GlobalSetting.objects.get(
            key="lens.skills.generator_model_ref",
        )
        self.assertEqual(
            setting.value,
            "016d5cf7-2245-4015-b242-d6323e795b58",
        )

    def test_global_setting_accepts_history_budget_object(self):
        response = self.client.post(
            "/api/lens/admin/global-settings/",
            {
                "key": "lens.history_budget",
                "value": {
                    "pairs": 8,
                    "message_chars": 3000,
                    "total_chars": 15000,
                },
                "description": "History replay budget",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        setting = GlobalSetting.objects.get(key="lens.history_budget")
        self.assertEqual(setting.value["pairs"], 8)

    def test_global_setting_rejects_non_positive_history_budget(self):
        response = self.client.post(
            "/api/lens/admin/global-settings/",
            {
                "key": "lens.history_budget",
                "value": {"pairs": -2, "message_chars": 0},
                "description": "History replay budget",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("pairs", str(response.data["value"]))

    def test_global_setting_list_returns_every_setting(self):
        GlobalSetting.objects.bulk_create(
            [
                GlobalSetting(
                    key=f"pagination.test.{index}",
                    value=index,
                )
                for index in range(11)
            ]
        )

        response = self.client.get("/api/lens/admin/global-settings/")

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 11)

    def test_skill_beautify_requires_generator_model(self):
        response = self.client.post(
            "/api/lens/admin/skills/beautify/",
            {"name": "Demo", "content": "draft"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_manual_skill_allows_empty_environment_schema(self):
        response = self.client.post(
            "/api/lens/admin/skills/",
            {
                "name": "Jira Connector",
                "definition": {"content": "Use Jira.", "environment": []},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            response.data["definition"]["environment"],
            [],
        )

    def test_manual_skill_update_accepts_legacy_definition_without_environment(
        self,
    ):
        legacy_skill = Skill.objects.create(
            name="Legacy Skill",
            package_name="legacy-skill",
            definition={"content": "Old instructions."},
        )

        response = self.client.patch(
            f"/api/lens/admin/skills/{legacy_skill.uuid}/",
            {
                "definition": {
                    "content": "Updated instructions.",
                }
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["definition"],
            {
                "content": "Updated instructions.",
                "environment": [],
            },
        )

    @patch("lens.skill_generation.run_completion")
    def test_skill_beautify_returns_polished_content(self, mock_run):
        GlobalSetting.objects.create(
            key="lens.skills.generator_model_ref",
            value="016d5cf7-2245-4015-b242-d6323e795b58",
        )
        mock_run.return_value = type(
            "Result", (), {"content": "```markdown\n# Polished\n```"}
        )()

        response = self.client.post(
            "/api/lens/admin/skills/beautify/",
            {"name": "Demo", "content": "rough draft"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["content"], "# Polished")
        _, kwargs = mock_run.call_args
        self.assertEqual(
            kwargs["model_ref"],
            "016d5cf7-2245-4015-b242-d6323e795b58",
        )
        self.assertEqual(kwargs["node_name"], "lens.skill_beautify")

    def test_skill_delete_impact_and_force_delete_bound_skill(self):
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
            enabled=True,
        )

        impact_response = self.client.get(
            f"/api/lens/admin/skills/{self.skill.uuid}/delete-impact/",
        )

        self.assertEqual(impact_response.status_code, 200)
        self.assertEqual(impact_response.data["bound_count"], 1)
        self.assertEqual(
            impact_response.data["bound_assistants"][0]["name"],
            self.assistant.name,
        )

        with patch("lens.views.skills.invalidate_skill_cache") as invalidate:
            with self.captureOnCommitCallbacks(execute=True):
                delete_response = self.client.post(
                    f"/api/lens/admin/skills/{self.skill.uuid}/force-delete/",
                    {"confirmation_name": self.skill.name},
                    format="json",
                )

        self.assertEqual(delete_response.status_code, 204)
        invalidate.assert_called_once_with(self.skill.uuid)
        self.assertFalse(Skill.objects.filter(pk=self.skill.pk).exists())
        self.assertFalse(
            AssistantSkill.objects.filter(assistant=self.assistant).exists()
        )

    def test_uploaded_skill_reads_environment_schema(self):
        environment = [
            {
                "name": "JIRA_API_TOKEN",
                "description": "Jira token",
                "required": True,
                "secret": True,
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "jira-connector",
                            "Use the Jira API.",
                            environment,
                        )
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["definition"]["environment"],
            environment,
        )

    def test_uploaded_skill_reads_plugin_capability_requirements(self):
        required_plugins = [
            {
                "plugin": "github",
                "capabilities": ["repository.read"],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "github-workflow",
                            "Use the installed GitHub Plugin tools.",
                            required_plugins=required_plugins,
                        )
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["definition"]["required_plugins"],
            required_plugins,
        )

    def test_uploaded_skill_accepts_thirty_five_megabyte_package_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload_with_file(
                            "binary-skill",
                            35 * 1024 * 1024,
                            zipfile.ZIP_STORED,
                        )
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 200)

    def test_uploaded_skill_accepts_windows_zip_member_paths(self):
        skill_md = (
            "---\n"
            "name: winrar-skill\n"
            "description: WinRAR skill\n"
            "---\n"
            "Use the bundled skill.\n"
        )
        package = skill_zip_upload_with_member_names(
            {
                "winrar-skill\\SKILL.md": skill_md,
                "winrar-skill\\scripts\\run.sh": "#!/bin/sh\n",
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {"file": package},
                    format="multipart",
                )

                self.assertEqual(response.status_code, 200)
                skill = Skill.objects.get(package_name="winrar-skill")
                self.assertTrue(
                    Path(skill.package_path).joinpath("scripts", "run.sh").is_file()
                )

    def test_uploaded_skill_rejects_package_file_over_fifty_megabytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload_with_file(
                            "oversized-binary-skill",
                            50 * 1024 * 1024 + 1,
                        )
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "Skill package contains an oversized file.",
        )

    def test_uploaded_skill_rejects_package_over_fifty_megabytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload_with_file(
                            "oversized-package-skill",
                            50 * 1024 * 1024,
                            zipfile.ZIP_STORED,
                        )
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "Skill package exceeds 50 MB.",
        )

    def test_uploaded_skill_accepts_environment_schema_override(self):
        environment = [
            {
                "name": "JIRA_API_TOKEN",
                "description": "Jira token",
                "required": True,
                "secret": True,
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "jira-connector",
                            "Use the Jira API.",
                        ),
                        "environment": json.dumps(environment),
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["definition"]["environment"],
            environment,
        )

    def test_uploaded_skill_rejects_invalid_environment_schema_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "jira-connector",
                            "Use the Jira API.",
                        ),
                        "environment": "{invalid",
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "Environment variables must be valid JSON.",
        )
        self.assertFalse(Skill.objects.filter(package_name="jira-connector").exists())

    def test_uploaded_skill_reads_api_access_policy(self):
        environment = [
            {
                "name": "JIRA_BASE_URL",
                "required": True,
                "secret": False,
            }
        ]
        api = {
            "base_url_env": "JIRA_BASE_URL",
            "routes": [
                {"path": "/rest/api/3/myself", "methods": ["GET"]},
                {
                    "path_prefix": "/rest/api/3/search/",
                    "methods": ["GET", "POST"],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "jira-api",
                            "Use the Jira API.",
                            environment,
                            api,
                        )
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["definition"]["api"], api)

    def test_uploaded_skill_recursively_enables_scripts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "script-permissions",
                            "Run the bundled script.",
                            package_files={
                                "scripts/nested/run": b"#!/bin/sh\n",
                                "scripts/helper.py": b"print('ok')\n",
                            },
                        )
                    },
                    format="multipart",
                )

                self.assertEqual(response.status_code, 200)
                skill = Skill.objects.get(package_name="script-permissions")
                package_root = Path(skill.package_path)
                self.assertEqual(
                    (package_root / "scripts/nested/run").stat().st_mode & 0o777,
                    0o755,
                )
                self.assertEqual(
                    (package_root / "scripts/helper.py").stat().st_mode & 0o777,
                    0o755,
                )

    def test_uploaded_skill_reads_declared_transform(self):
        transforms = {
            "summarize-orders": {
                "entrypoint": "scripts/summarize_orders.py",
                "input_format": "json",
                "environment": [],
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "order-transform",
                            "Summarize an order result reference.",
                            transforms=transforms,
                            package_files={
                                "scripts/summarize_orders.py": (b"import json, sys\n"),
                            },
                        )
                    },
                    format="multipart",
                )

                self.assertEqual(response.status_code, 200)
                saved_transform = response.data["definition"]["transforms"][
                    "summarize-orders"
                ]
                self.assertEqual(
                    saved_transform["entrypoint"],
                    "scripts/summarize_orders.py",
                )
                self.assertEqual(saved_transform["input_format"], "json")
                self.assertEqual(saved_transform["environment"], [])
                self.assertEqual(
                    saved_transform["sha256"],
                    hashlib.sha256(b"import json, sys\n").hexdigest(),
                )
                skill = Skill.objects.get(package_name="order-transform")
                with zipfile.ZipFile(package_zip_bytes(skill)) as archive:
                    config = json.loads(
                        archive.read("order-transform/sourcelens.json").decode("utf-8")
                    )
                self.assertEqual(config["transforms"], transforms)

    def test_uploaded_skill_rejects_transform_outside_scripts(self):
        transforms = {
            "summarize-orders": {
                "entrypoint": "bin/summarize_orders.py",
                "input_format": "json",
                "environment": [],
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "unsafe-transform",
                            "Run a transform.",
                            transforms=transforms,
                            package_files={
                                "bin/summarize_orders.py": b"print('no')\n",
                            },
                        )
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("scripts/", response.data["detail"])

    def test_uploaded_skill_rejects_transform_undeclared_environment(self):
        transforms = {
            "summarize-orders": {
                "entrypoint": "scripts/summarize_orders.py",
                "input_format": "json",
                "environment": ["ORDER_TOKEN"],
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "unsafe-transform-env",
                            "Run a transform.",
                            transforms=transforms,
                            package_files={
                                "scripts/summarize_orders.py": (b"print('no')\n"),
                            },
                        )
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("ORDER_TOKEN", response.data["detail"])

    def test_uploaded_skill_revalidates_transform_environment_override(self):
        transforms = {
            "summarize-orders": {
                "entrypoint": "scripts/summarize_orders.py",
                "input_format": "json",
                "environment": ["ORDER_TOKEN"],
            }
        }
        environment = [
            {
                "name": "ORDER_TOKEN",
                "required": True,
                "secret": True,
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    "/api/lens/admin/skills/upload/",
                    {
                        "file": skill_zip_upload(
                            "transform-override",
                            "Run a transform.",
                            environment=environment,
                            transforms=transforms,
                            package_files={
                                "scripts/summarize_orders.py": (b"print('ok')\n"),
                            },
                        ),
                        "environment": json.dumps([]),
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("ORDER_TOKEN", response.data["detail"])

    def test_manual_skill_rejects_api_route_with_path_traversal(self):
        response = self.client.post(
            "/api/lens/admin/skills/",
            {
                "name": "Unsafe Connector",
                "slug": "unsafe-connector",
                "definition": {
                    "content": "Use the connector.",
                    "environment": [
                        {
                            "name": "API_BASE_URL",
                            "required": True,
                            "secret": False,
                        }
                    ],
                    "api": {
                        "base_url_env": "API_BASE_URL",
                        "routes": [
                            {
                                "path_prefix": "/api/%252e%252e/admin/",
                                "methods": ["GET"],
                            }
                        ],
                    },
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("path traversal", str(response.data).lower())

    def test_uploaded_skill_update_preserves_assistant_binding(self):
        skill = Skill.objects.create(
            name="package-skill",
            package_name="package-skill",
            definition={"content": "old"},
            source_type="upload",
        )
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=skill,
            enabled=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    f"/api/lens/admin/skills/{skill.uuid}/update-upload/",
                    {"file": skill_zip_upload("package-skill", "new body")},
                    format="multipart",
                )

        self.assertEqual(response.status_code, 200)
        skill.refresh_from_db()
        self.assertEqual(skill.definition["content"], "new body")
        self.assertEqual(skill.source_type, "upload")
        self.assertTrue(skill.package_hash)
        self.assertTrue(
            AssistantSkill.objects.filter(
                assistant=self.assistant,
                skill=skill,
            ).exists()
        )

    def test_uploaded_skill_update_accepts_environment_schema_override(self):
        skill = Skill.objects.create(
            name="package-skill",
            package_name="package-skill",
            definition={"content": "old", "environment": []},
            source_type="upload",
        )
        environment = [
            {
                "name": "PACKAGE_TOKEN",
                "description": "Package token",
                "required": True,
                "secret": True,
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.settings(STORAGE_ROOT=temp_dir):
                response = self.client.post(
                    f"/api/lens/admin/skills/{skill.uuid}/update-upload/",
                    {
                        "file": skill_zip_upload(
                            "package-skill",
                            "new body",
                        ),
                        "environment": json.dumps(environment),
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["definition"]["environment"],
            environment,
        )

    def test_assistant_create_rejects_unreported_task(self):
        payload = {
            "name": "Bad Task",
            "slug": "bad-task",
            "lensnode_uuid": str(self.lensnode.uuid),
            "selected_task": "unknown",
            "selected_dirs": [{"path": "/workspace/repo"}],
        }

        response = self.client.post(
            "/api/lens/assistants/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("selected_task", response.data)

    def test_assistant_create_rejects_unreported_dir(self):
        payload = {
            "name": "Bad Dir",
            "slug": "bad-dir",
            "lensnode_uuid": str(self.lensnode.uuid),
            "selected_task": "knowledge_qa",
            "selected_dirs": [{"path": "/workspace/missing"}],
        }

        response = self.client.post(
            "/api/lens/assistants/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("selected_dirs", str(response.data))

    def test_general_chat_create_allows_empty_dirs_with_skill(self):
        payload = {
            "name": "Skill Runner",
            "slug": "skill-runner",
            "lensnode_uuid": str(self.lensnode.uuid),
            "selected_task": "general_chat",
            "selected_dirs": [],
            "skill_bindings": [{"skill_uuid": str(self.skill.uuid)}],
        }

        response = self.client.post(
            "/api/lens/assistants/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant = Assistant.objects.get(slug="skill-runner")
        self.assertEqual(assistant.selected_task, "general_chat")
        self.assertEqual(assistant.selected_dirs, [])
        self.assertEqual(assistant.lensnode_id, self.lensnode.id)
        self.assertEqual(assistant.skill_bindings.count(), 1)

    def test_general_chat_create_requires_enabled_skill(self):
        payload = {
            "name": "Skill Runner",
            "slug": "skill-runner-empty",
            "lensnode_uuid": str(self.lensnode.uuid),
            "selected_task": "general_chat",
            "selected_dirs": [],
            "skill_bindings": [],
        }

        response = self.client.post(
            "/api/lens/assistants/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("skill_bindings", response.data)

    def test_general_chat_create_rejects_globally_disabled_skill(self):
        self.skill.enabled = False
        self.skill.save(update_fields=["enabled"])
        payload = {
            "name": "Skill Runner",
            "slug": "skill-runner-disabled",
            "lensnode_uuid": str(self.lensnode.uuid),
            "selected_task": "general_chat",
            "selected_dirs": [],
            "skill_bindings": [{"skill_uuid": str(self.skill.uuid)}],
        }

        response = self.client.post(
            "/api/lens/assistants/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("skill_bindings", response.data)

    def test_loaded_skills_snapshot_excludes_package_file_bytes(self):
        assistant = Assistant.objects.create(
            name="Skill Snapshot",
            slug="skill-snapshot",
            lensnode=self.lensnode,
            selected_task="general_chat",
            selected_dirs=[],
        )
        AssistantSkill.objects.create(
            assistant=assistant,
            skill=self.skill,
            enabled=True,
        )

        loaded = build_loaded_skills(assistant)

        self.assertEqual(len(loaded), 1)
        self.assertNotIn("package_files", loaded[0])

    def test_environment_variable_set_encrypts_values_and_masks_api(self):
        response = self.client.post(
            "/api/lens/admin/environment-variable-sets/",
            {
                "name": "Jira - Production",
                "values": [
                    {
                        "key": "JIRA_BASE_URL",
                        "value": "https://jira.example.com",
                    },
                    {"key": "JIRA_API_TOKEN", "value": "secret-token"},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        variable_set = EnvironmentVariableSet.objects.get(name="Jira - Production")
        self.assertNotIn("secret-token", variable_set.encrypted_values)
        self.assertEqual(
            variable_set.get_values()["JIRA_API_TOKEN"],
            "secret-token",
        )
        self.assertNotIn("values", response.data)
        self.assertEqual(
            response.data["keys"],
            ["JIRA_API_TOKEN", "JIRA_BASE_URL"],
        )

    def test_assistant_skill_requires_declared_environment_values(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "description": "Jira token",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])
        response = self.client.post(
            "/api/lens/assistants/",
            {
                "name": "Jira Assistant",
                "slug": "jira-assistant",
                "lensnode_uuid": str(self.lensnode.uuid),
                "selected_task": "knowledge_qa",
                "selected_dirs": [{"path": "/workspace/repo"}],
                "skill_bindings": [{"skill_uuid": str(self.skill.uuid)}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("JIRA_API_TOKEN", str(response.data))

    def test_assistant_create_saves_inline_environment_values(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "description": "Jira token",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])

        response = self.client.post(
            "/api/lens/assistants/",
            {
                "name": "Jira Assistant",
                "slug": "jira-inline-environment",
                "lensnode_uuid": str(self.lensnode.uuid),
                "selected_task": "knowledge_qa",
                "selected_dirs": [{"path": "/workspace/repo"}],
                "skill_bindings": [
                    {
                        "skill_uuid": str(self.skill.uuid),
                        "environment_variable_set_name": "Jira - Staging",
                        "environment_values": [
                            {
                                "key": "JIRA_API_TOKEN",
                                "value": "staging-token",
                            }
                        ],
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        assistant = Assistant.objects.get(slug="jira-inline-environment")
        binding = assistant.skill_bindings.get(skill=self.skill)
        self.assertEqual(
            binding.environment_variable_set.name,
            "Jira - Staging",
        )
        self.assertEqual(
            binding.environment_variable_set.get_values(),
            {"JIRA_API_TOKEN": "staging-token"},
        )

    def test_assistant_create_rolls_back_inline_environment_on_failure(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])
        serializer = AssistantSerializer(
            data={
                "name": "Rollback Assistant",
                "slug": "rollback-assistant",
                "lensnode_uuid": str(self.lensnode.uuid),
                "selected_task": "knowledge_qa",
                "selected_dirs": [{"path": "/workspace/repo"}],
                "skill_bindings": [
                    {
                        "skill_uuid": str(self.skill.uuid),
                        "environment_variable_set_name": "Rollback Set",
                        "environment_values": [
                            {
                                "key": "JIRA_API_TOKEN",
                                "value": "temporary-token",
                            }
                        ],
                    }
                ],
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        with patch(
            "lens.serializers.check_assistant_model_refs",
            side_effect=RuntimeError("forced failure"),
        ):
            with self.assertRaises(RuntimeError):
                serializer.save()

        self.assertFalse(Assistant.objects.filter(slug="rollback-assistant").exists())
        self.assertFalse(
            EnvironmentVariableSet.objects.filter(name="Rollback Set").exists()
        )

    def test_orchestrator_is_not_an_assistant_capability(self):
        """Smart Collaboration is a mode, not an execution capability."""

        serializer = AssistantSerializer(
            data={
                "name": "Invalid Orchestrator",
                "slug": "invalid-orchestrator",
                "capability": "orchestrator",
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("capability", serializer.errors)

    def test_smart_mode_is_exposed_independently_from_capability(self):
        """Smart mode has its own API identity and coordinator capability."""

        serializer = AssistantSerializer(
            data={
                "name": "Support Team",
                "slug": "support-team-mode",
                "mode": "smart",
                "agent_model_ref": "11111111-1111-1111-1111-111111111111",
                "collaboration_member_uuids": [str(self.assistant.uuid)],
            },
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        assistant = serializer.save()
        self.assertEqual(assistant.mode, Assistant.Mode.SMART)
        self.assertEqual(
            AssistantSerializer(
                assistant,
                context={"request": SimpleNamespace(user=self.user)},
            ).data["mode"],
            Assistant.Mode.SMART,
        )
        self.assertEqual(
            assistant.mode_handler.execution_capability(assistant.capability),
            Assistant.Capability.GENERAL_CHAT,
        )

    def test_smart_mode_does_not_require_general_chat_skill(self):
        """Smart mode can coordinate members without direct Chat Skills."""

        serializer = AssistantSerializer(
            data={
                "name": "Skill Free Team",
                "slug": "skill-free-team",
                "mode": "smart",
                "capability": "code_analysis",
                "agent_model_ref": "11111111-1111-1111-1111-111111111111",
                "collaboration_member_uuids": [str(self.assistant.uuid)],
            },
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        assistant = serializer.save()
        self.assertEqual(assistant.capability, Assistant.Capability.GENERAL_CHAT)
        self.assertFalse(assistant.skill_bindings.filter(enabled=True).exists())

    def test_switching_to_smart_clears_direct_execution_resources(self):
        """Smart mode must not retain bindings from direct execution."""

        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
        )
        AssistantMCP.objects.create(
            assistant=self.assistant,
            mcp=self.mcp,
        )
        serializer = AssistantSerializer(
            self.assistant,
            data={
                "mode": "smart",
                "agent_model_ref": "11111111-1111-1111-1111-111111111111",
                "collaboration_member_uuids": [],
            },
            partial=True,
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertFalse(serializer.is_valid())

        serializer = AssistantSerializer(
            self.assistant,
            data={
                "mode": "smart",
                "agent_model_ref": "11111111-1111-1111-1111-111111111111",
                "collaboration_member_uuids": [str(self.assistant.uuid)],
            },
            partial=True,
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertFalse(serializer.is_valid())

        member = Assistant.objects.create(
            name="General Helper",
            slug="general-helper-for-switch",
            visibility=Assistant.Visibility.PUBLIC,
            selected_task="general_chat",
        )
        serializer = AssistantSerializer(
            self.assistant,
            data={
                "mode": "smart",
                "agent_model_ref": "11111111-1111-1111-1111-111111111111",
                "collaboration_member_uuids": [str(member.uuid)],
            },
            partial=True,
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        assistant = serializer.save()
        self.assertIsNone(assistant.lensnode)
        self.assertEqual(assistant.selected_dirs, [])
        self.assertFalse(assistant.skill_bindings.exists())
        self.assertFalse(assistant.mcp_bindings.exists())
        self.assertIsNone(assistant.multimodal_model_ref)

    def test_fixed_smart_assistant_persists_direct_members(self):
        """Admins can create a reusable Smart Collaboration Assistant."""

        second = Assistant.objects.create(
            name="General Helper",
            slug="general-helper",
            visibility=Assistant.Visibility.PUBLIC,
            selected_task="general_chat",
        )
        serializer = AssistantSerializer(
            data={
                "name": "Support Team",
                "slug": "support-team",
                "routing_mode": "smart",
                "agent_model_ref": "11111111-1111-1111-1111-111111111111",
                "collaboration_member_uuids": [
                    str(self.assistant.uuid),
                    str(second.uuid),
                ],
            },
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        assistant = serializer.save()
        assistant.refresh_from_db()

        self.assertEqual(assistant.routing_mode, Assistant.RoutingMode.SMART)
        self.assertEqual(
            list(assistant.collaboration_members.order_by("name")),
            [self.assistant, second],
        )
        payload = AssistantSerializer(
            assistant,
            context={"request": SimpleNamespace(user=self.user)},
        ).data
        self.assertEqual(
            [item["uuid"] for item in payload["collaboration_members"]],
            [str(self.assistant.uuid), str(second.uuid)],
        )

    def test_smart_member_list_uses_prefetched_members(self):
        """Serializing Smart team members does not issue one query per team."""

        second = Assistant.objects.create(
            name="Second Helper",
            slug="second-helper-for-prefetch",
            visibility=Assistant.Visibility.PUBLIC,
            selected_task="general_chat",
        )
        teams = []
        for name, slug in (
            ("First Team", "first-team-for-prefetch"),
            ("Second Team", "second-team-for-prefetch"),
        ):
            team = Assistant.objects.create(
                name=name,
                slug=slug,
                routing_mode=Assistant.RoutingMode.SMART,
                agent_model_ref="11111111-1111-1111-1111-111111111111",
            )
            team.collaboration_members.set([self.assistant, second])
            teams.append(team)

        assistants = list(
            AssistantViewSet.queryset.filter(pk__in=[team.pk for team in teams])
        )
        serializer = AssistantSerializer(
            context={"request": SimpleNamespace(user=self.user)}
        )

        with CaptureQueriesContext(connection) as context:
            members = [
                serializer.get_collaboration_members(assistant)
                for assistant in assistants
            ]

        self.assertEqual(len(context), 0)
        self.assertEqual([len(team_members) for team_members in members], [2, 2])

    def test_fixed_smart_assistant_rejects_nested_members(self):
        """A Smart team cannot contain another Smart Assistant."""

        nested = Assistant.objects.create(
            name="Nested Team",
            slug="nested-team",
            routing_mode=Assistant.RoutingMode.SMART,
            agent_model_ref="11111111-1111-1111-1111-111111111111",
        )
        serializer = AssistantSerializer(
            data={
                "name": "Outer Team",
                "slug": "outer-team",
                "routing_mode": "smart",
                "agent_model_ref": "11111111-1111-1111-1111-111111111111",
                "collaboration_member_uuids": [str(nested.uuid)],
            },
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("collaboration_member_uuids", serializer.errors)

    def test_fixed_smart_session_snapshots_members(self):
        """A fixed Assistant creates a Smart Session with a member snapshot."""

        second = Assistant.objects.create(
            name="General Helper",
            slug="general-helper",
            visibility=Assistant.Visibility.PUBLIC,
            selected_task="general_chat",
        )
        fixed = Assistant.objects.create(
            name="Support Team",
            slug="support-team",
            routing_mode=Assistant.RoutingMode.SMART,
            agent_model_ref="11111111-1111-1111-1111-111111111111",
            visibility=Assistant.Visibility.PUBLIC,
        )
        fixed.collaboration_members.set([self.assistant, second])

        serializer = SessionCreateSerializer(
            data={"assistant_uuid": str(fixed.uuid)},
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        session = serializer.save()

        self.assertEqual(session.routing_mode, Session.RoutingMode.SMART)
        self.assertEqual(
            set(session.allowed_assistant_uuids),
            {str(self.assistant.uuid), str(second.uuid)},
        )

    def test_smart_session_creation_rejects_mixed_assistant_payload(self):
        """A configured Smart Assistant cannot fall back to the ad-hoc flow."""

        serializer = SessionCreateSerializer(
            data={
                "assistant_uuid": str(self.assistant.uuid),
                "routing_mode": "smart",
            },
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("assistant_uuid", serializer.errors)

    def test_fixed_smart_session_scope_cannot_be_edited(self):
        """Smart Session member scope is immutable after creation."""

        fixed = Assistant.objects.create(
            name="Support Team",
            slug="support-team",
            routing_mode=Assistant.RoutingMode.SMART,
            agent_model_ref="11111111-1111-1111-1111-111111111111",
            visibility=Assistant.Visibility.PUBLIC,
        )
        fixed.collaboration_members.add(self.assistant)
        session = Session.objects.create(
            assistant=fixed,
            user=self.user,
            routing_mode=Session.RoutingMode.SMART,
            allowed_assistant_uuids=[str(self.assistant.uuid)],
        )
        serializer = SessionSerializer(
            session,
            data={"allowed_assistant_uuids": []},
            partial=True,
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("allowed_assistant_uuids", serializer.errors)

    def test_smart_collaboration_session_uses_global_model_and_allowed_range(self):
        GlobalSetting.objects.create(
            key="lens.smart_collaboration.model_ref",
            value="11111111-1111-1111-1111-111111111111",
        )
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.save(update_fields=["visibility"])
        serializer = SessionCreateSerializer(
            data={
                "routing_mode": "smart",
                "allowed_assistant_uuids": [str(self.assistant.uuid)],
            },
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        session = serializer.save()

        self.assertEqual(session.routing_mode, Session.RoutingMode.SMART)
        self.assertEqual(
            session.allowed_assistant_uuids,
            [str(self.assistant.uuid)],
        )
        self.assertTrue(session.assistant.is_system)
        self.assertEqual(
            str(session.assistant.agent_model_ref),
            "11111111-1111-1111-1111-111111111111",
        )

        update = SessionSerializer(
            session,
            data={"allowed_assistant_uuids": []},
            partial=True,
            context={"request": SimpleNamespace(user=self.user)},
        )
        self.assertTrue(update.is_valid(), update.errors)
        updated = update.save()
        self.assertEqual(updated.allowed_assistant_uuids, [])

    def test_smart_collaboration_session_defaults_to_empty_range(self):
        """Smart Collaboration requires an explicit participant choice."""

        GlobalSetting.objects.create(
            key="lens.smart_collaboration.model_ref",
            value="11111111-1111-1111-1111-111111111111",
        )
        serializer = SessionCreateSerializer(
            data={"routing_mode": "smart"},
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        session = serializer.save()

        self.assertEqual(session.allowed_assistant_uuids, [])
        run = RunCreateSerializer(
            data={"question": "Coordinate this request.", "enqueue": False},
            context={
                "session": session,
                "request": SimpleNamespace(user=self.user),
            },
        )
        self.assertTrue(run.is_valid(), run.errors)
        with self.assertRaises(PermissionDenied):
            run.save()

    def test_smart_collaboration_rejects_legacy_model_setting(self):
        """Only the final Smart Collaboration model setting is accepted."""

        GlobalSetting.objects.create(
            key="lens.smart_router.model_ref",
            value="11111111-1111-1111-1111-111111111111",
        )
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.save(update_fields=["visibility"])
        serializer = SessionCreateSerializer(
            data={"routing_mode": "smart"},
            context={"request": SimpleNamespace(user=self.user)},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        with self.assertRaises(PermissionDenied):
            serializer.save()

    def test_smart_session_run_allows_hidden_coordinator(self):
        """Smart sessions may run through their hidden system coordinator."""

        GlobalSetting.objects.create(
            key="lens.smart_collaboration.model_ref",
            value="11111111-1111-1111-1111-111111111111",
        )
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.save(update_fields=["visibility"])
        session_response = self.client.post(
            "/api/lens/sessions/",
            {
                "routing_mode": "smart",
                "allowed_assistant_uuids": [str(self.assistant.uuid)],
            },
            format="json",
        )
        self.assertEqual(session_response.status_code, 201)

        run_response = self.client.post(
            f"/api/lens/sessions/{session_response.data['uuid']}/runs/",
            {
                "question": "Coordinate this request.",
                "enqueue": False,
            },
            format="json",
        )

        self.assertEqual(run_response.status_code, 201)
        self.assertEqual(run_response.data["execution"]["task"], "general_chat")
        run = Run.objects.get(uuid=run_response.data["uuid"])
        self.assertFalse(run.execution.runtime_snapshot["routing_assistant_explicit"])

    def test_smart_session_normalizes_legacy_coordinator_capability(self):
        """Existing Smart sessions use General Chat after capability removal."""

        coordinator = Assistant.objects.create(
            name="Smart Collaboration",
            slug="__system-smart-collaboration__",
            capability="orchestrator",
            agent_model_ref="11111111-1111-1111-1111-111111111111",
            is_system=True,
        )
        session = Session.objects.create(
            assistant=coordinator,
            user=self.user,
            routing_mode=Session.RoutingMode.SMART,
            allowed_assistant_uuids=[str(self.assistant.uuid)],
        )

        response = self.client.post(
            f"/api/lens/sessions/{session.uuid}/runs/",
            {"question": "Coordinate this request.", "enqueue": False},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["execution"]["task"], "general_chat")
        coordinator.refresh_from_db()
        self.assertEqual(
            coordinator.capability,
            Assistant.Capability.GENERAL_CHAT,
        )

    def test_smart_run_can_limit_one_run_without_updating_session_scope(self):
        """A routing assistant override belongs to the Run snapshot only."""

        GlobalSetting.objects.create(
            key="lens.smart_collaboration.model_ref",
            value="11111111-1111-1111-1111-111111111111",
        )
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.description = "Use for focused repository analysis."
        self.assistant.save(update_fields=["visibility", "description"])
        self.user.profile.language = "es"
        self.user.profile.save(update_fields=["language"])
        session = SessionCreateSerializer(
            data={
                "routing_mode": "smart",
                "allowed_assistant_uuids": [str(self.assistant.uuid)],
            },
            context={"request": SimpleNamespace(user=self.user)},
        )
        self.assertTrue(session.is_valid(), session.errors)
        session = session.save()
        original_scope = list(session.allowed_assistant_uuids)
        serializer = RunCreateSerializer(
            data={
                "question": "@Code Advisor Analyze this request.",
                "routing_assistant_uuid": str(self.assistant.uuid),
                "enqueue": False,
            },
            context={
                "session": session,
                "request": SimpleNamespace(user=self.user),
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        run = serializer.save()

        session.refresh_from_db()
        self.assertEqual(session.allowed_assistant_uuids, original_scope)
        self.assertEqual(
            run.input_message.content,
            "@Code Advisor Analyze this request.",
        )
        self.assertEqual(
            run.execution.runtime_snapshot["allowed_assistant_uuids"],
            [str(self.assistant.uuid)],
        )
        self.assertTrue(run.execution.runtime_snapshot["routing_assistant_explicit"])
        routing_description = run.execution.runtime_snapshot["subagents"][0][
            "routing_description"
        ]
        self.assertIn(
            "Capacidad: Preguntas y respuestas de conocimiento.",
            routing_description,
        )
        self.assertIn(
            "Resumen del asistente: Use for focused repository analysis.",
            routing_description,
        )
        self.assertEqual(
            run.execution.runtime_snapshot["routing_question"],
            "Analyze this request.",
        )

    def test_smart_run_can_explicitly_route_to_multiple_assistants(self):
        """A single message can explicitly request several assistants."""

        GlobalSetting.objects.create(
            key="lens.smart_collaboration.model_ref",
            value="11111111-1111-1111-1111-111111111111",
        )
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.save(update_fields=["visibility"])
        second = Assistant.objects.create(
            name="Company Knowledge",
            slug="company-knowledge",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            visibility=Assistant.Visibility.PUBLIC,
        )
        requested = [str(self.assistant.uuid), str(second.uuid)]
        session_serializer = SessionCreateSerializer(
            data={
                "routing_mode": "smart",
                "allowed_assistant_uuids": requested,
            },
            context={"request": SimpleNamespace(user=self.user)},
        )
        self.assertTrue(session_serializer.is_valid(), session_serializer.errors)
        session = session_serializer.save()
        serializer = RunCreateSerializer(
            data={
                "question": ("@Code Advisor @Company Knowledge Compare the findings."),
                "routing_assistant_uuids": requested,
                "enqueue": False,
            },
            context={
                "session": session,
                "request": SimpleNamespace(user=self.user),
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        run = serializer.save()

        self.assertEqual(
            run.execution.runtime_snapshot["allowed_assistant_uuids"],
            requested,
        )
        self.assertEqual(
            run.execution.runtime_snapshot["routing_assistant_uuids"],
            requested,
        )
        self.assertEqual(
            run.execution.runtime_snapshot["routing_assistant_uuid"],
            "",
        )
        self.assertTrue(run.execution.runtime_snapshot["routing_assistant_explicit"])
        self.assertEqual(
            run.execution.runtime_snapshot["routing_question"],
            "Compare the findings.",
        )

    def test_assistant_update_forks_shared_environment_set(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])
        variable_set = EnvironmentVariableSet.objects.create(name="Jira - Shared")
        variable_set.set_values({"JIRA_API_TOKEN": "shared-token"})
        variable_set.save(update_fields=["encrypted_values"])
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
            environment_variable_set=variable_set,
        )
        other_assistant = Assistant.objects.create(
            name="Other Jira Assistant",
            slug="other-jira-assistant",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[{"path": "/workspace/repo"}],
        )
        AssistantSkill.objects.create(
            assistant=other_assistant,
            skill=self.skill,
            environment_variable_set=variable_set,
        )

        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {
                "skill_bindings": [
                    {
                        "skill_uuid": str(self.skill.uuid),
                        "environment_variable_set_uuid": str(variable_set.uuid),
                        "environment_values": [
                            {
                                "key": "JIRA_API_TOKEN",
                                "value": "assistant-token",
                            }
                        ],
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        variable_set.refresh_from_db()
        self.assertEqual(
            variable_set.get_values(),
            {"JIRA_API_TOKEN": "shared-token"},
        )
        updated_binding = self.assistant.skill_bindings.get(skill=self.skill)
        other_binding = other_assistant.skill_bindings.get(skill=self.skill)
        self.assertNotEqual(
            updated_binding.environment_variable_set_id,
            variable_set.id,
        )
        self.assertEqual(
            other_binding.environment_variable_set_id,
            variable_set.id,
        )
        self.assertEqual(
            updated_binding.environment_variable_set.get_values(),
            {"JIRA_API_TOKEN": "assistant-token"},
        )

    def test_assistant_update_preserves_exclusive_environment_set_uuid(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])
        variable_set = EnvironmentVariableSet.objects.create(name="Jira - Exclusive")
        variable_set.set_values({"JIRA_API_TOKEN": "old-token"})
        variable_set.save(update_fields=["encrypted_values"])
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
            environment_variable_set=variable_set,
        )

        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {
                "skill_bindings": [
                    {
                        "skill_uuid": str(self.skill.uuid),
                        "environment_variable_set_uuid": str(variable_set.uuid),
                        "environment_values": [
                            {
                                "key": "JIRA_API_TOKEN",
                                "value": "new-token",
                            }
                        ],
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        binding = self.assistant.skill_bindings.get(skill=self.skill)
        self.assertEqual(binding.environment_variable_set_id, variable_set.id)
        variable_set.refresh_from_db()
        self.assertEqual(
            variable_set.get_values(),
            {"JIRA_API_TOKEN": "new-token"},
        )

    def test_assistant_update_locks_environment_set_before_rebinding(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])
        variable_set = EnvironmentVariableSet.objects.create(name="Jira - Locked")
        variable_set.set_values({"JIRA_API_TOKEN": "old-token"})
        variable_set.save(update_fields=["encrypted_values"])
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
            environment_variable_set=variable_set,
        )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.patch(
                f"/api/lens/assistants/{self.assistant.uuid}/",
                {
                    "skill_bindings": [
                        {
                            "skill_uuid": str(self.skill.uuid),
                            "environment_variable_set_uuid": str(variable_set.uuid),
                            "environment_values": [
                                {
                                    "key": "JIRA_API_TOKEN",
                                    "value": "new-token",
                                }
                            ],
                        }
                    ]
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        table_name = EnvironmentVariableSet._meta.db_table.upper()
        lock_queries = [
            query["sql"]
            for query in queries.captured_queries
            if "FOR UPDATE" in query["sql"].upper()
            and table_name in query["sql"].upper()
        ]
        self.assertTrue(lock_queries)

    def test_disabled_binding_does_not_require_environment(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])

        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {
                "skill_bindings": [
                    {
                        "skill_uuid": str(self.skill.uuid),
                        "enabled": False,
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        binding = self.assistant.skill_bindings.get(skill=self.skill)
        self.assertFalse(binding.enabled)
        self.assertIsNone(binding.environment_variable_set)

    def test_dispatch_rejects_disabled_environment_variable_set(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])
        variable_set = EnvironmentVariableSet.objects.create(
            name="Jira - Disabled",
            enabled=False,
        )
        variable_set.set_values({"JIRA_API_TOKEN": "disabled-token"})
        variable_set.save(update_fields=["encrypted_values"])
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
            environment_variable_set=variable_set,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session, "Search Jira", enqueue=False)

        with self.assertRaises(LensNodeDispatchError) as context:
            validate_run_dispatch(run)

        self.assertEqual(
            str(context.exception),
            "SKILL_ENVIRONMENT_REQUIRED",
        )
        runtime = resolve_loaded_skill_environment(build_loaded_skills(self.assistant))
        self.assertEqual(runtime[0]["environment"], {})

    def test_dispatch_rejects_missing_mcp_environment(self):
        self.mcp.environment = [
            {
                "name": "GITHUB_TOKEN",
                "required": True,
                "secret": True,
            }
        ]
        self.mcp.save(update_fields=["environment"])
        AssistantMCP.objects.create(
            assistant=self.assistant,
            mcp=self.mcp,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session, "Search GitHub", enqueue=False)

        with self.assertRaises(LensNodeDispatchError) as context:
            validate_run_dispatch(run)

        self.assertEqual(
            str(context.exception),
            "MCP_ENVIRONMENT_REQUIRED",
        )

    def test_dispatch_requires_optional_mcp_environment_when_referenced(self):
        self.mcp.endpoint = "https://${MCP_TOKEN}/api"
        self.mcp.environment = [
            {
                "name": "MCP_TOKEN",
                "required": False,
                "secret": True,
            }
        ]
        self.mcp.save(update_fields=["endpoint", "environment"])
        AssistantMCP.objects.create(
            assistant=self.assistant,
            mcp=self.mcp,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session, "Search API", enqueue=False)

        with self.assertRaises(LensNodeDispatchError) as context:
            validate_run_dispatch(run)

        self.assertEqual(
            str(context.exception),
            "MCP_ENVIRONMENT_REQUIRED",
        )
        runtime = resolve_loaded_mcp_environment(run.execution.loaded_mcps)
        self.assertFalse(runtime[0]["environment_resolved"])

    def test_dispatch_rejects_run_queued_before_assistant_was_archived(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session, "Question", enqueue=False)
        self.assistant.status = Assistant.Status.ARCHIVED
        self.assistant.save(update_fields=["status"])
        run = run.__class__.objects.select_related("session__assistant").get(pk=run.pk)

        with self.assertRaises(LensNodeDispatchError) as context:
            validate_run_dispatch(run)

        self.assertEqual(str(context.exception), "ASSISTANT_ARCHIVED")

    def test_dispatch_validates_the_frozen_execution_snapshot(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session, "Question", enqueue=False)
        self.assistant.selected_task = "general_chat"
        self.assistant.selected_dirs = []
        self.assistant.save(update_fields=["selected_task", "selected_dirs"])
        run = run.__class__.objects.select_related(
            "execution",
            "session__assistant",
        ).get(pk=run.pk)

        validate_run_dispatch(run)

    def test_dispatch_rejects_invalid_frozen_snapshot_after_assistant_edit(self):
        self.assistant.selected_task = "general_chat"
        self.assistant.selected_dirs = []
        self.assistant.save(update_fields=["selected_task", "selected_dirs"])
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session, "Question", enqueue=False)
        self.assistant.selected_task = "knowledge_qa"
        self.assistant.selected_dirs = [{"path": "/workspace/repo"}]
        self.assistant.save(update_fields=["selected_task", "selected_dirs"])
        run = run.__class__.objects.select_related(
            "execution",
            "session__assistant",
        ).get(pk=run.pk)

        with self.assertRaises(LensNodeDispatchError) as context:
            validate_run_dispatch(run)

        self.assertEqual(
            str(context.exception),
            "GENERAL_CHAT_SKILL_REQUIRED",
        )

    def test_runtime_resolves_environment_without_snapshot_plaintext(self):
        self.skill.definition = {
            "environment": [
                {
                    "name": "JIRA_API_TOKEN",
                    "description": "Jira token",
                    "required": True,
                    "secret": True,
                }
            ]
        }
        self.skill.save(update_fields=["definition"])
        variable_set = EnvironmentVariableSet.objects.create(name="Jira - Production")
        variable_set.set_values(
            {"JIRA_API_TOKEN": "secret-token", "UNDECLARED": "hidden"}
        )
        variable_set.save(update_fields=["encrypted_values"])
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=self.skill,
            environment_variable_set=variable_set,
        )

        loaded = build_loaded_skills(self.assistant)
        runtime = resolve_loaded_skill_environment(loaded)

        self.assertNotIn("environment", loaded[0])
        self.assertNotIn("secret-token", str(loaded))
        self.assertEqual(
            runtime[0]["environment"],
            {"JIRA_API_TOKEN": "secret-token"},
        )

    def test_assistant_model_check_uses_agent_model_ref(self):
        config = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL,
            user=None,
            model_type=LLMConfig.MODEL_TYPE_LLM,
            provider="openai",
            config={"model": "gpt-test", "api_key": "test-key"},
            is_active=False,
        )

        response = self.client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {"agent_model_ref": str(config.uuid)},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        check = response.data["settings"]["_model_check"]
        self.assertEqual(check["agent_model_ref"]["status"], "error")
        self.assertIn("inactive", check["agent_model_ref"]["error"])

    def test_lensnode_issue_and_revoke_token(self):
        response = self.client.post(
            f"/api/lens/admin/lensnodes/{self.lensnode.uuid}/issue-token/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["token"])

        revoke_response = self.client.post(
            f"/api/lens/admin/lensnodes/{self.lensnode.uuid}/revoke-token/"
        )
        self.assertEqual(revoke_response.status_code, 200)
        self.lensnode.refresh_from_db()
        self.assertTrue(self.lensnode.token_revoked)
        self.assertEqual(self.lensnode.status, LensNode.Status.OFFLINE)

    def test_lensnode_ai_gateway_uses_lensnode_bearer_token(self):
        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        client = APIClient()

        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track",
            return_value=("ok", {"total_tokens": 1}),
        ):
            response = client.post(
                "/api/lens/lensnode/ai-gateway/",
                {
                    "model_ref": "016d5cf7-2245-4015-b242-d6323e795b58",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["content"], "ok")
        self.assertEqual(
            response.data["lensnode_uuid"],
            str(self.lensnode.uuid),
        )

    def test_lensnode_ai_gateway_reports_reasoning_length_truncation(self):
        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        client = APIClient()
        error = ValueError(
            "LLM returned empty response (finish_reason='length' "
            "has_reasoning_content=True reasoning_len=2334 "
            "model=deepseek/deepseek-v4-flash)"
        )

        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track",
            side_effect=error,
        ):
            response = client.post(
                "/api/lens/lensnode/ai-gateway/",
                {
                    "model_ref": "016d5cf7-2245-4015-b242-d6323e795b58",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.data,
            {
                "code": "MODEL_EMPTY_RESPONSE",
                "finish_reason": "length",
                "has_reasoning_content": True,
            },
        )

    def test_lensnode_ai_gateway_streams_tool_call_deltas(self):
        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        client = APIClient()

        def tracked_stream():
            yield (
                "tool_call",
                json.dumps(
                    {
                        "index": 0,
                        "id": "call-plan",
                        "name": "write_todos",
                        "arguments": '{"todos":[{"content":"Inspect"}',
                    }
                ),
            )
            return {
                "total_tokens": 8,
                "_tool_calls": [],
                "_finish_reason": "tool_calls",
            }

        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track",
            return_value=tracked_stream(),
        ):
            response = client.post(
                "/api/lens/lensnode/ai-gateway/",
                {
                    "model_ref": "016d5cf7-2245-4015-b242-d6323e795b58",
                    "messages": [{"role": "user", "content": "plan"}],
                    "stream": True,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )
            body = collect_stream(response.streaming_content).decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('"kind": "tool_call"', body)
        self.assertIn("write_todos", body)
        self.assertIn('"type": "done"', body)

    def test_lensnode_delegation_routes_to_assistant_bound_node(self):
        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        remote_node = LensNode.objects.create(
            name="Remote LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            tasks=[{"name": "knowledge_qa"}],
        )
        remote = Assistant.objects.create(
            name="Remote Assistant",
            slug="remote-api-assistant",
            lensnode=remote_node,
            selected_task="knowledge_qa",
            visibility=Assistant.Visibility.PUBLIC,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
            routing_mode=Session.RoutingMode.SMART,
            allowed_assistant_uuids=[str(remote.uuid)],
        )
        parent = create_execution_run(
            session=session,
            question="Coordinate this request",
            enqueue=False,
        )
        parent.status = Run.Status.RUNNING
        parent.save(update_fields=["status"])
        client = APIClient()
        url = f"/api/lens/lensnode/runs/{parent.uuid}/delegations/"
        payload = {
            "assistant_uuid": str(remote.uuid),
            "question": "Inspect production logs",
            "delegation_key": "call-remote-1",
            "delegation_group_key": "explicit-remote-assistant",
        }

        response = client.post(
            url,
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        replay = client.post(
            url,
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(replay.status_code, 202, replay.data)
        self.assertEqual(response.data["run_uuid"], replay.data["run_uuid"])
        child = Run.objects.get(uuid=response.data["run_uuid"])
        self.assertEqual(child.parent_run, parent)
        self.assertEqual(child.lensnode, remote_node)
        self.assertEqual(parent.delegated_runs.count(), 1)

        child.status = Run.Status.FAILED
        child.save(update_fields=["status"])
        retry_payload = {
            **payload,
            "question": "Finish the production log review",
            "delegation_key": "call-remote-2",
        }
        retry_response = client.post(
            url,
            retry_payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(retry_response.status_code, 202, retry_response.data)
        retry = Run.objects.get(uuid=retry_response.data["run_uuid"])
        self.assertEqual(retry.parent_run, parent)
        self.assertEqual(retry.retry_of_run, child)
        self.assertEqual(retry.session, child.session)
        self.assertEqual(parent.delegated_runs.count(), 2)

    def test_lensnode_ai_gateway_supports_tool_calling_payload(self):
        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        client = APIClient()
        message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_workspace",
                        "arguments": '{"query":"test"}',
                    },
                }
            ],
        }
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_workspace",
                    "parameters": {"type": "object"},
                },
            }
        ]

        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track",
            return_value=(message, {"total_tokens": 1}),
        ) as call_and_track:
            response = client.post(
                "/api/lens/lensnode/ai-gateway/",
                {
                    "model_ref": "016d5cf7-2245-4015-b242-d6323e795b58",
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": tools,
                    "tool_choice": "auto",
                    "return_message": True,
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["message"]["tool_calls"][0]["id"],
            "call_1",
        )
        self.assertEqual(response.data["content"], "")
        self.assertEqual(call_and_track.call_args.kwargs["tools"], tools)
        self.assertEqual(
            call_and_track.call_args.kwargs["tool_choice"],
            "auto",
        )
        self.assertTrue(call_and_track.call_args.kwargs["return_message"])

    def test_lensnode_ai_gateway_forwards_run_correlation(self):
        from lens.models import Session
        from lens.services import create_execution_run

        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        client = APIClient()
        chat_user = User.objects.create_user(
            username="chat-user",
            email="chat-user@example.com",
            password="pass12345",
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=chat_user,
        )
        run = create_execution_run(
            session=session,
            question="hello",
            enqueue=False,
        )

        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track",
            return_value=("ok", {"total_tokens": 1}),
        ) as call_and_track:
            response = client.post(
                "/api/lens/lensnode/ai-gateway/",
                {
                    "model_ref": "016d5cf7-2245-4015-b242-d6323e795b58",
                    "messages": [{"role": "user", "content": "hello"}],
                    "run_uuid": str(run.uuid),
                    "is_subagent": True,
                    "trace_context": {
                        "parent_observation_id": "a" * 16,
                        "generation_name": "llm.agent",
                    },
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 200)
        state = call_and_track.call_args.kwargs["state"]
        self.assertEqual(state["user_id"], chat_user.id)
        self.assertEqual(state["metadata"]["run_uuid"], str(run.uuid))
        self.assertTrue(state["metadata"]["is_subagent"])
        self.assertEqual(
            state["litellm_metadata"]["session_id"],
            str(session.uuid),
        )
        self.assertEqual(
            state["litellm_metadata"]["trace_user_id"],
            str(chat_user.id),
        )
        self.assertEqual(
            state["litellm_metadata"]["trace_name"],
            "sourcelens.run",
        )
        self.assertEqual(
            state["litellm_metadata"]["trace_id"],
            run.uuid.hex,
        )
        self.assertEqual(
            state["litellm_metadata"]["existing_trace_id"],
            run.uuid.hex,
        )
        self.assertEqual(
            state["litellm_metadata"]["parent_observation_id"],
            "a" * 16,
        )
        self.assertEqual(
            state["litellm_metadata"]["generation_name"],
            "llm.agent",
        )
        self.assertEqual(
            state["litellm_metadata"]["trace_metadata"],
            {
                "run_uuid": str(run.uuid),
                "is_subagent": True,
            },
        )
        self.assertEqual(
            state["otel_traceparent"],
            f"00-{run.uuid.hex}-{'a' * 16}-01",
        )

    def test_lensnode_ai_gateway_rejects_invalid_trace_context(self):
        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        client = APIClient()

        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track"
        ) as call_and_track:
            response = client.post(
                "/api/lens/lensnode/ai-gateway/",
                {
                    "model_ref": "016d5cf7-2245-4015-b242-d6323e795b58",
                    "messages": [{"role": "user", "content": "hello"}],
                    "trace_context": {
                        "parent_observation_id": "not-an-observation-id",
                        "generation_name": "llm.agent",
                    },
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Invalid trace_context.")
        call_and_track.assert_not_called()

    def test_lensnode_ai_gateway_attributes_every_call_to_run_owner(self):
        from lens.models import Session
        from lens.services import create_execution_run

        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        chat_user = User.objects.create_user(
            username="usage-chat-user",
            email="usage-chat-user@example.com",
            password="pass12345",
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=chat_user,
        )
        run = create_execution_run(
            session=session,
            question="hello",
            enqueue=False,
        )
        config = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL,
            model_type=LLMConfig.MODEL_TYPE_LLM,
            provider="openai",
            config={"api_key": "test", "model": "gpt-4o-mini"},
            is_active=True,
        )
        completion = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=2,
                completion_tokens=1,
                total_tokens=3,
                cached_tokens=0,
                reasoning_tokens=0,
            ),
            model="gpt-4o-mini",
        )
        client = APIClient()
        payload = {
            "model_ref": str(config.uuid),
            "messages": [{"role": "user", "content": "hello"}],
            "run_uuid": str(run.uuid),
        }

        with patch("litellm.completion", return_value=completion):
            for _ in range(2):
                response = client.post(
                    "/api/lens/lensnode/ai-gateway/",
                    payload,
                    format="json",
                    HTTP_AUTHORIZATION=f"Bearer {token}",
                )
                self.assertEqual(response.status_code, 200)

        usages = list(LLMUsage.objects.order_by("created_at"))
        self.assertEqual(len(usages), 2)
        self.assertTrue(all(item.user_id == chat_user.id for item in usages))
        self.assertTrue(
            all(item.metadata["run_uuid"] == str(run.uuid) for item in usages)
        )

    def test_lensnode_ai_gateway_rejects_run_from_another_lensnode(self):
        from lens.models import Session
        from lens.services import create_execution_run

        token = "other-lensnode-token"
        LensNode.objects.create(
            name="Other LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            auth_token_hash=hash_lensnode_token(token),
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(
            session=session,
            question="hello",
            enqueue=False,
        )
        client = APIClient()

        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track"
        ) as call_and_track:
            response = client.post(
                "/api/lens/lensnode/ai-gateway/",
                {
                    "model_ref": "016d5cf7-2245-4015-b242-d6323e795b58",
                    "messages": [{"role": "user", "content": "hello"}],
                    "run_uuid": str(run.uuid),
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 403)
        call_and_track.assert_not_called()

    def test_lensnode_ai_gateway_rejects_malformed_run_uuid(self):
        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        client = APIClient()

        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track"
        ) as call_and_track:
            response = client.post(
                "/api/lens/lensnode/ai-gateway/",
                {
                    "model_ref": "016d5cf7-2245-4015-b242-d6323e795b58",
                    "messages": [{"role": "user", "content": "hello"}],
                    "run_uuid": "not-a-uuid",
                },
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "Run not found.")
        call_and_track.assert_not_called()

    def test_lensnode_deliverable_upload_records_output_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from lens.models import RunOutputFile, Session
        from lens.services import create_execution_run

        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)

        client = APIClient()
        upload = SimpleUploadedFile(
            "brief.html", b"<html>brief</html>", content_type="text/html"
        )
        response = client.post(
            "/api/lens/lensnode/deliverables/",
            {
                "run_uuid": str(run.uuid),
                "file": upload,
                "filename": "brief.html",
                "content_type": "text/html",
            },
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 201)
        output = RunOutputFile.objects.get(run=run)
        self.assertEqual(output.filename, "brief.html")
        self.assertEqual(output.message_id, run.output_message_id)
        self.assertEqual(output.session_id, session.id)
        self.assertEqual(output.assistant_id, self.assistant.id)
        self.assertTrue(output.file.storage.exists(output.file.name))
        output.file.delete(save=False)

    def test_lensnode_deliverable_upload_rejects_bad_token(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from lens.models import Session
        from lens.services import create_execution_run

        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)
        response = APIClient().post(
            "/api/lens/lensnode/deliverables/",
            {
                "run_uuid": str(run.uuid),
                "file": SimpleUploadedFile("x.txt", b"x"),
            },
            format="multipart",
            HTTP_AUTHORIZATION="Bearer wrong-token",
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(DELIVERABLE_MAX_BYTES=8)
    def test_lensnode_deliverable_upload_rejects_oversized(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from lens.models import RunOutputFile, Session
        from lens.services import create_execution_run

        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)
        response = APIClient().post(
            "/api/lens/lensnode/deliverables/",
            {
                "run_uuid": str(run.uuid),
                "file": SimpleUploadedFile("big.html", b"way too many bytes"),
            },
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 413)
        self.assertFalse(RunOutputFile.objects.filter(run=run).exists())

    def test_lensnode_deliverable_upload_strips_path_from_filename(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from lens.models import RunOutputFile, Session
        from lens.services import create_execution_run

        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)
        response = APIClient().post(
            "/api/lens/lensnode/deliverables/",
            {
                "run_uuid": str(run.uuid),
                "file": SimpleUploadedFile("x.html", b"<html></html>"),
                "filename": "../../../etc/passwd",
            },
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 201)
        output = RunOutputFile.objects.get(run=run)
        self.assertEqual(output.filename, "passwd")
        self.assertNotIn("..", output.file.name)
        self.assertTrue(
            output.file.name.endswith("passwd") or "passwd" in output.file.name
        )
        output.file.delete(save=False)

    def _make_output_file(self, user=None):
        """Create a delivered output file linked to a fresh run."""

        from django.core.files.base import ContentFile
        from lens.models import RunOutputFile, Session
        from lens.services import create_execution_run

        session = Session.objects.create(
            assistant=self.assistant, user=user or self.user
        )
        run = create_execution_run(session=session, question="q", enqueue=False)
        output = RunOutputFile(
            run=run,
            message=run.output_message,
            session=session,
            assistant=self.assistant,
            filename="brief.html",
            content_type="text/html",
            byte_size=18,
            content_hash=hashlib.sha256(b"<html>brief</html>").hexdigest(),
        )
        output.file.save("brief.html", ContentFile(b"<html>brief</html>"), save=False)
        output.save()
        return session, run, output

    def _make_cited_run(self, user=None):
        """Create a run with one trusted citation and invalid path inputs."""

        session = Session.objects.create(
            assistant=self.assistant,
            user=user or self.user,
        )
        run = create_execution_run(
            session=session,
            question="Where is the handler?",
            enqueue=False,
        )
        append_lensnode_output(
            run.uuid,
            final_content="The handler is implemented in run.py.",
            citations=[
                {
                    "id": "evidence-handler",
                    "evidence_id": "evidence-handler",
                    "project": "SourceLens",
                    "repository": "sourcelens",
                    "revision": "abc123",
                    "path": "backend/lens/run.py",
                    "symbol": "run_handler",
                    "start_line": 40,
                    "end_line": 42,
                    "supports": "This function handles the run.",
                    "source": (
                        "def run_handler():\n" "    execute_run()\n" "    return True"
                    ),
                },
                {
                    "id": "evidence-absolute",
                    "path": "/workspace/private.py",
                    "start_line": 1,
                    "end_line": 1,
                    "source": "secret = True",
                },
                {
                    "id": "evidence-traversal",
                    "path": "../private.py",
                    "start_line": 1,
                    "end_line": 1,
                    "source": "secret = True",
                },
            ],
            planned_evidence={
                "sufficient": True,
                "gap_categories": [],
            },
        )
        return session, run

    def test_session_messages_expose_safe_citation_metadata(self):
        session, run = self._make_cited_run()

        response = self.client.get(f"/api/lens/sessions/{session.uuid}/messages/")

        self.assertEqual(response.status_code, 200)
        answer = next(item for item in response.data if item["role"] == "assistant")
        self.assertEqual(len(answer["citations"]), 1)
        citation = answer["citations"][0]
        self.assertEqual(citation["id"], "evidence-handler")
        self.assertEqual(citation["path"], "backend/lens/run.py")
        self.assertEqual(citation["start_line"], 40)
        self.assertEqual(citation["end_line"], 42)
        self.assertNotIn("source", citation)
        run.refresh_from_db()
        self.assertEqual(len(run.citations), 1)
        self.assertEqual(run.planned_evidence["sufficient"], True)

    def test_citation_source_returns_numbered_snapshot_to_run_owner(self):
        owner = User.objects.create_user(
            username="citation-reader",
            email="citation-reader@example.com",
            password="pass12345",
        )
        session, run = self._make_cited_run(owner)
        client = APIClient()
        client.force_authenticate(owner)

        response = client.get(f"/api/lens/runs/{run.uuid}/citations/evidence-handler/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["path"], "backend/lens/run.py")
        self.assertEqual(response.data["revision"], "abc123")
        self.assertEqual(response.data["highlight_start_line"], 40)
        self.assertEqual(response.data["highlight_end_line"], 42)
        self.assertEqual(
            response.data["lines"],
            [
                {"number": 40, "content": "def run_handler():"},
                {"number": 41, "content": "    execute_run()"},
                {"number": 42, "content": "    return True"},
            ],
        )
        self.assertNotIn("/workspace", str(response.data))

    def test_citation_source_is_not_available_to_another_user(self):
        owner = User.objects.create_user(
            username="citation-owner",
            email="citation-owner@example.com",
            password="pass12345",
        )
        session, run = self._make_cited_run(owner)
        other = User.objects.create_user(
            username="citation-other",
            email="citation-other@example.com",
            password="pass12345",
        )
        client = APIClient()
        client.force_authenticate(other)

        response = client.get(f"/api/lens/runs/{run.uuid}/citations/evidence-handler/")

        self.assertEqual(response.status_code, 404)

    def test_citation_source_is_available_to_staff_admin(self):
        owner = User.objects.create_user(
            username="citation-user",
            email="citation-user@example.com",
            password="pass12345",
        )
        session, run = self._make_cited_run(owner)

        response = self.client.get(
            f"/api/lens/runs/{run.uuid}/citations/evidence-handler/"
        )

        self.assertEqual(response.status_code, 200)

    def test_lensnode_can_download_prior_same_session_deliverable(self):
        from lens.services import create_execution_run

        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        session, prior, output = self._make_output_file()
        prior.status = Run.Status.DONE
        prior.outcome = Run.Outcome.COMPLETED
        prior.save(update_fields=["status", "outcome"])
        current = create_execution_run(
            session=session,
            question="Translate the previous file",
            enqueue=False,
        )

        try:
            response = APIClient().get(
                (
                    f"/api/lens/lensnode/runs/{current.uuid}/"
                    f"history-artifacts/{output.uuid}/"
                ),
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                collect_stream(response.streaming_content),
                b"<html>brief</html>",
            )
            self.assertEqual(
                response["X-Attachment-Hash"],
                output.content_hash,
            )
        finally:
            output.file.delete(save=False)

    def test_lensnode_cannot_download_deliverable_from_other_session(self):
        from lens.models import Session
        from lens.services import create_execution_run

        token = "dev-lensnode-token"
        self.lensnode.auth_token_hash = hash_lensnode_token(token)
        self.lensnode.save(update_fields=["auth_token_hash", "updated_at"])
        other_session, prior, output = self._make_output_file()
        prior.status = Run.Status.DONE
        prior.outcome = Run.Outcome.COMPLETED
        prior.save(update_fields=["status", "outcome"])
        current_session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        current = create_execution_run(
            session=current_session,
            question="Read another conversation",
            enqueue=False,
        )

        try:
            response = APIClient().get(
                (
                    f"/api/lens/lensnode/runs/{current.uuid}/"
                    f"history-artifacts/{output.uuid}/"
                ),
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )
            self.assertEqual(response.status_code, 404)
        finally:
            output.file.delete(save=False)

    def test_output_file_download_returns_attachment_to_owner(self):
        session, run, output = self._make_output_file()

        response = self.client.get(f"/api/lens/output-files/{output.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("brief.html", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"<html>brief</html>")
        output.file.delete(save=False)

    def test_output_file_download_forbidden_for_other_user(self):
        session, run, output = self._make_output_file()
        other = User.objects.create_user(
            username="intruder",
            email="intruder@example.com",
            password="pass12345",
        )
        client = APIClient()
        client.force_authenticate(other)

        response = client.get(f"/api/lens/output-files/{output.uuid}/")

        self.assertEqual(response.status_code, 403)
        output.file.delete(save=False)

    def test_output_file_download_returns_attachment_to_admin(self):
        owner = User.objects.create_user(
            username="file-owner",
            email="file-owner@example.com",
            password="pass12345",
        )
        session, run, output = self._make_output_file(owner)

        response = self.client.get(f"/api/lens/output-files/{output.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        output.file.delete(save=False)

    def test_session_messages_include_output_files(self):
        session, run, output = self._make_output_file()

        response = self.client.get(f"/api/lens/sessions/{session.uuid}/messages/")

        self.assertEqual(response.status_code, 200)
        answer = next(m for m in response.data if m["role"] == "assistant")
        self.assertEqual(len(answer["output_files"]), 1)
        chip = answer["output_files"][0]
        self.assertEqual(chip["filename"], "brief.html")
        self.assertIn(f"/api/lens/output-files/{output.uuid}/", chip["url"])
        output.file.delete(save=False)

    def test_admin_run_detail_includes_output_files(self):
        session, run, output = self._make_output_file()

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["output_files"]), 1)
        item = response.data["output_files"][0]
        self.assertEqual(item["uuid"], str(output.uuid))
        self.assertEqual(item["filename"], "brief.html")
        self.assertEqual(item["content_type"], "text/html")
        self.assertEqual(item["byte_size"], 18)
        self.assertIsNotNone(item["created_at"])
        self.assertIn(f"/api/lens/output-files/{output.uuid}/", item["url"])
        output.file.delete(save=False)

    def test_admin_run_detail_has_empty_output_files(self):
        from lens.models import Session
        from lens.services import create_execution_run

        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["output_files"], [])

    def test_admin_run_detail_exposes_lensnode_admission_wait(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(session=session, question="q", enqueue=False)
        created_at = timezone.now() - timedelta(seconds=20)
        started_at = created_at + timedelta(seconds=5)
        admitted_at = started_at + timedelta(seconds=7)
        run.created_at = created_at
        run.started_at = started_at
        run.save(update_fields=["created_at", "started_at"])
        run.execution.admitted_at = admitted_at
        run.execution.save(update_fields=["admitted_at"])

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["control_queue_seconds"], 5.0)
        self.assertEqual(response.data["admission_wait_seconds"], 7.0)
        self.assertEqual(response.data["admitted_at"], admitted_at.isoformat())

    def test_admin_run_detail_uses_agent_rounds_snapshot(self):
        from lens.models import Session
        from lens.services import create_execution_run

        self.assistant.agent_rounds = "max"
        self.assistant.save(update_fields=["agent_rounds"])
        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)
        self.assistant.agent_rounds = "flash"
        self.assistant.save(update_fields=["agent_rounds"])

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["agent_rounds"], "max")

    def test_admin_run_detail_summarizes_configured_and_called_resources(self):
        from lens.models import RunTraceEvent

        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)
        run.execution.loaded_skills = [
            {
                "skill_uuid": "skill-uuid",
                "skill_package_name": "order-tools",
                "skill_name": "Order tools",
            }
        ]
        run.execution.loaded_mcps = [
            {
                "mcp_uuid": "mcp-uuid",
                "mcp_name": "GitHub MCP",
                "transport": "stdio",
            }
        ]
        run.execution.save(update_fields=["loaded_skills", "loaded_mcps"])
        now = timezone.now()
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=1,
            event_type="tool.started",
            timestamp=now,
            call_id="skill-call",
            payload={
                "name": "run_skill_script",
                "arguments": {"skill_name": "Order tools"},
            },
        )
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=2,
            event_type="tool.completed",
            timestamp=now,
            call_id="skill-call",
            payload={"name": "run_skill_script"},
        )
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=3,
            event_type="tool.started",
            timestamp=now,
            call_id="mcp-call",
            payload={"name": "mcp__github__search"},
        )
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=4,
            event_type="tool.completed",
            timestamp=now,
            call_id="unnamed-call",
        )

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        usage = response.data["execution"]["resource_usage"]
        self.assertEqual(usage["configured_skills"][0]["skill_name"], "Order tools")
        self.assertEqual(usage["configured_mcps"][0]["mcp_name"], "GitHub MCP")
        self.assertEqual(
            usage["calls"],
            [
                {
                    "resource_type": "skill",
                    "name": "Order tools",
                    "calls": 1,
                },
                {
                    "resource_type": "mcp",
                    "name": "GitHub MCP",
                    "calls": 1,
                },
            ],
        )
        self.assertEqual(
            usage["resources"],
            [
                {
                    "resource_type": "skill",
                    "name": "Order tools",
                    "configured": True,
                    "calls": 1,
                },
                {
                    "resource_type": "mcp",
                    "name": "GitHub MCP",
                    "configured": True,
                    "calls": 1,
                },
            ],
        )

    def test_admin_run_resource_usage_keeps_named_call_after_unnamed_event(self):
        from lens.models import RunTraceEvent

        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)
        now = timezone.now()
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=1,
            event_type="tool.started",
            timestamp=now,
            call_id="same-call",
        )
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=2,
            event_type="tool.completed",
            timestamp=now,
            call_id="same-call",
            payload={"name": "run_skill_script"},
        )

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["execution"]["resource_usage"]["calls"],
            [
                {
                    "resource_type": "skill",
                    "name": "run_skill_script",
                    "calls": 1,
                }
            ],
        )

    def test_admin_run_resource_usage_attributes_plugin_tools(self):
        from lens.models import RunTraceEvent

        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)
        run.execution.loaded_plugins = [
            {
                "plugin_key": "github",
                "plugin_display_name": "GitHub",
                "plugin_version": "1.0.0",
                "tools": [
                    {"key": "github_repository_get"},
                    {"key": "github_issue_list"},
                ],
            }
        ]
        run.execution.loaded_skills = [
            {
                "skill_kind": "plugin_virtual",
                "skill_name": "GitHub Plugin",
                "skill_package_name": "plugin-virtual-github",
                "definition": {"plugin_virtual": True},
            }
        ]
        run.execution.save(update_fields=["loaded_plugins", "loaded_skills"])
        now = timezone.now()
        for sequence, (call_id, name) in enumerate(
            [
                ("plugin-call-1", "github_repository_get"),
                ("plugin-call-2", "github_repository_get"),
                ("runtime-call", "read_file"),
            ],
            start=1,
        ):
            RunTraceEvent.objects.create(
                run=run,
                event_id=uuid.uuid4(),
                sequence=sequence,
                event_type="tool.started",
                timestamp=now,
                call_id=call_id,
                payload={"name": name},
            )

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        usage = response.data["execution"]["resource_usage"]
        self.assertEqual(
            usage["configured_plugins"],
            [
                {
                    "plugin_key": "github",
                    "plugin_name": "GitHub",
                    "plugin_version": "1.0.0",
                    "tool_keys": [
                        "github_repository_get",
                        "github_issue_list",
                    ],
                }
            ],
        )
        self.assertEqual(usage["configured_skills"], [])
        self.assertEqual(
            usage["resources"],
            [
                {
                    "resource_type": "plugin",
                    "name": "github_repository_get",
                    "plugin_name": "GitHub",
                    "configured": True,
                    "calls": 2,
                },
                {
                    "resource_type": "tool",
                    "name": "read_file",
                    "configured": False,
                    "calls": 1,
                },
            ],
        )
        self.assertEqual(usage["configured_count"], 1)

    def test_admin_run_resource_usage_uses_step_events_for_skill_identity(self):
        from lens.models import RunStep

        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="q", enqueue=False)
        run.execution.loaded_skills = [
            {
                "skill_uuid": "skill-uuid",
                "skill_package_name": "license-cli",
                "skill_name": "License CLI",
            },
            {
                "skill_uuid": "other-skill",
                "skill_package_name": "other-cli",
                "skill_name": "Other CLI",
            },
        ]
        run.execution.save(update_fields=["loaded_skills"])
        RunStep.objects.create(
            run=run,
            step_type="general_chat",
            sequence=1,
            status="completed",
            detail={
                "events": [
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "skill": "license-cli",
                        "script": "bin/linux-arm64/income",
                    },
                    {
                        "agent_event": "tool.run_skill_script.done",
                        "skill": "license-cli",
                        "script": "bin/linux-arm64/income",
                    },
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "skill": "license-cli",
                        "script": "bin/linux-arm64/income",
                    },
                ]
            },
        )

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        usage = response.data["execution"]["resource_usage"]
        self.assertEqual(
            usage["resources"],
            [
                {
                    "resource_type": "skill",
                    "name": "License CLI",
                    "configured": True,
                    "calls": 2,
                },
                {
                    "resource_type": "skill",
                    "name": "Other CLI",
                    "configured": True,
                    "calls": 0,
                },
            ],
        )

    def test_admin_run_list_exposes_operational_metrics_and_filters(self):
        from lens.models import RunTraceEvent

        model_ref = uuid.uuid4()
        self.assistant.agent_model_ref = model_ref
        self.assistant.agent_rounds = "deep"
        self.assistant.token_budget_profile = "deep"
        self.assistant.save(
            update_fields=[
                "agent_model_ref",
                "agent_rounds",
                "token_budget_profile",
            ]
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        failed = create_execution_run(
            session=session,
            question="Investigate failed checkout",
            enqueue=False,
        )
        failed.status = Run.Status.FAILED
        failed.citations = [
            {
                "id": "citation-1",
                "project": "SourceLens",
                "repository": "sourcelens",
                "revision": "working-tree",
                "path": "backend/lens/services.py",
                "symbol": "create_execution_run",
                "start_line": 1,
                "end_line": 1,
                "supports": "The run is created from the source session.",
                "source": "def create_execution_run(...):",
            }
        ]
        failed.save(update_fields=["status", "citations"])
        second_failed = create_execution_run(
            session=session,
            question="Investigate another failed checkout",
            enqueue=False,
        )
        second_failed.status = Run.Status.FAILED
        second_failed.save(update_fields=["status"])
        RunTraceEvent.objects.create(
            run=failed,
            event_id=uuid.uuid4(),
            sequence=1,
            event_type="tool.started",
            timestamp=timezone.now(),
            call_id="tool-call-1",
        )
        retry = create_execution_run(
            session=session,
            question="Retry checkout investigation",
            retry_of_run=failed,
            enqueue=False,
        )
        delegated_session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        create_execution_run(
            session=delegated_session,
            question="Delegated checkout investigation",
            parent_run=failed,
            enqueue=False,
        )

        response = self.client.get(
            "/api/lens/admin/runs/",
            {
                "lensnode": str(self.lensnode.uuid),
                "model": str(model_ref),
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["total"], 3)
        self.assertEqual(response.data["summary"]["failed"], 2)
        failed_row = next(
            item
            for item in response.data["results"]
            if item["uuid"] == str(failed.uuid)
        )
        self.assertEqual(failed_row["model_ref"], str(model_ref))
        self.assertEqual(failed_row["tool_call_count"], 1)
        self.assertEqual(failed_row["retry_count"], 1)
        self.assertEqual(failed_row["token_budget_profile"], "deep")
        self.assertEqual(failed_row["token_budget_max_tokens"], 500000)
        detail_response = self.client.get(f"/api/lens/admin/runs/{failed.uuid}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["tool_call_count"], 1)
        self.assertEqual(detail_response.data["retry_count"], 1)
        self.assertEqual(
            detail_response.data["citations"][0]["path"],
            "backend/lens/services.py",
        )
        self.assertNotIn("source", detail_response.data["citations"][0])
        self.assertEqual(retry.retry_of_run, failed)

        active = create_execution_run(
            session=session,
            question="Active checkout investigation",
            enqueue=False,
        )
        active.status = Run.Status.RUNNING
        active.save(update_fields=["status"])
        active_response = self.client.get(
            "/api/lens/admin/runs/",
            {"status": "active"},
        )
        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(active_response.data["total"], 1)
        self.assertEqual(
            active_response.data["results"][0]["uuid"],
            str(active.uuid),
        )

    def test_lensnode_list_exposes_run_workload(self):
        from lens.models import RunTraceEvent

        LensNode.objects.create(
            name="Draining LensNode",
            status=LensNode.Status.DRAINING,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        queued = create_execution_run(
            session=session,
            question="Queued work",
            enqueue=False,
        )
        running = create_execution_run(
            session=session,
            question="Running work",
            enqueue=False,
        )
        running.status = Run.Status.RUNNING
        running.resume_by = timezone.now() + timedelta(minutes=2)
        running.save(update_fields=["status", "resume_by"])
        expired = create_execution_run(
            session=session,
            question="Expired resume window",
            enqueue=False,
        )
        expired.status = Run.Status.RUNNING
        expired.resume_by = timezone.now() - timedelta(minutes=2)
        expired.save(update_fields=["status", "resume_by"])
        RunTraceEvent.objects.create(
            run=running,
            event_id=uuid.uuid4(),
            sequence=1,
            event_type="model.completed",
            timestamp=timezone.now(),
            payload={"usage": {"total_tokens": 7}},
        )

        response = self.client.get("/api/lens/admin/lensnodes/")

        self.assertEqual(response.status_code, 200, response.data)
        row = next(
            item
            for item in response.data["results"]
            if item["uuid"] == str(self.lensnode.uuid)
        )
        self.assertEqual(row["active_run_count"], 2)
        self.assertEqual(row["queued_run_count"], 1)
        self.assertEqual(row["awaiting_resume_count"], 1)
        self.assertEqual(row["total_run_count"], 3)
        self.assertEqual(row["succeeded_run_count"], 0)
        self.assertEqual(row["failed_run_count"], 0)
        self.assertEqual(row["total_tokens"], 7)
        self.assertIsNotNone(row["last_run_at"])
        self.assertEqual(response.data["fleet_summary"]["active_runs"], 2)
        self.assertEqual(response.data["fleet_summary"]["queued_runs"], 1)
        self.assertEqual(response.data["fleet_summary"]["draining"], 1)
        self.assertEqual(
            response.data["fleet_summary"]["awaiting_resume"],
            1,
        )
        self.assertEqual(queued.status, Run.Status.QUEUED)

    @patch("lens.views.admin_runs.cancel_run_on_lensnode")
    def test_admin_can_cancel_another_users_active_run(self, cancel):
        owner = User.objects.create_user(
            username="run-owner",
            email="run-owner@example.com",
            password="pass12345",
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=owner,
        )
        run = create_execution_run(
            session=session,
            question="Long running task",
            enqueue=False,
        )
        run.status = Run.Status.RUNNING
        run.execution.status = RunExecution.Status.RUNNING
        run.save(update_fields=["status"])
        run.execution.save(update_fields=["status"])

        response = self.client.post(f"/api/lens/admin/runs/{run.uuid}/cancel/")

        self.assertEqual(response.status_code, 200, response.data)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.CANCELLED)
        cancel.assert_called_once_with(run)

    def test_admin_retry_is_idempotent_and_audits_the_actor(self):
        owner = User.objects.create_user(
            username="retry-owner",
            email="retry-owner@example.com",
            password="pass12345",
        )
        AssistantAccess.objects.create(
            assistant=self.assistant,
            user=owner,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=owner,
        )
        run = create_execution_run(
            session=session,
            question="Retry this failed task",
            enqueue=False,
        )
        run.status = Run.Status.FAILED
        run.save(update_fields=["status"])
        payload = {"idempotency_key": "admin-retry-request-1"}

        first = self.client.post(
            f"/api/lens/admin/runs/{run.uuid}/retry/",
            payload,
            format="json",
        )
        second = self.client.post(
            f"/api/lens/admin/runs/{run.uuid}/retry/",
            payload,
            format="json",
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["uuid"], second.data["uuid"])
        retry = Run.objects.get(uuid=first.data["uuid"])
        self.assertEqual(retry.retry_of_run, run)
        self.assertEqual(retry.session.user, owner)
        self.assertEqual(
            retry.execution.runtime_snapshot["admin_action"],
            {
                "action": "retry",
                "actor_user_id": self.user.pk,
                "source_run_uuid": str(run.uuid),
            },
        )

    @patch("lens.views.admin_runs.resume_awaiting_run")
    def test_admin_resume_requires_an_awaiting_run(self, resume):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(
            session=session,
            question="Resume from checkpoint",
            enqueue=False,
        )
        run.status = Run.Status.RUNNING
        run.resume_by = timezone.now() + timedelta(minutes=2)
        run.save(update_fields=["status", "resume_by"])
        resume.return_value = True

        detail = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")
        self.assertTrue(detail.data["available_actions"]["resume"])
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.save(update_fields=["status"])
        detail = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")
        self.assertFalse(detail.data["available_actions"]["resume"])
        self.lensnode.status = LensNode.Status.ONLINE
        self.lensnode.save(update_fields=["status"])

        response = self.client.post(f"/api/lens/admin/runs/{run.uuid}/resume/")

        self.assertEqual(response.status_code, 200, response.data)
        resume.assert_called_once_with(run.pk)

    def test_admin_run_detail_separates_executor_and_business_outcomes(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(
            session=session,
            question="Query July orders",
            enqueue=False,
        )
        run.status = Run.Status.DONE
        run.outcome = Run.Outcome.COMPLETED
        run.termination_detail = {}
        run.save(update_fields=["status", "outcome", "termination_detail"])
        run.execution.status = RunExecution.Status.COMPLETED
        run.execution.save(update_fields=["status"])
        RunStep.objects.create(
            run=run,
            step_type=RunStep.StepType.GENERAL_CHAT,
            status=RunStep.Status.DONE,
            sequence=3,
            detail={
                "events": [
                    "malformed persisted event",
                    {
                        "agent_event": "deepagents.runtime.outcome",
                        "outcome": "completed",
                        "unresolved_failure_count": 0,
                        "recovered_failure_count": 0,
                        "warning_count": 2,
                        "failures": [
                            {
                                "capability": "skill",
                                "error_type": "tool",
                                "scope": "warning",
                                "required": True,
                                "affects_required_evidence": False,
                                "arguments": {"authorization": "must-not-leak"},
                            }
                        ],
                    },
                ]
            },
        )

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "done")
        self.assertEqual(response.data["executor_status"], "completed")
        self.assertEqual(response.data["outcome"], "completed")
        self.assertEqual(response.data["termination_detail"], {})
        self.assertEqual(
            response.data["failure_summary"]["warning_count"],
            2,
        )
        failure = response.data["failure_summary"]["failures"][0]
        self.assertTrue(failure["required"])
        self.assertFalse(failure["affects_required_evidence"])
        self.assertEqual(failure["scope"], "warning")
        self.assertNotIn("arguments", failure)

    def test_admin_run_detail_ignores_malformed_failure_collection(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(
            session=session,
            question="Query July orders",
            enqueue=False,
        )
        RunStep.objects.create(
            run=run,
            step_type=RunStep.StepType.GENERAL_CHAT,
            status=RunStep.Status.DONE,
            sequence=3,
            detail={
                "events": [
                    {
                        "agent_event": "deepagents.runtime.outcome",
                        "warning_count": 1,
                        "failures": 42,
                    }
                ]
            },
        )

        response = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["failure_summary"]["warning_count"],
            1,
        )
        self.assertEqual(
            response.data["failure_summary"]["failures"],
            [],
        )

    def test_session_messages_use_run_finish_time_for_assistant(self):
        session, run, output = self._make_output_file()
        finished_at = timezone.now()
        run.finished_at = finished_at
        run.save(update_fields=["finished_at"])

        response = self.client.get(f"/api/lens/sessions/{session.uuid}/messages/")

        self.assertEqual(response.status_code, 200)
        question = next(m for m in response.data if m["role"] == "user")
        answer = next(m for m in response.data if m["role"] == "assistant")
        self.assertIsNone(question["completed_at"])
        self.assertEqual(
            answer["completed_at"],
            finished_at,
        )
        output.file.delete(save=False)

    def test_deleting_session_purges_output_file_bytes(self):
        session, run, output = self._make_output_file()
        storage = output.file.storage
        name = output.file.name
        self.assertTrue(storage.exists(name))

        response = self.client.delete(f"/api/lens/sessions/{session.uuid}/")

        self.assertIn(response.status_code, (200, 204))
        self.assertFalse(storage.exists(name))

    def test_session_run_flow_returns_completed_run_with_execution(self):
        session_response = self.client.post(
            "/api/lens/sessions/",
            {
                "assistant_uuid": str(self.assistant.uuid),
                "title": "Search code flow",
            },
            format="json",
        )
        self.assertEqual(session_response.status_code, 201)
        session_uuid = session_response.data["uuid"]

        run_response = self.client.post(
            f"/api/lens/sessions/{session_uuid}/runs/",
            {
                "question": "How does SSE work?",
                "idempotency_key": "run-1",
                "run_inline": True,
            },
            format="json",
        )

        self.assertEqual(run_response.status_code, 201)
        self.assertEqual(run_response.data["status"], "done")
        self.assertEqual(run_response.data["execution"]["task"], "knowledge_qa")
        self.assertEqual(
            run_response.data["execution"]["target_dirs"][0]["path"],
            "/workspace/repo",
        )

        stream_response = self.client.get(
            f"/api/lens/runs/{run_response.data['uuid']}/stream/",
            HTTP_AUTHORIZATION=bearer_header(self.user),
        )
        self.assertEqual(stream_response.status_code, 200)
        body = collect_stream(stream_response.streaming_content).decode()
        self.assertIn('"type": "sync"', body)
        self.assertIn('"type": "done"', body)

    def test_session_run_returns_service_unavailable_without_lensnode(self):
        """A temporarily unavailable execution node is retryable."""

        self.assistant.lensnode = None
        self.assistant.save(update_fields=["lensnode"])
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.save(update_fields=["status"])
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )

        response = self.client.post(
            f"/api/lens/sessions/{session.uuid}/runs/",
            {"question": "Retry when the node reconnects."},
            format="json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["detail"], "LENSNODE_UNAVAILABLE")

    def test_stream_does_not_replay_snapshot_steps_after_sync(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(
            session=session,
            question="Explain the event pipeline",
            enqueue=False,
        )
        run.status = Run.Status.DONE
        run.outcome = Run.Outcome.COMPLETED
        run.termination_detail = {}
        run.save(update_fields=["status", "outcome", "termination_detail"])
        run.execution.status = RunExecution.Status.COMPLETED
        run.execution.save(update_fields=["status"])
        for sequence in (1, 2):
            RunStep.objects.create(
                run=run,
                step_type=RunStep.StepType.GENERAL_CHAT,
                status=RunStep.Status.DONE,
                sequence=sequence,
                detail={"events": [{"agent_event": f"step.{sequence}"}]},
            )

        stream_response = self.client.get(
            f"/api/lens/runs/{run.uuid}/stream/",
            HTTP_AUTHORIZATION=bearer_header(self.user),
        )
        self.assertEqual(stream_response.status_code, 200)
        body = collect_stream(stream_response.streaming_content).decode()

        # The sync snapshot already carries every persisted step; the loop
        # must not replay them as duplicate standalone step events.
        self.assertIn('"type": "sync"', body)
        self.assertIn("step.1", body)
        self.assertIn("step.2", body)
        self.assertNotIn('"type": "step"', body)

    def test_clarification_answer_creates_one_continuation_run(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        parent = create_execution_run(
            session=session,
            question="Why did deployment fail?",
            enqueue=False,
        )
        request = {
            "request_id": "clarification-1",
            "question": "Which environment should I inspect?",
            "reason": "ambiguous_scope",
            "answer_type": "text",
        }
        parent.status = Run.Status.AWAITING_USER_INPUT
        parent.termination_detail = {
            "reason": "needs_user_input",
            "request": request,
        }
        parent.save(update_fields=["status", "termination_detail"])

        response = self.client.post(
            f"/api/lens/runs/{parent.uuid}/clarification/",
            {
                "request_id": "clarification-1",
                "answer": "Production",
                "enqueue": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        continuation = Run.objects.get(uuid=response.data["uuid"])
        self.assertEqual(continuation.retry_of_run_id, parent.id)
        self.assertEqual(continuation.input_message.content, "Production")

        replay = self.client.post(
            f"/api/lens/runs/{parent.uuid}/clarification/",
            {
                "request_id": "clarification-1",
                "answer": "Another environment",
                "enqueue": False,
            },
            format="json",
        )

        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data["uuid"], response.data["uuid"])

    def test_clarification_continuation_reuses_parent_attachments(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        attachment = MessageAttachment.objects.create(
            session=session,
            uploaded_by=self.user,
            kind=MessageAttachment.Kind.IMAGE,
            original_name="error.png",
            mime_type="image/png",
            byte_size=7,
        )
        parent = create_execution_run(
            session=session,
            question="",
            attachment_uuids=[str(attachment.uuid)],
            enqueue=False,
        )
        parent.status = Run.Status.AWAITING_USER_INPUT
        parent.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-1",
                "question": "Which image issue should I inspect?",
                "reason": "ambiguous_scope",
                "answer_type": "text",
            },
        }
        parent.save(update_fields=["status", "termination_detail"])

        response = self.client.post(
            f"/api/lens/runs/{parent.uuid}/clarification/",
            {
                "request_id": "clarification-1",
                "answer": "The deployment error",
                "enqueue": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        continuation = Run.objects.get(uuid=response.data["uuid"])
        self.assertEqual(
            continuation.execution.runtime_snapshot["session_attachment_uuids"],
            [str(attachment.uuid)],
        )

    def test_clarification_rejects_missing_documents_after_cache_eviction(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        parent = create_execution_run(
            session=session,
            question="Analyze the uploaded document",
            enqueue=False,
        )
        runtime_snapshot = dict(parent.execution.runtime_snapshot)
        runtime_snapshot["document_attachment_count"] = 1
        parent.execution.runtime_snapshot = runtime_snapshot
        parent.execution.save(update_fields=["runtime_snapshot"])
        parent.status = Run.Status.AWAITING_USER_INPUT
        parent.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-1",
                "question": "Which section should I inspect?",
                "reason": "ambiguous_scope",
                "answer_type": "text",
            },
        }
        parent.save(update_fields=["status", "termination_detail"])

        with (
            patch(
                "lens.views.sessions.get_run_document_attachments",
                return_value=[],
            ),
            patch(
                "lens.views.sessions.get_run_document_expectation",
                return_value=None,
            ),
        ):
            response = self.client.post(
                f"/api/lens/runs/{parent.uuid}/clarification/",
                {
                    "request_id": "clarification-1",
                    "answer": "The deployment section",
                    "enqueue": False,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(
            "DOCUMENT_ATTACHMENT_UNAVAILABLE",
            str(response.data),
        )

    def test_clarification_answer_rejects_wrong_request_id(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        parent = create_execution_run(
            session=session,
            question="Why did deployment fail?",
            enqueue=False,
        )
        parent.status = Run.Status.AWAITING_USER_INPUT
        parent.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-1",
                "question": "Which environment should I inspect?",
                "reason": "ambiguous_scope",
                "answer_type": "text",
            },
        }
        parent.save(update_fields=["status", "termination_detail"])

        response = self.client.post(
            f"/api/lens/runs/{parent.uuid}/clarification/",
            {
                "request_id": "clarification-other",
                "answer": "Production",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_explicit_retry_is_linked_and_idempotent(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        original = create_execution_run(
            session=session,
            question="Retry this question",
            idempotency_key="original-submission",
            enqueue=False,
        )
        payload = {
            "question": "Retry this question",
            "idempotency_key": "retry-submission",
            "retry_of_run_uuid": str(original.uuid),
            "enqueue": False,
        }

        first_response = self.client.post(
            f"/api/lens/sessions/{session.uuid}/runs/",
            payload,
            format="json",
        )
        replay_response = self.client.post(
            f"/api/lens/sessions/{session.uuid}/runs/",
            payload,
            format="json",
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(replay_response.status_code, 201)
        self.assertEqual(
            replay_response.data["uuid"],
            first_response.data["uuid"],
        )
        self.assertEqual(
            first_response.data["retry_of_run_uuid"],
            str(original.uuid),
        )
        self.assertEqual(session.message_set.count(), 4)

    def test_retry_rejects_run_from_another_session(self):
        source_session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        target_session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        original = create_execution_run(
            session=source_session,
            question="Source question",
            enqueue=False,
        )

        response = self.client.post(
            f"/api/lens/sessions/{target_session.uuid}/runs/",
            {
                "question": "Source question",
                "idempotency_key": "cross-session-retry",
                "retry_of_run_uuid": str(original.uuid),
                "enqueue": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(target_session.run_set.count(), 0)
        self.assertEqual(target_session.message_set.count(), 0)

    def test_retry_rejects_inaccessible_run_without_leaking_it(self):
        other_user = User.objects.create_user(
            username="retry-owner",
            email="retry-owner@example.com",
            password="pass12345",
        )
        private_session = Session.objects.create(
            assistant=self.assistant,
            user=other_user,
        )
        private_run = create_execution_run(
            session=private_session,
            question="Private question",
            enqueue=False,
        )
        target_session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )

        response = self.client.post(
            f"/api/lens/sessions/{target_session.uuid}/runs/",
            {
                "question": "Private question",
                "idempotency_key": "inaccessible-retry",
                "retry_of_run_uuid": str(private_run.uuid),
                "enqueue": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["retry_of_run_uuid"][0],
            "Invalid Retry Run.",
        )
        self.assertEqual(target_session.run_set.count(), 0)

    def test_retry_rejects_an_existing_cycle(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        first = create_execution_run(
            session=session,
            question="First attempt",
            enqueue=False,
        )
        second = create_execution_run(
            session=session,
            question="Second attempt",
            retry_of_run=first,
            enqueue=False,
        )
        Run.objects.filter(pk=first.pk).update(retry_of_run=second)

        response = self.client.post(
            f"/api/lens/sessions/{session.uuid}/runs/",
            {
                "question": "Third attempt",
                "idempotency_key": "cyclic-retry",
                "retry_of_run_uuid": str(second.uuid),
                "enqueue": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(session.run_set.count(), 2)

    def test_run_created_at_is_read_only(self):
        session_response = self.client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant.uuid)},
            format="json",
        )
        run_response = self.client.post(
            f"/api/lens/sessions/{session_response.data['uuid']}/runs/",
            {
                "question": "Can the creation time be changed?",
                "idempotency_key": "run-created-at-read-only",
                "enqueue": False,
            },
            format="json",
        )
        self.assertIn("created_at", run_response.data)
        created_at = run_response.data["created_at"]

        update_response = self.client.patch(
            f"/api/lens/runs/{run_response.data['uuid']}/",
            {"created_at": "2000-01-01T00:00:00Z"},
            format="json",
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data["created_at"], created_at)

    def test_run_stream_accepts_event_stream_header(self):
        session_response = self.client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant.uuid)},
            format="json",
        )
        run_response = self.client.post(
            f"/api/lens/sessions/{session_response.data['uuid']}/runs/",
            {
                "question": "What changed?",
                "idempotency_key": "run-event-stream",
                "run_inline": True,
            },
            format="json",
        )

        stream_response = self.client.get(
            f"/api/lens/runs/{run_response.data['uuid']}/stream/",
            HTTP_ACCEPT="text/event-stream",
            HTTP_AUTHORIZATION=bearer_header(self.user),
        )

        self.assertEqual(stream_response.status_code, 200)
        self.assertTrue(stream_response["Content-Type"].startswith("text/event-stream"))

    def test_run_detail_is_scoped_to_session_owner(self):
        session_response = self.client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant.uuid)},
            format="json",
        )
        session_uuid = session_response.data["uuid"]
        run_response = self.client.post(
            f"/api/lens/sessions/{session_uuid}/runs/",
            {
                "question": "How does SSE work?",
                "idempotency_key": "run-private",
                "run_inline": True,
            },
            format="json",
        )

        other_user = User.objects.create_user(
            username="lens-user-2",
            email="lens-user-2@example.com",
            password="pass12345",
        )
        self.client.force_authenticate(other_user)

        response = self.client.get(f"/api/lens/runs/{run_response.data['uuid']}/")

        self.assertEqual(response.status_code, 404)

    def test_running_run_can_be_cancelled(self):
        session_response = self.client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant.uuid)},
            format="json",
        )
        session_uuid = session_response.data["uuid"]
        run_response = self.client.post(
            f"/api/lens/sessions/{session_uuid}/runs/",
            {
                "question": "How does cancellation work?",
                "idempotency_key": "run-running",
                "enqueue": False,
            },
            format="json",
        )

        self.assertEqual(run_response.status_code, 201)
        self.assertEqual(run_response.data["status"], "queued")
        self.assertIsNotNone(run_response.data["created_at"])
        Run.objects.filter(uuid=run_response.data["uuid"]).update(
            status=Run.Status.RUNNING,
            resume_by=timezone.now() + timedelta(hours=1),
        )

        cancel_response = self.client.post(
            f"/api/lens/runs/{run_response.data['uuid']}/cancel/"
        )

        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.data["status"], "cancelled")
        self.assertIsNone(cancel_response.data["resume_by"])
        self.assertEqual(
            cancel_response.data["execution"]["status"],
            RunExecution.Status.CANCELLED,
        )

    def test_completed_run_feedback_is_persisted_and_reported(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(
            session,
            "Was this answer helpful?",
            enqueue=False,
        )
        run.output_message.content = "Yes, this is the answer."
        run.output_message.run = run
        run.output_message.save(update_fields=["content", "run"])
        run.status = Run.Status.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at"])

        response = self.client.patch(
            f"/api/lens/runs/{run.uuid}/feedback/",
            {"feedback": Run.Feedback.POSITIVE},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["feedback"], "positive")
        self.assertIsNotNone(response.data["feedback_updated_at"])
        messages = self.client.get(f"/api/lens/sessions/{session.uuid}/messages/")
        assistant_message = next(
            item for item in messages.data if item["role"] == "assistant"
        )
        self.assertEqual(assistant_message["feedback"], "positive")
        admin_runs = self.client.get(
            "/api/lens/admin/runs/",
            {"q": "Was this answer helpful?"},
        )
        self.assertEqual(admin_runs.status_code, 200, admin_runs.data)
        self.assertEqual(
            admin_runs.data["results"][0]["feedback"],
            "positive",
        )

        switched = self.client.patch(
            f"/api/lens/runs/{run.uuid}/feedback/",
            {"feedback": Run.Feedback.NEGATIVE},
            format="json",
        )
        cleared = self.client.patch(
            f"/api/lens/runs/{run.uuid}/feedback/",
            {"feedback": ""},
            format="json",
        )

        self.assertEqual(switched.status_code, 200, switched.data)
        self.assertEqual(switched.data["feedback"], "negative")
        self.assertEqual(cleared.status_code, 200, cleared.data)
        self.assertEqual(cleared.data["feedback"], "")

    def test_run_feedback_rejects_invalid_or_unfinished_runs(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(
            session,
            "Still running",
            enqueue=False,
        )

        unfinished = self.client.patch(
            f"/api/lens/runs/{run.uuid}/feedback/",
            {"feedback": Run.Feedback.POSITIVE},
            format="json",
        )
        invalid = self.client.patch(
            f"/api/lens/runs/{run.uuid}/feedback/",
            {"feedback": "maybe"},
            format="json",
        )

        self.assertEqual(unfinished.status_code, 400, unfinished.data)
        self.assertEqual(invalid.status_code, 400, invalid.data)

    def test_run_feedback_is_scoped_to_session_owner(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        run = create_execution_run(
            session,
            "Private feedback",
            enqueue=False,
        )
        run.status = Run.Status.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at"])
        other_user = User.objects.create_user(
            username="feedback-user-2",
            email="feedback-user-2@example.com",
            password="pass12345",
        )
        self.client.force_authenticate(other_user)

        response = self.client.patch(
            f"/api/lens/runs/{run.uuid}/feedback/",
            {"feedback": Run.Feedback.NEGATIVE},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_running_run_stream_returns_sync_without_replaying_status(self):
        session_response = self.client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant.uuid)},
            format="json",
        )
        session_uuid = session_response.data["uuid"]
        run_response = self.client.post(
            f"/api/lens/sessions/{session_uuid}/runs/",
            {
                "question": "How does streaming work?",
                "idempotency_key": "run-streaming",
                "enqueue": False,
            },
            format="json",
        )

        stream_response = self.client.get(
            f"/api/lens/runs/{run_response.data['uuid']}/stream/",
            HTTP_AUTHORIZATION=bearer_header(self.user),
        )
        body = collect_stream(stream_response.streaming_content, limit=2).decode()

        self.assertIn('"type": "sync"', body)
        # The sync snapshot already carries the current status; a duplicate
        # status event is only emitted when the run status actually changes.
        self.assertNotIn('"type": "status"', body)

    def test_datasource_create_uses_target_path(self):
        payload = {
            "name": "Scheduled Repo",
            "source_type": "git",
            "lensnode_uuid": str(self.lensnode.uuid),
            "config": {"repo_url": "https://example.com/repo.git"},
            "sync_policy": {"interval_seconds": 120},
            "target_path": "/workspace/scheduled",
        }

        response = self.client.post(
            "/api/lens/admin/datasources/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data["target_path"],
            "/workspace/scheduled",
        )

    def test_datasource_list_filters_by_plugin_key(self):
        self.datasource.plugin_key = "github"
        self.datasource.save(update_fields=["plugin_key"])
        DataSource.objects.create(
            name="Feishu Docs",
            plugin_key="feishu",
            source_type="feishu",
            lensnode=self.lensnode,
            config={"folder_url": "https://example.com/folder"},
            sync_policy={"interval_seconds": 3600},
            target_path="/workspace/feishu-docs",
        )

        response = self.client.get(
            "/api/lens/admin/datasources/",
            {"plugin_key": "github", "page_size": 100},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["plugin_key"], "github")

        all_response = self.client.get(
            "/api/lens/admin/datasources/",
            {"plugin_key": "all", "page_size": 100},
        )

        self.assertEqual(all_response.status_code, 200, all_response.data)
        self.assertEqual(all_response.data["count"], 2)

    def test_datasource_list_uses_a_fixed_query_count_for_sync_state(self):
        datasources = [self.datasource]
        for index in range(9):
            datasources.append(
                DataSource.objects.create(
                    name=f"Repo Cache {index}",
                    source_type="git",
                    lensnode=self.lensnode,
                    config={"repo_url": f"https://example.com/repo-{index}.git"},
                    sync_policy={"interval_seconds": 3600},
                    target_path=f"/workspace/repo-cache-{index}",
                )
            )
        for index, datasource in enumerate(datasources):
            ScheduledTask.objects.create(
                name=f"Datasource sync {index}",
                task_type=ScheduledTask.TaskType.SOURCE_SYNC,
                target_type="datasource",
                target_id=datasource.uuid,
                last_status=ScheduledTask.Status.RUNNING,
            )
            TaskExecution.objects.create(
                task_id=f"running-datasource-sync-{index}",
                task_name=f"source_sync:{datasource.name}",
                module="lens_datasource",
                status="STARTED",
                metadata={"datasource_uuid": str(datasource.uuid)},
            )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                "/api/lens/admin/datasources/",
                {"page": 1, "page_size": 20},
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertLessEqual(len(queries), 4)
        self.assertEqual(response.data["count"], 10)
        self.assertTrue(
            all(row["current_sync"] for row in response.data["results"])
        )
        self.assertTrue(
            all(
                row["sync_state"]["last_status"] == "running"
                for row in response.data["results"]
            )
        )

    def test_datasource_sync_statuses_returns_lightweight_page_updates(self):
        schedule = ScheduledTask.objects.create(
            name="Datasource sync",
            task_type=ScheduledTask.TaskType.SOURCE_SYNC,
            target_type="datasource",
            target_id=self.datasource.uuid,
            last_status=ScheduledTask.Status.RUNNING,
        )
        task = TaskExecution.objects.create(
            task_id="running-datasource-sync-status",
            task_name="source_sync:Repo Cache",
            module="lens_datasource",
            status="STARTED",
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "progress_percent": 42,
                "progress_message": "Indexing files",
            },
        )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                "/api/lens/admin/datasources/sync-statuses/",
                {"uuids": str(self.datasource.uuid)},
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertLessEqual(len(queries), 3)
        self.assertEqual(
            response.data,
            [
                {
                    "uuid": str(self.datasource.uuid),
                    "current_sync": {
                        "id": task.id,
                        "task_id": task.task_id,
                        "task_name": task.task_name,
                        "status": task.status,
                        "started_at": None,
                        "created_at": task.created_at,
                        "progress_step": "",
                        "progress_message": "Indexing files",
                        "progress_percent": 42,
                        "phase": "",
                        "overall_progress_percent": None,
                        "phase_progress": {},
                        "progress_counts": {},
                        "last_substantive_progress_at": None,
                    },
                    "sync_state": {
                        "enabled": schedule.enabled,
                        "last_status": "running",
                        "last_error": "",
                        "last_run_at": None,
                        "last_metrics": {},
                        "next_run_at": None,
                    },
                    "last_synced_at": None,
                    "last_error": "",
                }
            ],
        )

    def test_datasource_delete_rejects_active_sync(self):
        TaskExecution.objects.create(
            task_id="running-datasource-sync",
            task_name="source_sync:Repo Cache",
            module="lens_datasource",
            status="STARTED",
            metadata={"datasource_uuid": str(self.datasource.uuid)},
        )

        response = self.client.delete(
            f"/api/lens/admin/datasources/{self.datasource.uuid}/"
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["detail"], "DATASOURCE_SYNC_IN_PROGRESS")
        self.assertTrue(
            DataSource.objects.filter(pk=self.datasource.pk).exists()
        )

    def test_check_datasource_path_blocks_existing_datasource_path(self):
        response = self.client.post(
            f"/api/lens/admin/lensnodes/{self.lensnode.uuid}/" "check-datasource-path/",
            {
                "target_path": "/workspace/repo-cache",
                "source_type": "git",
                "config": {"repo_url": "https://example.com/other.git"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "blocked")
        self.assertEqual(
            response.data["message_code"],
            "datasource_path_in_use",
        )
        self.assertEqual(
            response.data["datasource_uuid"],
            str(self.datasource.uuid),
        )

    @patch("lens.views.lensnodes.check_datasource_path")
    def test_check_managed_workspace_path_blocks_nested_datasource(
        self,
        check_path,
    ):
        response = self.client.post(
            f"/api/lens/admin/lensnodes/{self.lensnode.uuid}/" "check-datasource-path/",
            {
                "target_path": "/workspace/repo-cache/restored",
                "source_type": "managed_workspace",
                "config": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "blocked")
        self.assertEqual(
            response.data["message_code"],
            "datasource_path_in_use",
        )
        check_path.assert_not_called()

    @patch("lens.views.lensnodes.check_datasource_path")
    def test_check_datasource_path_allows_current_datasource_path(
        self,
        check_path,
    ):
        check_path.return_value = {
            "status": "available",
            "message_code": "git_update",
        }

        response = self.client.post(
            f"/api/lens/admin/lensnodes/{self.lensnode.uuid}/" "check-datasource-path/",
            {
                "datasource_uuid": str(self.datasource.uuid),
                "target_path": "/workspace/repo-cache",
                "source_type": "git",
                "config": {"repo_url": "https://example.com/repo.git"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "available")
        check_path.assert_called_once()

    def test_datasource_create_enqueues_initial_sync(self):
        payload = {
            "name": "Initial Sync Repo",
            "source_type": "git",
            "lensnode_uuid": str(self.lensnode.uuid),
            "config": {"repo_url": "https://example.com/repo.git"},
            "target_path": "/workspace/initial-sync",
        }

        with patch(
            "lens.views.datasources.source_sync_task.apply_async"
        ) as apply_async:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    "/api/lens/admin/datasources/",
                    payload,
                    format="json",
                )

        self.assertEqual(response.status_code, 201)
        task_id = response.data["initial_sync_task_id"]
        apply_async.assert_called_once_with(
            args=[response.data["uuid"], "initial", task_id],
            task_id=ANY,
        )
        celery_task_id = apply_async.call_args.kwargs["task_id"]
        self.assertNotEqual(celery_task_id, task_id)
        task = TaskExecution.objects.get(task_id=task_id)
        self.assertEqual(task.task_name, "datasource_sync:Initial Sync Repo")
        self.assertEqual(task.module, "lens_datasource")
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.created_by, self.user)
        self.assertEqual(task.metadata["celery_task_id"], celery_task_id)

    @patch("lens.serializers.check_datasource_path")
    def test_managed_workspace_create_does_not_enqueue_sync(self, check_path):
        check_path.return_value = {
            "status": "available",
            "exists": True,
            "is_directory": True,
            "message": "Managed workspace directory is available.",
        }
        payload = {
            "name": "Restored Snapshot",
            "source_type": "managed_workspace",
            "lensnode_uuid": str(self.lensnode.uuid),
            "target_path": "/workspace/restores/finance",
            "config": {},
            "sync_policy": {},
        }

        with patch(
            "lens.views.datasources.source_sync_task.apply_async"
        ) as apply_async:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    "/api/lens/admin/datasources/",
                    payload,
                    format="json",
                )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["availability_status"], "available")
        self.assertNotIn("initial_sync_task_id", response.data)
        apply_async.assert_not_called()
        datasource = DataSource.objects.get(uuid=response.data["uuid"])
        self.assertFalse(
            ScheduledTask.objects.filter(target_id=datasource.uuid).exists()
        )

    @patch("lens.serializers.check_datasource_path")
    def test_managed_workspace_create_requires_existing_directory(
        self,
        check_path,
    ):
        check_path.return_value = {
            "status": "blocked",
            "exists": False,
            "is_directory": False,
            "message": "Managed workspace directory does not exist.",
        }

        response = self.client.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Missing Snapshot",
                "source_type": "managed_workspace",
                "lensnode_uuid": str(self.lensnode.uuid),
                "target_path": "/workspace/restores/missing",
                "config": {},
                "sync_policy": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["target_path"][0],
            "MANAGED_WORKSPACE_DIRECTORY_REQUIRED",
        )

    def test_managed_workspace_rejects_path_outside_workspace(self):
        response = self.client.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Outside Snapshot",
                "source_type": "managed_workspace",
                "lensnode_uuid": str(self.lensnode.uuid),
                "target_path": "/etc/data",
                "config": {},
                "sync_policy": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["target_path"][0],
            "LENS_SOURCE_TARGET_PATH_INVALID",
        )

    @patch("lens.serializers.check_datasource_path")
    def test_managed_workspace_rejects_overlapping_datasource_path(
        self,
        check_path,
    ):
        response = self.client.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Managed Parent",
                "source_type": "managed_workspace",
                "lensnode_uuid": str(self.lensnode.uuid),
                "target_path": "/workspace",
                "config": {},
                "sync_policy": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        check_path.assert_not_called()

        response = self.client.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Managed Child",
                "source_type": "managed_workspace",
                "lensnode_uuid": str(self.lensnode.uuid),
                "target_path": "/workspace/repo-cache/restored",
                "config": {},
                "sync_policy": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("overlapping", response.data["target_path"][0])
        check_path.assert_not_called()

    def test_managed_workspace_manual_sync_is_rejected(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )

        with patch(
            "lens.views.datasources.source_sync_task.apply_async"
        ) as apply_async:
            response = self.client.post(
                f"/api/lens/admin/datasources/{datasource.uuid}/sync/",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data["detail"],
            "DATASOURCE_SYNC_NOT_SUPPORTED",
        )
        apply_async.assert_not_called()

    def test_managed_workspace_upload_registers_and_queues_task(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        uploaded = SimpleUploadedFile(
            "requirements.pdf",
            b"pdf-content",
            content_type="application/pdf",
        )

        with (
            patch(
                "lens.views.datasources.default_storage.save",
                return_value="datasource-uploads/requirements.pdf",
            ) as save,
            patch(
                "lens.views.datasources.datasource_upload_task.apply_async"
            ) as apply_async,
        ):
            response = self.client.post(
                f"/api/lens/admin/datasources/{datasource.uuid}/upload/",
                {"file": uploaded},
                format="multipart",
            )

        self.assertEqual(response.status_code, 202, response.data)
        task_id = response.data["task_id"]
        self.assertEqual(response.data["filename"], "requirements.pdf")
        save.assert_called_once()
        apply_async.assert_called_once_with(
            args=[
                str(datasource.uuid),
                "datasource-uploads/requirements.pdf",
                "requirements.pdf",
            ],
            task_id=task_id,
        )
        task = TaskExecution.objects.get(task_id=task_id)
        self.assertEqual(task.module, "lens_datasource_upload")
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.created_by, self.user)
        self.assertEqual(task.metadata["filename"], "requirements.pdf")

    def test_datasource_upload_rejects_unsupported_source_and_file(self):
        unsupported = SimpleUploadedFile(
            "notes.txt",
            b"not supported",
            content_type="text/plain",
        )

        response = self.client.post(
            f"/api/lens/admin/datasources/{self.datasource.uuid}/upload/",
            {"file": unsupported},
            format="multipart",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data["detail"],
            "DATASOURCE_UPLOAD_NOT_SUPPORTED",
        )

        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        response = self.client.post(
            f"/api/lens/admin/datasources/{datasource.uuid}/upload/",
            {"file": unsupported},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "DATASOURCE_UPLOAD_FILE_TYPE_UNSUPPORTED",
        )

        response = self.client.post(
            f"/api/lens/admin/datasources/{datasource.uuid}/upload/",
            {},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "DATASOURCE_UPLOAD_FILE_REQUIRED",
        )

    def test_managed_workspace_conversion_registers_trackable_task(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )

        with patch(
            "lens.views.datasources.datasource_conversion_task.apply_async"
        ) as apply_async:
            response = self.client.post(
                f"/api/lens/admin/datasources/{datasource.uuid}/convert/",
                {
                    "conversion": {
                        "document": True,
                        "max_file_size_mb": 100,
                    },
                    "force": False,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 202, response.data)
        task_id = response.data["task_id"]
        task = TaskExecution.objects.get(task_id=task_id)
        self.assertEqual(response.data["task_execution_id"], task.id)
        self.assertEqual(task.module, "lens_datasource_conversion")
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.created_by, self.user)
        self.assertEqual(
            task.metadata["conversion"],
            {"document": True, "max_file_size_mb": 100},
        )
        apply_async.assert_called_once_with(
            args=[
                str(datasource.uuid),
                {"document": True, "max_file_size_mb": 100},
                False,
                task_id,
            ],
            task_id=ANY,
        )
        datasource.refresh_from_db()
        self.assertEqual(datasource.last_conversion_status, "PENDING")
        self.assertIsNone(datasource.last_conversion_at)
        detail = self.client.get(f"/api/lens/admin/datasources/{datasource.uuid}/")
        self.assertEqual(detail.data["last_conversion_status"], "PENDING")
        self.assertIsNone(detail.data["last_conversion_at"])
        tasks = self.client.get(
            f"/api/lens/admin/datasources/{datasource.uuid}/" "conversion-tasks/"
        )
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(tasks.data["results"][0]["task_id"], task_id)

    def test_non_managed_datasource_conversion_is_rejected(self):
        with patch(
            "lens.views.datasources.datasource_conversion_task.apply_async"
        ) as apply_async:
            response = self.client.post(
                f"/api/lens/admin/datasources/{self.datasource.uuid}/convert/",
                {"conversion": {"document": True}},
                format="json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data["detail"],
            "DATASOURCE_CONVERSION_NOT_SUPPORTED",
        )
        apply_async.assert_not_called()

    def test_managed_workspace_conversion_validates_policy(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )

        response = self.client.post(
            f"/api/lens/admin/datasources/{datasource.uuid}/convert/",
            {"conversion": {"document": "yes"}},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("conversion.document", response.data["conversion"][0])

    def test_managed_workspace_conversion_requires_admin(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        user = User.objects.create_user(
            username="regular-user",
            email="regular-user@example.com",
            password="pass12345",
        )
        self.client.force_authenticate(user)

        response = self.client.post(
            f"/api/lens/admin/datasources/{datasource.uuid}/convert/",
            {"conversion": {"document": True}},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_cancel_managed_workspace_conversion_waits_for_callback(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
            last_conversion_status="STARTED",
        )
        task = TaskExecution.objects.create(
            task_id="running-conversion",
            task_name="datasource_convert:Managed Snapshot",
            module="lens_datasource_conversion",
            status="STARTED",
            metadata={
                "datasource_uuid": str(datasource.uuid),
                "lock_token": "running-conversion",
                "celery_task_id": "celery-conversion",
            },
        )
        acquire_datasource_lock(
            datasource.uuid,
            token="running-conversion",
            ttl_s=60,
        )

        with (
            patch("core.celery.app.control.revoke") as revoke,
            patch(
                "lens.views.datasources." "cancel_datasource_conversion_on_lensnode"
            ) as cancel,
        ):
            response = self.client.post(
                f"/api/lens/admin/datasources/{datasource.uuid}/" "cancel-conversion/",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        revoke.assert_called_once_with("celery-conversion", terminate=False)
        cancel.assert_called_once_with(self.lensnode, "running-conversion")
        task.refresh_from_db()
        datasource.refresh_from_db()
        self.assertEqual(task.status, "CANCELLING")
        self.assertEqual(datasource.last_conversion_status, "CANCELLING")
        self.assertIsNone(datasource.last_conversion_at)

        with self.assertRaises(SourceSyncBusy):
            acquire_datasource_lock(
                datasource.uuid,
                token="new-conversion",
                ttl_s=60,
            )
        release_datasource_lock(
            datasource.uuid,
            token="running-conversion",
        )

    @patch("lens.views.datasources.check_datasource_path")
    def test_managed_workspace_refresh_updates_availability(self, check_path):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
            availability_status=DataSource.AvailabilityStatus.AVAILABLE,
        )
        check_path.return_value = {
            "status": "blocked",
            "exists": False,
            "is_directory": False,
            "message": "Managed workspace directory does not exist.",
        }

        response = self.client.post(
            f"/api/lens/admin/datasources/{datasource.uuid}/" "refresh-availability/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["availability_status"], "unavailable")
        datasource.refresh_from_db()
        self.assertIsNotNone(datasource.availability_checked_at)

        check_path.side_effect = DataSourceDispatchError("LENSNODE_OFFLINE")
        response = self.client.post(
            f"/api/lens/admin/datasources/{datasource.uuid}/" "refresh-availability/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["availability_status"], "error")
        self.assertEqual(
            response.data["availability_message"],
            "LENSNODE_OFFLINE",
        )

    @patch("lens.serializers.check_datasource_path")
    def test_managed_workspace_metadata_update_skips_path_check(
        self,
        check_path,
    ):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )

        response = self.client.patch(
            f"/api/lens/admin/datasources/{datasource.uuid}/",
            {"name": "Renamed Snapshot"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        check_path.assert_not_called()

    def test_managed_workspace_delete_only_removes_catalog_record(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )

        with patch("lens.datasource_services._send_lensnode_command") as send:
            response = self.client.delete(
                f"/api/lens/admin/datasources/{datasource.uuid}/"
            )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(DataSource.objects.filter(pk=datasource.pk).exists())
        send.assert_not_called()

    def test_datasource_delete_cleans_plugin_audit_records(self):
        material = SecretMaterial.objects.create(name="Datasource PAT")
        version = SecretVersion.objects.create(
            material=material,
            encrypted_value="encrypted",
        )
        connection_obj = Connection.objects.create(
            name="Datasource GitHub",
            plugin_key="github",
            endpoint="https://github.com",
            secret_version=version,
        )
        self.datasource.connection = connection_obj
        self.datasource.plugin_key = "github"
        self.datasource.save(update_fields=["connection", "plugin_key"])
        snapshot = ExecutionSnapshot.objects.create(
            kind=ExecutionSnapshot.Kind.DATASOURCE_SYNC,
            connection=connection_obj,
            datasource=self.datasource,
            secret_version=version,
            plugin_key="github",
            plugin_version="1.0.0",
            protocol_version=1,
        )
        invocation = PluginInvocation.objects.create(
            snapshot=snapshot,
            connection=connection_obj,
            datasource=self.datasource,
            lensnode=self.lensnode,
            kind=ExecutionSnapshot.Kind.DATASOURCE_SYNC,
            plugin_key="github",
        )
        lease = CredentialLease.objects.create(
            snapshot=snapshot,
            lensnode=self.lensnode,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        schedule = ScheduledTask.objects.create(
            name=f"source_sync:{self.datasource.uuid}",
            task_type=ScheduledTask.TaskType.SOURCE_SYNC,
            target_type="datasource",
            target_id=self.datasource.uuid,
        )

        response = self.client.delete(
            f"/api/lens/admin/datasources/{self.datasource.uuid}/"
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            DataSource.objects.filter(pk=self.datasource.pk).exists()
        )
        self.assertFalse(
            ExecutionSnapshot.objects.filter(pk=snapshot.pk).exists()
        )
        self.assertFalse(
            PluginInvocation.objects.filter(pk=invocation.pk).exists()
        )
        self.assertFalse(
            CredentialLease.objects.filter(pk=lease.pk).exists()
        )
        self.assertFalse(
            ScheduledTask.objects.filter(pk=schedule.pk).exists()
        )

    def test_datasource_manual_sync_registers_task(self):
        with patch(
            "lens.views.datasources.source_sync_task.apply_async"
        ) as apply_async:
            response = self.client.post(
                f"/api/lens/admin/datasources/{self.datasource.uuid}/sync/",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 202)
        task_id = response.data["task_id"]
        self.assertEqual(
            response.data["task_execution_id"],
            TaskExecution.objects.get(task_id=task_id).id,
        )
        apply_async.assert_called_once_with(
            args=[str(self.datasource.uuid), "manual", task_id],
            task_id=ANY,
        )
        celery_task_id = apply_async.call_args.kwargs["task_id"]
        self.assertNotEqual(celery_task_id, task_id)
        task = TaskExecution.objects.get(task_id=task_id)
        self.assertEqual(task.task_name, "datasource_sync:Repo Cache")
        self.assertEqual(task.module, "lens_datasource")
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.created_by, self.user)
        self.assertEqual(task.metadata["celery_task_id"], celery_task_id)

    def test_datasource_sync_tasks_uses_a_fixed_query_count(self):
        for index in range(10):
            TaskExecution.objects.create(
                task_id=f"datasource-sync-history-{index}",
                task_name="datasource_sync:Repo Cache",
                module="lens_datasource",
                status="SUCCESS",
                created_by=self.user,
                metadata={
                    "datasource_uuid": str(self.datasource.uuid),
                    "trigger": "scheduled",
                },
            )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                f"/api/lens/admin/datasources/{self.datasource.uuid}/sync-tasks/",
                {"page": 1, "page_size": 10, "metadata_fields": "trigger"},
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertLessEqual(len(queries), 3)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertEqual(response.data["results"][0]["metadata"], {
            "trigger": "scheduled",
        })

    @patch("lens.views.datasources.list_datasource_files")
    def test_datasource_files_returns_manifest_catalog(self, list_files):
        list_files.return_value = {
            "count": 1,
            "page": 1,
            "page_size": 20,
            "results": [
                {
                    "path": "MIW Production Export/report.pdf",
                    "name": "report.pdf",
                    "extension": "pdf",
                    "sync_status": "synced",
                    "conversion_status": "success",
                    "source_updated_at": "2026-09-09T00:00:00Z",
                    "converted_at": "2026-09-09T00:01:00Z",
                    "conversion_error": "",
                }
            ],
        }

        response = self.client.get(
            f"/api/lens/admin/datasources/{self.datasource.uuid}/files/",
            {"query": "MIW", "page": 1, "page_size": 20},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["path"],
            "MIW Production Export/report.pdf",
        )
        self.assertNotIn("/workspace", response.data["results"][0]["path"])
        list_files.assert_called_once()

    def test_disabled_datasource_rejects_manual_sync(self):
        self.datasource.status = DataSource.Status.DISABLED
        self.datasource.save(update_fields=["status", "updated_at"])

        with patch(
            "lens.views.datasources.source_sync_task.apply_async"
        ) as apply_async:
            response = self.client.post(
                f"/api/lens/admin/datasources/{self.datasource.uuid}/sync/",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "DATASOURCE_DISABLED")
        apply_async.assert_not_called()

    def test_cancel_datasource_sync_waits_for_stop_confirmation(self):
        task = TaskExecution.objects.create(
            task_id="running-sync",
            task_name="datasource_sync:Repo Cache",
            module="lens_datasource",
            status="STARTED",
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "lock_token": "running-sync",
                "celery_task_id": "celery-sync",
            },
        )
        acquire_datasource_lock(
            self.datasource.uuid,
            token="running-sync",
            ttl_s=60,
        )

        with (
            patch("core.celery.app.control.revoke") as revoke,
            patch(
                "lens.views.datasources.cancel_datasource_sync_on_lensnode"
            ) as cancel,
        ):
            url = f"/api/lens/admin/datasources/{self.datasource.uuid}" "/cancel-sync/"
            response = self.client.post(
                url,
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        revoke.assert_called_once_with(
            "celery-sync",
            terminate=False,
        )
        cancel.assert_called_once_with(self.lensnode, "running-sync")
        task.refresh_from_db()
        self.assertEqual(task.status, "CANCELLING")

        delete_response = self.client.delete(
            f"/api/lens/admin/datasources/{self.datasource.uuid}/"
        )
        self.assertEqual(delete_response.status_code, 409)

        with self.assertRaises(SourceSyncBusy):
            acquire_datasource_lock(
                self.datasource.uuid,
                token="new-sync",
                ttl_s=60,
            )

        complete_datasource_sync_task(
            task.task_id,
            {
                "status": "cancelled",
                "error": "DATASOURCE_SYNC_CANCELLED",
            },
        )

        task.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        acquire_datasource_lock(
            self.datasource.uuid,
            token="new-sync",
            ttl_s=60,
        )
        release_datasource_lock(self.datasource.uuid, token="new-sync")

    def test_datasource_create_uses_lensnode_workspace_path(self):
        self.lensnode.workspace_path = "/data/lens-workspace"
        self.lensnode.save(update_fields=["workspace_path", "updated_at"])
        payload = {
            "name": "Custom Workspace Repo",
            "source_type": "git",
            "lensnode_uuid": str(self.lensnode.uuid),
            "config": {"repo_url": "https://example.com/repo.git"},
            "target_path": "repos/custom",
        }

        response = self.client.post(
            "/api/lens/admin/datasources/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data["target_path"],
            "/data/lens-workspace/repos/custom",
        )

    def test_datasource_create_rejects_path_outside_lensnode_workspace(self):
        self.lensnode.workspace_path = "/data/lens-workspace"
        self.lensnode.save(update_fields=["workspace_path", "updated_at"])
        payload = {
            "name": "Outside Workspace Repo",
            "source_type": "git",
            "lensnode_uuid": str(self.lensnode.uuid),
            "config": {"repo_url": "https://example.com/repo.git"},
            "target_path": "/workspace/old-root",
        }

        response = self.client.post(
            "/api/lens/admin/datasources/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("LENS_SOURCE_TARGET_PATH_INVALID", str(response.data))

    def test_datasource_rejects_inline_credentials(self):
        payload = {
            "name": "Secret Repo",
            "source_type": "git",
            "lensnode_uuid": str(self.lensnode.uuid),
            "config": {
                "repo_url": "https://example.com/repo.git",
                "token": "secret",
            },
            "target_path": "/workspace/secret",
        }

        response = self.client.post(
            "/api/lens/admin/datasources/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("secret fields", str(response.data))

    def test_datasource_allows_git_credential_binding(self):
        credential = DataSourceCredential.objects.create(
            name="Git token",
            provider=DataSourceCredential.Provider.GENERIC,
            auth_type=DataSourceCredential.AuthType.HTTPS_TOKEN,
        )
        credential.set_secret("ghp_example")
        credential.save()
        payload = {
            "name": "Token Repo",
            "source_type": "git",
            "lensnode_uuid": str(self.lensnode.uuid),
            "credential_uuid": str(credential.uuid),
            "config": {
                "repo_url": "https://example.com/repo.git",
                "auth_scheme": "token",
            },
            "target_path": "/workspace/token-repo",
        }

        response = self.client.post(
            "/api/lens/admin/datasources/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["credential_configured"])
        self.assertNotIn("access_token", response.data["config"])
        datasource = DataSource.objects.get(uuid=response.data["uuid"])
        self.assertNotIn("access_token", datasource.config)
        self.assertEqual(datasource.credential, credential)

    def test_datasource_allows_git_no_auth_credential_binding(self):
        credential = DataSourceCredential.objects.create(
            name="Public GitHub repo",
            provider=DataSourceCredential.Provider.GITHUB,
            auth_type=DataSourceCredential.AuthType.NONE,
            endpoint_url="https://github.com",
            scope_config={"organization_url": "https://github.com/example/repo"},
        )
        payload = {
            "name": "Public Repo",
            "source_type": "git",
            "lensnode_uuid": str(self.lensnode.uuid),
            "credential_uuid": str(credential.uuid),
            "config": {
                "repo_url": "https://github.com/example/repo.git",
                "auth_scheme": "none",
            },
            "target_path": "/workspace/public-repo",
        }

        response = self.client.post(
            "/api/lens/admin/datasources/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(credential.has_secret)
        datasource = DataSource.objects.get(uuid=response.data["uuid"])
        self.assertEqual(datasource.credential, credential)
        self.assertNotIn("access_token", datasource.config)

    def test_datasource_allows_feishu_drive_folder_credential_binding(self):
        credential = DataSourceCredential.objects.create(
            name="Feishu app",
            provider=DataSourceCredential.Provider.FEISHU,
            auth_type=DataSourceCredential.AuthType.FEISHU_APP,
        )
        credential.set_secret("cli_example:secret_example")
        credential.save()
        payload = {
            "name": "Feishu Folder",
            "source_type": "feishu",
            "lensnode_uuid": str(self.lensnode.uuid),
            "credential_uuid": str(credential.uuid),
            "config": {
                "sync_mode": "drive_folder",
                "folder_url": "https://example.feishu.cn/drive/folder/fld1",
                "recursive": True,
                "max_depth": 5,
            },
            "target_path": "/workspace/feishu-folder",
        }

        response = self.client.post(
            "/api/lens/admin/datasources/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["credential_configured"])
        self.assertNotIn("app_id", response.data["config"])
        self.assertNotIn("app_secret", response.data["config"])
        datasource = DataSource.objects.get(uuid=response.data["uuid"])
        self.assertNotIn("app_id", datasource.config)
        self.assertNotIn("app_secret", datasource.config)
        self.assertEqual(datasource.credential, credential)

    def test_lensnode_tests_datasource_connection(self):
        with patch(
            "lens.views.lensnodes.test_datasource_connection",
            return_value={
                "status": "success",
                "message_code": "git_branch_available",
            },
        ) as test_connection:
            response = self.client.post(
                (
                    "/api/lens/admin/lensnodes/"
                    f"{self.lensnode.uuid}/test-datasource-connection/"
                ),
                {
                    "source_type": "git",
                    "config": {
                        "repo_url": "https://example.com/repo.git",
                        "branch": "main",
                    },
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["message_code"], "git_branch_available")
        test_connection.assert_called_once()

    def test_git_connection_test_injects_github_token_credential(self):
        credential = DataSourceCredential.objects.create(
            name="GitHub token",
            provider=DataSourceCredential.Provider.GITHUB,
            auth_type=DataSourceCredential.AuthType.HTTPS_TOKEN,
            endpoint_url="https://github.com",
            sync_scope="service",
            scope_config={"organization_url": "https://github.com/CarltonXu/"},
        )
        credential.set_secret("ghp_example")
        credential.save()
        sent_payloads = []

        def capture_command(_lensnode, payload):
            sent_payloads.append(payload)

        with (
            patch(
                "lens.datasource_services._send_lensnode_command",
                side_effect=capture_command,
            ),
            patch(
                "lens.datasource_services._wait_cache_result",
                return_value={"status": "success"},
            ),
        ):
            run_datasource_connection_test(
                self.lensnode,
                "git",
                config={
                    "repo_url": "https://github.com/CarltonXu/",
                    "auth_scheme": "token",
                },
                credential_uuid=str(credential.uuid),
            )

        config = sent_payloads[0]["config"]
        self.assertEqual(config["provider"], "github")
        self.assertEqual(config["endpoint_url"], "https://github.com")
        self.assertEqual(config["access_token"], "ghp_example")
        self.assertEqual(
            config["credential_scope"]["organization_url"],
            "https://github.com/CarltonXu/",
        )

    def test_system_health_returns_node_and_retention_tasks(self):
        ScheduledTask.objects.create(
            name="lensnode_cleanup",
            task_type="lensnode_cleanup",
            enabled=True,
        )
        ScheduledTask.objects.create(
            name="lensnode_health",
            task_type="lensnode_health",
            enabled=True,
        )

        response = self.client.get("/api/lens/admin/global-settings/system-health/")

        self.assertEqual(response.status_code, 200)
        task_types = {item["task_type"] for item in response.data}
        self.assertIn("lensnode_cleanup", task_types)
        self.assertIn("lensnode_health", task_types)

    def test_system_health_patch_updates_enabled_state(self):
        task = ScheduledTask.objects.create(
            name="lensnode_cleanup",
            task_type="lensnode_cleanup",
            enabled=True,
        )

        response = self.client.patch(
            "/api/lens/admin/global-settings/system-health/",
            {
                "task_type": "lensnode_cleanup",
                "enabled": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertFalse(task.enabled)
        self.assertFalse(response.data["enabled"])

    def test_global_setting_interval_syncs_periodic_task(self):
        from django_celery_beat.models import PeriodicTask

        response = self.client.patch(
            "/api/lens/admin/global-settings/" "lensnode_cleanup.interval_seconds/",
            {
                "key": "lensnode_cleanup.interval_seconds",
                "value": 7200,
                "description": "Cleanup interval",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        task = PeriodicTask.objects.get(name="lens-lensnode-cleanup")
        self.assertEqual(task.interval.every, 7200)
        self.assertEqual(task.interval.period, "seconds")


class AssistantAccessTests(TestCase):
    """Visibility + group/user authorization for assistants and QA."""

    def setUp(self):
        self.lensnode = LensNode.objects.create(
            name="Node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            available_dirs=[{"path": "/workspace/repo"}],
            tasks=[{"name": "knowledge_qa", "description": "qa"}],
        )
        self.assistant = Assistant.objects.create(
            name="Private One",
            description="Answers private workspace questions.",
            slug="private-one",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[{"path": "/workspace/repo"}],
            status=Assistant.Status.ACTIVE,
            visibility=Assistant.Visibility.PRIVATE,
        )
        self.admin = get_user_model().objects.create_user(
            username="aac-admin", password="x", is_staff=True
        )
        self.member = get_user_model().objects.create_user(
            username="aac-member", password="x"
        )

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_public_view_404_for_private_assistant(self):
        resp = self.client.get(f"/api/lens/public/assistants/{self.assistant.slug}/")
        self.assertEqual(resp.status_code, 404)

    def test_public_view_200_when_public(self):
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.save(update_fields=["visibility"])
        resp = self.client.get(f"/api/lens/public/assistants/{self.assistant.slug}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["description"], self.assistant.description)

    def test_list_hides_private_from_unauthorized_then_group_grant(self):
        client = self._client(self.member)
        slugs = [a["slug"] for a in client.get("/api/lens/assistants/").data["results"]]
        self.assertNotIn(self.assistant.slug, slugs)

        group = Group.objects.create(name="team")
        self.member.groups.add(group)
        AssistantAccess.objects.create(assistant=self.assistant, group=group)

        slugs = [a["slug"] for a in client.get("/api/lens/assistants/").data["results"]]
        self.assertIn(self.assistant.slug, slugs)

    def test_list_shows_private_to_admin(self):
        slugs = [
            a["slug"]
            for a in self._client(self.admin)
            .get("/api/lens/assistants/")
            .data["results"]
        ]
        self.assertIn(self.assistant.slug, slugs)

    def test_session_create_403_then_201_with_user_grant(self):
        client = self._client(self.member)
        payload = {"assistant_uuid": str(self.assistant.uuid)}
        resp = client.post("/api/lens/sessions/", payload, format="json")
        self.assertEqual(resp.status_code, 403)

        AssistantAccess.objects.create(assistant=self.assistant, user=self.member)
        resp = client.post("/api/lens/sessions/", payload, format="json")
        self.assertEqual(resp.status_code, 201)

    def test_run_blocked_after_access_revoked(self):
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.save(update_fields=["visibility"])
        client = self._client(self.member)
        session = client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant.uuid)},
            format="json",
        )
        self.assertEqual(session.status_code, 201)
        session_uuid = session.data["uuid"]

        self.assistant.visibility = Assistant.Visibility.PRIVATE
        self.assistant.save(update_fields=["visibility"])

        run = client.post(
            f"/api/lens/sessions/{session_uuid}/runs/",
            {"question": "still there?"},
            format="json",
        )
        self.assertEqual(run.status_code, 403)

    def test_archived_assistant_cannot_start_new_conversations(self):
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.status = "archived"
        self.assistant.save(update_fields=["visibility", "status"])
        client = self._client(self.member)

        response = client.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant.uuid)},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_public_qa_requires_login_for_private_assistant(self):
        share = SharedQA.objects.create(
            token="tok-private",
            assistant=self.assistant,
            assistant_name=self.assistant.name,
            assistant_slug=self.assistant.slug,
            question="q",
            answer="a",
            title="t",
            is_listed=True,
            status=SharedQA.Status.PUBLISHED,
        )
        list_resp = self.client.get(
            f"/api/lens/public/assistants/{self.assistant.slug}/qa/"
        )
        self.assertEqual(list_resp.status_code, 403)
        self.assertEqual(
            list_resp.data["code"],
            "AUTHENTICATION_REQUIRED",
        )
        single_resp = self.client.get(f"/api/lens/public/qa/{share.token}/")
        self.assertEqual(single_resp.status_code, 403)
        self.assertEqual(
            single_resp.data["code"],
            "AUTHENTICATION_REQUIRED",
        )

    def test_access_grants_round_trip_via_serializer(self):
        group = Group.objects.create(name="grp")
        client = self._client(self.admin)
        resp = client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {
                "visibility": "private",
                "access_grants": [{"type": "group", "id": group.pk}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.assistant.access_grants.filter(group=group).count(), 1)
        grants = resp.data["access_grants"]
        self.assertEqual(grants, [{"type": "group", "id": group.pk, "name": "grp"}])

    def test_user_access_grants_include_selector_metadata(self):
        user = User.objects.create_user(
            username="selector-user",
            email="selector@example.com",
            password="x",
        )
        client = self._client(self.admin)
        resp = client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {
                "visibility": "private",
                "access_grants": [{"type": "user", "id": user.pk}],
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data["access_grants"],
            [
                {
                    "type": "user",
                    "id": user.pk,
                    "name": "selector-user",
                    "username": "selector-user",
                    "email": "selector@example.com",
                }
            ],
        )

    def test_write_requires_admin_console(self):
        self.assistant.visibility = Assistant.Visibility.PUBLIC
        self.assistant.save(update_fields=["visibility"])
        client = self._client(self.member)

        update = client.patch(
            f"/api/lens/assistants/{self.assistant.uuid}/",
            {"name": "Renamed"},
            format="json",
        )
        self.assertEqual(update.status_code, 403)

        create = client.post(
            "/api/lens/assistants/",
            {
                "name": "X",
                "slug": "x-new",
                "lensnode_uuid": str(self.lensnode.uuid),
                "selected_task": "knowledge_qa",
                "selected_dirs": [{"path": "/workspace/repo"}],
            },
            format="json",
        )
        self.assertEqual(create.status_code, 403)

        archive = client.post(
            f"/api/lens/assistants/{self.assistant.uuid}/archive/",
        )
        self.assertEqual(archive.status_code, 403)


class AdminAccessSubjectTests(TestCase):
    """Admin user/group insights and stable history filters."""

    def setUp(self):
        self.node = LensNode.objects.create(
            name="Access detail node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            tasks=[{"name": "general_chat", "description": "chat"}],
        )
        self.admin = User.objects.create_user(
            username="access-admin",
            password="x",
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username="detail-user",
            email="detail@example.com",
            password="x",
        )
        self.group = Group.objects.create(name="Detail group")
        self.user.groups.add(self.group)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _assistant(self, name, slug, visibility="private"):
        return Assistant.objects.create(
            name=name,
            slug=slug,
            lensnode=self.node,
            selected_task="general_chat",
            visibility=visibility,
        )

    def test_user_detail_combines_access_sources_and_activity(self):
        direct = self._assistant("Direct", "detail-direct")
        grouped = self._assistant("Grouped", "detail-grouped")
        public = self._assistant(
            "Public",
            "detail-public",
            Assistant.Visibility.PUBLIC,
        )
        history = self._assistant("History", "detail-history")
        AssistantAccess.objects.create(assistant=direct, user=self.user)
        AssistantAccess.objects.create(assistant=grouped, group=self.group)
        session = Session.objects.create(
            assistant=history,
            user=self.user,
        )
        create_execution_run(session, "History question", enqueue=False)

        response = self.client.get(f"/api/lens/admin/access/users/{self.user.pk}/")

        self.assertEqual(response.status_code, 200, response.data)
        rows = {item["slug"]: item for item in response.data["assistants"]}
        self.assertEqual(rows[direct.slug]["access_sources"], ["direct"])
        self.assertEqual(rows[grouped.slug]["access_sources"], ["group"])
        self.assertEqual(rows[public.slug]["access_sources"], ["public"])
        self.assertEqual(rows[history.slug]["access_sources"], ["history"])
        self.assertEqual(rows[history.slug]["conversations"], 1)
        self.assertEqual(rows[history.slug]["qa_records"], 1)
        self.assertEqual(response.data["stats"]["conversations"], 1)
        self.assertEqual(response.data["stats"]["qa_records"], 1)

    def test_group_detail_paginates_searches_and_counts(self):
        second = User.objects.create_user(
            username="another-member",
            email="another@example.com",
            password="x",
        )
        second.groups.add(self.group)
        assistant = self._assistant("Group assistant", "group-assistant")
        AssistantAccess.objects.create(assistant=assistant, group=self.group)
        role = Role.objects.create(
            name="Group admin",
            visible_features=["admin_console"],
        )
        role.groups.add(self.group)

        response = self.client.get(
            f"/api/lens/admin/access/groups/{self.group.pk}/",
            {"search": "detail@", "page": 1, "page_size": 1},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["stats"]["members"], 2)
        self.assertEqual(response.data["stats"]["assigned_assistants"], 1)
        self.assertEqual(response.data["stats"]["roles"], 1)
        self.assertEqual(response.data["members"]["count"], 1)
        self.assertEqual(
            response.data["members"]["results"][0]["id"],
            self.user.pk,
        )
        self.assertEqual(
            response.data["assistants"][0]["access_sources"],
            ["group"],
        )

    def test_history_filters_by_exact_user_group_and_assistant(self):
        assistant = self._assistant("Filtered", "filtered-assistant")
        other = User.objects.create_user(
            username="other-detail-user",
            password="x",
        )
        other.groups.add(self.group)
        first_session = Session.objects.create(
            assistant=assistant,
            user=self.user,
        )
        second_session = Session.objects.create(
            assistant=assistant,
            user=other,
        )
        first = create_execution_run(
            first_session,
            "First question",
            enqueue=False,
        )
        create_execution_run(second_session, "Second question", enqueue=False)

        response = self.client.get(
            "/api/lens/admin/runs/",
            {
                "user_id": self.user.pk,
                "group_id": self.group.pk,
                "assistant": assistant.slug,
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["uuid"], str(first.uuid))

    def test_role_granted_admin_can_open_detail_and_history(self):
        role = Role.objects.create(
            name="Role granted admin",
            visible_features=["admin_console"],
        )
        role.users.add(self.user)
        client = APIClient()
        client.force_authenticate(self.user)

        detail = client.get(f"/api/lens/admin/access/users/{self.user.pk}/")
        history = client.get("/api/lens/admin/runs/")

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(history.status_code, 200)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AssistantArchiveConcurrencyTests(TransactionTestCase):
    """Serialize assistant archival with creation of new work."""

    def setUp(self):
        self.lensnode = LensNode.objects.create(
            name="Archive Race Node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
        )
        self.assistant = Assistant.objects.create(
            name="Archive Race Assistant",
            slug="archive-race-assistant",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[{"path": "/workspace/repo"}],
            visibility=Assistant.Visibility.PUBLIC,
            multimodal_model_ref=uuid.uuid4(),
        )
        self.user = User.objects.create_user(
            username="archive-race-user",
            password="x",
        )
        self.admin = User.objects.create_user(
            username="archive-race-admin",
            password="x",
            is_staff=True,
        )

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _race_new_work_with_archive(self, create_request):
        check_entered = threading.Event()
        release_check = threading.Event()
        archive_finished = threading.Event()
        responses = {}
        errors = []
        original_check = Assistant.is_runnable_by

        def pause_after_check(assistant, user):
            allowed = original_check(assistant, user)
            if threading.current_thread().name == "new-assistant-work":
                check_entered.set()
                if not release_check.wait(timeout=5):
                    raise TimeoutError("Timed out waiting to resume creation")
            return allowed

        def run_create_request():
            close_old_connections()
            try:
                responses["create"] = create_request()
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def run_archive_request():
            close_old_connections()
            try:
                responses["archive"] = self._client(self.admin).post(
                    f"/api/lens/assistants/{self.assistant.uuid}/archive/"
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                archive_finished.set()
                close_old_connections()

        with patch.object(Assistant, "is_runnable_by", pause_after_check):
            create_thread = threading.Thread(
                target=run_create_request,
                name="new-assistant-work",
            )
            archive_thread = threading.Thread(
                target=run_archive_request,
                name="archive-assistant",
            )
            create_thread.start()
            self.assertTrue(check_entered.wait(timeout=5))
            archive_thread.start()
            archive_overtook_creation = archive_finished.wait(timeout=1)
            release_check.set()
            create_thread.join(timeout=5)
            archive_thread.join(timeout=5)

        self.assertFalse(create_thread.is_alive())
        self.assertFalse(archive_thread.is_alive())
        if errors:
            raise errors[0]
        self.assertFalse(archive_overtook_creation)
        self.assertEqual(responses["create"].status_code, 201)
        self.assertEqual(responses["archive"].status_code, 200)
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.status, Assistant.Status.ARCHIVED)

    def test_archive_waits_for_session_creation(self):
        initial_count = Session.objects.count()

        self._race_new_work_with_archive(
            lambda: self._client(self.user).post(
                "/api/lens/sessions/",
                {"assistant_uuid": str(self.assistant.uuid)},
                format="json",
            )
        )

        self.assertEqual(Session.objects.count(), initial_count + 1)

    def test_archive_waits_for_run_creation(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        initial_count = Run.objects.count()

        self._race_new_work_with_archive(
            lambda: self._client(self.user).post(
                f"/api/lens/sessions/{session.uuid}/runs/",
                {"question": "Explain the race", "enqueue": False},
                format="json",
            )
        )

        self.assertEqual(Run.objects.count(), initial_count + 1)

    def test_archive_waits_for_attachment_creation(self):
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        initial_count = MessageAttachment.objects.count()

        def upload_attachment():
            from PIL import Image

            image = io.BytesIO()
            Image.new("RGB", (2, 2), (120, 200, 80)).save(
                image,
                format="PNG",
            )
            uploaded = SimpleUploadedFile(
                "race.png",
                image.getvalue(),
                content_type="image/png",
            )
            return self._client(self.user).post(
                f"/api/lens/sessions/{session.uuid}/attachments/",
                {"file": uploaded},
                format="multipart",
            )

        self._race_new_work_with_archive(upload_attachment)

        self.assertEqual(
            MessageAttachment.objects.count(),
            initial_count + 1,
        )
