from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lens.lensnode_auth import issue_lensnode_token
from lens.models import (
    Connection,
    CredentialLease,
    DataSource,
    ExecutionSnapshot,
    LensNode,
    SecretMaterial,
    SecretVersion,
)


class PluginLeaseTests(TestCase):
    """Verify node-authenticated, snapshot-bound plugin leases."""

    def setUp(self):
        material = SecretMaterial.objects.create(name="GitHub PAT")
        version = SecretVersion.objects.create(
            material=material,
        )
        version.set_value("github-secret")
        version.save(update_fields=["encrypted_value"])
        self.connection = Connection.objects.create(
            name="GitHub readonly",
            plugin_key="github",
            endpoint="https://github.com",
            secret_version=version,
        )
        self.node = LensNode.objects.create(
            name="Node",
            workspace_path="/workspace",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.datasource = DataSource.objects.create(
            name="Repository",
            source_type=DataSource.SourceType.GIT,
            lensnode=self.node,
            connection=self.connection,
            plugin_key="github",
            datasource_config={"repository": "owner/repository"},
            target_path="/workspace/repository",
        )
        self.snapshot = ExecutionSnapshot.objects.create(
            kind=ExecutionSnapshot.Kind.DATASOURCE_SYNC,
            connection=self.connection,
            datasource=self.datasource,
            secret_version=version,
            plugin_key="github",
            plugin_version="1.0.0",
            protocol_version=1,
            resolved_config={
                "repository": "owner/repository",
                "access_token": "must-not-leave-control-plane",
                "lensnode_uuid": str(self.node.uuid),
            },
        )
        self.token = issue_lensnode_token(self.node)
        self.client = APIClient()

    def test_node_can_issue_lease_without_secret_payload(self):
        response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": str(self.snapshot.uuid)},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn("lease_uuid", response.data)
        self.assertIn("expires_at", response.data)
        self.assertNotIn("secret", response.data)

    def test_lease_rejects_a_snapshot_for_another_node(self):
        other = LensNode.objects.create(
            name="Other",
            workspace_path="/workspace",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.datasource.lensnode = other
        self.datasource.save(update_fields=["lensnode"])
        self.snapshot.resolved_config = {
            **self.snapshot.resolved_config,
            "lensnode_uuid": str(other.uuid),
        }
        self.snapshot.save(update_fields=["resolved_config"])

        response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": str(self.snapshot.uuid)},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 403)

    def test_node_can_resolve_material_for_an_active_lease(self):
        lease_response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": str(self.snapshot.uuid)},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        material_response = self.client.post(
            "/api/lens/plugin-runtime/leases/"
            f"{lease_response.data['lease_uuid']}/material/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(material_response.status_code, 200)
        self.assertEqual(material_response.data["value"], "github-secret")
        self.assertEqual(material_response.data["plugin_key"], "github")

    def test_node_can_fetch_non_sensitive_snapshot_config(self):
        response = self.client.get(
            f"/api/lens/plugin-runtime/snapshots/{self.snapshot.uuid}/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["resolved_config"]["repository"],
            "owner/repository",
        )
        self.assertNotIn(
            "access_token",
            response.data["resolved_config"],
        )
        self.assertNotIn("encrypted_value", response.data)

    def test_feishu_snapshot_keeps_non_secret_resource_tokens(self):
        self.snapshot.plugin_key = "feishu"
        self.snapshot.resolved_config = {
            "token": "must-not-leave-control-plane",
            "lensnode_uuid": str(self.node.uuid),
            "connection_config": {
                "kind": "credential",
                "token": "nested-secret",
            },
            "datasource_config": {
                "resources": [
                    {"kind": "folder", "token": "fld_resource"}
                ]
            },
        }
        self.snapshot.save(update_fields=["plugin_key", "resolved_config"])

        response = self.client.get(
            f"/api/lens/plugin-runtime/snapshots/{self.snapshot.uuid}/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("token", response.data["resolved_config"])
        self.assertNotIn(
            "token",
            response.data["resolved_config"]["connection_config"],
        )
        resources = response.data["resolved_config"]["datasource_config"][
            "resources"
        ]
        self.assertEqual(
            resources[0],
            {"kind": "folder", "token": "fld_resource"},
        )

    def test_expired_lease_cannot_resolve_material(self):
        lease_response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": str(self.snapshot.uuid)},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        CredentialLease.objects.filter(
            uuid=lease_response.data["lease_uuid"]
        ).update(expires_at=timezone.now() - timedelta(seconds=1))
        material_response = self.client.post(
            "/api/lens/plugin-runtime/leases/"
            f"{lease_response.data['lease_uuid']}/material/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(material_response.status_code, 410)

    def test_disabled_secret_version_cannot_issue_a_lease(self):
        self.snapshot.secret_version.status = "disabled"
        self.snapshot.secret_version.save(update_fields=["status"])

        response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": str(self.snapshot.uuid)},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "SECRET_VERSION_DISABLED")

    def test_disabled_secret_material_cannot_resolve_material(self):
        lease_response = self.client.post(
            "/api/lens/plugin-runtime/leases/",
            {"snapshot_uuid": str(self.snapshot.uuid)},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.snapshot.secret_version.material.status = "disabled"
        self.snapshot.secret_version.material.save(update_fields=["status"])

        response = self.client.post(
            "/api/lens/plugin-runtime/leases/"
            f"{lease_response.data['lease_uuid']}/material/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "SECRET_MATERIAL_DISABLED")
