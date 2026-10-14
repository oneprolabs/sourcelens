import io
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from lens.models import (
    Assistant,
    Connection,
    CredentialLease,
    ExecutionSnapshot,
    LensNode,
    PluginInvocation,
    Run,
    RunDiagnostic,
    RunDiagnosticEvidence,
    SecretMaterial,
    SecretVersion,
    Session,
    SessionCleanupOperation,
    SharedQA,
)
from lens.services import create_execution_run
from lens.tasks import session_workspace_cleanup_task


class SessionManagementTests(TestCase):
    """Cover persisted pinning and the active/archive lifecycle."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="session-owner",
            password="pass12345",
        )
        self.other = get_user_model().objects.create_user(
            username="other-session-owner",
            password="pass12345",
        )
        self.node = LensNode.objects.create(
            name="Session management node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            tasks=[{"name": "general_chat"}],
        )
        self.assistant = Assistant.objects.create(
            name="Session management assistant",
            slug="session-management-assistant",
            lensnode=self.node,
            selected_task="general_chat",
            multimodal_model_ref=uuid.uuid4(),
            visibility=Assistant.Visibility.PUBLIC,
        )
        self.first = self._session("First")
        self.second = self._session("Second")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _session(self, title, status=Session.Status.ACTIVE):
        return Session.objects.create(
            assistant=self.assistant,
            user=self.user,
            title=title,
            status=status,
        )

    def _list(self, archived=False):
        response = self.client.get(
            "/api/lens/sessions/",
            {
                "assistant_slug": self.assistant.slug,
                "archived": str(archived).lower(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"]

    def _image(self):
        image = io.BytesIO()
        Image.new("RGB", (2, 2), (20, 80, 140)).save(image, format="PNG")
        return SimpleUploadedFile(
            "session.png",
            image.getvalue(),
            content_type="image/png",
        )

    def test_pinned_sessions_are_persisted_and_listed_first(self):
        first_pin = self.client.post(
            f"/api/lens/sessions/{self.first.uuid}/pin/"
        )
        second_pin = self.client.post(
            f"/api/lens/sessions/{self.second.uuid}/pin/"
        )

        self.assertEqual(first_pin.status_code, 200, first_pin.data)
        self.assertEqual(second_pin.status_code, 200, second_pin.data)
        rows = self._list()
        self.assertEqual(
            [row["uuid"] for row in rows[:2]],
            [str(self.second.uuid), str(self.first.uuid)],
        )
        self.first.refresh_from_db()
        self.assertIsNotNone(self.first.pinned_at)

        unpin = self.client.post(
            f"/api/lens/sessions/{self.second.uuid}/unpin/"
        )

        self.assertEqual(unpin.status_code, 200, unpin.data)
        self.second.refresh_from_db()
        self.assertIsNone(self.second.pinned_at)
        self.assertEqual(self._list()[0]["uuid"], str(self.first.uuid))

    def test_archive_filters_lists_clears_pin_and_restores_history(self):
        run = create_execution_run(
            self.first,
            "Keep this historical question",
            enqueue=False,
            user=self.user,
        )
        run.status = Run.Status.DONE
        run.output_message.content = "Keep this historical answer"
        run.output_message.save(update_fields=["content"])
        run.save(update_fields=["status"])
        share = SharedQA.objects.create(
            token="session-management-share",
            run=run,
            assistant=self.assistant,
            assistant_name=self.assistant.name,
            assistant_slug=self.assistant.slug,
            question=run.input_message.content,
            answer=run.output_message.content,
            published_by=self.user,
        )
        self.client.post(f"/api/lens/sessions/{self.first.uuid}/pin/")

        response = self.client.post(
            f"/api/lens/sessions/{self.first.uuid}/archive/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Session.Status.ARCHIVED)
        self.assertIsNone(self.first.pinned_at)
        direct_restore = self.client.patch(
            f"/api/lens/sessions/{self.first.uuid}/",
            {"status": Session.Status.ACTIVE},
            format="json",
        )
        self.assertEqual(direct_restore.status_code, 200, direct_restore.data)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Session.Status.ARCHIVED)
        self.assertNotIn(
            str(self.first.uuid),
            [row["uuid"] for row in self._list()],
        )
        self.assertEqual(
            self._list(archived=True)[0]["uuid"],
            str(self.first.uuid),
        )
        self.assertTrue(Run.objects.filter(pk=run.pk).exists())
        self.assertTrue(SharedQA.objects.filter(pk=share.pk).exists())

        messages = self.client.get(
            f"/api/lens/sessions/{self.first.uuid}/messages/"
        )
        restore = self.client.post(
            f"/api/lens/sessions/{self.first.uuid}/restore/"
        )

        self.assertEqual(messages.status_code, 200, messages.data)
        self.assertEqual(restore.status_code, 200, restore.data)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Session.Status.ACTIVE)
        self.assertIsNone(self.first.pinned_at)

    def test_archived_session_rejects_new_runs_and_attachments(self):
        self.first.status = Session.Status.ARCHIVED
        self.first.save(update_fields=["status"])

        run = self.client.post(
            f"/api/lens/sessions/{self.first.uuid}/runs/",
            {"question": "Do not create this", "enqueue": False},
            format="json",
        )
        attachment = self.client.post(
            f"/api/lens/sessions/{self.first.uuid}/attachments/",
            {"file": self._image()},
            format="multipart",
        )

        self.assertEqual(run.status_code, 400, run.data)
        self.assertEqual(attachment.status_code, 400, attachment.data)
        self.assertEqual(self.first.run_set.count(), 0)
        self.assertEqual(self.first.attachments.count(), 0)

    def test_list_reports_whether_a_session_has_a_shareable_answer(self):
        run = create_execution_run(
            self.first,
            "Shareable question",
            enqueue=False,
            user=self.user,
        )
        run.status = Run.Status.DONE
        run.save(update_fields=["status"])

        rows = {row["uuid"]: row for row in self._list()}

        self.assertTrue(rows[str(self.first.uuid)]["has_shareable_answer"])
        self.assertFalse(rows[str(self.second.uuid)]["has_shareable_answer"])

    def test_delete_session_with_run_diagnostic_history(self):
        run = create_execution_run(
            self.first,
            "Diagnosed question",
            enqueue=False,
            user=self.user,
        )
        evidence = RunDiagnosticEvidence.objects.create(
            run=run,
            payload={"events": []},
        )
        diagnostic = RunDiagnostic.objects.create(
            run=run,
            evidence=evidence,
            requested_by=self.user,
        )

        response = self.client.delete(
            f"/api/lens/sessions/{self.first.uuid}/"
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Session.objects.filter(pk=self.first.pk).exists())
        self.assertFalse(Run.objects.filter(pk=run.pk).exists())
        self.assertFalse(
            RunDiagnostic.objects.filter(pk=diagnostic.pk).exists()
        )
        self.assertFalse(
            RunDiagnosticEvidence.objects.filter(pk=evidence.pk).exists()
        )

    def test_delete_session_with_plugin_tool_snapshot(self):
        run = create_execution_run(
            self.first,
            "Plugin question",
            enqueue=False,
            user=self.user,
        )
        material = SecretMaterial.objects.create(name="Plugin secret")
        secret_version = SecretVersion.objects.create(
            material=material,
            encrypted_value="encrypted",
        )
        connection = Connection.objects.create(
            name="Plugin connection",
            plugin_key="github",
            endpoint="https://github.com",
            secret_version=secret_version,
        )
        snapshot = ExecutionSnapshot.objects.create(
            kind=ExecutionSnapshot.Kind.TOOL_INVOKE,
            connection=connection,
            run=run,
            secret_version=secret_version,
            plugin_key="github",
            plugin_version="1.0.0",
            protocol_version=1,
            tool_key="github_read_file",
            invocation_id="tool-call-1",
            resolved_config={},
        )
        invocation = PluginInvocation.objects.create(
            snapshot=snapshot,
            connection=connection,
            run=run,
            actor=self.user,
            lensnode=self.node,
            kind=ExecutionSnapshot.Kind.TOOL_INVOKE,
            plugin_key="github",
            tool_key="github_read_file",
        )
        lease = CredentialLease.objects.create(
            snapshot=snapshot,
            lensnode=self.node,
            expires_at=timezone.now(),
        )

        response = self.client.delete(
            f"/api/lens/sessions/{self.first.uuid}/"
        )

        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(Session.objects.filter(pk=self.first.pk).exists())
        self.assertFalse(Run.objects.filter(pk=run.pk).exists())
        self.assertFalse(
            ExecutionSnapshot.objects.filter(pk=snapshot.pk).exists()
        )
        self.assertFalse(
            PluginInvocation.objects.filter(pk=invocation.pk).exists()
        )
        self.assertFalse(CredentialLease.objects.filter(pk=lease.pk).exists())

    @override_settings(
        CHANNEL_LAYERS={
            "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
        }
    )
    def test_cleanup_dispatch_targets_lensnode_runs(self):
        run = create_execution_run(
            self.first,
            "Cleanup question",
            enqueue=False,
            user=self.user,
        )
        run.status = Run.Status.DONE
        run.lensnode = self.node
        run.save(update_fields=["status", "lensnode"])
        Session.objects.filter(pk=self.first.pk).update(
            status=Session.Status.ARCHIVED,
            updated_at=timezone.now() - timedelta(days=8),
        )

        session_workspace_cleanup_task()

        self.assertTrue(
            SessionCleanupOperation.objects.filter(
                session_uuid=self.first.uuid,
                lensnode_uuid=self.node.uuid,
            ).exists()
        )

    def test_other_users_cannot_manage_sessions_they_do_not_own(self):
        self.client.force_authenticate(self.other)

        response = self.client.post(
            f"/api/lens/sessions/{self.first.uuid}/archive/"
        )

        self.assertEqual(response.status_code, 404)
