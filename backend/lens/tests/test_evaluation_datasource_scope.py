"""Historical evaluation resources must remain within their frozen tenant scope."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Tenant
from lens.evaluation.engine.batch import _known_datasource_paths
from lens.evaluation.revisions import baseline_metadata
from lens.models import (
    Assistant,
    AssistantDataSourceBinding,
    DataSource,
    DataSourceItem,
    DataSourceVersion,
    EvaluationDataset,
    EvaluationRun,
    Message,
    Run,
    Session,
    SessionDataSource,
)


class EvaluationDatasourceScopeTests(TestCase):
    """Use real malformed associations rather than mocking the resource query."""

    def setUp(self):
        """Create two source trees and one tenant-owned evaluation."""
        self.tenant = Tenant.objects.create(name="Resource owner", slug="eval-resource-owner")
        self.other = Tenant.objects.create(name="Resource other", slug="eval-resource-other")
        self.user = get_user_model().objects.create_user(username="eval-resource-admin")
        self.assistant = Assistant.objects.create(tenant=self.tenant, name="Resource assistant", slug="eval-resource")
        self.session = Session.objects.create(tenant=self.tenant, assistant=self.assistant, user=self.user)
        question = Message.objects.create(session=self.session, role="user", sequence=1, content="Question")
        self.run = Run.objects.create(tenant=self.tenant, session=self.session, input_message=question)
        self.own, self.item, self.version = self.source(self.tenant, "own")
        self.foreign, self.foreign_item, self.foreign_version = self.source(self.other, "foreign")
        self.dataset = EvaluationDataset.objects.create(assistant=self.assistant, name="Resource evaluation")
        self.batch = EvaluationRun.objects.create(dataset=self.dataset)

    def source(self, tenant, name):
        """Create an immutable old version alongside a newer live version."""
        source = DataSource.objects.create(tenant=tenant, name=name, source_type="git")
        item = DataSourceItem.objects.create(
            datasource=source, name=name, source_type="git", storage_key=name, current_version="v2"
        )
        version = DataSourceVersion.objects.create(item=item, version="v1", storage_key=name, checksum=f"{name}-hash")
        DataSourceVersion.objects.create(item=item, version="v2", storage_key=name, checksum="new-hash")
        return source, item, version

    def snapshot(self, datasource=None, item=None, version=None, **values):
        """Freeze selected relationships without model-level repair."""
        return SessionDataSource.objects.create(
            session=self.session,
            datasource=datasource or self.own,
            item=item,
            version=version,
            datasource_version="v1",
            storage_key="snapshot",
            mount_name=f"mount-{self.session.datasource_snapshots.count()}",
            **values,
        )

    def test_baseline_omits_foreign_and_mismatched_snapshot_relations(self):
        """No identifiers or checksums may cross the datasource/item/version boundary."""
        self.snapshot(self.own, self.item, self.version)
        self.snapshot(self.foreign, self.foreign_item, self.foreign_version)
        self.snapshot(self.own, self.foreign_item, self.foreign_version)
        self.snapshot(self.own, self.item, self.foreign_version)
        self.snapshot(self.own, None, self.version)
        rows = baseline_metadata(self.run)["datasources"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["datasource_uuid"], str(self.own.uuid))
        self.assertEqual(rows[0]["version"], "v1")
        self.assertEqual(rows[0]["checksum"], "own-hash")

    def test_whole_datasource_snapshot_without_item_or_version_remains_valid(self):
        """An optional child relation is not required for whole-source snapshots."""
        self.snapshot()
        rows = baseline_metadata(self.run)["datasources"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["item_uuid"])
        self.assertIsNone(rows[0]["version_uuid"])

    def manifest(self, *bindings):
        """Use precisely the frozen binding selection, including removed bindings."""
        self.batch.config_snapshot = {"assistant": {"datasource_binding_uuids": [str(row.uuid) for row in bindings]}}
        return _known_datasource_paths(self.batch)

    def binding(self, datasource, item=None, name="docs"):
        """Bind a source without invoking unrelated routing generation."""
        binding = AssistantDataSourceBinding(
            assistant=self.assistant, datasource=datasource, item=item, mount_name=name
        )
        AssistantDataSourceBinding.objects.bulk_create([binding])
        return binding

    def test_manifest_skips_incomplete_or_invalid_frozen_bindings(self):
        """A partial manifest must never turn valid citations into scoring failures."""
        own = self.binding(self.own)
        foreign = self.binding(self.foreign, name="foreign")
        mismatch = self.binding(self.own, self.foreign_item, name="mismatch")
        for bindings in ((foreign,), (mismatch,), (own, foreign)):
            self.assertIsNone(self.manifest(*bindings))
        removed = self.binding(self.own, name="removed")
        AssistantDataSourceBinding.objects.filter(pk=removed.pk).delete()
        self.assertIsNone(self.manifest(own, removed))

    def test_manifest_respects_item_selection_and_whole_source_selection(self):
        """An item binding does not implicitly authorize sibling storage paths."""
        DataSourceItem.objects.create(datasource=self.own, name="sibling", source_type="git", storage_key="sibling")
        item_binding = self.binding(self.own, self.item)
        self.assertEqual(self.manifest(item_binding), ["own"])
        whole_binding = self.binding(self.own, name="whole")
        self.assertEqual(self.manifest(whole_binding), ["own", "sibling"])

    def test_legacy_baseline_response_filters_invalid_frozen_resource_metadata(self):
        """Fixing new captures also protects baseline metadata frozen by older code."""
        from lens.evaluation.evidence import project_baseline_evidence

        self.snapshot(self.own, self.item, self.version)
        self.snapshot(self.foreign, self.foreign_item, self.foreign_version)
        valid = {
            "datasource_uuid": str(self.own.uuid),
            "item_uuid": str(self.item.uuid),
            "version_uuid": str(self.version.uuid),
            "checksum": "own-hash",
        }
        foreign = {
            "datasource_uuid": str(self.foreign.uuid),
            "item_uuid": str(self.foreign_item.uuid),
            "version_uuid": str(self.foreign_version.uuid),
            "checksum": "foreign-hash",
        }
        mismatch = {**valid, "version_uuid": str(self.foreign_version.uuid), "checksum": "foreign-hash"}
        payload = {
            "baseline": {
                "run_uuid": str(self.run.uuid),
                "answer": "Reference",
                "metadata": {"datasources": [valid, foreign, mismatch]},
            }
        }
        result = project_baseline_evidence(payload, self.tenant.pk)
        self.assertEqual(result["baseline"]["metadata"]["datasources"], [valid])
        self.assertEqual(len(payload["baseline"]["metadata"]["datasources"]), 3)

    def test_malformed_legacy_baseline_metadata_does_not_break_response(self):
        """User-editable metadata may be absent or scalar in historical case configs."""
        from lens.evaluation.evidence import project_baseline_evidence

        for metadata in (None, "manual reference", 7, {"datasources": []}, {"datasources": 7}):
            payload = {"baseline": {"run_uuid": str(self.run.uuid), "metadata": metadata}}
            self.assertIsInstance(project_baseline_evidence(payload, self.tenant.pk), dict)
