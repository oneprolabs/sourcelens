"""Evaluation outputs do not grant access to a public provider's runtime configuration."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Tenant, TenantMembership
from lens.evaluation.comparison import compare_batches
from lens.evaluation.engine.batch import score_case_result
from lens.evaluation.revisions import record_dataset_revision
from lens.evaluation.snapshots import freeze_evaluation_results_for_runs, freeze_run_result
from lens.models import (
    Assistant,
    EvaluationCalibration,
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    Message,
    Run,
    RunTraceEvent,
    Session,
)


class EvaluationEvidenceScopeTests(TestCase):
    """Exercise real collaboration families, frozen snapshots and all read surfaces."""

    def setUp(self):
        """Build a consumer-owned evaluation with a foreign public collaborator."""
        self.tenant = Tenant.objects.create(name="Evidence owner", slug="eval-evidence-owner")
        self.other = Tenant.objects.create(name="Evidence provider", slug="eval-evidence-provider")
        self.user = get_user_model().objects.create_user(username="eval-evidence-admin")
        TenantMembership.objects.create(user=self.user, tenant=self.tenant, role="tenant_admin")
        self.assistant = Assistant.objects.create(tenant=self.tenant, name="Own", slug="evidence-own")
        self.provider = Assistant.objects.create(
            tenant=self.other, name="Provider", slug="evidence-provider", visibility="public"
        )
        self.dataset = EvaluationDataset.objects.create(assistant=self.assistant, name="Evidence")
        self.case = EvaluationCase.objects.create(dataset=self.dataset, input="Question")
        self.batch = EvaluationRun.objects.create(dataset=self.dataset, status="completed")
        self.parent = self.source(self.assistant)
        self.child = self.source(self.provider, parent_run=self.parent)
        self.secret = "provider-private-model-and-path"
        RunTraceEvent.objects.create(
            run=self.parent,
            tenant=self.tenant,
            event_id=uuid4(),
            sequence=1,
            timestamp=timezone.now(),
            event_type="model.started",
            payload={"model": self.secret},
        )
        self.scores = {
            "execution_success": {
                "score": 0.5,
                "passed": False,
                "category": "reliability",
                "detail": {"reason": self.secret},
            }
        }
        self.result = EvaluationResult.objects.create(
            evaluation_run=self.batch,
            case=self.case,
            source_run=self.parent,
            output_snapshot="Consumer answer",
            scores=self.scores,
            trace_summary={"events": [{"payload": {"model": self.secret}}]},
            error=self.secret,
            issues=[{"code": "execution_success_below_threshold", "detail": {"reason": self.secret}}],
            execution_metrics={"total_tokens": 23, "model": self.secret},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.base = "/api/lens/admin/evaluation/"
        self.detail_url = f"runs/{self.batch.pk}/results/{self.result.pk}/"

    def source(self, assistant, **values):
        """Collaborator runs retain the consuming tenant's conversation ownership."""
        session = Session.objects.create(tenant=self.tenant, assistant=assistant, user=self.user)
        question = Message.objects.create(session=session, role="user", content="Question", sequence=1)
        answer = Message.objects.create(session=session, role="assistant", content="Consumer answer", sequence=2)
        return Run.objects.create(
            tenant=self.tenant, session=session, input_message=question, output_message=answer, status="done", **values
        )

    def detail(self):
        """Fetch a real result response while preserving success assertions."""
        response = self.client.get(self.base + self.detail_url)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def assert_projected(self, data):
        """Keep user content and aggregate scores while removing runtime details."""
        self.assertNotIn(self.secret, str(data))
        self.assertEqual(data["scores"]["execution_success"]["score"], 0.5)
        self.assertEqual(data["execution_metrics"]["total_tokens"], 23)

    def test_mixed_family_reads_preserve_answers_and_scores_without_configuration(self):
        """List, detail, compare and historical calibration use the same projection."""
        EvaluationCalibration.objects.create(
            result=self.result,
            reviewer=self.user,
            answer_verdict="pass",
            evidence_verdict="fail",
            explanation="Reviewed",
            context_snapshot={"output": "Consumer answer", "scores": self.scores},
        )
        data = self.detail()
        self.assert_projected(data)
        self.assertEqual(data["output_snapshot"], "Consumer answer")
        self.assertFalse(data["trace_summary"])
        self.assertEqual(data["error"], "LENS_RUN_FAILED")
        listing = self.client.get(self.base + f"runs/{self.batch.pk}/results/").json()["data"]["results"]
        self.assert_projected(listing[0])
        comparison = compare_batches(self.batch, self.batch)["cases"][0]["left"]
        self.assert_projected(comparison)
        reviews = self.client.get(self.base + self.detail_url + "calibrations/").json()["data"]
        self.assertNotIn(self.secret, str(reviews))
        self.assertEqual(reviews[0]["context_snapshot"]["output"], "Consumer answer")

    def test_freeze_and_scoring_persist_restriction_but_keep_deterministic_verdict(self):
        """An allowed mixed evaluation is scored rather than discarded."""
        self.parent.status = "failed"
        self.parent.error = self.secret
        self.parent.termination_detail = {"reason": self.secret}
        self.parent.save(update_fields=["status", "error", "termination_detail"])
        self.batch.config_snapshot = {
            "metrics": [
                {
                    "name": "execution_success",
                    "evaluator_type": "outcome",
                    "category": "reliability",
                    "threshold": 1,
                    "weight": 1,
                }
            ]
        }
        self.batch.save(update_fields=["config_snapshot"])
        result = score_case_result(self.result)
        self.assertFalse(result.passed)
        self.assertTrue(result.evidence_restricted)
        self.child.delete()
        self.parent.delete()
        data = self.detail()
        self.assertNotIn(self.secret, str(data))
        self.assertEqual(data["output_snapshot"], "Consumer answer")
        self.assertFalse(data["scores"]["execution_success"]["passed"])

    def test_child_deletion_seals_previously_frozen_parent_evidence(self):
        """Deleting a collaborator cannot erase the evidence access boundary."""
        self.child.delete()
        self.assert_projected(self.detail())
        self.parent.delete()
        self.assert_projected(self.detail())

    def test_own_family_retains_trace_after_retention(self):
        """The persisted scope distinguishes verified own history from unknown history."""
        self.parent = self.source(self.assistant)
        self.source(self.assistant, parent_run=self.parent)
        RunTraceEvent.objects.create(
            run=self.parent,
            tenant=self.tenant,
            event_id=uuid4(),
            sequence=1,
            timestamp=timezone.now(),
            event_type="model.started",
            payload={"model": self.secret},
        )
        EvaluationResult.objects.filter(pk=self.result.pk).update(
            source_run=self.parent, source_run_uuid=self.parent.uuid, evidence_restricted=None
        )
        result = freeze_run_result(evaluation_run=self.batch, case=self.case, run=self.parent)
        self.assertIn(self.secret, str(self.detail()["trace_summary"]))
        self.assertFalse(result.evidence_restricted)
        freeze_evaluation_results_for_runs([self.parent.pk])
        self.parent.delete()
        self.assertIn(self.secret, str(self.detail()["trace_summary"]))

    def test_unknown_retained_snapshot_does_not_assume_provider_authority(self):
        """Old rows with no live source cannot establish configuration ownership."""
        EvaluationResult.objects.filter(pk=self.result.pk).update(source_run=None)
        self.assert_projected(self.detail())

    def test_sibling_family_and_late_collaborators_are_checked(self):
        """Checking only descendants or the original frozen flag would miss providers."""
        sibling = self.source(self.assistant, parent_run=self.parent)
        EvaluationResult.objects.filter(pk=self.result.pk).update(source_run=sibling)
        self.assert_projected(self.detail())

    def test_legacy_baseline_metadata_is_projected_on_all_snapshot_surfaces(self):
        """Cases, revisions, batch snapshots and calibration expectations cannot bypass scope."""
        baseline = {
            "run_uuid": str(self.parent.uuid),
            "answer": "Reference answer",
            "metadata": {"model_refs": self.secret},
        }
        self.case.config = {"baseline": baseline}
        self.case.save(update_fields=["config"])
        revision = record_dataset_revision(self.dataset)
        self.batch.config_snapshot = {"case_expectations": {str(self.case.pk): {"baseline": baseline}}}
        self.batch.save(update_fields=["config_snapshot"])
        paths = [
            f"datasets/{self.dataset.pk}/cases/",
            f"cases/{self.case.pk}/",
            f"datasets/{self.dataset.pk}/revisions/?revision={revision.revision}",
            f"runs/{self.batch.pk}/",
            f"runs/{self.batch.pk}/report/",
            self.detail_url,
        ]
        for phase in ("live", "child_deleted", "source_deleted"):
            if phase == "child_deleted":
                self.child.delete()
            elif phase == "source_deleted":
                self.parent.delete()
            for path in paths:
                response = self.client.get(self.base + path)
                self.assertEqual(response.status_code, 200, (path, response.content))
                self.assertNotIn(self.secret, str(response.json()), (phase, path))
                self.assertIn("Reference answer", str(response.json()))

    def test_result_listing_batches_family_queries(self):
        """A page of results does not traverse each collaboration family separately."""
        for index in range(7):
            case = EvaluationCase.objects.create(dataset=self.dataset, input=f"Question {index}")
            source = self.source(self.assistant)
            self.source(self.provider, parent_run=source)
            EvaluationResult.objects.create(evaluation_run=self.batch, case=case, source_run=source, scores=self.scores)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.base + f"runs/{self.batch.pk}/results/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.secret, str(response.json()))
        family_queries = [row for row in queries if 'FROM "lens_run"' in row["sql"]]
        self.assertLessEqual(len(family_queries), 4)

    def test_collaborator_detachment_cannot_reopen_frozen_evidence(self):
        """The live family can shrink without changing its historical authority."""
        self.child.parent_run = None
        self.child.save(update_fields=["parent_run"])
        self.assert_projected(self.detail())

    def test_management_list_and_detail_preserve_scope_after_child_cleanup(self):
        """Shared run inspection honors the same durable restriction marker."""
        self.child.delete()
        listing = self.client.get("/api/lens/admin/runs/")
        self.assertEqual(listing.status_code, 200, listing.content)
        rows = listing.json()["data"]["results"]
        row = next(item for item in rows if item["uuid"] == str(self.parent.uuid))
        self.assertFalse(row["available_actions"]["inspect_configuration"])
        self.assertEqual(self.client.get(f"/api/lens/admin/runs/{self.parent.uuid}/trajectory/").status_code, 403)

    def test_stale_full_run_save_cannot_clear_sealed_restriction(self):
        """A cached parent from before delegation cannot overwrite the persistent marker."""
        self.assertFalse(self.parent.configuration_evidence_restricted)
        self.parent.save()
        self.child.delete()
        self.assert_projected(self.detail())
        self.parent.refresh_from_db()
        self.assertTrue(self.parent.configuration_evidence_restricted)

    def test_bulk_child_deletion_preserves_scope_and_reused_queryset_checks_new_runs(self):
        """Pre-delete batching seals each family once, including a later reuse."""
        children = Run.objects.filter(parent_run=self.parent)
        children.delete()
        Run.objects.filter(pk=self.parent.pk).update(configuration_evidence_restricted=False)
        EvaluationResult.objects.filter(pk=self.result.pk).update(evidence_restricted=None)
        self.source(self.provider, parent_run=self.parent)
        children.delete()
        self.assert_projected(self.detail())

    def test_late_collaborator_restricts_previously_verified_result(self):
        """A verified flag is never a permanent bypass of the current collaboration family."""
        parent = self.source(self.assistant)
        EvaluationResult.objects.filter(pk=self.result.pk).update(
            source_run=parent, source_run_uuid=parent.uuid, evidence_restricted=False
        )
        self.assertIn(self.secret, str(self.detail()))
        self.source(self.provider, parent_run=parent)
        self.assert_projected(self.detail())

    def test_stale_calibration_does_not_borrow_current_owned_run_authority(self):
        """Historical review scores keep their old scope when the case is rerun."""
        EvaluationCalibration.objects.create(
            result=self.result,
            reviewer=self.user,
            answer_verdict="pass",
            evidence_verdict="fail",
            explanation="Reviewed",
            context_snapshot={
                "source_run_uuid": str(self.parent.uuid),
                "output": "Consumer answer",
                "scores": self.scores,
            },
        )
        parent = self.source(self.assistant)
        EvaluationResult.objects.filter(pk=self.result.pk).update(
            source_run=parent, source_run_uuid=parent.uuid, evidence_restricted=False
        )
        reviews = self.client.get(self.base + self.detail_url + "calibrations/").json()["data"]
        self.assertNotIn(self.secret, str(reviews))

    def test_baseline_projection_does_not_mutate_frozen_revisions(self):
        """Response filtering must not silently rewrite the original snapshot or hash."""
        baseline = {"run_uuid": str(self.parent.uuid), "answer": "Reference", "metadata": {"model_refs": self.secret}}
        self.case.config = {"baseline": baseline}
        self.case.save(update_fields=["config"])
        revision = record_dataset_revision(self.dataset)
        original_hash = revision.content_hash
        response = self.client.get(self.base + f"datasets/{self.dataset.pk}/revisions/?revision={revision.revision}")
        self.assertNotIn(self.secret, str(response.json()))
        revision.refresh_from_db()
        self.assertEqual(revision.content_hash, original_hash)
        self.assertEqual(revision.cases[0]["config"]["baseline"], baseline)

    def test_deletion_scope_updates_roll_back_with_run_deletion(self):
        """A failed cleanup cannot change another run's retained access facts."""
        from django.db import transaction

        Run.objects.filter(pk__in=[self.parent.pk, self.child.pk]).update(configuration_evidence_restricted=False)
        EvaluationResult.objects.filter(pk=self.result.pk).update(evidence_restricted=None)
        with self.assertRaisesRegex(RuntimeError, "abort cleanup"):
            with transaction.atomic():
                self.child.delete()
                raise RuntimeError("abort cleanup")
        self.parent.refresh_from_db()
        self.result.refresh_from_db()
        self.assertFalse(self.parent.configuration_evidence_restricted)
        self.assertIsNone(self.result.evidence_restricted)
        self.assert_projected(self.detail())

    def test_failed_pre_delete_does_not_poison_reused_queryset_scope_cache(self):
        """A later cleanup must reseal facts rolled back by another delete handler."""
        from django.db import transaction
        from django.db.models.signals import pre_delete

        Run.objects.filter(pk__in=[self.parent.pk, self.child.pk]).update(configuration_evidence_restricted=False)
        EvaluationResult.objects.filter(pk=self.result.pk).update(evidence_restricted=None)
        children = Run.objects.filter(pk=self.child.pk)

        def reject_cleanup(sender, instance, **kwargs):
            """Fail after the evidence receiver has run but before rows are deleted."""
            raise RuntimeError("cleanup rejected")

        pre_delete.connect(reject_cleanup, sender=Run)
        try:
            with self.assertRaisesRegex(RuntimeError, "cleanup rejected"):
                with transaction.atomic():
                    children.delete()
        finally:
            pre_delete.disconnect(reject_cleanup, sender=Run)
        self.parent.refresh_from_db()
        self.assertFalse(self.parent.configuration_evidence_restricted)
        children.delete()
        self.parent.refresh_from_db()
        self.assertTrue(self.parent.configuration_evidence_restricted)
        self.assert_projected(self.detail())

    def test_retry_resets_scope_only_with_the_old_case_snapshot(self):
        """A new owned attempt can regain its own trace without reviving an old mixed trace."""
        from lens.evaluation.engine.batch import retry_failed_cases

        EvaluationResult.objects.filter(pk=self.result.pk).update(evidence_restricted=True, passed=False)
        self.assertEqual(retry_failed_cases(self.batch.pk), 1)
        self.result.refresh_from_db()
        self.assertIsNone(self.result.evidence_restricted)
        self.assertEqual(self.result.trace_summary, {})
        source = self.source(self.assistant)
        result = freeze_run_result(evaluation_run=self.batch, case=self.case, run=source)
        self.assertFalse(result.evidence_restricted)
