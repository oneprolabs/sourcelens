import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from lens.models import (
    Assistant,
    AssistantAccess,
    GlobalSetting,
    LensNode,
    Message,
    MessageAttachment,
    Run,
    RunOutputFile,
    Session,
    SharedQA,
    SharedQAFile,
)

User = get_user_model()

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
}

TEST_MEDIA_ROOT = tempfile.mkdtemp()
TEST_DELIVERABLE_ROOT = tempfile.mkdtemp()
TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": TEST_MEDIA_ROOT},
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
    "deliverables": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": TEST_DELIVERABLE_ROOT},
    },
}


def _results(data):
    """Return list rows from a paginated or plain list response."""

    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


@override_settings(CACHES=TEST_CACHES, STORAGES=TEST_STORAGES)
class SharedQAApiTests(TestCase):
    """Integration tests for the public shareable Q&A feature."""

    def setUp(self):
        self.client = APIClient()
        self._use_test_deliverable_storage()
        self.owner = User.objects.create_user(
            username="qa-owner",
            email="owner@example.com",
            password="pass12345",
        )
        self.other = User.objects.create_user(
            username="qa-other",
            email="other@example.com",
            password="pass12345",
        )
        self.group_peer = User.objects.create_user(
            username="qa-peer",
            email="peer@example.com",
            password="pass12345",
        )
        self.admin = User.objects.create_user(
            username="qa-admin",
            email="admin@example.com",
            password="pass12345",
            is_staff=True,
        )
        self.group = Group.objects.create(name="qa-group")
        self.owner.groups.add(self.group)
        self.group_peer.groups.add(self.group)
        self.lensnode = LensNode.objects.create(
            name="Node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.assistant = Assistant.objects.create(
            name="Data Helper",
            slug="data-helper",
            lensnode=self.lensnode,
            selected_task="qa",
            status=Assistant.Status.ACTIVE,
            visibility=Assistant.Visibility.PUBLIC,
        )
        self.run = self._make_done_run()

    def _use_test_deliverable_storage(self):
        """Point callable FileFields at the overridden test storage."""

        for model in [RunOutputFile, SharedQAFile]:
            field = model._meta.get_field("file")
            original_storage = field.storage
            field.storage = storages["deliverables"]
            self.addCleanup(
                setattr,
                field,
                "storage",
                original_storage,
            )

    def _make_done_run(self, assistant=None):
        """Create a completed run with a question and an answer message."""

        session = Session.objects.create(
            assistant=assistant or self.assistant,
            user=self.owner,
            title="t",
        )
        question = Message.objects.create(
            session=session,
            role=Message.Role.USER,
            content="What is X?",
            sequence=1,
        )
        run = Run.objects.create(
            session=session,
            status=Run.Status.DONE,
            input_message=question,
        )
        answer = Message.objects.create(
            session=session,
            role=Message.Role.ASSISTANT,
            content="X is a thing.",
            run=run,
            sequence=2,
        )
        run.output_message = answer
        run.save(update_fields=["output_message"])
        return run

    def _share(self, user=None, payload=None, run=None):
        """POST the run share action as the given (default owner) user."""

        self.client.force_authenticate(user or self.owner)
        return self.client.post(
            f"/api/lens/runs/{(run or self.run).uuid}/share/",
            payload or {},
            format="json",
        )

    def _private_share_token(self, with_files=False):
        """Create a share token from a private assistant run."""

        private_assistant = Assistant.objects.create(
            name="Private Helper",
            slug="private-helper",
            lensnode=self.lensnode,
            selected_task="qa",
            status=Assistant.Status.ACTIVE,
            visibility=Assistant.Visibility.PRIVATE,
        )
        run = self._make_done_run(private_assistant)
        if with_files:
            self._add_run_files(run)
        return self._share(run=run).data["token"]

    def _add_run_files(self, run=None):
        """Add one input image and one output deliverable to a run."""

        run = run or self.run
        attachment_bytes = b"question image bytes"
        output_bytes = b"<h1>Shared report</h1>"
        attachment = MessageAttachment.objects.create(
            session=run.session,
            message=run.input_message,
            uploaded_by=self.owner,
            file=SimpleUploadedFile(
                "question.png",
                attachment_bytes,
                content_type="image/png",
            ),
            original_name="question.png",
            mime_type="image/png",
            byte_size=len(attachment_bytes),
            order=0,
        )
        output = RunOutputFile.objects.create(
            run=run,
            message=run.output_message,
            session=run.session,
            assistant=run.session.assistant,
            file=SimpleUploadedFile(
                "report.html",
                output_bytes,
                content_type="text/html",
            ),
            filename="report.html",
            content_type="text/html",
            byte_size=len(output_bytes),
        )
        return attachment, output, attachment_bytes, output_bytes

    def _public_file_url(self, token, file_uuid):
        return f"/api/lens/public/qa/{token}/files/{file_uuid}/"

    def _legacy_share(self, token):
        """Create a pre-file-snapshot share for compatibility tests."""

        return SharedQA.objects.create(
            token=token,
            run=self.run,
            assistant=self.assistant,
            assistant_name=self.assistant.name,
            assistant_slug=self.assistant.slug,
            question="What is X?",
            answer="X is a thing.",
            title="Legacy share",
            published_by=self.owner,
            published_at=timezone.now(),
        )

    def test_share_creates_snapshot(self):
        resp = self._share()
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(resp.data["is_listed"])
        self.assertEqual(resp.data["status"], "published")
        self.assertEqual(resp.data["run_uuid"], str(self.run.uuid))
        share = SharedQA.objects.get(token=resp.data["token"])
        self.assertEqual(share.question, "What is X?")
        self.assertEqual(share.answer, "X is a thing.")
        self.assertEqual(share.content_language, "zh-CN")
        self.assertEqual(share.assistant_slug, "data-helper")
        self.assertEqual(share.assistant_name, "Data Helper")
        self.assertEqual(share.published_by, self.owner)
        self.assertIsNotNone(share.published_at)
        self.assertTrue(share.title)

    def test_public_share_returns_content_language(self):
        token = self._share().data["token"]
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/lens/public/qa/{token}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["content_language"], "zh-CN")

    def test_share_snapshots_input_and_output_file_bytes(self):
        _, _, attachment_bytes, output_bytes = self._add_run_files()

        resp = self._share()

        self.assertEqual(resp.status_code, 201)
        share = SharedQA.objects.get(token=resp.data["token"])
        files = {
            item.kind: item
            for item in share.files.order_by("kind", "order")
        }
        self.assertEqual(
            files[SharedQAFile.Kind.INPUT].file.read(),
            attachment_bytes,
        )
        self.assertEqual(
            files[SharedQAFile.Kind.OUTPUT].file.read(),
            output_bytes,
        )

    def test_public_share_lists_files_in_turn_context(self):
        self._add_run_files()
        token = self._share().data["token"]
        self.client.force_authenticate(self.other)

        resp = self.client.get(f"/api/lens/public/qa/{token}/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [item["filename"] for item in resp.data["input_attachments"]],
            ["question.png"],
        )
        self.assertEqual(
            [item["filename"] for item in resp.data["output_files"]],
            ["report.html"],
        )
        for field in ["input_attachments", "output_files"]:
            item = resp.data[field][0]
            self.assertIn(token, item["url"])
            self.assertIn(str(item["uuid"]), item["url"])
            self.assertGreater(item["byte_size"], 0)
            self.assertTrue(item["content_type"])

    def test_share_file_allows_anonymous_for_public_assistant(self):
        self._add_run_files()
        token = self._share().data["token"]
        snapshot = SharedQAFile.objects.get(
            share__token=token,
            kind=SharedQAFile.Kind.OUTPUT,
        )
        url = self._public_file_url(token, snapshot.uuid)

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 200)
        self.assertEqual(
            anonymous["Content-Type"],
            snapshot.content_type,
        )

    def test_share_file_requires_login_for_private_assistant(self):
        private_token = self._private_share_token(with_files=True)
        private_share = SharedQA.objects.get(token=private_token)
        private_snapshot = private_share.files.get(
            kind=SharedQAFile.Kind.OUTPUT
        )
        private_url = self._public_file_url(
            private_token,
            private_snapshot.uuid,
        )

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(private_url)
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(
            anonymous.data["code"],
            "AUTHENTICATION_REQUIRED",
        )

        self.client.force_authenticate(self.other)
        denied = self.client.get(private_url)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "ASSISTANT_ACCESS_DENIED")

    def test_share_file_download_is_private_and_token_scoped(self):
        _, _, _, output_bytes = self._add_run_files()
        token = self._share().data["token"]
        snapshot = SharedQAFile.objects.get(
            share__token=token,
            kind=SharedQAFile.Kind.OUTPUT,
        )
        self.client.force_authenticate(self.other)

        response = self.client.get(
            self._public_file_url(token, snapshot.uuid)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), output_bytes)
        self.assertTrue(response["Cache-Control"].startswith("private"))
        self.assertIn("attachment", response["Content-Disposition"])

        other_run = self._make_done_run()
        self._add_run_files(other_run)
        other_token = self._share(run=other_run).data["token"]
        mismatched = self.client.get(
            self._public_file_url(other_token, snapshot.uuid)
        )
        self.assertEqual(mismatched.status_code, 404)

    def test_hidden_or_deleted_share_cannot_serve_snapshot_file(self):
        self._add_run_files()
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        snapshot = share.files.first()
        url = self._public_file_url(token, snapshot.uuid)
        self.client.force_authenticate(self.other)

        share.status = SharedQA.Status.HIDDEN
        share.save(update_fields=["status"])
        self.assertEqual(self.client.get(url).status_code, 404)

        share.status = SharedQA.Status.PUBLISHED
        share.save(update_fields=["status"])
        share.delete()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_snapshot_survives_source_run_and_session_deletion(self):
        self._add_run_files()
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        snapshot = share.files.get(kind=SharedQAFile.Kind.OUTPUT)
        url = self._public_file_url(token, snapshot.uuid)

        session = self.run.session
        self.run.delete()
        session.delete()
        share.refresh_from_db()
        self.assertIsNone(share.run)

        self.client.force_authenticate(self.other)
        metadata = self.client.get(f"/api/lens/public/qa/{token}/")
        file_response = self.client.get(url)
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(len(metadata.data["output_files"]), 1)
        self.assertEqual(file_response.status_code, 200)

    def test_legacy_share_snapshots_still_available_source_files(self):
        self._add_run_files()
        share = self._legacy_share("legacy-share")
        self.assertEqual(share.files.count(), 0)
        self.client.force_authenticate(self.other)

        resp = self.client.get("/api/lens/public/qa/legacy-share/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(share.files.count(), 2)
        self.assertEqual(len(resp.data["input_attachments"]), 1)
        self.assertEqual(len(resp.data["output_files"]), 1)

    def test_missing_legacy_file_does_not_break_text_snapshot(self):
        _, output, _, _ = self._add_run_files()
        share = self._legacy_share("legacy-missing-file")
        output.file.delete(save=False)
        self.client.force_authenticate(self.other)

        resp = self.client.get(
            "/api/lens/public/qa/legacy-missing-file/"
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["answer"], "X is a thing.")
        self.assertEqual(len(resp.data["input_attachments"]), 1)
        self.assertEqual(resp.data["output_files"], [])
        self.assertEqual(share.files.count(), 1)

    def test_deleting_share_purges_owned_snapshot_bytes(self):
        self._add_run_files()
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        snapshots = list(share.files.all())
        storage_names = [item.file.name for item in snapshots]
        storage = snapshots[0].file.storage
        self.assertTrue(all(storage.exists(name) for name in storage_names))

        share.delete()

        self.assertTrue(
            all(not storage.exists(name) for name in storage_names)
        )

    def test_share_is_idempotent_per_run(self):
        first = self._share()
        second = self._share()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["token"], second.data["token"])
        self.assertEqual(SharedQA.objects.filter(run=self.run).count(), 1)

    def test_share_rejects_unfinished_run(self):
        self.run.status = Run.Status.RUNNING
        self.run.save(update_fields=["status"])
        resp = self._share()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"], "RUN_NOT_SHAREABLE")

    def test_share_requires_run_ownership(self):
        resp = self._share(user=self.other)
        self.assertEqual(resp.status_code, 404)

    def test_public_assistant_single_allows_anonymous_and_authenticated_viewers(self):
        token = self._share().data["token"]
        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 200)
        self.assertEqual(anonymous.data["question"], "What is X?")

        self.client.force_authenticate(self.other)
        other = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.data["question"], "What is X?")

        self.client.force_authenticate(self.group_peer)
        peer = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(peer.status_code, 200)
        self.assertEqual(peer.data["question"], "What is X?")
        self.assertEqual(peer.data["view_count"], 3)

    def test_archiving_assistant_preserves_shared_qa_snapshot_access(self):
        token = self._share().data["token"]
        self.assistant.status = Assistant.Status.ARCHIVED
        self.assistant.save(update_fields=["status"])
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/lens/public/qa/{token}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["question"], "What is X?")

    def test_unlisted_single_allows_authenticated_session_user(self):
        token = self._share().data["token"]
        self.client.force_authenticate(user=None)
        self.assertTrue(
            self.client.login(username="qa-peer", password="pass12345")
        )

        resp = self.client.get(f"/api/lens/public/qa/{token}/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["question"], "What is X?")

    def test_unlisted_single_is_visible_to_owner(self):
        token = self._share().data["token"]
        self.client.force_authenticate(self.owner)
        resp = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(resp.status_code, 200)

    def test_listed_single_counts_views_for_anonymous_and_authenticated(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        share.is_listed = True
        share.save(update_fields=["is_listed"])

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 200)
        self.assertEqual(anonymous.data["answer"], "X is a thing.")
        self.assertEqual(anonymous.data["view_count"], 1)

        self.client.force_authenticate(self.other)
        resp = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["question"], "What is X?")
        self.assertEqual(resp.data["answer"], "X is a thing.")
        self.assertEqual(resp.data["view_count"], 2)
        self.assertNotIn("published_by", resp.data)
        again = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(again.data["view_count"], 3)

    def test_private_assistant_single_requires_assistant_access(self):
        token = self._private_share_token()

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.data["code"], "AUTHENTICATION_REQUIRED")

        self.client.force_authenticate(self.other)
        denied = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "ASSISTANT_ACCESS_DENIED")

        self.client.force_authenticate(self.admin)
        admin = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(admin.data["question"], "What is X?")

    def test_private_assistant_single_allows_user_and_group_grants(self):
        token = self._private_share_token()
        share = SharedQA.objects.select_related("assistant").get(token=token)
        AssistantAccess.objects.create(assistant=share.assistant, user=self.other)

        self.client.force_authenticate(self.other)
        user_grant = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(user_grant.status_code, 200)

        share.assistant.access_grants.all().delete()
        AssistantAccess.objects.create(assistant=share.assistant, group=self.group)
        self.client.force_authenticate(self.group_peer)
        group_grant = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(group_grant.status_code, 200)

    def test_private_assistant_listed_single_still_requires_assistant_access(self):
        token = self._private_share_token()
        share = SharedQA.objects.select_related("assistant").get(token=token)
        share.is_listed = True
        share.save(update_fields=["is_listed"])

        self.client.force_authenticate(self.other)
        denied = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "ASSISTANT_ACCESS_DENIED")

        self.client.force_authenticate(self.admin)
        authorized = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(authorized.status_code, 200)

    def test_orphaned_single_share_is_not_visible(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        share.assistant = None
        share.save(update_fields=["assistant"])

        self.client.force_authenticate(self.admin)
        resp = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(resp.status_code, 404)

    def test_hidden_single_returns_404(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        share.status = SharedQA.Status.HIDDEN
        share.save(update_fields=["status"])
        self.client.force_authenticate(user=None)
        resp = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(resp.status_code, 404)

    def test_missing_single_returns_404_for_anonymous_viewer(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/lens/public/qa/not-a-token/")
        self.assertEqual(resp.status_code, 404)

    def test_public_list_shows_only_listed(self):
        token = self._share().data["token"]
        url = "/api/lens/public/assistants/data-helper/qa/"
        self.client.force_authenticate(user=None)
        before = self.client.get(url)
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.data["total"], 0)

        self.client.force_authenticate(self.other)
        before_auth = self.client.get(url)
        self.assertEqual(before_auth.status_code, 200)
        self.assertEqual(before_auth.data["total"], 0)

        share = SharedQA.objects.get(token=token)
        self.client.force_authenticate(self.admin)
        patch = self.client.patch(
            f"/api/lens/admin/shares/{share.uuid}/",
            {"is_listed": True},
            format="json",
        )
        self.assertEqual(patch.status_code, 200)

        self.client.force_authenticate(self.other)
        after = self.client.get(url, HTTP_ACCEPT_LANGUAGE="zh-CN")
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.data["total"], 1)
        self.assertEqual(after.data["results"][0]["token"], token)
        self.assertEqual(
            after.data["results"][0]["content_language"], "zh-CN"
        )
        self.assertIn("answer_snippet", after.data["results"][0])

    def test_public_list_only_shows_viewer_language(self):
        chinese = self._legacy_share("chinese-share")
        chinese.content_language = "zh-CN"
        chinese.is_listed = True
        chinese.save(update_fields=["content_language", "is_listed"])
        english = self._legacy_share("english-share")
        english.content_language = "en-US"
        english.is_listed = True
        english.save(update_fields=["content_language", "is_listed"])
        unknown = self._legacy_share("unknown-share")
        unknown.is_listed = True
        unknown.save(update_fields=["is_listed"])
        url = "/api/lens/public/assistants/data-helper/qa/"
        self.client.force_authenticate(self.other)

        chinese_response = self.client.get(
            url,
            HTTP_ACCEPT_LANGUAGE="zh-CN",
        )
        english_response = self.client.get(
            url,
            HTTP_ACCEPT_LANGUAGE="en-US",
        )

        self.assertEqual(chinese_response.data["total"], 1)
        self.assertEqual(
            chinese_response.data["content_language"],
            "zh-CN",
        )
        self.assertEqual(
            chinese_response.data["results"][0]["token"],
            chinese.token,
        )
        self.assertEqual(english_response.data["total"], 1)
        self.assertEqual(
            english_response.data["content_language"],
            "en-US",
        )
        self.assertEqual(
            english_response.data["results"][0]["token"],
            english.token,
        )

    def test_public_list_does_not_leak_listed_private_assistant_share(self):
        token = self._private_share_token()
        share = SharedQA.objects.get(token=token)
        share.is_listed = True
        share.save(update_fields=["is_listed"])

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(
            "/api/lens/public/assistants/private-helper/qa/"
        )
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.data["code"], "AUTHENTICATION_REQUIRED")

        self.client.force_authenticate(self.other)
        denied = self.client.get("/api/lens/public/assistants/private-helper/qa/")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "ASSISTANT_ACCESS_DENIED")

        self.client.force_authenticate(self.admin)
        authorized = self.client.get(
            "/api/lens/public/assistants/private-helper/qa/",
            HTTP_ACCEPT_LANGUAGE="zh-CN",
        )
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.data["total"], 1)
        self.assertEqual(authorized.data["results"][0]["token"], token)

    def test_my_shares_list_and_revoke(self):
        token = self._share().data["token"]
        self.client.force_authenticate(self.owner)
        listing = self.client.get("/api/lens/shares/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(_results(listing.data)), 1)

        share = SharedQA.objects.get(token=token)
        deleted = self.client.delete(f"/api/lens/shares/{share.uuid}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(SharedQA.objects.filter(token=token).exists())
        public = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(public.status_code, 404)

    def test_my_shares_isolated_per_user(self):
        self._share()
        self.client.force_authenticate(self.other)
        listing = self.client.get("/api/lens/shares/")
        self.assertEqual(len(_results(listing.data)), 0)

    def test_moderation_requires_admin(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        self.client.force_authenticate(self.owner)
        detail = self.client.get(
            f"/api/lens/admin/shares/{share.uuid}/",
        )
        resp = self.client.patch(
            f"/api/lens/admin/shares/{share.uuid}/",
            {"is_listed": True},
            format="json",
        )
        self.assertEqual(detail.status_code, 403)
        self.assertEqual(resp.status_code, 403)

    def test_admin_detail_returns_complete_content_for_all_states(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        self.client.force_authenticate(self.admin)

        states = [
            (False, SharedQA.Status.PUBLISHED),
            (True, SharedQA.Status.PUBLISHED),
            (False, SharedQA.Status.HIDDEN),
            (True, SharedQA.Status.HIDDEN),
        ]
        for is_listed, share_status in states:
            with self.subTest(
                is_listed=is_listed,
                status=share_status,
            ):
                SharedQA.objects.filter(pk=share.pk).update(
                    is_listed=is_listed,
                    status=share_status,
                )
                before_views = share.view_count
                resp = self.client.get(
                    f"/api/lens/admin/shares/{share.uuid}/",
                )

                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.data["question"], "What is X?")
                self.assertEqual(resp.data["answer"], "X is a thing.")
                self.assertEqual(resp.data["published_by"], "qa-owner")
                self.assertEqual(resp.data["is_listed"], is_listed)
                self.assertEqual(resp.data["status"], share_status)
                share.refresh_from_db()
                self.assertEqual(share.view_count, before_views)

    def test_admin_list_omits_complete_content(self):
        self._share()
        self.client.force_authenticate(self.admin)

        resp = self.client.get("/api/lens/admin/shares/")

        self.assertEqual(resp.status_code, 200)
        row = _results(resp.data)[0]
        self.assertIn("answer_snippet", row)
        self.assertNotIn("question", row)
        self.assertNotIn("answer", row)

    def test_owner_can_rename_share(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            f"/api/lens/shares/{share.uuid}/",
            {"title": "Renamed Title"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["title"], "Renamed Title")
        share.refresh_from_db()
        self.assertEqual(share.title, "Renamed Title")

    def test_rename_requires_ownership(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            f"/api/lens/shares/{share.uuid}/",
            {"title": "Hacked"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def _set_global_policy(self, policy):
        """Store the deployment-wide share policy."""

        GlobalSetting.objects.update_or_create(
            key="lens.public_share.policy",
            defaults={"value": policy},
        )

    def _set_assistant_policy(self, policy, assistant=None):
        """Store the Assistant-level share policy."""

        target = assistant or self.assistant
        settings = dict(target.settings or {})
        settings["public_share"] = {"policy": policy}
        target.settings = settings
        target.save(update_fields=["settings"])

    def test_global_policy_requires_login_for_public_share(self):
        self._set_global_policy("login")
        token = self._share().data["token"]

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.data["code"], "AUTHENTICATION_REQUIRED")

        self.client.force_authenticate(self.other)
        signed_in = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(signed_in.status_code, 200)

    def test_assistant_policy_overrides_global_policy(self):
        self._set_global_policy("login")
        self._set_assistant_policy("public")
        token = self._share().data["token"]

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 200)

    def test_share_choice_applies_when_no_layer_is_configured(self):
        token = self._share(payload={"require_login": True}).data["token"]
        share = SharedQA.objects.get(token=token)
        self.assertIs(share.require_login, True)

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.data["code"], "AUTHENTICATION_REQUIRED")

    def test_share_choice_keeps_public_share_open(self):
        token = self._share(payload={"require_login": False}).data["token"]
        share = SharedQA.objects.get(token=token)
        self.assertIs(share.require_login, False)

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 200)

    def test_assistant_policy_wins_over_the_share_choice(self):
        self._set_assistant_policy("public")
        token = self._share(payload={"require_login": True}).data["token"]
        share = SharedQA.objects.get(token=token)
        self.assertIs(share.require_login, True)

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 200)

    def test_configured_policy_applies_to_a_share_without_a_choice(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)
        self.assertIsNone(share.require_login)

        self._set_global_policy("login")
        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.data["code"], "AUTHENTICATION_REQUIRED")

    def test_public_list_hides_shares_that_require_sign_in(self):
        token = self._share(payload={"require_login": True}).data["token"]
        share = SharedQA.objects.get(token=token)
        share.is_listed = True
        share.save(update_fields=["is_listed"])

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(
            "/api/lens/public/assistants/data-helper/qa/",
            HTTP_ACCEPT_LANGUAGE="zh-CN",
        )
        self.assertEqual(anonymous.status_code, 200)
        self.assertEqual(anonymous.data["total"], 0)
        self.assertEqual(anonymous.data["results"], [])

        self.client.force_authenticate(self.other)
        signed_in = self.client.get(
            "/api/lens/public/assistants/data-helper/qa/",
            HTTP_ACCEPT_LANGUAGE="zh-CN",
        )
        self.assertEqual(signed_in.status_code, 200)
        self.assertEqual(signed_in.data["total"], 1)
        self.assertEqual(signed_in.data["results"][0]["token"], token)

    def test_configured_policy_wins_over_the_share_choice(self):
        self._set_global_policy("login")
        token = self._share(payload={"require_login": False}).data["token"]
        share = SharedQA.objects.get(token=token)
        self.assertIs(share.require_login, False)

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.data["code"], "AUTHENTICATION_REQUIRED")

    def test_open_global_policy_reopens_a_share_that_required_login(self):
        token = self._share(payload={"require_login": True}).data["token"]
        share = SharedQA.objects.get(token=token)
        self.assertIs(share.require_login, True)

        self.client.force_authenticate(user=None)
        denied = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(denied.status_code, 403)

        self._set_global_policy("public")
        allowed = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(allowed.status_code, 200)

    def test_owner_can_change_the_access_mode_of_an_existing_share(self):
        token = self._share().data["token"]
        share = SharedQA.objects.get(token=token)

        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            f"/api/lens/shares/{share.uuid}/",
            {"require_login": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.data["require_login"], True)
        share.refresh_from_db()
        self.assertIs(share.require_login, True)

        self.client.force_authenticate(user=None)
        denied = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "AUTHENTICATION_REQUIRED")

        self.client.force_authenticate(self.owner)
        reopened = self.client.patch(
            f"/api/lens/shares/{share.uuid}/",
            {"require_login": False},
            format="json",
        )
        self.assertEqual(reopened.status_code, 200)
        self.assertIs(reopened.data["require_login"], False)

        self.client.force_authenticate(user=None)
        allowed = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(allowed.status_code, 200)

    def test_private_assistant_share_keeps_its_access_mode_unchanged(self):
        private_token = self._private_share_token()
        private_share = SharedQA.objects.get(token=private_token)

        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            f"/api/lens/shares/{private_share.uuid}/",
            {"require_login": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["require_login"])

    def test_private_assistant_never_offers_a_share_choice(self):
        private_assistant = Assistant.objects.create(
            name="Private Helper",
            slug="private-helper",
            lensnode=self.lensnode,
            selected_task="qa",
            status=Assistant.Status.ACTIVE,
            visibility=Assistant.Visibility.PRIVATE,
        )
        run = self._make_done_run(private_assistant)
        token = self._share(run=run, payload={"require_login": False}).data["token"]
        share = SharedQA.objects.get(token=token)
        self.assertIsNone(share.require_login)

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/lens/public/qa/{token}/")
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.data["code"], "AUTHENTICATION_REQUIRED")

    def test_policy_endpoint_reports_inherited_policy(self):
        self.client.force_authenticate(self.owner)
        url = f"/api/lens/shares/policy/?assistant_uuid={self.assistant.uuid}"

        inherited = self.client.get(url)
        self.assertEqual(inherited.status_code, 200)
        self.assertEqual(inherited.data["policy"], "inherit")
        self.assertTrue(inherited.data["requires_choice"])

        self._set_global_policy("login")
        configured = self.client.get(url)
        self.assertEqual(configured.data["policy"], "login")
        self.assertFalse(configured.data["requires_choice"])

        self._set_assistant_policy("public")
        overridden = self.client.get(url)
        self.assertEqual(overridden.data["policy"], "public")
        self.assertFalse(overridden.data["requires_choice"])

    def test_policy_endpoint_rejects_a_malformed_assistant_uuid(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/lens/shares/policy/?assistant_uuid=abc")
        self.assertEqual(resp.status_code, 404)

    def test_policy_endpoint_reports_the_deciding_layer(self):
        self.client.force_authenticate(self.owner)
        url = f"/api/lens/shares/policy/?assistant_uuid={self.assistant.uuid}"

        self._set_global_policy("login")
        global_source = self.client.get(url)
        self.assertEqual(global_source.data["policy"], "login")
        self.assertEqual(global_source.data["source"], "global")

        self._set_assistant_policy("inherit")
        inherit_source = self.client.get(url)
        self.assertEqual(inherit_source.data["policy"], "login")
        self.assertEqual(inherit_source.data["source"], "global")

        self._set_assistant_policy("public")
        assistant_source = self.client.get(url)
        self.assertEqual(assistant_source.data["policy"], "public")
        self.assertEqual(assistant_source.data["source"], "assistant")

    def test_policy_endpoint_requires_sign_in(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/lens/shares/policy/")
        self.assertEqual(resp.status_code, 401)
