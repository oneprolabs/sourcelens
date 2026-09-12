import json
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from lens.models import (
    Connection,
    DataSource,
    LensNode,
    SecretMaterial,
    SecretVersion,
)
from lens.plugins.contracts import ToolProviderError
from lens.plugins.providers import get_datasource_provider
from lens.plugins.providers.base import DatasourceProviderError
from lens.plugins.registry import installed_plugin
from lens.plugins.snapshots import create_datasource_sync_snapshot
from lens.plugins.tool_providers import get_tool_provider
from lens.serializers import DataSourceSerializer


class FeishuPluginManifestTests(TestCase):
    """Verify the bundled Feishu datasource Plugin contract."""

    def test_bundled_feishu_plugin_is_datasource_only(self):
        plugin = installed_plugin("feishu")

        self.assertEqual(plugin.version, "1.0.0")
        self.assertEqual(plugin.display_name, "Feishu")
        self.assertEqual(plugin.datasource_source_type, "feishu")
        self.assertEqual(
            [tool.key for tool in plugin.tools],
            ["feishu_get_document"],
        )
        self.assertEqual(
            plugin.connection_schema["required"],
            ["app_id", "app_secret"],
        )
        self.assertEqual(
            plugin.datasource_schema["required"],
            ["resource_urls"],
        )
        resource_urls = plugin.datasource_schema["properties"][
            "resource_urls"
        ]
        self.assertEqual(resource_urls["minItems"], 1)
        self.assertEqual(resource_urls["maxItems"], 100)
        self.assertEqual(resource_urls["items"]["format"], "uri")


class FeishuDatasourceProviderTests(TestCase):
    """Verify Feishu application and mixed resource validation."""

    def setUp(self):
        self.provider = get_datasource_provider("feishu", "1.0.0")

    def test_connection_uses_fixed_endpoint_without_resource_scope(self):
        endpoint = self.provider.validate_connection(
            "",
            {"app_id": "cli_example123"},
        )

        self.assertEqual(endpoint, "https://open.feishu.cn")
        self.assertEqual(self.provider.validate_connection_scope({}), {})

    def test_classifies_and_deduplicates_mixed_resource_urls(self):
        config = self.provider.validate_datasource_config(
            {},
            {
                "resource_urls": [
                    (
                        "https://tenant.feishu.cn/drive/folder/fld_one"
                        "?from=space"
                    ),
                    "https://tenant.feishu.cn/docx/doc_one",
                    "https://tenant.feishu.cn/docx/doc_one#heading",
                    "https://tenant.feishu.cn/wiki/wik_one",
                ],
                "recursive": True,
                "max_depth": 12,
                "incremental": True,
                "delete_missing": False,
            },
        )

        self.assertEqual(
            config["resource_urls"],
            [
                "https://tenant.feishu.cn/drive/folder/fld_one",
                "https://tenant.feishu.cn/docx/doc_one",
                "https://tenant.feishu.cn/wiki/wik_one",
            ],
        )
        self.assertEqual(
            config["resources"],
            [
                {"kind": "folder", "token": "fld_one"},
                {"kind": "docx", "token": "doc_one"},
                {"kind": "wiki", "token": "wik_one"},
            ],
        )

    def test_rejects_non_feishu_and_unsupported_resource_urls(self):
        invalid_urls = [
            "https://example.com/docx/doc_one",
            "http://tenant.feishu.cn/docx/doc_one",
            "https://tenant.feishu.cn/calendar/cal_one",
        ]

        for resource_url in invalid_urls:
            with self.subTest(resource_url=resource_url):
                with self.assertRaises(DatasourceProviderError):
                    self.provider.validate_datasource_config(
                        {},
                        {"resource_urls": [resource_url]},
                    )

    def test_live_validation_exchanges_tenant_token(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": "tenant-token",
                    "expire": 7200,
                },
                request=request,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = self.provider.validate_live_connection(
                "app-secret",
                connection_config={"app_id": "cli_example123"},
                client=client,
            )

        self.assertEqual(seen["method"], "POST")
        self.assertEqual(
            seen["path"],
            "/open-apis/auth/v3/tenant_access_token/internal",
        )
        self.assertEqual(
            seen["payload"],
            {"app_id": "cli_example123", "app_secret": "app-secret"},
        )
        self.assertEqual(result, {"authenticated": True, "expires_in": 7200})

    def test_document_tool_validates_endpoint_and_token(self):
        tool_provider = get_tool_provider("feishu", "1.0.0")
        endpoint, arguments = tool_provider.validate_request(
            "https://open.feishu.cn/",
            {},
            "feishu_get_document",
            {"token": "doc_one"},
        )
        self.assertEqual(endpoint, "https://open.feishu.cn")
        self.assertEqual(arguments, {"token": "doc_one"})
        with self.assertRaises(ToolProviderError):
            tool_provider.validate_request(
                "https://open.feishu.cn",
                {},
                "feishu_get_document",
                {"token": "../secret"},
            )

    def test_datasource_access_checks_each_url_without_connection_scope(self):
        requests = []

        def handler(request):
            requests.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "payload": (
                        json.loads(request.content)
                        if request.content
                        else None
                    ),
                }
            )
            if request.url.path.endswith("tenant_access_token/internal"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "tenant_access_token": "tenant-token",
                    },
                    request=request,
                )
            if request.url.path == "/open-apis/drive/v1/files":
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"files": []}},
                    request=request,
                )
            if request.url.path.endswith("/wiki/v2/spaces/get_node"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "node": {
                                "obj_token": "doc_wiki",
                                "obj_type": "docx",
                            }
                        },
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "metas": [
                            {
                                "doc_token": "doc_one",
                                "doc_type": "docx",
                            }
                        ],
                        "failed_list": [],
                    },
                },
                request=request,
            )

        urls = [
            "https://tenant.feishu.cn/drive/folder/fld_one",
            "https://tenant.feishu.cn/docx/doc_one",
            "https://tenant.feishu.cn/wiki/wik_one",
        ]
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = self.provider.validate_datasource_access(
                "app-secret",
                {"resource_urls": urls},
                connection_config={"app_id": "cli_example123"},
                client=client,
            )

        self.assertTrue(result["valid"])
        self.assertEqual(
            result["resources"],
            [
                {
                    "url": url,
                    "kind": kind,
                    "accessible": True,
                }
                for url, kind in zip(urls, ["folder", "docx", "wiki"])
            ],
        )
        self.assertEqual(
            requests[0]["path"],
            "/open-apis/auth/v3/tenant_access_token/internal",
        )
        metadata_request = next(
            item
            for item in requests
            if item["path"] == "/open-apis/drive/v1/metas/batch_query"
        )
        self.assertEqual(
            metadata_request["payload"],
            {
                "request_docs": [
                    {"doc_token": "doc_one", "doc_type": "docx"}
                ],
                "with_url": False,
            },
        )
        self.assertNotIn("tenant-token", json.dumps(result))
        self.assertNotIn("app-secret", json.dumps(result))

    def test_datasource_access_reports_the_inaccessible_url(self):
        def handler(request):
            if request.url.path.endswith("tenant_access_token/internal"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "tenant_access_token": "tenant-token",
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "metas": [],
                        "failed_list": [
                            {"token": "doc_denied", "code": 1069302}
                        ],
                    },
                },
                request=request,
            )

        url = "https://tenant.feishu.cn/docx/doc_denied"
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = self.provider.validate_datasource_access(
                "app-secret",
                {"resource_urls": [url]},
                connection_config={"app_id": "cli_example123"},
                client=client,
            )

        self.assertFalse(result["valid"])
        self.assertEqual(
            result["resources"],
            [
                {
                    "url": url,
                    "kind": "docx",
                    "accessible": False,
                    "error": "FEISHU_RESOURCE_ACCESS_DENIED",
                }
            ],
        )


class FeishuDatasourceIntegrationTests(TestCase):
    """Verify Feishu Connection and DataSource persistence boundaries."""

    def setUp(self):
        material = SecretMaterial.objects.create(name="Feishu secret")
        version = SecretVersion.objects.create(
            material=material,
            encrypted_value="encrypted",
        )
        self.connection = Connection.objects.create(
            name="Feishu app",
            plugin_key="feishu",
            endpoint="https://open.feishu.cn",
            config={"app_id": "cli_example123"},
            allowed_scope={},
            secret_version=version,
        )
        self.node = LensNode.objects.create(
            name="Feishu node",
            workspace_path="/workspace",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )

    def test_serializer_persists_normalized_resources_without_secret(self):
        serializer = DataSourceSerializer(
            data={
                "name": "Feishu knowledge",
                "source_type": "feishu",
                "lensnode_uuid": str(self.node.uuid),
                "connection_uuid": str(self.connection.uuid),
                "plugin_key": "feishu",
                "datasource_config": {
                    "resource_urls": [
                        "https://tenant.feishu.cn/drive/folder/fld_one",
                        "https://tenant.feishu.cn/docx/doc_one",
                    ]
                },
                "config": {},
                "sync_policy": {},
                "target_path": "/workspace/feishu",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        datasource = serializer.save()
        self.assertEqual(datasource.config, {})
        self.assertEqual(
            datasource.datasource_config["resources"],
            [
                {"kind": "folder", "token": "fld_one"},
                {"kind": "docx", "token": "doc_one"},
            ],
        )
        snapshot = create_datasource_sync_snapshot(datasource)
        serialized_snapshot = json.dumps(snapshot.resolved_config)
        self.assertNotIn("app_secret", serialized_snapshot)
        self.assertNotIn("encrypted", serialized_snapshot)


class FeishuDatasourceAccessAPITests(TestCase):
    """Verify URL access validation before Feishu datasource persistence."""

    def setUp(self):
        user = get_user_model().objects.create_user(
            username="feishu-datasource-admin",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user)
        material = SecretMaterial.objects.create(name="Feishu API secret")
        version = SecretVersion(material=material)
        version.set_value("app-secret")
        version.save()
        self.connection = Connection.objects.create(
            name="Feishu app",
            plugin_key="feishu",
            endpoint="https://open.feishu.cn",
            config={"app_id": "cli_example123"},
            allowed_scope={},
            secret_version=version,
        )
        self.node = LensNode.objects.create(
            name="Feishu access node",
            workspace_path="/workspace",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.provider = get_datasource_provider("feishu", "1.0.0")
        self.url = "https://tenant.feishu.cn/docx/doc_denied"

    def test_connection_endpoint_returns_each_failed_url(self):
        result = {
            "valid": False,
            "resources": [
                {
                    "url": self.url,
                    "kind": "docx",
                    "accessible": False,
                    "error": "FEISHU_RESOURCE_ACCESS_DENIED",
                }
            ],
        }
        with patch.object(
            self.provider,
            "validate_datasource_access",
            return_value=result,
        ):
            response = self.client.post(
                (
                    "/api/lens/admin/connections/"
                    f"{self.connection.uuid}/validate-datasource/"
                ),
                {"datasource_config": {"resource_urls": [self.url]}},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data["valid"])
        self.assertEqual(response.data["resources"], result["resources"])
        self.assertIn(self.url, response.data["detail"])
        self.assertNotIn("app-secret", str(response.data))

    def test_create_revalidates_urls_and_does_not_persist_on_failure(self):
        result = {
            "valid": False,
            "resources": [
                {
                    "url": self.url,
                    "kind": "docx",
                    "accessible": False,
                    "error": "FEISHU_RESOURCE_ACCESS_DENIED",
                }
            ],
        }
        with patch.object(
            self.provider,
            "validate_datasource_access",
            return_value=result,
        ):
            response = self.client.post(
                "/api/lens/admin/datasources/",
                {
                    "name": "Denied Feishu resource",
                    "source_type": "feishu",
                    "lensnode_uuid": str(self.node.uuid),
                    "connection_uuid": str(self.connection.uuid),
                    "plugin_key": "feishu",
                    "datasource_config": {
                        "resource_urls": [self.url]
                    },
                    "config": {},
                    "sync_policy": {},
                    "target_path": "/workspace/denied-feishu",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(self.url, response.data["detail"])
        self.assertFalse(
            DataSource.objects.filter(name="Denied Feishu resource").exists()
        )

    def test_update_revalidates_urls_with_the_existing_connection(self):
        datasource = DataSource.objects.create(
            name="Existing Feishu resource",
            source_type="feishu",
            lensnode=self.node,
            connection=self.connection,
            plugin_key="feishu",
            datasource_config={
                "resource_urls": [
                    "https://tenant.feishu.cn/docx/doc_original"
                ],
                "resources": [{"kind": "docx", "token": "doc_original"}],
                "recursive": True,
                "max_depth": 10,
                "incremental": True,
                "delete_missing": False,
            },
            target_path="/workspace/existing-feishu",
        )
        result = {
            "valid": False,
            "resources": [
                {
                    "url": self.url,
                    "kind": "docx",
                    "accessible": False,
                    "error": "FEISHU_RESOURCE_ACCESS_DENIED",
                }
            ],
        }
        with patch.object(
            self.provider,
            "validate_datasource_access",
            return_value=result,
        ):
            response = self.client.patch(
                f"/api/lens/admin/datasources/{datasource.uuid}/",
                {"datasource_config": {"resource_urls": [self.url]}},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        datasource.refresh_from_db()
        self.assertEqual(
            datasource.datasource_config["resource_urls"],
            ["https://tenant.feishu.cn/docx/doc_original"],
        )
