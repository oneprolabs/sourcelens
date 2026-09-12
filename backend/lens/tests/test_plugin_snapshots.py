import json
from unittest.mock import patch

from django.test import TestCase
from lens.datasource.services import dispatch_datasource_sync_async
from lens.models import (
    Connection,
    DataSource,
    LensNode,
    PluginInvocation,
    SecretMaterial,
    SecretVersion,
)
from lens.plugins.registry import PluginRegistryError
from lens.plugins.snapshots import create_datasource_sync_snapshot


class PluginSnapshotTests(TestCase):
    """Verify resolved datasource execution snapshots."""

    def setUp(self):
        material = SecretMaterial.objects.create(name="GitHub PAT")
        version = SecretVersion.objects.create(
            material=material,
            encrypted_value="encrypted",
        )
        self.connection = Connection.objects.create(
            name="GitHub readonly",
            plugin_key="github",
            endpoint="https://github.com",
            allowed_scope={"repositories": ["HyperBDR/sourcelens"]},
            secret_version=version,
        )
        self.node = LensNode.objects.create(
            name="Node",
            workspace_path="/workspace",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.datasource = DataSource.objects.create(
            name="SourceLens repository",
            source_type=DataSource.SourceType.GIT,
            lensnode=self.node,
            connection=self.connection,
            plugin_key="github",
            datasource_config={
                "repository": "HyperBDR/sourcelens",
                "branch": "main",
                "directory": "docs",
            },
            sync_policy={"interval_seconds": 3600},
            target_path="/workspace/sourcelens",
        )

    def test_snapshot_contains_complete_resolved_sync_config(self):
        snapshot = create_datasource_sync_snapshot(self.datasource)

        self.assertEqual(snapshot.plugin_key, "github")
        self.assertEqual(snapshot.plugin_version, "1.0.0")
        self.assertEqual(
            snapshot.secret_version, self.connection.secret_version
        )
        self.assertEqual(
            snapshot.resolved_config["endpoint"], "https://github.com"
        )
        self.assertEqual(
            snapshot.resolved_config["datasource_config"]["branch"],
            "main",
        )
        self.assertEqual(
            snapshot.resolved_config["datasource_config"]["directory"],
            "docs",
        )
        self.assertEqual(
            snapshot.resolved_config["target_path"], "/workspace/sourcelens"
        )
        self.assertNotIn("encrypted", json.dumps(snapshot.resolved_config))
        invocation = PluginInvocation.objects.get(snapshot=snapshot)
        self.assertEqual(invocation.status, PluginInvocation.Status.AUTHORIZED)
        self.assertEqual(invocation.datasource, self.datasource)
        self.assertEqual(
            invocation.resource_summary,
            {
                "repository": "HyperBDR/sourcelens",
                "branch": "main",
                "directory": "docs",
            },
        )

    def test_snapshot_rejects_credential_shaped_datasource_config(self):
        self.datasource.datasource_config["access_token"] = "must-not-be-here"
        self.datasource.save(update_fields=["datasource_config"])

        with self.assertRaisesMessage(PluginRegistryError, "credentials"):
            create_datasource_sync_snapshot(self.datasource)

    def test_snapshot_rejects_resource_outside_connection_scope(self):
        self.datasource.datasource_config["repository"] = "other/repository"
        self.datasource.save(update_fields=["datasource_config"])

        with self.assertRaisesMessage(PluginRegistryError, "scope"):
            create_datasource_sync_snapshot(self.datasource)

    def test_snapshot_rejects_disabled_secret_version(self):
        self.connection.secret_version.status = "disabled"
        self.connection.secret_version.save(update_fields=["status"])

        with self.assertRaisesMessage(PluginRegistryError, "secret"):
            create_datasource_sync_snapshot(self.datasource)

    def test_plugin_datasource_dispatch_sends_snapshot_metadata_only(self):
        with patch("lens.datasource.services._send_lensnode_command") as send:
            dispatch_datasource_sync_async(
                self.datasource,
                task_id="sync-task",
                trigger="manual",
            )

        payload = send.call_args.args[1]
        self.assertEqual(payload["type"], "plugin_datasource_sync")
        self.assertEqual(payload["plugin_version"], "1.0.0")
        self.assertIn("snapshot_uuid", payload)
        self.assertNotIn("config", payload)
        self.assertNotIn("access_token", json.dumps(payload))

    def test_independent_snapshot_uses_admitted_node_without_binding(self):
        self.datasource.lensnode = None
        self.datasource.target_path = ''
        self.datasource.save(update_fields=['lensnode', 'target_path'])
        first = create_datasource_sync_snapshot(
            self.datasource, lensnode=self.node
        )
        second = create_datasource_sync_snapshot(
            self.datasource, lensnode=self.node
        )
        self.assertEqual(
            first.resolved_config['lensnode_uuid'], str(self.node.uuid)
        )
        self.assertEqual(
            first.resolved_config['target_path'],
            second.resolved_config['target_path'],
        )
        self.datasource.datasource_config['branch'] = 'develop'
        self.datasource.save(update_fields=['datasource_config'])
        changed = create_datasource_sync_snapshot(
            self.datasource, lensnode=self.node
        )
        self.assertNotEqual(
            first.resolved_config['target_path'],
            changed.resolved_config['target_path'],
        )
        self.datasource.refresh_from_db()
        self.assertIsNone(self.datasource.lensnode_id)
        self.assertEqual(self.datasource.target_path, '')
