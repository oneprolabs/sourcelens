from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from lens.models import (
    Connection,
    DataSource,
    ExecutionSnapshot,
    LensNode,
    SecretMaterial,
    SecretVersion,
)
from lens.serializers import DataSourceSerializer
from rest_framework.test import APIClient


class PluginModelTests(TestCase):
    """Verify the persistent boundary for integration plugins."""

    def setUp(self):
        self.secret = SecretMaterial.objects.create(name="GitHub PAT")
        self.version = SecretVersion.objects.create(
            material=self.secret,
            encrypted_value="encrypted",
        )
        self.connection = Connection.objects.create(
            name="GitHub readonly",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["HyperBDR/sourcelens"]},
            secret_version=self.version,
        )
        self.lensnode = LensNode.objects.create(
            name="Node",
            workspace_path="/workspace",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.user = get_user_model().objects.create_user(
            username="plugin-datasource-admin",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_datasource_can_use_a_connection_without_embedding_auth_config(
        self,
    ):
        datasource = DataSource.objects.create(
            name="SourceLens repository",
            source_type=DataSource.SourceType.GIT,
            lensnode=self.lensnode,
            connection=self.connection,
            plugin_key="github",
            datasource_config={"repository": "HyperBDR/sourcelens"},
            target_path="/workspace/sourcelens",
        )

        self.assertEqual(datasource.connection, self.connection)
        self.assertNotIn("access_token", datasource.datasource_config)

    def test_snapshot_keeps_resolved_connection_and_datasource_values(self):
        datasource = DataSource.objects.create(
            name="SourceLens repository",
            source_type=DataSource.SourceType.GIT,
            lensnode=self.lensnode,
            connection=self.connection,
            plugin_key="github",
            datasource_config={"repository": "HyperBDR/sourcelens"},
            target_path="/workspace/sourcelens",
        )
        snapshot = ExecutionSnapshot.objects.create(
            kind=ExecutionSnapshot.Kind.DATASOURCE_SYNC,
            connection=self.connection,
            datasource=datasource,
            secret_version=self.version,
            plugin_key="github",
            plugin_version="1.0.0",
            protocol_version=1,
            resolved_config={
                "endpoint": "https://github.com",
                "repository": "HyperBDR/sourcelens",
            },
        )
        datasource.datasource_config = {"repository": "other/repository"}
        datasource.save(update_fields=["datasource_config"])

        snapshot.refresh_from_db()
        self.assertEqual(
            snapshot.resolved_config["repository"],
            "HyperBDR/sourcelens",
        )

    def test_serializer_accepts_provider_datasource_configuration(self):
        serializer = DataSourceSerializer(
            data={
                "name": "Repository",
                "source_type": "git",
                "lensnode_uuid": str(self.lensnode.uuid),
                "connection_uuid": str(self.connection.uuid),
                "plugin_key": "github",
                "datasource_config": {"repository": "HyperBDR/sourcelens"},
                "sync_policy": {},
                "target_path": "/workspace/repository",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["datasource_config"],
            {"repository": "HyperBDR/sourcelens"},
        )
        self.assertIsNone(serializer.validated_data["credential"])

    @patch("lens.views.datasources.validate_connection_datasource_access", return_value={"valid": True})
    def test_api_creates_github_datasource_with_connection_identity(self, _validate_access):
        response = self.client.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Repository",
                "source_type": "git",
                "lensnode_uuid": str(self.lensnode.uuid),
                "connection_uuid": str(self.connection.uuid),
                "plugin_key": "github",
                "datasource_config": {
                    "repository": "HyperBDR/sourcelens",
                    "branch": "main",
                    "directory": "docs",
                },
                "config": {},
                "sync_policy": {"interval_seconds": 3600},
                "target_path": "/workspace/repository",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        datasource = DataSource.objects.get(uuid=response.data["uuid"])
        self.assertEqual(datasource.connection, self.connection)
        self.assertEqual(datasource.plugin_key, "github")
        self.assertEqual(datasource.datasource_config["directory"], "docs")
        self.assertIsNone(datasource.credential)
        self.assertEqual(
            response.data["connection"],
            str(self.connection.uuid),
        )
        self.assertEqual(response.data["connection_name"], "GitHub readonly")
        self.assertEqual(
            response.data["connection_endpoint"],
            "https://github.com",
        )
        self.assertNotIn("credential_uuid", response.data)

    def test_serializer_rejects_plugin_config_without_connection(self):
        serializer = DataSourceSerializer(
            data={
                "name": "Repository",
                "source_type": "git",
                "lensnode_uuid": str(self.lensnode.uuid),
                "plugin_key": "github",
                "datasource_config": {"repository": "HyperBDR/sourcelens"},
                "config": {
                    "repo_url": "https://github.com/HyperBDR/sourcelens.git",
                    "branch": "main",
                },
                "sync_policy": {},
                "target_path": "/workspace/repository",
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("connection_uuid", serializer.errors)

    def test_serializer_rejects_disabled_plugin_connection(self):
        self.connection.status = Connection.Status.DISABLED
        self.connection.save(update_fields=["status"])
        serializer = DataSourceSerializer(
            data={
                "name": "Repository",
                "source_type": "git",
                "lensnode_uuid": str(self.lensnode.uuid),
                "connection_uuid": str(self.connection.uuid),
                "plugin_key": "github",
                "datasource_config": {"repository": "HyperBDR/sourcelens"},
                "sync_policy": {},
                "target_path": "/workspace/repository",
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("connection_uuid", serializer.errors)

    def test_serializer_rejects_plugin_connection_without_active_secret(self):
        self.version.status = "disabled"
        self.version.save(update_fields=["status"])
        serializer = DataSourceSerializer(
            data={
                "name": "Repository",
                "source_type": "git",
                "lensnode_uuid": str(self.lensnode.uuid),
                "connection_uuid": str(self.connection.uuid),
                "plugin_key": "github",
                "datasource_config": {"repository": "HyperBDR/sourcelens"},
                "sync_policy": {},
                "target_path": "/workspace/repository",
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("connection_uuid", serializer.errors)

    def test_serializer_rejects_github_connection_for_feishu_source(self):
        serializer = DataSourceSerializer(
            data={
                "name": "Repository",
                "source_type": "feishu",
                "lensnode_uuid": str(self.lensnode.uuid),
                "connection_uuid": str(self.connection.uuid),
                "plugin_key": "github",
                "datasource_config": {"repository": "HyperBDR/sourcelens"},
                "sync_policy": {},
                "target_path": "/workspace/repository",
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("source_type", serializer.errors)
