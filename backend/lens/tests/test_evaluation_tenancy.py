"""Evaluation content belongs to a tenant, independently of staff and creator identity."""

from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantDelegation, TenantMembership
from lens.evaluation.comparison import compare_batches
from lens.evaluation.engine.batch import (
    _reenqueue_stuck_case_runs,
    advance_evaluation_run,
    build_report,
    cancel_evaluation_run,
    case_run_key,
    create_evaluation_run,
    ensure_case_run,
    finalize_case_result,
    finalize_evaluation_results_for_retention,
    retry_failed_cases,
)
from lens.evaluation.revisions import record_dataset_revision
from lens.models import (
    Assistant,
    EvaluationCalibration,
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    LensNode,
    Message,
    Run,
    RunExecution,
    RunTraceEvent,
    Session,
)


class EvaluationTenancyTests(TestCase):
    """Use real memberships and delegation headers across the complete evaluation API."""

    def setUp(self):
        """Create two tenant histories and an explicitly unowned legacy history."""
        self.tenant = Tenant.objects.create(name="Evaluation owner", slug="eval-owner")
        self.other = Tenant.objects.create(name="Evaluation other", slug="eval-other")
        self.admin = self.user("eval-admin", self.tenant, "tenant_admin")
        self.colleague = self.user("eval-colleague", self.tenant, "tenant_admin")
        self.foreign_admin = self.user("eval-foreign", self.other, "tenant_admin")
        self.member = self.user("eval-member", self.tenant, "member", is_staff=True)
        self.platform = get_user_model().objects.create_user(username="eval-platform", is_staff=True)
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        self.legacy = get_user_model().objects.create_user(username="eval-legacy", is_staff=True)
        self.node = LensNode.objects.create(name="Eval node", status="online", enrollment_status="approved")
        self.assistant = Assistant.objects.create(tenant=self.tenant, name="Eval own", slug="eval-own")
        self.foreign_assistant = Assistant.objects.create(
            tenant=self.other, name="Eval public", slug="eval-public", visibility="public"
        )
        self.dataset, self.case, self.batch, self.result = self.history(self.assistant, self.colleague)
        self.foreign_dataset, self.foreign_case, self.foreign_batch, self.foreign_result = self.history(
            self.foreign_assistant, self.foreign_admin
        )
        legacy_assistant = Assistant.objects.create(name="Eval legacy", slug="eval-legacy")
        self.legacy_dataset, _case, self.legacy_batch, _result = self.history(legacy_assistant, self.legacy)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.base = "/api/lens/admin/evaluation/"

    def user(self, name, tenant, role, **kwargs):
        """Persist a live tenant role rather than patching feature checks."""
        user = get_user_model().objects.create_user(username=name, **kwargs)
        TenantMembership.objects.create(user=user, tenant=tenant, role=role)
        return user

    def history(self, assistant, user):
        """Freeze an existing result with a distinct content marker."""
        dataset = EvaluationDataset.objects.create(assistant=assistant, name=assistant.name, created_by=user)
        case = EvaluationCase.objects.create(dataset=dataset, input=f"Question for {assistant.slug}")
        record_dataset_revision(dataset)
        batch = EvaluationRun.objects.create(
            dataset=dataset, requested_by=user, status="completed", config_snapshot={"requested_by": str(user.pk)}
        )
        result = EvaluationResult.objects.create(
            evaluation_run=batch, case=case, output_snapshot=f"Answer for {assistant.slug}", scores={"answer": 1}
        )
        return dataset, case, batch, result

    def content_urls(self, dataset, case, batch, result):
        """Cover every public read route that can return frozen content."""
        return [
            f"datasets/{dataset.pk}/",
            f"datasets/{dataset.pk}/cases/",
            f"datasets/{dataset.pk}/revisions/?revision=1",
            f"datasets/{dataset.pk}/runs/",
            f"cases/{case.pk}/",
            f"runs/{batch.pk}/",
            f"runs/{batch.pk}/results/",
            f"runs/{batch.pk}/results/{result.pk}/",
            f"runs/{batch.pk}/results/{result.pk}/calibrations/",
            f"runs/{batch.pk}/report/",
        ]

    def test_dataset_list_filters_before_pagination_and_projects_latest_owned_batch(self):
        """List filters cover all rows and the latest batch preserves unknown and zero scores."""
        self.dataset.status = "draft"
        self.dataset.save(update_fields=["status"])
        self.batch.aggregate_score = 0
        self.batch.save(update_fields=["aggregate_score"])
        self.client.force_authenticate(self.admin)
        url = self.base + "datasets/"
        response = self.client.get(url, {"q": "Eval own", "status": "draft", "assistant_slug": self.assistant.slug})
        row = response.json()["data"]["results"][0]
        self.assertEqual(row["latest_run"]["uuid"], str(self.batch.pk))
        self.assertEqual(float(row["latest_run"]["aggregate_score"]), 0)
        self.assertEqual(self.client.get(url, {"status": "active"}).json()["data"]["count"], 0)
        self.assertEqual(self.client.get(url, {"q": "missing"}).json()["data"]["count"], 0)
        self.assertEqual(self.client.get(url, {"assistant_slug": self.foreign_assistant.slug}).json()["data"]["count"], 0)
        foreign = EvaluationRun.objects.create(dataset=self.dataset, status="completed", aggregate_score=1)
        EvaluationRun.objects.filter(pk=foreign.pk).update(tenant=self.other)
        row = self.client.get(url).json()["data"]["results"][0]
        self.assertEqual(row["latest_run"]["uuid"], str(self.batch.pk))
        latest = EvaluationRun.objects.create(dataset=self.dataset, status="queued")
        row = self.client.get(url).json()["data"]["results"][0]
        self.assertEqual(row["latest_run"]["uuid"], str(latest.pk))
        self.assertIsNone(row["latest_run"]["aggregate_score"])

    def test_tenant_admin_reads_colleagues_content_but_not_public_provider_history(self):
        """Administrative scope is the tenant rather than the original creator."""
        for path in self.content_urls(self.dataset, self.case, self.batch, self.result):
            response = self.client.get(self.base + path)
            self.assertEqual(response.status_code, 200, (path, response.content))
        listing = self.client.get(self.base + "datasets/").json()["data"]["results"]
        self.assertEqual([row["uuid"] for row in listing], [str(self.dataset.pk)])
        batches = self.client.get(self.base + "runs/").json()["data"]["results"]
        self.assertEqual([row["uuid"] for row in batches], [str(self.batch.pk)])
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        for path in self.content_urls(self.foreign_dataset, self.foreign_case, self.foreign_batch, self.foreign_result):
            response = self.client.get(self.base + path)
            self.assertEqual(response.status_code, 404, (path, response.content))

    def test_public_usage_does_not_allow_evaluation_binding_or_rebinding(self):
        """Evaluation snapshots inspect configuration and therefore require ownership."""
        response = self.client.post(
            self.base + "datasets/",
            {"name": "Provider copy", "assistant": str(self.foreign_assistant.uuid)},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        response = self.client.patch(
            self.base + f"datasets/{self.dataset.pk}/", {"assistant": str(self.foreign_assistant.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.assistant_id, self.assistant.pk)

    def test_platform_and_staff_member_do_not_inherit_tenant_evaluation_authority(self):
        """Role checks precede all evaluation content queries."""
        for actor in (self.platform, self.member):
            self.client.force_authenticate(actor)
            for path in ("access/", "datasets/", "runs/", f"datasets/{self.dataset.pk}/"):
                response = self.client.get(self.base + path)
                self.assertEqual(response.status_code, 403, response.content)

    def test_legacy_operator_reads_only_unowned_evaluation_history(self):
        """No-tenant compatibility never means a global collection."""
        self.client.force_authenticate(self.legacy)
        listing = self.client.get(self.base + "datasets/").json()["data"]["results"]
        self.assertEqual([row["uuid"] for row in listing], [str(self.legacy_dataset.pk)])
        self.assertEqual(self.client.get(self.base + f"runs/{self.batch.pk}/").status_code, 404)

    def test_cross_tenant_comparison_and_case_mutation_are_denied(self):
        """Foreign IDs cannot be combined with an authorized parent."""
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        response = self.client.get(self.base + f"runs/{self.batch.pk}/compare/", {"other": str(self.foreign_batch.pk)})
        self.assertEqual(response.status_code, 404, response.content)
        response = self.client.patch(self.base + f"cases/{self.foreign_case.pk}/", {"input": "Changed"}, format="json")
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(self.client.delete(self.base + f"cases/{self.foreign_case.pk}/").status_code, 404)

    def delegate(self, mode):
        """Use the same real delegation path as the web client."""
        delegation = TenantDelegation.objects.create(
            platform_user=self.platform,
            tenant=self.tenant,
            mode=mode,
            expires_at=timezone.now() + timedelta(hours=1),
            reason="Evaluation support",
        )
        self.client.force_authenticate(self.platform)
        self.client.credentials(HTTP_X_DELEGATION_ID=str(delegation.uuid))

    def test_readonly_delegation_can_read_but_cannot_mutate_evaluation_content(self):
        """Cases, imports, batches and calibration cannot bypass read-only support."""
        self.delegate("readonly")
        self.assertEqual(self.client.get(self.base + f"runs/{self.batch.pk}/report/").status_code, 200)
        mutations = [
            ("post", "datasets/", {"name": "New", "assistant": str(self.assistant.uuid)}),
            ("patch", f"datasets/{self.dataset.pk}/", {"name": "Changed"}),
            ("post", f"datasets/{self.dataset.pk}/cases/", {"input": "Changed"}),
            ("post", f"datasets/{self.dataset.pk}/import/", {"text": '{"input":"Changed"}'}),
            ("post", f"datasets/{self.dataset.pk}/runs/", {}),
            ("patch", f"cases/{self.case.pk}/", {"input": "Changed"}),
            ("delete", f"cases/{self.case.pk}/", {}),
            ("post", f"runs/{self.batch.pk}/results/{self.result.pk}/calibrations/", {}),
            *[("post", f"runs/{self.batch.pk}/{action}/", {}) for action in ("cancel", "retry", "pause", "resume")],
        ]
        for method, path, payload in mutations:
            response = getattr(self.client, method)(self.base + path, payload, format="json")
            self.assertEqual(response.status_code, 403, (path, response.content))

    def test_delegation_omits_structured_directory_identities(self):
        """Do not return creator, requester or human reviewer identities to support operators."""
        EvaluationCalibration.objects.create(
            result=self.result,
            reviewer=self.colleague,
            answer_verdict="pass",
            evidence_verdict="pass",
            explanation="Reviewed",
        )
        self.delegate("readonly")
        dataset = self.client.get(self.base + f"datasets/{self.dataset.pk}/").json()["data"]
        self.assertNotIn("created_by", dataset)
        self.assertNotIn("created_by_email", dataset)
        batch = self.client.get(self.base + f"runs/{self.batch.pk}/").json()["data"]
        self.assertNotIn("requested_by", batch["config_snapshot"])
        report = self.client.get(self.base + f"runs/{self.batch.pk}/report/").json()["data"]
        self.assertNotIn("requested_by", report["evaluation_run"]["config_snapshot"])
        reviews = self.client.get(self.base + f"runs/{self.batch.pk}/results/{self.result.pk}/calibrations/").json()[
            "data"
        ]
        self.assertNotIn("reviewer", reviews[0])

    def test_source_run_scope_cannot_be_used_to_copy_foreign_content(self):
        """A corrupted source relation is rejected before freezing or judging it."""
        from lens.evaluation.snapshots import freeze_run_result

        session = Session.objects.create(tenant=self.other, assistant=self.foreign_assistant, user=self.foreign_admin)
        question = Message.objects.create(session=session, role="user", sequence=1, content="Foreign question")
        answer = Message.objects.create(session=session, role="assistant", sequence=2, content="Private foreign answer")
        source = Run.objects.create(
            tenant=self.other, session=session, input_message=question, output_message=answer, status="done"
        )
        with self.assertRaisesRegex(ValueError, "EVALUATION_TENANT_MISMATCH"):
            freeze_run_result(evaluation_run=self.batch, case=self.case, run=source)
        self.result.refresh_from_db()
        self.assertNotIn("Private foreign", self.result.output_snapshot)

    def test_batch_runtime_ownership_is_persisted_and_cannot_drift_with_assistant(self):
        """Queue handoff retains ownership even if an assistant relation changes later."""
        batch = create_evaluation_run(dataset=self.dataset, requested_by=self.admin)
        self.assertEqual(batch.tenant_id, self.tenant.pk)
        self.assertEqual(batch.results.get().tenant_id, self.tenant.pk)
        Assistant.objects.filter(pk=self.assistant.pk).update(tenant=self.other)
        batch.refresh_from_db()
        with self.assertRaisesRegex(ValueError, "EVALUATION_TENANT_MISMATCH"):
            ensure_case_run(batch, self.case)

    def test_tenant_cannot_mutate_global_metric_defaults(self):
        """Shared scoring templates must not be editable through a tenant credential."""
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        response = self.client.post(
            self.base + "metrics/",
            {"name": "Global override", "category": "reliability", "evaluator_type": "outcome", "weight": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.content)

    def source(self, assistant=None, **values):
        """Create a real source conversation with tenant-owned input and output."""
        assistant = assistant or self.assistant
        session = Session.objects.create(assistant=assistant, tenant=assistant.tenant, user=self.admin)
        question = Message.objects.create(session=session, role="user", sequence=1, content="Evaluation input")
        answer = Message.objects.create(session=session, role="assistant", sequence=2, content="Source answer")
        return Run.objects.create(
            tenant=assistant.tenant,
            session=session,
            input_message=question,
            output_message=answer,
            **{"status": "done", **values},
        )

    def test_platform_can_read_and_write_global_metrics_but_keys_cannot(self):
        """Global metric administration does not expose any tenant content."""
        self.client.force_authenticate(self.platform)
        self.assertEqual(self.client.get(self.base + "metrics/").status_code, 200)
        self.assertEqual(
            self.client.post(
                self.base + "metrics/",
                {"name": "Platform metric", "category": "reliability", "evaluator_type": "outcome", "weight": 1},
                format="json",
            ).status_code,
            201,
        )
        self.client.force_authenticate(self.platform, token={"principal_type": "tenant_api_key"})
        self.assertEqual(self.client.get(self.base + "metrics/").status_code, 403)
        self.assertEqual(self.client.post(self.base + "metrics/", {}, format="json").status_code, 403)

    def test_malformed_results_do_not_leak_through_reads_reports_or_comparison(self):
        """A valid batch URL does not confer authority over a foreign case or source."""
        for pollution in (
            {"case": self.foreign_case},
            {"source_run": self.source(self.foreign_assistant)},
            {"tenant": self.other},
        ):
            EvaluationResult.objects.filter(pk=self.result.pk).update(**pollution)
            rows = self.client.get(self.base + f"runs/{self.batch.pk}/results/").json()["data"]["results"]
            self.assertEqual(rows, [])
            self.assertEqual(
                self.client.get(self.base + f"runs/{self.batch.pk}/results/{self.result.pk}/").status_code, 404
            )
            self.assertEqual(compare_batches(self.batch, self.batch)["cases"], [])
            self.assertNotIn(self.result.output_snapshot, str(build_report(self.batch)))
            EvaluationResult.objects.filter(pk=self.result.pk).update(
                tenant=self.tenant, case=self.case, source_run=None
            )

    def test_existing_result_with_foreign_owner_cannot_be_overwritten(self):
        """Snapshot upserts never silently repair or copy into another tenant's row."""
        from lens.evaluation.snapshots import freeze_run_result

        source = self.source()
        EvaluationResult.objects.filter(pk=self.result.pk).update(tenant=self.other)
        with self.assertRaisesRegex(ValueError, "EVALUATION_TENANT_MISMATCH"):
            freeze_run_result(evaluation_run=self.batch, case=self.case, run=source)
        self.result.refresh_from_db()
        self.assertEqual(self.result.output_snapshot, "Answer for eval-own")

    def test_retention_cannot_make_invalid_frozen_evidence_visible_again(self):
        """A rejected source remains quarantined after its live foreign key is cleared."""
        source = self.source(self.foreign_assistant)
        EvaluationResult.objects.filter(pk=self.result.pk).update(source_run=source, output_snapshot="Foreign snapshot")
        finalize_evaluation_results_for_retention([source.pk])
        source.delete()
        self.result.refresh_from_db()
        self.assertIsNone(self.result.source_run_id)
        self.assertEqual(self.result.error, "EVALUATION_TENANT_MISMATCH")
        self.assertEqual(
            self.client.get(self.base + f"runs/{self.batch.pk}/results/{self.result.pk}/").status_code, 404
        )
        self.assertEqual(compare_batches(self.batch, self.batch)["cases"], [])

    def test_foreign_message_links_cannot_be_frozen_or_used_as_baselines(self):
        """Even a correctly owned source Run must reference its own conversation messages."""
        from lens.evaluation.snapshots import freeze_run_result

        foreign = self.source(self.foreign_assistant)
        for field in ("input_message", "output_message"):
            source = self.source(outcome="completed")
            Run.objects.filter(pk=source.pk).update(**{field: getattr(foreign, field)})
            source.refresh_from_db()
            with self.assertRaisesRegex(ValueError, "EVALUATION_TENANT_MISMATCH"):
                freeze_run_result(evaluation_run=self.batch, case=self.case, run=source)
            EvaluationResult.objects.filter(pk=self.result.pk).update(source_run=source)
            self.assertEqual(
                self.client.get(self.base + f"runs/{self.batch.pk}/results/{self.result.pk}/").status_code, 404
            )
            response = self.client.post(
                self.base + f"datasets/{self.dataset.pk}/baseline/", {"run_uuid": str(source.uuid)}, format="json"
            )
            self.assertEqual(response.status_code, 400, response.content)

    def test_idempotency_lookup_cannot_reuse_a_foreign_run(self):
        """A colliding key cannot attach foreign execution evidence to this batch."""
        self.source(self.foreign_assistant, idempotency_key=case_run_key(self.batch, self.case))
        with self.assertRaisesRegex(ValueError, "EVALUATION_TENANT_MISMATCH"):
            ensure_case_run(self.batch, self.case)

    def test_run_details_link_colleague_batches_only_with_matching_ownership(self):
        """The operations shortcut follows the same tenant scope as evaluation pages."""
        source = self.source(source="evaluation")
        RunExecution.objects.create(run=source, lensnode=self.node, status="completed")
        EvaluationResult.objects.filter(pk=self.result.pk).update(source_run=source)
        url = f"/api/lens/admin/runs/{source.uuid}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["evaluation_run_uuid"], str(self.batch.uuid))
        EvaluationResult.objects.filter(pk=self.result.pk).update(case=self.foreign_case)
        self.assertIsNone(self.client.get(url).data["evaluation_run_uuid"])

    def test_owned_batch_creates_a_real_execution_in_the_frozen_tenant(self):
        """Normal tenant evaluation remains runnable after ownership enforcement."""
        from agentcore_metering.adapters.django.models import LLMConfig

        from lens.models import LLMConfigScope

        model = LLMConfig.objects.create(provider="openai", config={"model": "evaluation-model"})
        LLMConfigScope.objects.create(config=model, tenant=self.tenant)
        self.assistant.agent_model_ref = model.uuid
        self.assistant.selected_task = "knowledge_qa"
        self.assistant.save(update_fields=["agent_model_ref", "selected_task"])
        batch = create_evaluation_run(dataset=self.dataset, requested_by=self.admin)
        source = ensure_case_run(batch, self.case, self.node)
        self.assertEqual(source.tenant_id, self.tenant.pk)
        self.assertEqual(source.session.tenant_id, self.tenant.pk)
        self.assertEqual(source.source, "evaluation")
        self.assertEqual(source.execution.lensnode_id, self.node.pk)
        self.assertEqual(ensure_case_run(batch, self.case, self.node).pk, source.pk)

    def test_malformed_run_is_neither_cancelled_reenqueued_nor_retried(self):
        """Recovery and cancellation touch only results owned by the batch."""
        source = self.source(self.foreign_assistant, status="queued")
        Run.objects.filter(pk=source.pk).update(created_at=timezone.now() - timedelta(hours=1))
        RunExecution.objects.create(run=source, lensnode=self.node, status="queued")
        EvaluationResult.objects.filter(pk=self.result.pk).update(source_run=source)
        self.batch.status = "running"
        self.batch.save(update_fields=["status"])
        with patch("lens.evaluation.engine.batch._enqueue_run") as enqueue:
            self.assertEqual(_reenqueue_stuck_case_runs(self.batch), 0)
            enqueue.assert_not_called()
        cancel_evaluation_run(self.batch.pk)
        source.refresh_from_db()
        self.assertEqual(source.status, "queued")
        self.assertEqual(retry_failed_cases(self.batch.pk), 0)
        self.result.refresh_from_db()
        self.assertEqual(self.result.source_run_id, source.pk)

    def test_inactive_tenant_stops_dispatch_and_judge_but_retains_local_evidence(self):
        """Suspension blocks new external work without losing completed snapshots."""
        source = self.source()
        self.batch.config_snapshot = {"judge": {"model_ref": "configured-model"}}
        self.batch.status = "paused"
        self.batch.save(update_fields=["config_snapshot", "status"])
        EvaluationResult.objects.filter(pk=self.result.pk).update(source_run=source, scores={}, output_snapshot="")
        self.tenant.status = "suspended"
        self.tenant.save(update_fields=["status"])
        with patch("lens.evaluation.engine.batch.judge_result") as judge:
            self.assertEqual(finalize_evaluation_results_for_retention([source.pk]), 1)
            judge.assert_not_called()
        self.result.refresh_from_db()
        self.assertEqual(self.result.output_snapshot, "Source answer")
        batch = advance_evaluation_run(self.batch.pk)
        self.assertEqual(batch.status, "failed")
        self.assertEqual(batch.error, "EVALUATION_TENANT_INACTIVE")
        with self.assertRaisesRegex(ValueError, "EVALUATION_TENANT_INACTIVE"):
            ensure_case_run(batch, self.case)

    def test_invalid_batch_is_failed_before_reconciliation_scores_or_dispatches(self):
        """Malformed rows cannot make a partial aggregate look like a completed batch."""
        self.batch.status = "running"
        self.batch.total_cases = 1
        self.batch.save(update_fields=["status", "total_cases"])
        EvaluationResult.objects.filter(pk=self.result.pk).update(case=self.foreign_case)
        batch = advance_evaluation_run(self.batch.pk)
        self.assertEqual(batch.status, "failed")
        self.assertEqual(batch.error, "EVALUATION_TENANT_MISMATCH")

    def test_finalizer_skips_foreign_evidence_without_interrupting_other_runs(self):
        """Terminal-run and retention hooks ignore invalid related evidence."""
        source = self.source(self.foreign_assistant)
        EvaluationResult.objects.filter(pk=self.result.pk).update(source_run=source, scores={})
        self.assertIsNone(finalize_case_result(source.uuid))
        self.assertEqual(finalize_evaluation_results_for_retention([source.pk]), 0)
        self.result.refresh_from_db()
        self.assertEqual(self.result.output_snapshot, "Answer for eval-own")

    def test_trace_loading_ignores_events_owned_by_another_tenant(self):
        """A trace FK cannot import another tenant's metrics or provider payload."""
        from uuid import uuid4

        from lens.evaluation.evaluators.trace import load_trace_events

        source = self.source()
        event = RunTraceEvent.objects.create(
            run=source,
            event_id=uuid4(),
            sequence=1,
            timestamp=timezone.now(),
            event_type="model.completed",
            payload={"model": "Foreign secret"},
        )
        RunTraceEvent.objects.filter(pk=event.pk).update(tenant=self.other)
        self.assertEqual(load_trace_events(source), [])
        from lens.evaluation.revisions import baseline_metadata

        RunTraceEvent.objects.filter(pk=event.pk).update(event_type="model.started")
        self.assertIsNone(baseline_metadata(source)["models"])

    def test_ownership_backfill_preserves_legacy_and_already_assigned_rows(self):
        """Data migration follows assistant ownership, is idempotent and never guesses."""
        migration = import_module("lens.migrations.0070_multitenancy")
        EvaluationDataset.objects.filter(pk=self.dataset.pk).update(tenant=None)
        EvaluationRun.objects.filter(pk=self.batch.pk).update(tenant=None)
        EvaluationResult.objects.filter(pk=self.result.pk).update(tenant=None)
        EvaluationResult.objects.filter(pk=self.foreign_result.pk).update(tenant=self.tenant)
        for _ in range(2):
            migration.backfill_evaluation_tenants(apps, SimpleNamespace(connection=connection))
        for row in (self.dataset, self.batch, self.result):
            row.refresh_from_db()
            self.assertEqual(row.tenant_id, self.tenant.pk)
        for row in (self.legacy_dataset, self.legacy_batch):
            row.refresh_from_db()
            self.assertIsNone(row.tenant_id)
        self.foreign_result.refresh_from_db()
        self.assertEqual(self.foreign_result.tenant_id, self.tenant.pk)
