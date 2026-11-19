"""Verify confirmed tenant assistant deletion and its bounded impact preview."""

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, Tenant, TenantMembership
from lens.models import Assistant, AssistantDataSourceBinding, DataSource, Message, Run, Session, SharedQA


class AssistantDeletionTests(TestCase):
    """Only confirmed versions in the administrator's tenant can be deleted."""

    def setUp(self):
        """Create assistant history and an independent knowledge resource."""
        self.tenant = Tenant.objects.create(name="Delete", slug="delete-tenant")
        self.user = get_user_model().objects.create_user(username="delete-admin")
        TenantMembership.objects.create(user=self.user, tenant=self.tenant, role="tenant_admin")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.assistant = Assistant.objects.create(tenant=self.tenant, name="Delete assistant", slug="delete-assistant")
        self.source = DataSource.objects.create(tenant=self.tenant, name="Keep source", source_type="upload")
        AssistantDataSourceBinding.objects.create(assistant=self.assistant, datasource=self.source, mount_name="docs")
        self.session = Session.objects.create(tenant=self.tenant, assistant=self.assistant, user=self.user)
        message = Message.objects.create(session=self.session, role="user", content="Private question", sequence=1)
        self.run = Run.objects.create(tenant=self.tenant, session=self.session, input_message=message, status="done")
        self.share = SharedQA.objects.create(
            assistant=self.assistant,
            run=self.run,
            token="delete-preview-share",
            question="Private question",
            answer="Private answer",
        )

    def payload(self, rows=None, **changes):
        """Pin versions and require exact per-assistant confirmation slugs."""
        return {
            "assistants": [
                {"uuid": str(row.uuid), "updated_at": row.updated_at.isoformat(), "confirmation_slug": row.slug}
                for row in (rows or [self.assistant])
            ],
            "preview": True,
            **changes,
        }

    def post(self, payload=None, **changes):
        """Use the same endpoint for preview and confirmed application."""
        return self.client.post("/api/lens/assistants/bulk-delete/", payload or self.payload(**changes), format="json")

    def test_preview_is_readonly_and_returns_counts_without_history_content(self):
        """The review surface contains identities, counts and a signed confirmation."""
        response = self.post()
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["assistants"][0]
        self.assertEqual(row["impact"]["sessions"], 1)
        self.assertEqual(row["impact"]["questions"], 1)
        self.assertEqual(row["impact"]["shares"], 1)
        self.assertNotIn("Private question", str(response.data))
        self.assertNotIn("Private answer", str(response.data))
        self.assertTrue(response.data["confirmation_token"])
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="assistant.bulk_delete").exists())

    def test_confirmed_delete_removes_history_shares_and_bindings_but_keeps_knowledge(self):
        """Deletion matches the prototype's permanent scope and preserves sources."""
        preview = self.post()
        self.assertEqual(preview.status_code, 200, preview.data)
        response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["deleted"], 1)
        self.assertFalse(Assistant.objects.filter(pk=self.assistant.pk).exists())
        self.assertFalse(Session.objects.filter(pk=self.session.pk).exists())
        self.assertFalse(Run.objects.filter(pk=self.run.pk).exists())
        self.assertFalse(SharedQA.objects.filter(pk=self.share.pk).exists())
        self.assertTrue(DataSource.objects.filter(pk=self.source.pk).exists())
        self.assertEqual(self.client.get(f"/api/lens/public/qa/{self.share.token}/").status_code, 404)
        self.assertEqual(AuditLog.objects.get(action="assistant.bulk_delete").tenant, self.tenant)

    def test_missing_or_inexact_slug_and_preview_token_cannot_delete(self):
        """A direct request cannot bypass either human confirmation or review."""
        preview = self.post()
        self.assertEqual(preview.status_code, 200, preview.data)
        token = preview.data["confirmation_token"]
        for slug in (None, "", self.assistant.slug.upper(), self.assistant.slug + " "):
            payload = self.payload(preview=False, confirmation_token=token)
            if slug is None:
                payload["assistants"][0].pop("confirmation_slug")
            else:
                payload["assistants"][0]["confirmation_slug"] = slug
            self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual(self.post(preview=False).status_code, 400)
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())

    def test_new_history_or_changed_configuration_requires_a_new_preview(self):
        """An old confirmation cannot silently delete newly added records."""
        preview = self.post()
        self.assertEqual(preview.status_code, 200, preview.data)
        Session.objects.create(tenant=self.tenant, assistant=self.assistant, user=self.user)
        response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())

    def test_busy_rows_are_skipped_without_preventing_eligible_deletion(self):
        """Active work is reported and retained while unrelated rows are removed."""
        other = Assistant.objects.create(tenant=self.tenant, name="Idle", slug="delete-idle", status="archived")
        self.run.status = "running"
        self.run.save()
        rows = [self.assistant, other]
        preview = self.post(self.payload(rows))
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(
            next(row for row in preview.data["assistants"] if row["uuid"] == str(self.assistant.uuid))["skip_reason"],
            "active_work",
        )
        response = self.post(self.payload(rows, preview=False, confirmation_token=preview.data["confirmation_token"]))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["deleted"], response.data["skipped"]), (1, 1))
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())
        self.assertFalse(Assistant.objects.filter(pk=other.pk).exists())

    def test_foreign_system_and_stale_batches_are_rejected_atomically(self):
        """A malformed batch cannot delete its otherwise eligible rows."""
        from django.utils import timezone

        foreign = Tenant.objects.create(name="Foreign", slug="deletion-foreign")
        other = Assistant.objects.create(tenant=self.tenant, name="Other", slug="deletion-other")
        for changes, expected in (
            ({"tenant": foreign}, 400),
            ({"is_system": True}, 400),
            ({"updated_at": timezone.now()}, 409),
        ):
            Assistant.objects.filter(pk=other.pk).update(**changes)
            response = self.post(self.payload([self.assistant, other]))
            self.assertEqual(response.status_code, expected, response.data)
            self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())
            Assistant.objects.filter(pk=other.pk).update(
                tenant=self.tenant, is_system=False, updated_at=other.updated_at
            )

    def test_duplicates_empty_and_oversized_batches_are_rejected(self):
        """Bound resource use before starting a deletion transaction."""
        items = self.payload()["assistants"]
        for rows in ([], items * 2, items * 101):
            self.assertEqual(self.post({"assistants": rows, "preview": True}).status_code, 400)

    def test_member_staff_and_api_keys_cannot_preview_or_apply(self):
        """Public resource access never grants administrative deletion authority."""
        for role, staff, auth in (
            ("member", False, None),
            ("member", True, None),
            ("tenant_admin", False, {"principal_type": "tenant_api_key"}),
        ):
            TenantMembership.objects.filter(user=self.user).update(role=role)
            self.user.is_staff = staff
            self.user.save()
            self.client.force_authenticate(get_user_model().objects.get(pk=self.user.pk), token=auth)
            self.assertEqual(self.post().status_code, 403)
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())

    def test_confirmation_cannot_move_between_administrators(self):
        """The preview signature is tied to the actual operator."""
        preview = self.post()
        other = get_user_model().objects.create_user(username="other-delete-admin")
        TenantMembership.objects.create(user=other, tenant=self.tenant, role="tenant_admin")
        self.client.force_authenticate(other)
        response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())

    def test_tampered_and_expired_confirmations_are_rejected(self):
        """Signed confirmation is short lived and cannot be edited by clients."""
        from unittest.mock import patch

        preview = self.post()
        token = preview.data["confirmation_token"]
        self.assertEqual(self.post(preview=False, confirmation_token=token + "x").status_code, 400)
        with patch("django.core.signing.time.time", return_value=9999999999):
            self.assertEqual(self.post(preview=False, confirmation_token=token).status_code, 400)

    def test_configuration_and_added_share_require_review_again(self):
        """Both version and impact drift invalidate a confirmation."""
        preview = self.post()
        SharedQA.objects.create(assistant=self.assistant, token="added-deletion-share")
        self.assertEqual(
            self.post(preview=False, confirmation_token=preview.data["confirmation_token"]).status_code, 409
        )
        preview = self.post()
        self.assistant.description = "Changed configuration"
        self.assistant.save()
        self.assertEqual(
            self.post(preview=False, confirmation_token=preview.data["confirmation_token"]).status_code, 409
        )

    def test_foreign_history_is_skipped_without_counts(self):
        """Tenant administrators cannot delete or enumerate another tenant's records."""
        foreign = Tenant.objects.create(name="Foreign", slug="deletion-history-foreign")
        Session.objects.create(tenant=foreign, assistant=self.assistant, user=self.user)
        preview = self.post()
        row = preview.data["assistants"][0]
        self.assertEqual(row["skip_reason"], "foreign_history")
        self.assertEqual(row["impact"], {})
        response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual((response.data["deleted"], response.data["skipped"]), (0, 1))
        self.assertTrue(Session.objects.filter(pk=self.session.pk).exists())

    def test_evaluation_collaboration_and_automation_references_are_protected(self):
        """Deletion cannot erase evaluation ownership or broaden an empty rule scope."""
        from lens.evaluation.models import EvaluationDataset
        from lens.models import AlertRule

        dataset = EvaluationDataset.objects.create(assistant=self.assistant, name="Keep evaluation")
        self.assertEqual(self.post().data["assistants"][0]["skip_reason"], "evaluation_data")
        dataset.delete()
        coordinator = Assistant.objects.create(tenant=self.tenant, name="Team", slug="deletion-team")
        coordinator.collaboration_members.add(self.assistant)
        self.assertEqual(self.post().data["assistants"][0]["skip_reason"], "collaboration_reference")
        coordinator.collaboration_members.clear()
        rule = AlertRule.objects.create(tenant=self.tenant, name="Scoped rule")
        rule.assistants.add(self.assistant)
        preview = self.post()
        self.assertEqual(preview.data["assistants"][0]["skip_reason"], "automation_reference")
        response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(response.data["deleted"], 0)
        self.assertTrue(rule.assistants.filter(pk=self.assistant.pk).exists())

    def test_orphan_share_snapshots_are_deleted_without_slug_matching(self):
        """Use ownership relations so a reused slug does not delete unrelated history."""
        orphan = SharedQA.objects.create(assistant=self.assistant, token="orphan-deletion-share")
        unrelated = SharedQA.objects.create(assistant_slug=self.assistant.slug, token="keep-deletion-share")
        preview = self.post()
        self.assertEqual(preview.data["assistants"][0]["impact"]["shares"], 2)
        response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(SharedQA.objects.filter(pk=orphan.pk).exists())
        self.assertTrue(SharedQA.objects.filter(pk=unrelated.pk).exists())

    def test_readonly_delegation_previews_and_operational_delegation_audits_real_actor(self):
        """A change of delegation mode still requires write authority at confirmation."""
        from django.utils import timezone

        from accounts.models import Profile, TenantDelegation

        platform = get_user_model().objects.create_user(username="delete-platform")
        Profile.objects.filter(user=platform).update(platform_role="super_admin")
        self.client.force_authenticate(platform)
        self.assertEqual(self.post().status_code, 403)
        delegation = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.tenant,
            mode="readonly",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        url = "/api/lens/assistants/bulk-delete/"
        preview = self.client.post(url, self.payload(), format="json", **headers)
        self.assertEqual(preview.status_code, 200, preview.data)
        payload = self.payload(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(self.client.post(url, payload, format="json", **headers).status_code, 403)
        delegation.mode = "operational"
        delegation.save()
        response = self.client.post(url, payload, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        audit = AuditLog.objects.get(action="assistant.bulk_delete")
        self.assertEqual(audit.actor_user, platform)
        self.assertEqual(audit.delegation, delegation)
        self.assertNotIn("Private question", str(audit.metadata))
        self.assertNotIn("confirmation_token", audit.metadata)

    def test_generic_delete_cannot_bypass_confirmation(self):
        """The ordinary detail route continues to reject destructive DELETE."""
        self.assertEqual(self.client.delete(f"/api/lens/assistants/{self.assistant.uuid}/").status_code, 405)

    def test_removed_assistant_is_not_runnable_through_cached_objects(self):
        """Deletion races fail through the normal unavailable-work exception."""
        from lens.assistant_lifecycle import (
            AssistantNotRunnableError,
            create_assistant_session,
            lock_assistant_for_new_work,
        )

        preview = self.post()
        self.assertEqual(
            self.post(preview=False, confirmation_token=preview.data["confirmation_token"]).status_code, 200
        )
        with self.assertRaises(AssistantNotRunnableError):
            lock_assistant_for_new_work(self.assistant, self.user)
        with self.assertRaises(AssistantNotRunnableError):
            create_assistant_session(self.assistant.uuid, self.user)

    def file_fixtures(self):
        """Store all three file classes under an isolated test directory."""
        from tempfile import TemporaryDirectory

        from django.conf import settings
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from lens.models import MessageAttachment, RunOutputFile, SharedQAFile

        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        stores = {
            **settings.STORAGES,
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": media.name}},
            "deliverables": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": media.name},
            },
        }
        override = override_settings(STORAGES=stores)
        override.enable()
        self.addCleanup(override.disable)
        image = MessageAttachment.objects.create(session=self.session, file=SimpleUploadedFile("image.png", b"image"))
        output = RunOutputFile.objects.create(
            tenant=self.tenant,
            assistant=self.assistant,
            session=self.session,
            run=self.run,
            filename="report.txt",
            file=SimpleUploadedFile("report.txt", b"private report"),
        )
        shared = SharedQAFile.objects.create(
            share=self.share,
            kind="output",
            filename="shared.txt",
            file=SimpleUploadedFile("shared.txt", b"published report"),
        )
        return [(row.file.storage, row.file.name) for row in (image, output, shared)]

    def test_committed_delete_cleans_all_owned_file_types(self):
        """Image uploads, outputs and share snapshots are purged after commit."""
        from unittest.mock import patch

        files = self.file_fixtures()
        preview = self.post()
        self.assertEqual(preview.status_code, 200, preview.data)
        impact = preview.data["assistants"][0]["impact"]
        self.assertEqual((impact["attachments"], impact["output_files"], impact["shared_files"]), (1, 1, 1))
        with patch("lens.tasks.session_workspace_cleanup_task.delay"), self.captureOnCommitCallbacks(execute=True):
            response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(all(storage.exists(name) for storage, name in files))
        self.assertTrue(all(not storage.exists(name) for storage, name in files))

    def test_late_protected_dependency_rolls_back_rows_and_preserves_file_bytes(self):
        """A failed deletion must never leave restored database rows with lost files."""
        from unittest.mock import patch

        from django.db.models.deletion import ProtectedError

        from lens.session_deletion import delete_sessions

        files = self.file_fixtures()
        preview = self.post()

        def fail_after_session_cleanup(queryset):
            """Model a dependency race after the first destructive queries."""
            delete_sessions(queryset)
            raise ProtectedError("Concurrent dependency", {self.source})

        with patch(
            "lens.assistant_deletion.delete_sessions", side_effect=fail_after_session_cleanup
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())
        self.assertTrue(Session.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(SharedQA.objects.filter(pk=self.share.pk).exists())
        self.assertTrue(all(storage.exists(name) for storage, name in files))
        self.assertFalse(AuditLog.objects.filter(action="assistant.bulk_delete").exists())

    def test_diagnostics_finish_before_history_and_evidence_can_be_deleted(self):
        """Running diagnosis work is retained; completed protected evidence is cleaned."""
        from lens.models import RunDiagnostic, RunDiagnosticEvidence, RunDiagnosticTurn

        evidence = RunDiagnosticEvidence.objects.create(run=self.run, payload={"safe": True})
        diagnostic = RunDiagnostic.objects.create(run=self.run, evidence=evidence, status="running")
        self.assertEqual(self.post().data["assistants"][0]["skip_reason"], "active_work")
        diagnostic.status = "completed"
        diagnostic.save()
        turn = RunDiagnosticTurn.objects.create(
            diagnostic=diagnostic, question="Follow up", idempotency_key="delete-turn"
        )
        self.assertEqual(self.post().data["assistants"][0]["skip_reason"], "active_work")
        turn.status = "completed"
        turn.save()
        preview = self.post()
        self.assertIsNone(preview.data["assistants"][0]["skip_reason"])
        response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(RunDiagnosticEvidence.objects.filter(pk=evidence.pk).exists())
        self.assertFalse(RunDiagnosticTurn.objects.filter(pk=turn.pk).exists())

    def test_remote_cleanup_outbox_survives_the_deleted_session(self):
        """Nodes referenced only by execution snapshots still receive durable cleanup."""
        from lens.models import LensNode, RunExecution, SessionCleanupOperation

        node = LensNode.objects.create(name="Cleanup execution node")
        RunExecution.objects.create(run=self.run, lensnode=node, task="general_chat")
        preview = self.post()
        response = self.post(preview=False, confirmation_token=preview.data["confirmation_token"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            SessionCleanupOperation.objects.filter(
                session_uuid=self.session.uuid, lensnode_uuid=node.uuid, status="pending"
            ).exists()
        )


class AssistantDeletionConcurrencyTests(TransactionTestCase):
    """Serialize irreversible deletion against requests already creating sessions."""

    def setUp(self):
        """Prepare a signed preview for an initially unused tenant assistant."""
        self.tenant = Tenant.objects.create(name="Delete race", slug="delete-race")
        self.user = get_user_model().objects.create_user(username="delete-race-admin")
        TenantMembership.objects.create(user=self.user, tenant=self.tenant, role="tenant_admin")
        self.assistant = Assistant.objects.create(tenant=self.tenant, name="Race", slug="delete-race-assistant")
        self.payload = {
            "assistants": [
                {
                    "uuid": str(self.assistant.uuid),
                    "updated_at": self.assistant.updated_at.isoformat(),
                    "confirmation_slug": self.assistant.slug,
                }
            ],
            "preview": True,
        }
        client = APIClient()
        client.force_authenticate(self.user)
        preview = client.post("/api/lens/assistants/bulk-delete/", self.payload, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.payload.update(preview=False, confirmation_token=preview.data["confirmation_token"])

    def race(self, pause_deletion):
        """Hold the first writer's assistant lock while starting the second writer."""
        import threading
        from unittest.mock import patch

        from django.db import close_old_connections

        from lens.assistant_deletion import _impact
        from lens.assistant_lifecycle import AssistantNotRunnableError, create_assistant_session

        entered = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        second_finished = threading.Event()
        results = {}
        errors = []
        original_check = Assistant.is_runnable_by

        def hold():
            """Give the competing writer time to attempt the locked operation."""
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Deletion race was not released")

        def paused_impact(assistant):
            """Pause deletion only after the authoritative locks have been taken."""
            result = _impact(assistant)
            hold()
            return result

        def paused_check(assistant, user):
            """Pause session creation while it owns the assistant lock."""
            result = original_check(assistant, user)
            hold()
            return result

        def create():
            """Exercise the real lifecycle transaction on its own connection."""
            close_old_connections()
            try:
                results["session"] = create_assistant_session(self.assistant.uuid, self.user)
            except AssistantNotRunnableError:
                results["unavailable"] = True
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def delete():
            """Exercise the confirmed API transaction on its own connection."""
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
                results["delete"] = client.post("/api/lens/assistants/bulk-delete/", self.payload, format="json")
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def second():
            """Record completion so the test can detect a missing lock."""
            second_started.set()
            try:
                (create if pause_deletion else delete)()
            finally:
                second_finished.set()

        target = "lens.assistant_deletion._impact" if pause_deletion else "lens.models.Assistant.is_runnable_by"
        replacement = paused_impact if pause_deletion else paused_check
        with patch(target, replacement):
            first = threading.Thread(target=delete if pause_deletion else create)
            competing = threading.Thread(target=second)
            first.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                competing.start()
                self.assertTrue(second_started.wait(timeout=5))
                overtook = second_finished.wait(timeout=0.25)
            finally:
                release.set()
                first.join(timeout=5)
                if competing.ident is not None:
                    competing.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertFalse(competing.is_alive())
        if errors:
            raise errors[0]
        self.assertFalse(overtook)
        return results

    def test_session_creation_waits_for_deletion_then_reports_unavailable(self):
        """A stale object cannot recreate work after permanent deletion wins."""
        results = self.race(pause_deletion=True)
        self.assertEqual(results["delete"].status_code, 200, results["delete"].data)
        self.assertTrue(results["unavailable"])
        self.assertFalse(Session.objects.filter(assistant_id=self.assistant.pk).exists())

    def test_deletion_waits_for_new_session_then_requires_a_fresh_review(self):
        """A session created during confirmation survives until explicitly reviewed."""
        results = self.race(pause_deletion=False)
        self.assertEqual(results["delete"].status_code, 409, results["delete"].data)
        self.assertTrue(Session.objects.filter(pk=results["session"].pk).exists())
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())
