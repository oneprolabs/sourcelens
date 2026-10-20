"""Run attachment listing and downloads respect scoped credentials."""

from datetime import timedelta
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AgentToken
from lens.models import Assistant, Message, Run, RunOutputFile, Session


class RunOutputFilesAPITests(TestCase):
    """Check the public contract using stored bytes and real credentials."""

    def setUp(self):
        """Isolate file storage and create a completed run with a draft."""

        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        storage = override_settings(
            STORAGES={
                **settings.STORAGES,
                "deliverables": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": media.name},
                },
            }
        )
        storage.enable()
        self.addCleanup(storage.disable)
        self.owner = get_user_model().objects.create_user(username="run-file-owner")
        self.staff = get_user_model().objects.create_user(username="run-file-staff", is_staff=True)
        assistant = Assistant.objects.create(name="Report", slug="run-files-api")
        self.session = Session.objects.create(user=self.owner, assistant=assistant)
        question = Message.objects.create(session=self.session, role="user", sequence=1, content="Report")
        self.answer = Message.objects.create(session=self.session, role="assistant", sequence=2, content="Ready")
        self.run = Run.objects.create(
            session=self.session, input_message=question, output_message=self.answer, status=Run.Status.DONE
        )
        self.published = self.make_file("报告.txt", self.answer)
        self.draft = self.make_file("draft.txt", None)
        self.list_url = f"/api/lens/runs/{self.run.uuid}/output-files/"
        self.download_url = f"/api/lens/output-files/{self.published.uuid}/"

    def make_file(self, filename, message):
        """Persist a deliverable with a chosen publication state."""

        output = RunOutputFile(
            run=self.run,
            session=self.session,
            assistant=self.session.assistant,
            message=message,
            filename=filename,
            content_type="text/plain",
            byte_size=6,
        )
        output.file.save(filename, ContentFile(b"report"), save=False)
        output.save()
        self.addCleanup(output.file.delete, save=False)
        return output

    def client_for(self, user, credential="permanent"):
        """Authenticate with permanent, finite, legacy, or web credentials."""

        client = APIClient()
        if credential in {"permanent", "finite"}:
            expires_at = timezone.now() + timedelta(days=1) if credential == "finite" else None
            _, token = AgentToken.issue(user=user, expires_at=expires_at)
        else:
            token = AccessToken.for_user(user)
            if credential == "legacy":
                token["scope"] = "agent"
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def test_list_matches_run_metadata_for_supported_credentials(self):
        """All supported callers discover final files without storage paths."""

        for credential in ("permanent", "finite", "legacy", "web"):
            with self.subTest(credential=credential):
                client = self.client_for(self.owner, credential)
                response = client.get(self.list_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["count"], 1)
                files = response.data["results"]
                detail = client.get(f"/api/lens/runs/{self.run.uuid}/")
                self.assertEqual(files, detail.data["output_files"])
                self.assertEqual(files[0]["filename"], "报告.txt")
                self.assertEqual(files[0]["url"], self.download_url)
                self.assertEqual(files[0]["status"], "published")
                self.assertEqual(files[0]["byte_size"], 6)
                self.assertEqual(files[0]["content_type"], "text/plain")
                self.assertNotIn("file", files[0])

    def test_list_paginates_published_files(self):
        """Pagination does not include unpublished candidates."""

        second = self.make_file("second.txt", self.answer)
        client = self.client_for(self.owner)
        first_page = client.get(self.list_url, {"page_size": 1})
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.data["count"], 2)
        self.assertIsNotNone(first_page.data["next"])
        self.assertEqual(first_page.data["results"][0]["uuid"], str(self.published.uuid))
        second_page = client.get(self.list_url, {"page_size": 1, "page": 2})
        self.assertEqual(second_page.data["results"][0]["uuid"], str(second.uuid))
        self.assertIsNone(second_page.data["next"])

    def test_no_final_answer_has_empty_list(self):
        """Candidate files remain private before a final answer is published."""

        self.run.output_message = None
        self.run.status = Run.Status.RUNNING
        self.run.save(update_fields=["output_message", "status"])
        response = self.client_for(self.owner).get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_other_users_cannot_list_run_files_even_when_staff(self):
        """Run ownership is required for both web and agent callers."""

        for credential in ("permanent", "legacy", "web"):
            with self.subTest(credential=credential):
                response = self.client_for(self.staff, credential).get(self.list_url)
                self.assertEqual(response.status_code, 404)

    def test_staff_agent_cannot_download_other_users_files_or_own_drafts(self):
        """Agent scope never inherits the staff download audit bypass."""

        for credential in ("permanent", "legacy"):
            with self.subTest(credential=credential):
                client = self.client_for(self.staff, credential)
                self.assertEqual(client.get(self.download_url).status_code, 403)
        self.session.user = self.staff
        self.session.save(update_fields=["user"])
        for credential in ("permanent", "legacy"):
            with self.subTest(credential=credential):
                client = self.client_for(self.staff, credential)
                self.assertEqual(client.get(f"/api/lens/output-files/{self.draft.uuid}/").status_code, 404)
                response = client.get(self.download_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cache-Control"], "private, no-store")
                self.assertEqual(b"".join(response.streaming_content), b"report")

    def test_web_staff_retains_download_audit_access(self):
        """Ordinary staff login can still inspect published and draft files."""

        client = self.client_for(self.staff, "web")
        for output in (self.published, self.draft):
            response = client.get(f"/api/lens/output-files/{output.uuid}/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertEqual(b"".join(response.streaming_content), b"report")

    def test_revoked_token_cannot_list_or_download(self):
        """Revocation is checked again for each attachment request."""

        record, token = AgentToken.issue(user=self.owner, expires_at=None)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        record.revoked_at = timezone.now()
        record.save(update_fields=["revoked_at"])
        for url in (self.list_url, self.download_url):
            self.assertEqual(client.get(url).status_code, 401)

    def test_list_requires_authentication_and_is_read_only(self):
        """Allowlisting GET does not grant write operations or public access."""

        self.assertEqual(APIClient().get(self.list_url).status_code, 401)
        client = self.client_for(self.owner)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(self.list_url)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data["detail"], "AGENT_TOKEN_SCOPE_RESTRICTED")
