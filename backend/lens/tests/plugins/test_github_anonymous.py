"""Anonymous GitHub connections and manually entered repositories."""

from threading import Lock
from time import sleep
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from lens.lensnode_auth import issue_lensnode_token
from lens.models import Connection, DataSource, LensNode, SecretMaterial, SecretVersion
from lens.plugins.providers import DatasourceProviderError, get_datasource_provider
from lens.plugins.snapshots import create_datasource_sync_snapshot


class GitHubAnonymousProviderTests(SimpleTestCase):
    """Verify public access without credentials or scope bypasses."""

    def setUp(self):
        self.provider = get_datasource_provider("github")

    def test_normalizes_repository_urls_before_scope_validation(self):
        result = self.provider.validate_datasource_config(
            {"repositories": ["owner/repo"]},
            {"repositories": ["https://github.com/owner/repo.git"]},
        )
        self.assertEqual(result, {"repositories": ["owner/repo"]})
        with self.assertRaises(DatasourceProviderError):
            self.provider.validate_datasource_config(
                {"repositories": ["owner/repo"]},
                {"repositories": ["https://github.com/other/repo"]},
            )

    def test_rejects_unsafe_urls(self):
        for url in (
            "https://[invalid/owner/repo",
            "https://example.com/owner/repo",
            "http://github.com/owner/repo",
            "https://user:pass@github.com/owner/repo",
            "https://github.com:443/owner/repo",
            "https://github.com/owner/repo?token=x",
            "https://github.com/owner/repo/tree/main",
        ):
            with self.subTest(url=url), self.assertRaises(DatasourceProviderError):
                self.provider.validate_datasource_config(
                    {"repositories": ["*"]},
                    {"repositories": [url]},
                )

    def test_validates_public_repository_without_authorization_header(self):
        def respond(request):
            self.assertNotIn("authorization", request.headers)
            self.assertIn(request.url.path, ("/repos/owner/repo", "/repos/owner/repo/commits"))
            return httpx.Response(200, json={"full_name": "owner/repo", "private": False, "default_branch": "main"})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = self.provider.validate_datasource_access(
                "",
                {"repositories": ["owner/repo"]},
                client=client,
            )
        self.assertTrue(result["valid"])
        self.assertEqual(result["resources"][0]["default_branch"], "main")

    def test_missing_repository_is_not_reported_as_accessible(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
            result = self.provider.validate_datasource_access(
                "",
                {"repositories": ["owner/private"]},
                client=client,
            )
        self.assertFalse(result["valid"])
        self.assertEqual(result["resources"][0]["error"], "GITHUB_NOT_FOUND")

    def test_metadata_only_token_cannot_approve_default_branch_access(self):
        paths = []

        def respond(request):
            paths.append(request.url.path)
            if request.url.path.endswith("/commits"):
                return httpx.Response(403)
            return httpx.Response(200, json={"full_name": "owner/repo", "private": True, "default_branch": "main"})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = self.provider.validate_datasource_access(
                "metadata-only-token",
                {"repositories": ["owner/repo"]},
                client=client,
            )
        self.assertFalse(result["valid"])
        self.assertEqual(result["resources"][0]["error"], "GITHUB_ACCESS_DENIED")
        self.assertEqual(paths, ["/repos/owner/repo", "/repos/owner/repo/commits"])

    def test_large_commit_diff_does_not_prevent_access_validation(self):
        def respond(request):
            if request.url.path.endswith("/commits/main"):
                return httpx.Response(200, json={"files": [{"patch": "x" * 500_000}]})
            if request.url.path.endswith("/commits"):
                self.assertEqual(request.url.params["sha"], "main")
                self.assertEqual(request.url.params["per_page"], "1")
                return httpx.Response(200, json=[{"sha": "a" * 40}])
            return httpx.Response(200, json={"full_name": "owner/repo", "private": False, "default_branch": "main"})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = self.provider.validate_datasource_access("", {"repositories": ["owner/repo"]}, client=client)
        self.assertTrue(result["valid"])

    def test_bulk_access_validation_uses_bounded_concurrency(self):
        repositories = [f"owner/repo-{index}" for index in range(50)]
        active = 0
        peak = 0
        lock = Lock()

        def respond(request):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            sleep(0.01)
            with lock:
                active -= 1
            if request.url.path.endswith("/commits"):
                return httpx.Response(200, json=[{"sha": "a" * 40}])
            return httpx.Response(
                200,
                json={
                    "full_name": request.url.path.removeprefix("/repos/"),
                    "private": False,
                    "default_branch": "main",
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = self.provider.validate_datasource_access(
                "test-token", {"repositories": repositories}, client=client
            )
        self.assertTrue(result["valid"])
        self.assertEqual([item["repository"] for item in result["resources"]], repositories)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 5)

    def test_anonymous_discovery_does_not_call_user_repositories(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: self.fail(str(request.url)))) as client:
            result = self.provider.discover_resources({"repositories": ["*"]}, "", client=client)
        self.assertEqual(result["resources"]["repositories"]["items"], [])

    def test_token_access_checks_selected_ref_and_preserves_authentication(self):
        paths = []

        def respond(request):
            paths.append(request.url.path)
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            if request.url.path.endswith("/commits"):
                self.assertEqual(request.url.params["sha"], "missing")
                return httpx.Response(404)
            return httpx.Response(200, json={"full_name": "owner/repo", "private": True})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = self.provider.validate_datasource_access(
                "test-token",
                {"repositories": ["owner/repo"], "branch": "missing"},
                client=client,
            )
        self.assertFalse(result["valid"])
        self.assertEqual(paths, ["/repos/owner/repo", "/repos/owner/repo/commits"])

    def test_primary_rate_limit_is_reported_as_rate_limit(self):
        from lens.plugins.providers.base import PluginRequestContext

        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}))
        ) as client:
            result = self.provider.validate_datasource_access(
                "",
                {"repositories": ["owner/repo"]},
                client=client,
                request_context=PluginRequestContext(max_retries=0),
            )
        self.assertEqual(result["resources"][0]["error"], "GITHUB_RATE_LIMITED")


class GitHubAnonymousConnectionTests(TestCase):
    """Verify anonymous connections use the normal administrative API."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(get_user_model().objects.create_user(username="public-admin", is_staff=True))

    def test_create_anonymous_connection_without_secret_material(self):

        response = self.client.post(
            "/api/lens/admin/connections/",
            {"name": "Public GitHub", "plugin_key": "github", "allowed_scope": {"repositories": ["*"]}},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["has_secret"])
        self.assertIsNone(Connection.objects.get(uuid=response.data["uuid"]).secret_version)
        self.assertFalse(SecretMaterial.objects.exists())

    def test_blank_secret_is_anonymous_on_create_but_preserves_an_existing_token(self):
        payload = {"name": "Public", "plugin_key": "github", "allowed_scope": {"repositories": ["*"]}}
        response = self.client.post("/api/lens/admin/connections/", {**payload, "secret_value": ""}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["secret_version_uuid"])
        response = self.client.post(
            "/api/lens/admin/connections/",
            {**payload, "secret_value": "test-token"},
            format="json",
        )
        version = response.data["secret_version_uuid"]
        response = self.client.patch(
            f'/api/lens/admin/connections/{response.data["uuid"]}/',
            {"secret_value": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["secret_version_uuid"], version)

    @patch("lens.views.plugins.get_datasource_provider")
    def test_anonymous_connection_can_be_tested(self, factory):
        connection = Connection.objects.create(name="Public", plugin_key="github", endpoint="https://github.com")
        factory.return_value.http_origins.return_value = ("https://api.github.com",)
        factory.return_value.validate_live_connection.return_value = {"authentication": "anonymous"}
        response = self.client.post(f"/api/lens/admin/connections/{connection.uuid}/validate/")
        self.assertEqual(response.status_code, 200, response.data)

    def test_create_validate_and_lease_public_datasource(self):
        connection = Connection.objects.create(
            name="Public",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["*"]},
        )
        node = LensNode.objects.create(
            name="Node",
            workspace_path="/workspace",
            status="online",
            enrollment_status="approved",
        )
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"full_name": "owner/repo", "private": False, "default_branch": "main"}
                )
            )
        ) as client, patch("lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client):
            response = self.client.post(
                "/api/lens/admin/datasources/",
                {
                    "name": "Public repository",
                    "source_type": "git",
                    "connection_uuid": str(connection.uuid),
                    "lensnode_uuid": str(node.uuid),
                    "plugin_key": "github",
                    "datasource_config": {"repositories": ["https://github.com/owner/repo.git"]},
                    "target_path": "/workspace/public",
                    "sync_policy": {},
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.data)
        datasource = DataSource.objects.get(uuid=response.data["uuid"])
        self.assertEqual(datasource.datasource_config["repositories"], ["owner/repo"])
        snapshot = create_datasource_sync_snapshot(datasource)
        self.assertIsNone(snapshot.secret_version)
        node_client = APIClient()
        node_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_lensnode_token(node)}")
        response = node_client.post(
            "/api/lens/plugin-runtime/leases/", {"snapshot_uuid": str(snapshot.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        response = node_client.post(f'/api/lens/plugin-runtime/leases/{response.data["lease_uuid"]}/material/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["authentication"], "anonymous")
        self.assertEqual(response.data["value"], "")

    def test_private_repository_cannot_be_saved_by_skipping_preflight(self):
        connection = Connection.objects.create(
            name="Public",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["*"]},
        )
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client, patch(
            "lens.plugins.datasource_access.plugin_http_pool.bind",
            return_value=client,
        ):
            response = self.client.post(
                "/api/lens/admin/datasources/",
                {
                    "name": "Private",
                    "source_type": "git",
                    "connection_uuid": str(connection.uuid),
                    "plugin_key": "github",
                    "datasource_config": {"repositories": ["owner/private"]},
                    "sync_policy": {},
                },
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("GITHUB_NOT_FOUND", str(response.data))
        self.assertFalse(DataSource.objects.exists())

    def test_empty_stored_secret_does_not_fall_back_to_anonymous(self):
        material = SecretMaterial.objects.create(name="Broken token")
        version = SecretVersion.objects.create(material=material)
        connection = Connection.objects.create(name="Broken", plugin_key="github", secret_version=version)
        response = self.client.post(f"/api/lens/admin/connections/{connection.uuid}/validate/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "SECRET_UNAVAILABLE")

    def test_disabling_skips_remote_access_but_reenabling_checks_it(self):
        connection = Connection.objects.create(
            name="Public", plugin_key="github", endpoint="https://github.com", allowed_scope={"repositories": ["*"]}
        )
        datasource = DataSource.objects.create(
            name="Unavailable repository",
            source_type="git",
            plugin_key="github",
            connection=connection,
            datasource_config={"repositories": ["owner/repo"]},
        )
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client, patch(
            "lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client
        ) as bind:
            response = self.client.patch(
                f"/api/lens/admin/datasources/{datasource.uuid}/", {"status": "disabled"}, format="json"
            )
            self.assertEqual(response.status_code, 200, response.data)
            bind.assert_not_called()
            response = self.client.patch(
                f"/api/lens/admin/datasources/{datasource.uuid}/", {"status": "active"}, format="json"
            )
            self.assertEqual(response.status_code, 400, response.data)
            bind.assert_called_once()
        datasource.refresh_from_db()
        self.assertEqual(datasource.status, "disabled")

    def test_mcp_adapter_accepts_anonymous_connection_but_rejects_empty_stored_secret(self):
        connection = Connection.objects.create(
            name="Public", plugin_key="github", endpoint="https://github.com", allowed_scope={"repositories": ["*"]}
        )
        payload = {
            "name": "Public repository tools",
            "transport": "plugin",
            "connection_uuid": str(connection.uuid),
            "tools": ["github_read_file"],
        }
        response = self.client.post("/api/lens/admin/mcp-servers/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        material = SecretMaterial.objects.create(name="Broken")
        connection.secret_version = SecretVersion.objects.create(material=material)
        connection.save(update_fields=["secret_version"])
        response = self.client.patch(
            f'/api/lens/admin/mcp-servers/{response.data["uuid"]}/', {"name": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("connection_uuid", response.data)
