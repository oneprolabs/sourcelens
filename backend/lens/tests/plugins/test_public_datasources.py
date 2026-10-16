"""Public repositories need no user-managed connection or credentials."""

from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from lens.lensnode_auth import issue_lensnode_token
from lens.models import Connection, DataSource, LensNode
from lens.plugins.public_connections import public_datasource_connection
from lens.plugins.snapshots import create_datasource_sync_snapshot


class PublicDatasourceTests(TestCase):
    """Exercise anonymous validation, persistence, and frozen sync scope."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(get_user_model().objects.create_user(username="public", is_staff=True))

    def test_existing_anonymous_uuid_is_reused_without_extra_identity_fields(self):
        anonymous, _ = public_datasource_connection("github", {"repositories": ["owner/repo"]})
        existing = Connection.objects.create(
            uuid=anonymous.uuid,
            name=anonymous.name,
            plugin_key=anonymous.plugin_key,
            endpoint=anonymous.endpoint,
            allowed_scope=anonymous.allowed_scope,
        )
        with httpx.Client(transport=httpx.MockTransport(self.response)) as client, patch(
            "lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client
        ), patch("lens.views.datasources.DataSourceViewSet._enqueue_datasource_sync"):
            created = self.client.post("/api/lens/admin/datasources/", self.payload("github"), format="json")
            self.assertEqual(created.status_code, 201, created.data)
            for repositories in (["owner/repo", "owner/other"], ["owner/other"]):
                edited = self.client.patch(
                    f'/api/lens/admin/datasources/{created.data["uuid"]}/',
                    {"datasource_config": {"repositories": repositories}},
                    format="json",
                )
                self.assertEqual(edited.status_code, 200, edited.data)
                self.assertEqual(edited.data["connection"], str(existing.uuid))
                self.assertEqual(edited.data["datasource_config"]["repositories"], repositories)
        self.assertEqual(Connection.objects.count(), 1)

    def response(self, request):
        """Serve public metadata and readable refs without authentication."""

        self.assertNotIn("authorization", request.headers)
        self.assertNotIn("private-token", request.headers)
        if "repository/commits" in request.url.path or request.url.path.endswith("/commits"):
            return httpx.Response(200, json=[{"id": "abc", "sha": "abc"}])
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"full_name": "owner/repo", "private": False, "default_branch": "main"})
        return httpx.Response(
            200, json={"path_with_namespace": "group/sub/repo", "visibility": "public", "default_branch": "main"}
        )

    def payload(self, plugin_key):
        """Return the connection-free administrative payload."""

        key = "repositories" if plugin_key == "github" else "projects"
        url = (
            "https://github.com/owner/repo.git"
            if plugin_key == "github"
            else "https://gitlab.example/group/sub/repo.git"
        )
        return {
            "name": "Public",
            "source_type": "git",
            "plugin_key": plugin_key,
            "connection_uuid": None,
            "datasource_config": {key: [url]},
            "sync_policy": {},
        }

    def test_create_reuses_hidden_anonymous_connections_and_builds_snapshot(self):
        node = LensNode.objects.create(
            name="Node", workspace_path="/workspace", status="online", enrollment_status="approved"
        )
        with httpx.Client(transport=httpx.MockTransport(self.response)) as client, patch(
            "lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client
        ), patch("lens.views.datasources.DataSourceViewSet._enqueue_datasource_sync"):
            for plugin_key in ("github", "gitlab"):
                for index in range(2):
                    payload = {**self.payload(plugin_key), "name": f"{plugin_key}-{index}"}
                    response = self.client.post("/api/lens/admin/datasources/", payload, format="json")
                    self.assertEqual(response.status_code, 201, response.data)
                    self.assertTrue(response.data["connection_is_public"])
                    datasource = DataSource.objects.get(uuid=response.data["uuid"])
                    snapshot = create_datasource_sync_snapshot(datasource, lensnode=node)
                    self.assertIsNone(snapshot.secret_version)
                    self.assertEqual(snapshot.resolved_config["connection_scope"], datasource.connection.allowed_scope)
                    node_client = APIClient()
                    node_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_lensnode_token(node)}")
                    lease = node_client.post(
                        "/api/lens/plugin-runtime/leases/", {"snapshot_uuid": str(snapshot.uuid)}, format="json"
                    )
                    self.assertEqual(lease.status_code, 201, lease.data)
                    material = node_client.post(f'/api/lens/plugin-runtime/leases/{lease.data["lease_uuid"]}/material/')
                    self.assertEqual(material.status_code, 200, material.data)
                    self.assertEqual(material.data["authentication"], "anonymous")
                    self.assertEqual(material.data["value"], "")
                    hidden = self.client.patch(
                        f"/api/lens/admin/connections/{datasource.connection.uuid}/", {"name": "Changed"}, format="json"
                    )
                    self.assertEqual(hidden.status_code, 404)
                self.assertEqual(Connection.objects.filter(plugin_key=plugin_key).count(), 1)
        response = self.client.get("/api/lens/admin/connections/")
        self.assertEqual(response.data["results"], [])

    def test_public_preflight_does_not_persist_connections(self):
        with httpx.Client(transport=httpx.MockTransport(self.response)) as client, patch(
            "lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client
        ):
            for plugin_key in ("github", "gitlab"):
                response = self.client.post(
                    "/api/lens/admin/connections/validate-public-datasource/", self.payload(plugin_key), format="json"
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertTrue(response.data["valid"])
        self.assertFalse(Connection.objects.exists())

    def test_edit_keeps_anonymous_access_and_does_not_change_user_connection_scope(self):
        connection = Connection.objects.create(
            name="Private",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["private/repo"]},
        )
        with httpx.Client(transport=httpx.MockTransport(self.response)) as client, patch(
            "lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client
        ), patch("lens.views.datasources.DataSourceViewSet._enqueue_datasource_sync"):
            created = self.client.post("/api/lens/admin/datasources/", self.payload("github"), format="json")
            response = self.client.patch(
                f'/api/lens/admin/datasources/{created.data["uuid"]}/',
                {"name": "Renamed", "datasource_config": {"repositories": ["owner/other"]}},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["connection"], created.data["connection"])
        connection.refresh_from_db()
        self.assertEqual(connection.allowed_scope, {"repositories": ["private/repo"]})

    def test_gitlab_checks_selected_ref_and_rejects_nonpublic_metadata(self):
        for visibility in ("public", "private", "internal"):

            def respond(request):
                if "repository/commits" in request.url.path:
                    self.assertEqual(request.url.params["ref_name"], "missing")
                    return httpx.Response(404)
                return httpx.Response(
                    200,
                    json={"path_with_namespace": "group/sub/repo", "visibility": visibility, "default_branch": "main"},
                )

            with httpx.Client(transport=httpx.MockTransport(respond)) as client, patch(
                "lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client
            ):
                response = self.client.post(
                    "/api/lens/admin/connections/validate-public-datasource/",
                    {
                        **self.payload("gitlab"),
                        "datasource_config": {"projects": ["group/sub/repo"], "branch": "missing"},
                    },
                    format="json",
                )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertFalse(response.data["valid"])
        self.assertFalse(Connection.objects.exists())

    def test_public_branch_options_do_not_create_a_connection(self):
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[{"name": "main"}]))
        ) as client, patch("lens.views.plugins.plugin_http_pool.bind", return_value=client):
            for plugin_key in ("github", "gitlab"):
                response = self.client.post(
                    "/api/lens/admin/connections/validate-public-datasource/",
                    {**self.payload(plugin_key), "resource": "branches"},
                    format="json",
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["resources"]["branches"]["items"], [{"value": "main", "label": "main"}])
        self.assertFalse(Connection.objects.exists())

    def test_private_or_unreadable_repositories_are_rejected_without_saving(self):
        for plugin_key in ("github", "gitlab"):
            for status in (403, 404):
                with self.subTest(plugin_key=plugin_key, status=status), httpx.Client(
                    transport=httpx.MockTransport(lambda request: httpx.Response(status))
                ) as client, patch("lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client):
                    response = self.client.post("/api/lens/admin/datasources/", self.payload(plugin_key), format="json")
                    self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Connection.objects.exists())
        self.assertFalse(DataSource.objects.exists())

    def test_gitlab_metadata_alone_cannot_approve_repository_access(self):
        def respond(request):
            if "repository/commits" in request.url.path:
                return httpx.Response(403)
            return self.response(request)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client, patch(
            "lens.plugins.datasource_access.plugin_http_pool.bind", return_value=client
        ):
            response = self.client.post("/api/lens/admin/datasources/", self.payload("gitlab"), format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Connection.objects.exists())

    def test_gitlab_rejects_mixed_origins_and_credential_urls(self):
        for projects in (
            ["https://a.example/group/repo", "https://b.example/group/repo"],
            ["https://user:pass@gitlab.example/group/repo"],
        ):
            response = self.client.post(
                "/api/lens/admin/datasources/",
                {**self.payload("gitlab"), "datasource_config": {"projects": projects}},
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Connection.objects.exists())
