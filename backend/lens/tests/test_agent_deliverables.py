"""Agent clients can discover and download only published, authorized files."""

from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from lens.models import Assistant, Message, Run, RunOutputFile, Session


class AgentDeliverableTests(TestCase):
    """Exercise metadata and private downloads with real agent credentials."""

    def setUp(self):
        """Create an owner, a final response, and isolated deliverable storage."""

        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        storage_settings = override_settings(
            STORAGES={
                **settings.STORAGES,
                "deliverables": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": media.name},
                },
            }
        )
        storage_settings.enable()
        self.addCleanup(storage_settings.disable)
        self.user = get_user_model().objects.create_user(username="agent-file-owner")
        self.assistant = Assistant.objects.create(name="Files", slug="agent-files")
        self.session = Session.objects.create(assistant=self.assistant, user=self.user)
        question = Message.objects.create(session=self.session, role="user", sequence=1, content="Report")
        self.message = Message.objects.create(session=self.session, role="assistant", sequence=2, content="Ready")
        self.run = Run.objects.create(
            session=self.session, input_message=question, output_message=self.message, status=Run.Status.DONE
        )
        self.message.run = self.run
        self.message.save(update_fields=["run"])
        self.published = self.make_file("report.txt", self.message)
        self.draft = self.make_file("rejected.txt", None)
        self.client = self.agent_client(self.user)

    def make_file(self, filename, message):
        """Persist actual file bytes with an explicit publication link."""

        output = RunOutputFile(
            run=self.run, session=self.session, assistant=self.assistant, message=message,
            filename=filename, content_type="text/plain", byte_size=6,
        )
        output.file.save(filename, ContentFile(b"report"), save=False)
        output.save()
        self.addCleanup(output.file.delete, save=False)
        return output

    @staticmethod
    def agent_client(user):
        """Use the legacy scoped JWT, supported alongside managed tokens."""

        token = AccessToken.for_user(user)
        token["scope"] = "agent"
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def test_run_lists_only_published_files_with_download_metadata(self):
        """Polling a Run exposes final attachments without leaking drafts."""

        response = self.client.get(f"/api/lens/runs/{self.run.uuid}/")
        self.assertEqual(response.status_code, 200)
        files = response.data["output_files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(str(files[0]["uuid"]), str(self.published.uuid))
        self.assertEqual(files[0]["filename"], "report.txt")
        self.assertEqual(files[0]["status"], "published")
        self.assertEqual(files[0]["url"], f"/api/lens/output-files/{self.published.uuid}/")
        self.assertEqual(files[0]["byte_size"], 6)
        self.assertNotIn("file", files[0])

    def test_run_without_output_message_returns_empty_files(self):
        """Pending runs have a stable empty attachment collection."""

        self.run.output_message = None
        self.run.status = Run.Status.RUNNING
        self.run.save(update_fields=["output_message", "status"])
        response = self.client.get(f"/api/lens/runs/{self.run.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["output_files"], [])

    def test_agent_can_download_published_bytes_but_not_drafts(self):
        """The route allowlist preserves the download view's publication check."""

        response = self.client.get(f"/api/lens/output-files/{self.published.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"report")
        self.assertEqual(self.client.get(f"/api/lens/output-files/{self.draft.uuid}/").status_code, 404)

    def test_current_minted_agent_credential_can_download(self):
        """Credentials from the settings endpoint use the same download scope."""

        web = APIClient()
        web.force_authenticate(self.user)
        issued = web.post("/api/v1/auth/agent/token", {}, format="json")
        self.assertEqual(issued.status_code, 200)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.data['access']}")
        response = client.get(f"/api/lens/output-files/{self.published.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"report")

    def test_other_user_cannot_discover_or_download_owner_files(self):
        """Allowlisting file reads does not grant access to another session."""

        other = get_user_model().objects.create_user(username="agent-file-other")
        client = self.agent_client(other)
        self.assertEqual(client.get(f"/api/lens/runs/{self.run.uuid}/").status_code, 404)
        self.assertEqual(client.get(f"/api/lens/output-files/{self.published.uuid}/").status_code, 403)

    def test_download_route_remains_read_only_and_requires_authentication(self):
        """The new scope cannot write files or expose them anonymously."""

        url = f"/api/lens/output-files/{self.published.uuid}/"
        self.assertEqual(APIClient().get(url).status_code, 401)
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.data["detail"], "AGENT_TOKEN_SCOPE_RESTRICTED")
