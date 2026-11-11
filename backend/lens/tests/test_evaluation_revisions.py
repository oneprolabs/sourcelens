import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lens.evaluation.revisions import baseline_metadata
from lens.models import (
    Assistant,
    DataSource,
    DataSourceItem,
    DataSourceVersion,
    EvaluationDataset,
    LensNode,
    RunTraceEvent,
    Session,
    SessionDataSource,
)
from lens.services import create_execution_run


class EvaluationRevisionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="revision-admin", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.node = LensNode.objects.create(name="node", status="online", enrollment_status="approved")
        self.assistant = Assistant.objects.create(
            name="assistant",
            slug="revision-assistant",
            lensnode=self.node,
            selected_task="knowledge_qa",
            agent_model_ref=uuid.uuid4(),
        )
        response = self.client.post(
            "/api/lens/admin/evaluation/datasets/",
            {
                "name": "Revisions",
                "assistant": str(self.assistant.uuid),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.dataset = EvaluationDataset.objects.get(uuid=response.json()["data"]["uuid"])
        self.base = f"/api/lens/admin/evaluation/datasets/{self.dataset.uuid}/"

    def test_create_edit_delete_and_import_are_separate_immutable_revisions(self):
        created = self.client.post(self.base + "cases/", {"input": "first", "expected_answer": "old"}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        case_id = created.json()["data"]["uuid"]
        changed = self.client.patch(
            f"/api/lens/admin/evaluation/cases/{case_id}/", {"expected_answer": "new"}, format="json"
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        old = self.client.get(self.base + "revisions/?revision=2").json()["data"]
        self.assertEqual(old["cases"][0]["expected_answer"], "old")
        self.assertEqual(self.client.delete(f"/api/lens/admin/evaluation/cases/{case_id}/").status_code, 204)
        imported = self.client.post(
            self.base + "import/", {"text": '{"input":"second"}\n{"input":"third"}'}, format="json"
        )
        self.assertEqual(imported.status_code, 201, imported.content)
        revisions = self.client.get(self.base + "revisions/").json()["data"]
        self.assertEqual([item["case_count"] for item in reversed(revisions)], [0, 1, 1, 0, 2])
        self.assertEqual(self.client.get(self.base + "revisions/?revision=2").json()["data"], old)

    def test_historical_metadata_uses_execution_snapshot_not_live_node(self):
        session = Session.objects.create(assistant=self.assistant, user=self.user)
        self.node.agent_version = "old"
        self.node.save(update_fields=["agent_version"])
        run = create_execution_run(session=session, question="question", enqueue=False)
        self.node.agent_version = "new"
        self.node.save(update_fields=["agent_version"])
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=1,
            event_type="model.started",
            timestamp=timezone.now(),
            payload={"model": "observed-model"},
        )
        metadata = baseline_metadata(run)
        self.assertEqual(metadata["lensnode_version"], "old")
        self.assertEqual(metadata["models"], ["observed-model"])
        self.assertIsNone(metadata["platform_version"])
        self.assertIsNone(metadata["datasources"])

    def test_dataset_metadata_changes_create_revision(self):
        response = self.client.patch(self.base, {"name": "Renamed"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        old = self.client.get(self.base + "revisions/?revision=1").json()["data"]
        new = self.client.get(self.base + "revisions/?revision=2").json()["data"]
        self.assertEqual(old["metadata"]["name"], "Revisions")
        self.assertEqual(new["metadata"]["name"], "Renamed")

    def test_baseline_metadata_uses_frozen_datasource_version(self):
        session = Session.objects.create(assistant=self.assistant, user=self.user)
        run = create_execution_run(session=session, question="question", enqueue=False)
        datasource = DataSource.objects.create(name="knowledge", source_type="git")
        item = DataSourceItem.objects.create(datasource=datasource, name="docs", source_type="git", storage_key="docs")
        old = DataSourceVersion.objects.create(item=item, version="v1", storage_key="v1", checksum="abc")
        SessionDataSource.objects.create(
            session=session,
            datasource=datasource,
            item=item,
            version=old,
            datasource_version="v1",
            storage_key="v1",
            mount_name="docs",
        )
        item.current_version = "v2"
        item.save(update_fields=["current_version"])
        DataSourceVersion.objects.create(item=item, version="v2", storage_key="v2", checksum="def")
        metadata = baseline_metadata(run)
        self.assertEqual(metadata["datasources"][0]["version"], "v1")
        self.assertEqual(metadata["datasources"][0]["checksum"], "abc")

    def test_batch_records_revision_and_uses_its_frozen_question(self):
        from unittest.mock import patch
        from lens.evaluation.engine.batch import create_evaluation_run, start_evaluation_run
        from lens.models import EvaluationCase, Run

        self.client.post(self.base + "cases/", {"input": "first question"}, format="json")
        batch = create_evaluation_run(dataset=self.dataset, requested_by=self.user)
        self.assertEqual(batch.config_snapshot["dataset"]["revision"], 2)
        case = EvaluationCase.objects.get(dataset=self.dataset)
        self.client.patch(f"/api/lens/admin/evaluation/cases/{case.uuid}/", {"input": "changed"}, format="json")
        with patch("lens.evaluation.engine.batch._enqueue_run"):
            with self.captureOnCommitCallbacks(execute=True):
                start_evaluation_run(batch.uuid)
        run = Run.objects.get(source="evaluation")
        self.assertEqual(run.input_message.content, "first question")

    def test_batch_reserves_cases_before_dispatch(self):
        from lens.evaluation.engine.batch import create_evaluation_run

        response = self.client.post(self.base + "cases/", {"input": "frozen"}, format="json")
        case_uuid = response.json()["data"]["uuid"]
        batch = create_evaluation_run(dataset=self.dataset, requested_by=self.user)

        response = self.client.delete(f"/api/lens/admin/evaluation/cases/{case_uuid}/")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(batch.results.count(), 1)

    def test_legacy_answer_migration_preserves_explicit_modes_and_revisions(self):
        from importlib import import_module

        from django.apps import apps

        from lens.evaluation.engine.batch import create_evaluation_run
        from lens.models import EvaluationCase

        legacy = self.client.post(
            self.base + "cases/", {"input": "legacy", "expected_answer": "answer"}, format="json"
        ).json()["data"]
        semantic = self.client.post(
            self.base + "cases/",
            {"input": "semantic", "expected_answer": "answer", "config": {"answer_match_mode": "semantic"}},
            format="json",
        ).json()["data"]
        batch = create_evaluation_run(dataset=self.dataset, requested_by=self.user)
        revision_count = self.dataset.revisions.count()

        migration = import_module("lens.migrations.0069_evaluation_revisions_and_reliability")
        migration.preserve_legacy_answer_modes(apps, None)

        self.assertEqual(EvaluationCase.objects.get(pk=legacy["uuid"]).config["answer_match_mode"], "exact")
        self.assertEqual(EvaluationCase.objects.get(pk=semantic["uuid"]).config["answer_match_mode"], "semantic")
        batch.refresh_from_db()
        self.assertEqual(batch.config_snapshot["case_expectations"][legacy["uuid"]]["answer_match_mode"], "exact")
        self.assertEqual(self.dataset.revisions.count(), revision_count + 1)
        self.assertEqual(self.dataset.revisions.first().cases[0]["config"]["answer_match_mode"], "exact")
        migration.preserve_legacy_answer_modes(apps, None)
        self.assertEqual(self.dataset.revisions.count(), revision_count + 1)
