import httpx
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from lens.lensnode_auth import issue_lensnode_token
from lens.models import (
    Assistant,
    AssistantPluginBinding,
    Connection,
    ExecutionSnapshot,
    LensNode,
    Run,
    SecretMaterial,
    SecretVersion,
    Session,
)
from lens.plugins.providers.base import DatasourceProviderError
from lens.plugins.providers import get_datasource_provider
from lens.plugins.registry import installed_plugin
from lens.plugins.tool_providers import ToolProviderError, get_tool_provider
from lens.services import create_execution_run
from rest_framework.test import APIClient


class GitLabPluginManifestTests(TestCase):
    """Verify the bundled GitLab Plugin contract is installed."""

    def test_bundled_gitlab_plugin_is_discoverable(self):
        plugin = installed_plugin("gitlab")

        self.assertEqual(plugin.version, "1.0.0")
        self.assertEqual(plugin.display_name, "GitLab")
        self.assertEqual(plugin.datasource_source_type, "git")
        self.assertEqual(
            [tool.key for tool in plugin.tools],
            [
                "gitlab_read_file",
                "gitlab_search_code",
                "gitlab_activity_summary",
            ],
        )


class GitLabDatasourceProviderTests(TestCase):
    """Verify GitLab implements the datasource Provider contract."""

    def setUp(self):
        self.provider = get_datasource_provider("gitlab", "1.0.0")
        self.scope = {"projects": ["platform/sourcelens"]}

    def test_accepts_nested_project_within_connection_scope(self):
        config = self.provider.validate_datasource_config(
            {"projects": ["platform/backend/sourcelens"]},
            {
                "project": "platform/backend/sourcelens",
                "branch": "main",
                "directory": "docs",
            },
        )

        self.assertEqual(config["project"], "platform/backend/sourcelens")
        self.assertEqual(config["directory"], "docs")

    def test_accepts_multiple_projects(self):
        config = self.provider.validate_datasource_config(
            {"projects": ["platform/a", "platform/b"]},
            {
                "projects": ["platform/a", "platform/b"],
                "branch": "main",
            },
        )

        self.assertEqual(
            config,
            {
                "projects": ["platform/a", "platform/b"],
                "branch": "main",
            },
        )

    def test_rejects_project_outside_connection_scope(self):
        with self.assertRaisesMessage(DatasourceProviderError, "scope"):
            self.provider.validate_datasource_config(
                self.scope,
                {"project": "other/repository"},
            )

    def test_accepts_public_and_self_managed_http_endpoints(self):
        endpoints = [
            "https://gitlab.com/",
            "https://gitlab.internal.example/",
            "http://gitlab.internal.example:8080/",
        ]

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.provider.validate_connection(endpoint, {}),
                    endpoint.rstrip("/"),
                )

    def test_rejects_endpoint_paths_and_unsupported_schemes(self):
        endpoints = [
            "ftp://gitlab.example",
            "https://gitlab.example/nested",
            "https://user@gitlab.example",
        ]

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesMessage(
                    DatasourceProviderError,
                    "endpoint",
                ):
                    self.provider.validate_connection(endpoint, {})

    def test_live_validation_uses_selected_endpoint_without_redirects(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(
                200,
                json={"id": 7, "username": "plugin-admin"},
                request=request,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = self.provider.validate_live_connection(
                "secret",
                endpoint="https://gitlab.internal.example",
                client=client,
            )

        self.assertEqual(
            seen,
            ["https://gitlab.internal.example/api/v4/user"],
        )
        self.assertEqual(result["account"]["username"], "plugin-admin")

    def test_discovers_projects_visible_to_connection_token(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json=[
                    {
                        "path_with_namespace": "platform/sourcelens",
                        "visibility": "private",
                        "last_activity_at": "2026-09-04T01:00:00Z",
                    }
                ],
                request=request,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            resources = self.provider.discover_connection_resources(
                "secret",
                endpoint="https://gitlab.internal.example",
                query="source",
                limit=25,
                client=client,
            )

        self.assertEqual(seen["path"], "/api/v4/projects")
        self.assertEqual(seen["params"]["membership"], "true")
        self.assertEqual(seen["params"]["search"], "source")
        self.assertEqual(
            resources["resources"]["projects"]["items"],
            [
                {
                    "value": "platform/sourcelens",
                    "label": "platform/sourcelens",
                    "metadata": {
                        "visibility": "private",
                        "last_activity_at": "2026-09-04T01:00:00Z",
                    },
                }
            ],
        )

    def test_discovers_allowed_projects_without_remote_requests(self):
        def handler(request):
            raise AssertionError(request.url)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            resources = self.provider.discover_resources(
                {"projects": ["group/one", "group/two"]},
                "secret",
                endpoint="https://gitlab.example",
                client=client,
            )

        self.assertEqual(
            resources["resources"]["projects"]["items"],
            [
                {"value": "group/one", "label": "group/one"},
                {"value": "group/two", "label": "group/two"},
            ],
        )

    def test_discovers_branches_for_one_allowed_project(self):
        def handler(request):
            self.assertEqual(
                request.url.path,
                "/api/v4/projects/group/one/repository/branches",
            )
            return httpx.Response(
                200,
                json=[{"name": "main"}, {"name": "release"}],
                request=request,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            resources = self.provider.discover_resource_options(
                {"projects": ["group/one"]},
                "secret",
                "branches",
                {"project": "group/one"},
                endpoint="https://gitlab.example",
                client=client,
            )

        self.assertEqual(
            resources["resources"]["branches"]["items"],
            [
                {"value": "main", "label": "main"},
                {"value": "release", "label": "release"},
            ],
        )


class GitLabToolProviderTests(TestCase):
    """Verify GitLab model Tool requests remain within Connection scope."""

    def setUp(self):
        self.provider = get_tool_provider("gitlab", "1.0.0")
        self.scope = {"projects": ["platform/backend/sourcelens"]}

    def test_normalizes_read_file_request(self):
        endpoint, arguments = self.provider.validate_request(
            "https://gitlab.internal.example",
            self.scope,
            "gitlab_read_file",
            {
                "project": "platform/backend/sourcelens",
                "path": "README.md",
                "ref": "main",
            },
        )

        self.assertEqual(endpoint, "https://gitlab.internal.example")
        self.assertEqual(arguments["path"], "README.md")

    def test_rejects_project_outside_scope(self):
        with self.assertRaisesMessage(ToolProviderError, "scope"):
            self.provider.validate_request(
                "https://gitlab.example",
                self.scope,
                "gitlab_read_file",
                {"project": "other/repository", "path": "README.md"},
            )

    def test_activity_summary_accepts_only_allowed_projects(self):
        endpoint, arguments = self.provider.validate_request(
            "https://gitlab.internal.example",
            {"projects": ["platform/backend/sourcelens", "group/ops"]},
            "gitlab_activity_summary",
            {
                "projects": ["group/ops", "platform/backend/sourcelens"],
                "since": "2026-09-01T16:00:00Z",
                "until": "2026-09-02T15:59:59Z",
            },
        )

        self.assertEqual(endpoint, "https://gitlab.internal.example")
        self.assertEqual(
            arguments["projects"],
            ["group/ops", "platform/backend/sourcelens"],
        )

        with self.assertRaisesMessage(ToolProviderError, "scope"):
            self.provider.validate_request(
                endpoint,
                {"projects": ["group/ops"]},
                "gitlab_activity_summary",
                {
                    "projects": ["other/project"],
                    "since": "2026-09-01T16:00:00Z",
                    "until": "2026-09-02T15:59:59Z",
                },
            )

    def test_activity_summary_accepts_all_projects_sentinel(self):
        endpoint, arguments = self.provider.validate_request(
            "https://gitlab.internal.example",
            {"projects": ["*"]},
            "gitlab_activity_summary",
            {
                "projects": ["*"],
                "since": "2026-09-01T16:00:00Z",
                "until": "2026-09-02T15:59:59Z",
            },
        )

        self.assertEqual(endpoint, "https://gitlab.internal.example")
        self.assertEqual(arguments["projects"], ["*"])

    def test_activity_summary_rejects_all_projects_outside_all_scope(self):
        with self.assertRaisesMessage(ToolProviderError, "scope"):
            self.provider.validate_request(
                "https://gitlab.internal.example",
                self.scope,
                "gitlab_activity_summary",
                {
                    "projects": ["*"],
                    "since": "2026-09-01T16:00:00Z",
                    "until": "2026-09-02T15:59:59Z",
                },
            )

    def test_activity_summary_rejects_mixed_all_projects(self):
        with self.assertRaises(ToolProviderError):
            self.provider.validate_request(
                "https://gitlab.internal.example",
                {"projects": ["*"]},
                "gitlab_activity_summary",
                {
                    "projects": ["*", "group/ops"],
                    "since": "2026-09-01T16:00:00Z",
                    "until": "2026-09-02T15:59:59Z",
                },
            )


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
)
class GitLabToolSnapshotTests(TestCase):
    """Verify GitLab uses the shared snapshot and lease control plane."""

    def test_node_creates_gitlab_tool_snapshot_without_secret(self):
        user = get_user_model().objects.create_user("gitlab-tool-user")
        node = LensNode.objects.create(
            name="GitLab node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            tasks=[{"name": "general_chat"}],
        )
        token = issue_lensnode_token(node)
        material = SecretMaterial.objects.create(name="GitLab PAT")
        version = SecretVersion(material=material)
        version.set_value("gitlab-tool-secret")
        version.save()
        connection = Connection.objects.create(
            name="GitLab readonly",
            plugin_key="gitlab",
            endpoint="https://gitlab.internal.example",
            allowed_scope={"projects": ["platform/backend/sourcelens"]},
            secret_version=version,
        )
        assistant = Assistant.objects.create(
            name="GitLab Assistant",
            slug="gitlab-tool-assistant",
            lensnode=node,
            selected_task="general_chat",
            visibility=Assistant.Visibility.PUBLIC,
        )
        AssistantPluginBinding.objects.create(
            assistant=assistant,
            connection=connection,
            tools=["gitlab_read_file", "gitlab_search_code"],
        )
        session = Session.objects.create(assistant=assistant, user=user)
        run = create_execution_run(
            session,
            "Read the README",
            enqueue=False,
        )
        run.status = Run.Status.STREAMING
        run.save(update_fields=["status"])
        client = APIClient()

        response = client.post(
            "/api/lens/plugin-runtime/tool-snapshots/",
            {
                "run_uuid": str(run.uuid),
                "connection_uuid": str(connection.uuid),
                "tool_key": "gitlab_read_file",
                "call_id": "gitlab-call-1",
                "arguments": {
                    "project": "platform/backend/sourcelens",
                    "path": "README.md",
                    "ref": "main",
                },
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 201, response.data)
        snapshot = ExecutionSnapshot.objects.get(
            uuid=response.data["snapshot_uuid"]
        )
        self.assertEqual(snapshot.plugin_key, "gitlab")
        self.assertEqual(
            snapshot.resolved_config["endpoint"],
            "https://gitlab.internal.example",
        )
        self.assertNotIn("gitlab-tool-secret", str(snapshot.resolved_config))

    def test_activity_snapshot_accepts_all_projects_for_all_scope(self):
        user = get_user_model().objects.create_user("gitlab-all-user")
        node = LensNode.objects.create(
            name="GitLab all node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            tasks=[{"name": "general_chat"}],
        )
        token = issue_lensnode_token(node)
        material = SecretMaterial.objects.create(name="GitLab all PAT")
        version = SecretVersion(material=material)
        version.set_value("gitlab-all-secret")
        version.save()
        connection = Connection.objects.create(
            name="GitLab all projects",
            plugin_key="gitlab",
            endpoint="https://gitlab.internal.example",
            allowed_scope={"projects": ["*"]},
            secret_version=version,
        )
        assistant = Assistant.objects.create(
            name="GitLab all Assistant",
            slug="gitlab-all-assistant",
            lensnode=node,
            selected_task="general_chat",
            visibility=Assistant.Visibility.PUBLIC,
        )
        AssistantPluginBinding.objects.create(
            assistant=assistant,
            connection=connection,
            tools=["gitlab_activity_summary"],
        )
        session = Session.objects.create(assistant=assistant, user=user)
        run = create_execution_run(
            session,
            "Summarize activity",
            enqueue=False,
        )
        run.status = Run.Status.STREAMING
        run.save(update_fields=["status"])
        client = APIClient()

        response = client.post(
            "/api/lens/plugin-runtime/tool-snapshots/",
            {
                "run_uuid": str(run.uuid),
                "connection_uuid": str(connection.uuid),
                "tool_key": "gitlab_activity_summary",
                "call_id": "gitlab-all-call-1",
                "arguments": {
                    "projects": ["*"],
                    "since": "2026-09-01T16:00:00Z",
                    "until": "2026-09-02T15:59:59Z",
                },
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 201, response.data)
        snapshot = ExecutionSnapshot.objects.get(
            uuid=response.data["snapshot_uuid"]
        )
        self.assertEqual(
            snapshot.resolved_config["arguments"]["projects"],
            ["*"],
        )
