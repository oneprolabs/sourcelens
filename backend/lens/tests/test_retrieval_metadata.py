"""Datasource processing metadata is persisted and dispatched as read-only context."""

from types import SimpleNamespace
from django.test import TestCase

from lens.datasource.packages import run_datasource_snapshots
from lens.models import DataSource
from lens.serializers import DataSourceSerializer
from lens.tasks import _save_datasource_metadata


class RetrievalMetadataTests(TestCase):
    """Metadata belongs to its datasource and describes the reporting node."""

    def setUp(self):
        self.datasource = DataSource.objects.create(
            name="Docs", source_type="git", metadata={"custom": "preserved"},
        )
        self.retrieval = {
            "analysis_status": "complete", "files": 1,
            "index": {"status": "ready"},
            "recommendation": {"ranked_tool": "search_indexed_workspace"},
        }

    def test_processing_metadata_merges_without_touching_configuration(self):
        _save_datasource_metadata(
            self.datasource,
            {"status": "success", "datasource_metadata": {"retrieval": self.retrieval}},
            {"lensnode_uuid": "node-one"},
        )
        self.datasource.refresh_from_db()
        self.assertEqual(self.datasource.metadata["custom"], "preserved")
        self.assertEqual(self.datasource.metadata["retrieval"]["lensnode_uuid"], "node-one")
        self.assertEqual(self.datasource.config, {})
        self.assertEqual(self.datasource.sync_policy, {})
        serializer = DataSourceSerializer(self.datasource)
        self.assertTrue(serializer.fields["metadata"].read_only)
        self.assertEqual(serializer.data["metadata"], self.datasource.metadata)

    def test_failed_processing_invalidates_previous_readiness(self):
        self.datasource.metadata["retrieval"] = self.retrieval
        self.datasource.save(update_fields=["metadata"])
        _save_datasource_metadata(self.datasource, {"status": "failed"}, {})
        self.datasource.refresh_from_db()
        self.assertEqual(self.datasource.metadata["retrieval"]["index"]["status"], "unverified")
        self.assertNotIn("ranked_tool", self.datasource.metadata["retrieval"]["recommendation"])
        self.assertEqual(self.datasource.metadata["custom"], "preserved")

    def test_run_dispatch_does_not_reuse_another_nodes_index_metadata(self):
        self.datasource.metadata["retrieval"] = {**self.retrieval, "lensnode_uuid": "node-one"}
        row = SimpleNamespace(
            uuid="snapshot", version_id=None, datasource=self.datasource,
            mount_name="docs", required=True,
        )
        run = SimpleNamespace(
            session=SimpleNamespace(datasource_snapshots=SimpleNamespace(select_related=lambda *_: [row])),
            lensnode=SimpleNamespace(uuid="node-one"),
        )
        self.assertEqual(run_datasource_snapshots(run)[0]["metadata"]["retrieval"]["index"]["status"], "ready")
        run.lensnode.uuid = "node-two"
        self.assertNotIn("retrieval", run_datasource_snapshots(run)[0]["metadata"])
        self.assertIn("retrieval", self.datasource.metadata)
