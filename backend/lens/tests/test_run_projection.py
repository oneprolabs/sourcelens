"""Run usage projection and tenant operations boundaries."""

import json
import uuid
from unittest.mock import patch

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Profile, Tenant, TenantApiKey, TenantDelegation, TenantMembership
from lens.models import (
    Assistant,
    LensNode,
    Message,
    MessageAttachment,
    Run,
    RunDiagnostic,
    RunDiagnosticEvidence,
    RunExecution,
    RunOutputFile,
    RunStep,
    RunTraceEvent,
    Session,
)
from lens.services import cancel_descendant_runs, stream_run_events


class RunProjectionTests(TransactionTestCase):
    """Using an assistant never grants its execution configuration or tenant administration."""

    def setUp(self):
        """Create separate tenant histories with runtime-only details."""
        self.tenant = Tenant.objects.create(name="Run tenant", slug="run-projection")
        self.other = Tenant.objects.create(name="Other tenant", slug="run-projection-other")
        self.admin = self.user("run-admin", "tenant_admin")
        self.member = self.user("run-member", "member")
        self.platform = get_user_model().objects.create_user(username="run-platform", is_staff=True)
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        self.legacy = get_user_model().objects.create_user(username="run-legacy", is_staff=True)
        self.node = LensNode.objects.create(name="Private node", workspace_path="/private/host/work")
        self.assistant = Assistant.objects.create(
            tenant=self.tenant, name="Run assistant", slug="run-projection", lensnode=self.node, visibility="tenant"
        )
        self.run = self.make_run(self.member, self.tenant, self.assistant)
        self.client = APIClient()
        self.client.force_authenticate(self.member)

    def user(self, username, role):
        """Create a live membership without staff inheritance."""
        user = get_user_model().objects.create_user(username=username)
        TenantMembership.objects.create(user=user, tenant=self.tenant, role=role)
        return user

    def make_run(self, user, tenant, assistant):
        """Persist a terminal run with deliberately private runtime configuration."""
        session = Session.objects.create(user=user, tenant=tenant, assistant=assistant)
        message = Message.objects.create(session=session, role="user", sequence=1, content="Question")
        answer = Message.objects.create(session=session, role="assistant", sequence=2, content="Public answer")
        run = Run.objects.create(
            tenant=tenant,
            session=session,
            input_message=message,
            output_message=answer,
            lensnode=self.node,
            metering_ref=uuid.uuid4(),
            status="failed",
            error="Private provider URL /secret/key",
        )
        RunExecution.objects.create(
            run=run,
            lensnode=self.node,
            task="general_chat",
            target_dirs=["/private/workspace/repository"],
            loaded_skills=[{"skill_uuid": "private-skill", "skill_name": "Internal Skill"}],
            loaded_mcps=[{"mcp_uuid": "private-mcp", "mcp_name": "Internal MCP"}],
        )
        RunStep.objects.create(
            run=run,
            step_type="general_chat",
            sequence=1,
            status="failed",
            detail={
                "events": [{"event_type": "phase.changed", "visibility": "user", "payload": {"phase": "analyzing"}}],
                "raw": "Private provider URL",
            },
        )
        return run

    def test_usage_run_omits_configuration_but_keeps_answer_progress_and_timing(self):
        """A member's own run uses a deliberate execution allowlist."""
        response = self.client.get(f"/api/lens/runs/{self.run.uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["answer"], "Public answer")
        self.assertNotIn("lensnode", response.data)
        self.assertNotIn("metering_ref", response.data)
        self.assertEqual(set(response.data["execution"]), {"uuid", "status", "started_at", "finished_at"})
        self.assertEqual(response.data["error"], "LENS_RUN_FAILED")
        self.assertEqual(response.data["steps"][0]["detail"]["events"][0]["payload"]["phase"], "analyzing")
        self.assertNotIn("Private provider", str(response.data))
        self.assertNotIn("/private/", str(response.data))

    def test_history_preview_search_and_result_filters_use_scoped_messages(self):
        """History distinguishes completed outcomes and never borrows another session's answer."""
        self.client.force_authenticate(self.admin)
        Run.objects.filter(pk=self.run.pk).update(status="done", outcome="partial")
        response = self.client.get("/api/lens/admin/runs/", {"q": "Public answer", "result": "partial"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["answer_preview"], "Public answer")
        self.assertEqual(response.data["result_summary"]["partial"], 1)
        self.assertEqual(response.data["result_summary"]["completed"], 0)
        self.assertEqual(self.client.get("/api/lens/admin/runs/", {"result": "completed"}).data["total"], 0)
        foreign = self.make_run(self.member, self.other, self.assistant)
        Run.objects.filter(pk=self.run.pk).update(output_message=foreign.output_message)
        response = self.client.get("/api/lens/admin/runs/", {"q": "Public answer"})
        self.assertEqual(response.data["total"], 0)
        response = self.client.get("/api/lens/admin/runs/")
        self.assertEqual(response.data["results"][0]["answer_preview"], "")

    def test_api_key_does_not_inherit_creator_configuration_access(self):
        """Machine credentials stay on the usage projection even when created by an admin."""
        run = self.make_run(self.admin, self.tenant, self.assistant)
        key, secret = TenantApiKey.issue(
            tenant=self.tenant, name="Run key", scope="all", assistant_uuids=[], created_by=self.admin, expires_at=None
        )
        run.session.user = None
        run.session.api_key = key
        run.session.save(update_fields=["user", "api_key"])
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        response = external.get(f"/api/lens/runs/{run.uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("target_dirs", response.data["execution"])
        self.assertNotIn("lensnode", response.data)

    def test_own_tenant_admin_keeps_execution_inspection(self):
        """The same response remains useful to an explicitly authorized configuration owner."""
        run = self.make_run(self.admin, self.tenant, self.assistant)
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/lens/runs/{run.uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["execution"]["target_dirs"], ["/private/workspace/repository"])

    def test_native_and_drf_stream_errors_never_include_provider_details(self):
        """Shared SSE construction projects failures, including non-HTTP consumers."""
        events = list(stream_run_events(self.run))
        self.assertNotIn("Private provider", json.dumps(events))
        self.assertEqual(events[-1]["error"]["code"], "LENS_RUN_FAILED")
        self.run.error = "MODEL_TIMEOUT"
        self.run.save(update_fields=["error"])
        self.assertEqual(list(stream_run_events(self.run))[-1]["error"]["code"], "MODEL_TIMEOUT")

    def test_platform_without_delegation_and_staff_members_cannot_read_admin_history(self):
        """A global console feature is insufficient authority over tenant content."""
        urls = [
            "/api/lens/admin/runs/",
            f"/api/lens/admin/runs/{self.run.uuid}/",
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/",
            f"/api/lens/admin/runs/{self.run.uuid}/diagnostics/",
        ]
        self.member.is_staff = True
        self.member.save()
        for actor in (self.platform, self.member):
            self.client.force_authenticate(actor)
            for url in urls:
                with self.subTest(actor=actor.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_legacy_admin_can_read_only_unowned_history(self):
        """Preserve pre-tenancy administration without a null-context global query."""
        legacy_assistant = Assistant.objects.create(name="Legacy", slug="run-legacy", lensnode=self.node)
        own = self.make_run(self.legacy, None, legacy_assistant)
        self.client.force_authenticate(self.legacy)
        response = self.client.get("/api/lens/admin/runs/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["uuid"], str(own.uuid))
        self.assertEqual(self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/").status_code, 404)

    def delegate(self, mode):
        """Attach a real active delegation for the platform user."""
        from datetime import timedelta

        delegation = TenantDelegation.objects.create(
            platform_user=self.platform,
            tenant=self.tenant,
            mode=mode,
            expires_at=timezone.now() + timedelta(hours=1),
            reason="Inspect runtime",
        )
        self.client.force_authenticate(self.platform)
        self.client.credentials(HTTP_X_DELEGATION_ID=str(delegation.pk))

    def test_readonly_delegation_cannot_cancel_retry_resume_or_create_diagnostics(self):
        """A read-only inspection must not mutate runs or incur model usage."""
        self.delegate("readonly")
        detail = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertFalse(any(detail.data["available_actions"][key] for key in ("cancel", "retry", "resume")))
        for path, data in (("cancel", {}), ("retry", {"idempotency_key": "test"}), ("resume", {}), ("diagnostics", {})):
            with self.subTest(path=path):
                response = self.client.post(f"/api/lens/admin/runs/{self.run.uuid}/{path}/", data, format="json")
                self.assertEqual(response.status_code, 403, response.data)

    def test_delegation_does_not_return_or_filter_directory_identities(self):
        """Personnel projection also applies to list and post-action responses."""
        self.delegate("operational")
        listing = self.client.get("/api/lens/admin/runs/")
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertNotIn("username", listing.data["results"][0])
        detail = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertNotIn("username", detail.data)
        filtered = self.client.get("/api/lens/admin/runs/", {"username": self.member.username})
        self.assertEqual(filtered.status_code, 403, filtered.data)

    def test_admin_trace_does_not_follow_foreign_child_or_corrupted_run_session(self):
        """Old cross-tenant relations cannot expand a trace or its status summary."""
        foreign_assistant = Assistant.objects.create(name="Foreign", slug="run-foreign", tenant=self.other)
        foreign = self.make_run(self.member, self.other, foreign_assistant)
        foreign.parent_run = self.run
        foreign.save(update_fields=["parent_run"])
        RunTraceEvent.objects.create(
            tenant=self.other,
            run=foreign,
            sequence=1,
            event_id=uuid.uuid4(),
            timestamp=timezone.now(),
            event_type="model.completed",
            payload={"private": "foreign runtime trace"},
        )
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/trajectory/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("foreign runtime trace", str(response.data))
        Run.objects.filter(pk=foreign.pk).update(tenant=self.tenant, parent_run=None)
        listing = self.client.get("/api/lens/admin/runs/")
        self.assertEqual(listing.data["total"], 1)
        self.assertEqual(self.client.get(f"/api/lens/admin/runs/{foreign.uuid}/").status_code, 404)

    def test_stream_and_cancel_do_not_follow_foreign_delegated_runs(self):
        """Usage progress and cancellation cannot traverse a corrupt foreign child."""
        foreign = self.make_run(self.member, self.other, self.assistant)
        foreign.parent_run = self.run
        foreign.status = Run.Status.RUNNING
        foreign.save(update_fields=["parent_run", "status"])
        foreign.input_message.content = "Private foreign delegated task"
        foreign.input_message.save(update_fields=["content"])
        self.assertNotIn("Private foreign delegated task", json.dumps(list(stream_run_events(self.run))))
        history = self.client.get(f"/api/lens/sessions/{self.run.session.uuid}/messages/")
        self.assertEqual(history.status_code, 200)
        self.assertNotIn("Private foreign delegated task", str(history.data))
        self.assertEqual(cancel_descendant_runs(self.run), [])
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, Run.Status.RUNNING)
        child = self.make_run(self.member, self.tenant, self.assistant)
        child.parent_run = self.run
        child.status = Run.Status.RUNNING
        child.save(update_fields=["parent_run", "status"])
        self.assertEqual([item.pk for item in cancel_descendant_runs(self.run)], [child.pk])
        child.refresh_from_db()
        self.assertEqual(child.status, Run.Status.CANCELLED)

    def test_trace_events_must_match_the_run_tenant_even_with_a_valid_run_relation(self):
        """A mismatched event cannot affect detail, counters or trajectory snapshots."""
        RunTraceEvent.objects.create(
            tenant=self.other,
            run=self.run,
            sequence=1,
            event_id=uuid.uuid4(),
            timestamp=timezone.now(),
            event_type="tool.completed",
            call_id="private-call",
            payload={"name": "private_foreign_tool", "private": "Private foreign trace"},
        )
        self.client.force_authenticate(self.admin)
        detail = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["trace_event_count"], 0)
        self.assertEqual(detail.data["tool_call_count"], 0)
        self.assertNotIn("private_foreign_tool", str(detail.data))
        trace = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/trajectory/")
        self.assertEqual(trace.status_code, 200, trace.data)
        self.assertEqual(trace.data["results"], [])
        self.assertEqual(trace.data["summary"]["event_count"], 0)

    def test_foreign_children_cannot_contribute_model_usage_to_a_tenant_run(self):
        """Detail and batched list aggregation share the child ownership boundary."""
        from agentcore_metering.adapters.django.models import LLMUsage

        foreign = self.make_run(self.member, self.other, self.assistant)
        foreign.parent_run = self.run
        foreign.save(update_fields=["parent_run"])
        LLMUsage.objects.create(model="Foreign model", total_tokens=12345, metadata={"run_uuid": str(foreign.uuid)})
        self.client.force_authenticate(self.admin)
        listing = self.client.get("/api/lens/admin/runs/")
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertNotEqual(listing.data["results"][0]["total_tokens"], 12345)
        detail = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertNotIn("Foreign model", str(detail.data))

    def test_delegation_diagnostics_omit_requester_but_tenant_admins_keep_it(self):
        """Structured personnel identifiers stay off delegated diagnostic responses."""
        evidence = RunDiagnosticEvidence.objects.create(run=self.run, payload={})
        RunDiagnostic.objects.create(run=self.run, evidence=evidence, requested_by=self.member)
        self.delegate("readonly")
        response = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/diagnostics/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("requested_by", response.data[0])
        self.client.credentials()
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/diagnostics/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]["requested_by"], self.member.pk)

    def test_native_admin_stream_enforces_tenant_and_platform_authority_with_real_jwt(self):
        """Native SSE performs the same scope checks as the REST trajectory endpoint."""
        self.member.is_staff = True
        self.member.save(update_fields=["is_staff"])
        client = Client()
        url = f"/api/lens/admin/runs/{self.run.uuid}/trajectory/stream/"
        for actor, expected in ((self.platform, 403), (self.member, 403), (self.legacy, 404), (self.admin, 200)):
            token = str(RefreshToken.for_user(actor).access_token)
            response = client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}")
            self.assertEqual(response.status_code, expected)
            response.close()

    def test_tenant_admin_using_a_foreign_public_assistant_gets_only_usage_projection(self):
        """Admin authority over a consumer tenant does not reveal another tenant's configuration."""
        assistant = Assistant.objects.create(
            tenant=self.other, name="Public", slug="foreign-public", visibility="public"
        )
        run = self.make_run(self.admin, self.tenant, assistant)
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/lens/runs/{run.uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("target_dirs", response.data["execution"])
        self.assertNotIn("lensnode", response.data)

    def test_native_delegation_stream_stops_when_its_authority_is_revoked(self):
        """An open stream must not retain tenant content access after delegation ends."""
        self.delegate("readonly")
        delegation = TenantDelegation.objects.get(platform_user=self.platform)
        client = Client()
        token = str(RefreshToken.for_user(self.platform).access_token)
        response = client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/stream/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_X_DELEGATION_ID=str(delegation.uuid),
        )
        self.assertEqual(response.status_code, 200)

        async def consume():
            iterator = response.streaming_content.__aiter__()
            first = await anext(iterator)
            self.assertIn(b'"type": "sync"', first)
            await sync_to_async(TenantDelegation.objects.filter(pk=delegation.pk).update)(status="ended")
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)

        async_to_sync(consume)()
        response.close()

    def public_assistant_run(self):
        """Create consumer-owned history for a provider-owned public assistant."""
        assistant = Assistant.objects.create(
            tenant=self.other, name="Provider public", slug="provider-public", visibility="public"
        )
        run = self.make_run(self.member, self.tenant, assistant)
        run.execution.runtime_snapshot = {"model_refs": {"agent": "Private provider model"}}
        run.execution.save(update_fields=["runtime_snapshot"])
        return run

    def test_admin_consumer_history_preserves_content_and_aggregate_usage_without_provider_configuration(self):
        """Tenant content auditing does not grant the public assistant's runtime internals."""
        from agentcore_metering.adapters.django.models import LLMUsage

        run = self.public_assistant_run()
        LLMUsage.objects.create(model="Private provider model", total_tokens=123, metadata={"run_uuid": str(run.uuid)})
        self.client.force_authenticate(self.admin)
        listing = self.client.get("/api/lens/admin/runs/")
        row = next(item for item in listing.data["results"] if item["uuid"] == str(run.uuid))
        detail = self.client.get(f"/api/lens/admin/runs/{run.uuid}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["question"], "Question")
        self.assertEqual(detail.data["answer"], "Public answer")
        self.assertEqual(detail.data["total_tokens"], 123)
        self.assertEqual(detail.data["error"], "LENS_RUN_FAILED")
        self.assertEqual(detail.data["steps"][0]["events"][0]["payload"]["phase"], "analyzing")
        self.assertFalse(detail.data["available_actions"]["inspect_configuration"])
        self.assertTrue(detail.data["available_actions"]["retry"])
        for payload in (row, detail.data):
            for field in (
                "lensnode_name",
                "lensnode_uuid",
                "lensnode_workspace_path",
                "model_ref",
                "token_budget_profile",
                "allowed_assistant_uuids",
                "delegated_assistants",
                "models_used",
                "model_calls",
                "failure_summary",
            ):
                self.assertNotIn(field, payload)
            self.assertNotIn("Private provider", str(payload))
            self.assertNotIn("/private/", str(payload))
        self.assertEqual(set(detail.data["execution"]), {"status"})
        own = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/")
        self.assertTrue(own.data["available_actions"]["inspect_configuration"])
        self.assertEqual(own.data["execution"]["target_dirs"], ["/private/workspace/repository"])

    def test_configuration_filters_cannot_probe_a_provider_node_or_model(self):
        """Hidden provider attributes cannot be discovered through list match counts."""
        self.public_assistant_run()
        self.client.force_authenticate(self.admin)
        for params in (
            {"model": "Private provider model"},
            {"lensnode": str(self.node.uuid), "assistant": "provider-public"},
        ):
            response = self.client.get("/api/lens/admin/runs/", params)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["total"], 0)
            self.assertEqual(response.data["summary"]["total"], 0)

    def test_baseline_cannot_copy_provider_configuration_from_consumer_history(self):
        """A baseline's frozen metadata is also operational evidence."""
        from lens.models import EvaluationDataset

        run = self.public_assistant_run()
        run.status, run.outcome = Run.Status.DONE, Run.Outcome.COMPLETED
        run.save(update_fields=["status", "outcome"])
        dataset = EvaluationDataset.objects.create(name="Consumer dataset", assistant=run.session.assistant)
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/lens/admin/evaluation/datasets/{dataset.uuid}/baseline/", {"run_uuid": str(run.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(dataset.cases.exists())

    def test_consumer_admin_can_cancel_own_tenant_work_without_provider_configuration(self):
        """Content authority retains cancellation and safe post-action responses."""
        run = self.public_assistant_run()
        run.status = Run.Status.RUNNING
        run.save(update_fields=["status"])
        self.client.force_authenticate(self.admin)
        with patch("lens.views.admin_runs.cancel_run_on_lensnode"):
            response = self.client.post(f"/api/lens/admin/runs/{run.uuid}/cancel/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], Run.Status.CANCELLED)
        self.assertFalse(response.data["available_actions"]["inspect_configuration"])
        self.assertNotIn("/private/", str(response.data))
        self.assertNotIn("Private provider", str(response.data))

    def assert_evidence_denied(self, run):
        """Both durable and live evidence routes enforce provider configuration ownership."""
        evidence = RunDiagnosticEvidence.objects.create(run=run, payload={})
        diagnostic = RunDiagnostic.objects.create(run=run, evidence=evidence, requested_by=self.admin)
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.client.force_authenticate(self.admin)
        base = f"/api/lens/admin/runs/{run.uuid}"
        for method, path, data in (
            ("get", "/trajectory/", {}),
            ("get", "/diagnostics/", {}),
            ("post", "/diagnostics/", {}),
            ("post", f"/diagnostics/{diagnostic.uuid}/turns/", {"question": "Why?"}),
        ):
            response = getattr(self.client, method)(base + path, data, format="json")
            self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(RunDiagnostic.objects.filter(run=run).count(), 1)
        self.assertFalse(diagnostic.turns.exists())
        token = str(RefreshToken.for_user(self.admin).access_token)
        response = Client().get(base + "/trajectory/stream/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 403)
        response.close()

    def test_admin_cannot_read_or_generate_provider_evidence_for_a_consumed_public_assistant(self):
        """Frozen diagnoses and live traces share the configuration boundary."""
        self.assert_evidence_denied(self.public_assistant_run())

    def test_mixed_assistant_run_family_does_not_expose_provider_evidence_through_owned_parent(self):
        """A parent trace or frozen diagnosis can include delegated provider internals."""
        child = self.public_assistant_run()
        child.parent_run = self.run
        child.save(update_fields=["parent_run"])
        self.assert_evidence_denied(self.run)
        detail = self.client.get(f"/api/lens/admin/runs/{self.run.uuid}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertFalse(detail.data["available_actions"]["inspect_configuration"])
        self.assertNotIn("/private/", str(detail.data))
        listing = self.client.get("/api/lens/admin/runs/")
        row = next(item for item in listing.data["results"] if item["uuid"] == str(self.run.uuid))
        self.assertFalse(row["available_actions"]["inspect_configuration"])
        self.assertNotIn("/private/", str(row))
        self.run.status = Run.Status.RUNNING
        self.run.save(update_fields=["status"])
        with patch("lens.views.admin_runs.cancel_run_on_lensnode"):
            cancelled = self.client.post(f"/api/lens/admin/runs/{self.run.uuid}/cancel/", {}, format="json")
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertNotIn("/private/", str(cancelled.data))
        self.assertFalse(cancelled.data["available_actions"]["inspect_configuration"])

    def test_owned_child_cannot_expose_a_provider_parent_in_progress_or_diagnoses(self):
        """The progress root and frozen evidence follow the same boundary in both directions."""
        parent = self.public_assistant_run()
        self.run.parent_run = parent
        self.run.save(update_fields=["parent_run"])
        self.assert_evidence_denied(self.run)

    def test_native_stream_stops_when_assistant_configuration_ownership_changes(self):
        """An open trace must recheck assistant ownership before every delivery."""
        token = str(RefreshToken.for_user(self.admin).access_token)
        response = Client().get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/stream/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 200)

        async def consume():
            iterator = response.streaming_content.__aiter__()
            self.assertIn(b'"type": "sync"', await anext(iterator))
            await sync_to_async(Assistant.objects.filter(pk=self.assistant.pk).update)(tenant=self.other)
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)

        async_to_sync(consume)()
        response.close()

    def test_runtime_content_links_enforce_the_same_owner_and_tenant_admin_boundary(self):
        """Known image, output and source UUIDs cannot bypass run history permissions."""
        image = MessageAttachment.objects.create(session=self.run.session, uploaded_by=self.member)
        image.file.save("runtime-image.png", ContentFile(b"test image"))
        self.addCleanup(image.file.delete, save=False)
        output = RunOutputFile.objects.create(
            tenant=self.tenant,
            run=self.run,
            session=self.run.session,
            assistant=self.assistant,
            message=self.run.output_message,
            filename="runtime-result.txt",
        )
        output.file.save("runtime-result.txt", ContentFile(b"Private tenant result"))
        self.addCleanup(output.file.delete, save=False)
        self.run.citations = [
            {
                "id": "source",
                "path": "src/runtime.py",
                "source": "Private tenant source",
                "start_line": 1,
                "end_line": 1,
                "revision": "runtime-v1",
                "supports": "Runtime evidence",
            }
        ]
        self.run.save(update_fields=["citations"])
        urls = [
            f"/api/lens/attachments/{image.uuid}/",
            f"/api/lens/output-files/{output.uuid}/",
            f"/api/lens/runs/{self.run.uuid}/citations/source/",
        ]
        staff_member = self.user("run-staff-content", "member")
        staff_member.is_staff = True
        staff_member.save(update_fields=["is_staff"])
        for actor in (self.platform, self.legacy, staff_member):
            self.client.force_authenticate(actor)
            for url in urls:
                with self.subTest(actor=actor.username, url=url):
                    self.assertIn(self.client.get(url).status_code, (403, 404))
        for actor in (self.member, self.admin):
            self.client.force_authenticate(actor)
            for url in urls:
                with self.subTest(actor=actor.username, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    response.close()
        self.delegate("readonly")
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_readonly_delegation_cannot_bypass_operations_through_usage_routes(self):
        """Even a previously created owned session cannot turn read-only support into writes."""
        owned = self.make_run(self.platform, self.tenant, self.assistant)
        self.delegate("readonly")
        requests = [
            ("post", f"/api/lens/runs/{owned.uuid}/cancel/", {}),
            ("post", f"/api/lens/runs/{owned.uuid}/clarification/", {}),
            ("post", f"/api/lens/sessions/{owned.session.uuid}/runs/", {"question": "New run"}),
            ("delete", f"/api/lens/sessions/{owned.session.uuid}/", {}),
            ("delete", f"/api/lens/attachments/{uuid.uuid4()}/", {}),
        ]
        for method, url, data in requests:
            response = getattr(self.client, method)(url, data, format="json")
            self.assertEqual(response.status_code, 403, response.data)
        owned.refresh_from_db()
        self.assertEqual(owned.status, Run.Status.FAILED)
        self.assertTrue(Session.objects.filter(pk=owned.session_id).exists())

    def test_document_download_and_delete_use_the_metadata_session_scope(self):
        """Cache-backed document metadata must not inherit a global staff bypass."""
        metadata = {
            "session_uuid": str(self.run.session.uuid),
            "uploaded_by_id": self.member.pk,
            "storage_name": "private-document.pdf",
            "original_name": "private-document.pdf",
            "mime_type": "application/pdf",
            "byte_size": 7,
            "content_hash": "document-hash",
            "run_uuid": "",
        }
        with patch("lens.views.sessions.get_document_attachment", return_value=metadata), patch(
            "lens.views.sessions.document_attachment_storage"
        ) as storage, patch("lens.views.sessions.delete_document_attachment") as delete:
            storage.return_value.exists.return_value = True
            storage.return_value.open.return_value = ContentFile(b"Private")
            url = f"/api/lens/attachments/{uuid.uuid4()}/"
            for actor in (self.platform, self.legacy):
                self.client.force_authenticate(actor)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.delete(url).status_code, 403)
            delete.assert_not_called()
            self.delegate("readonly")
            read = self.client.get(url)
            self.assertEqual(read.status_code, 200)
            read.close()
            self.assertEqual(self.client.delete(url).status_code, 403)
            delete.assert_not_called()
            self.client.credentials()
            self.client.force_authenticate(self.admin)
            self.assertEqual(self.client.delete(url).status_code, 204)
            delete.assert_called_once()
