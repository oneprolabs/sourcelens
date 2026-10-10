"""Regression coverage for reviewed deliverables and atomic final publication."""

from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from lens.lensnode_auth import hash_lensnode_token
from lens.models import Assistant, LensNode, Run, RunOutputFile, Session
from lens.serializers import RunOutputFileSerializer
from lens.services import create_execution_run, finish_lensnode_run


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class DeliverableLifecycleTests(TestCase):
    """Only approved final-manifest files become user-visible deliverables."""

    def setUp(self):
        """Create an isolated owner, node, and run with private file storage."""

        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        storages = {
            **settings.STORAGES,
            "deliverables": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": media.name},
            },
        }
        settings_override = override_settings(STORAGES=storages)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.user = get_user_model().objects.create_user(username="deliverable-owner")
        self.node_token = "deliverable-test-node-token"
        self.node = LensNode.objects.create(
            name="Review node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            auth_token_hash=hash_lensnode_token(self.node_token),
        )
        self.assistant = Assistant.objects.create(
            name="Reviewer", slug="deliverable-reviewer", lensnode=self.node, selected_task="knowledge_qa"
        )
        self.session = Session.objects.create(assistant=self.assistant, user=self.user)
        self.run = create_execution_run(session=self.session, question="Generate a report", enqueue=False)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _upload(self, filename, *, run=None, staged=True, body=b"reviewed report"):
        """Upload through the real node API and return the persisted file."""

        payload = {
            "run_uuid": str((run or self.run).uuid),
            "file": SimpleUploadedFile(filename, body, content_type="text/plain"),
            "filename": filename,
        }
        if staged:
            payload["staged"] = "true"
        response = APIClient().post(
            "/api/lens/lensnode/deliverables/",
            payload,
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {self.node_token}",
        )
        self.assertEqual(response.status_code, 201, response.data)
        output = RunOutputFile.objects.get(uuid=response.json()["data"]["uuid"])
        self.addCleanup(output.file.delete, save=False)
        return output

    def _visible_files(self):
        """Read the files visible under the run's answer in the public API."""

        response = self.client.get(f"/api/lens/sessions/{self.session.uuid}/messages/")
        self.assertEqual(response.status_code, 200, response.data)
        answer = next(message for message in response.data if str(message["uuid"]) == str(self.run.output_message.uuid))
        return answer["output_files"]

    def _status(self, output):
        """Read the computed API status without a database status column."""

        return RunOutputFileSerializer(output).data["status"]

    def test_staged_upload_is_not_visible_or_downloadable(self):
        """A draft stays private until terminal review approves it."""

        draft = self._upload("draft.txt")
        self.assertEqual(self._status(draft), "candidate")
        self.assertIsNone(draft.message_id)
        self.assertEqual(self._visible_files(), [])
        response = self.client.get(f"/api/lens/output-files/{draft.uuid}/")
        self.assertEqual(response.status_code, 404)

    def test_regenerated_renamed_draft_is_superseded_and_bytes_retained(self):
        """A rejected first filename must not survive as a visible extra file."""

        rejected = self._upload("report-draft.txt", body=b"rejected report")
        approved = self._upload("report-final.txt", body=b"approved report")
        finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[str(approved.uuid)])
        rejected.refresh_from_db()
        approved.refresh_from_db()
        self.assertEqual(self._status(rejected), "superseded")
        self.assertIsNone(rejected.message_id)
        self.assertTrue(rejected.file.storage.exists(rejected.file.name))
        self.assertEqual(self._status(approved), "published")
        self.assertEqual(approved.message_id, self.run.output_message_id)
        self.assertEqual([item["filename"] for item in self._visible_files()], ["report-final.txt"])
        self.assertEqual(self.client.get(f"/api/lens/output-files/{rejected.uuid}/").status_code, 404)
        response = self.client.get(f"/api/lens/output-files/{approved.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"approved report")

    def test_manifest_can_publish_multiple_files_from_one_approved_batch(self):
        """A valid multi-file response keeps every approved artifact."""

        report = self._upload("report.txt")
        appendix = self._upload("appendix.txt")
        finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[str(report.uuid), str(appendix.uuid)])
        self.assertCountEqual([item["filename"] for item in self._visible_files()], ["report.txt", "appendix.txt"])
        self.assertEqual(self.run.output_message.output_files.count(), 2)

    def test_every_accepted_file_can_be_published_in_a_large_batch(self):
        """Successful uploads must not exceed a separate terminal-only limit."""

        outputs = [self._upload(f"report-{index}.txt") for index in range(101)]
        finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[str(output.uuid) for output in outputs])

        self.run.refresh_from_db()
        self.assertEqual(self.run.status, Run.Status.DONE)
        self.assertEqual(len(self._visible_files()), 101)
        self.assertEqual(self.run.output_message.output_files.count(), 101)

    def test_repeated_terminal_frame_cannot_publish_a_rejected_file(self):
        """At-least-once terminal delivery must not change the approved set."""

        approved = self._upload("approved.txt")
        rejected = self._upload("rejected.txt")
        finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[str(approved.uuid)])
        finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[str(rejected.uuid)])
        self.assertEqual([item["filename"] for item in self._visible_files()], ["approved.txt"])
        self.assertEqual(RunOutputFile.objects.filter(run=self.run).count(), 2)
        rejected.refresh_from_db()
        self.assertEqual(self._status(rejected), "superseded")

    def test_manifest_rejects_files_from_another_run_atomically(self):
        """A foreign UUID cannot publish either run's private candidate."""

        own = self._upload("own.txt")
        other_session = Session.objects.create(assistant=self.assistant, user=self.user)
        other_run = create_execution_run(session=other_session, question="Other report", enqueue=False)
        foreign = self._upload("foreign.txt", run=other_run)
        with self.assertRaises(ValidationError):
            finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[str(own.uuid), str(foreign.uuid)])
        self.run.refresh_from_db()
        self.assertNotEqual(self.run.status, Run.Status.DONE)
        for output in (own, foreign):
            output.refresh_from_db()
            self.assertEqual(self._status(output), "candidate")
            self.assertIsNone(output.message_id)
        self.assertEqual(self._visible_files(), [])

    def test_failed_run_never_publishes_manifest_candidates(self):
        """Files from a failed answer remain unavailable even with a manifest."""

        draft = self._upload("failed-report.txt")
        finish_lensnode_run(
            self.run.uuid, Run.Status.FAILED, error="REVIEW_FAILED", deliverable_uuids=[str(draft.uuid)]
        )
        draft.refresh_from_db()
        self.assertEqual(self._status(draft), "superseded")
        self.assertIsNone(draft.message_id)
        self.assertEqual(self._visible_files(), [])
        self.assertEqual(self.client.get(f"/api/lens/output-files/{draft.uuid}/").status_code, 404)

    def test_already_cancelled_run_never_publishes_a_late_manifest(self):
        """An in-flight terminal frame cannot expose cancelled-run drafts."""

        draft = self._upload("cancelled-report.txt")
        Run.objects.filter(pk=self.run.pk).update(status=Run.Status.CANCELLED)
        finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[str(draft.uuid)])
        draft.refresh_from_db()
        self.assertNotEqual(self._status(draft), "published")
        self.assertIsNone(draft.message_id)
        self.assertEqual(self._visible_files(), [])

    def test_empty_manifest_publishes_no_candidates(self):
        """An approved text-only answer must not expose abandoned drafts."""

        draft = self._upload("abandoned.txt")
        finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[])
        draft.refresh_from_db()
        self.assertEqual(self._status(draft), "superseded")
        self.assertIsNone(draft.message_id)
        self.assertEqual(self._visible_files(), [])

    def test_legacy_upload_and_terminal_frame_remain_visible(self):
        """Nodes without staged or manifest fields retain the old contract."""

        output = self._upload("legacy.txt", staged=False)
        self.assertEqual(self._status(output), "published")
        self.assertEqual(output.message_id, self.run.output_message_id)
        finish_lensnode_run(self.run.uuid, Run.Status.DONE)
        self.assertEqual([item["filename"] for item in self._visible_files()], ["legacy.txt"])
        response = self.client.get(f"/api/lens/output-files/{output.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"reviewed report")

    def test_missing_manifest_does_not_implicitly_publish_staged_uploads(self):
        """A terminal frame without a manifest cannot approve staged drafts."""

        draft = self._upload("unreviewed.txt")
        finish_lensnode_run(self.run.uuid, Run.Status.DONE)
        draft.refresh_from_db()
        self.assertNotEqual(self._status(draft), "published")
        self.assertIsNone(draft.message_id)
        self.assertEqual(self._visible_files(), [])

    def test_admin_can_audit_rejected_file_status_and_download_bytes(self):
        """Admins retain the rejected version while owners see the final set."""

        rejected = self._upload("audit-draft.txt", body=b"rejected report")
        approved = self._upload("approved.txt")
        finish_lensnode_run(self.run.uuid, Run.Status.DONE, deliverable_uuids=[str(approved.uuid)])
        admin = get_user_model().objects.create_user(username="deliverable-admin", is_staff=True)
        admin_client = APIClient()
        admin_client.force_authenticate(admin)
        response = admin_client.get(f"/api/lens/admin/runs/{self.run.uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        statuses = {item["filename"]: item["status"] for item in response.data["output_files"]}
        self.assertEqual(statuses, {"audit-draft.txt": "superseded", "approved.txt": "published"})
        response = admin_client.get(f"/api/lens/output-files/{rejected.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"rejected report")
