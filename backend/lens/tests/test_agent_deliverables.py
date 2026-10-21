"""Agent clients can discover and download only published, authorized files."""

from tempfile import TemporaryDirectory
from unittest.mock import patch

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

    def add_citation(self):
        """Attach captured evidence without changing the stored response later."""

        self.message.content = "See docs/guide.md. [[source: docs/guide.md:1-2]]\n\nDone."
        self.message.save(update_fields=["content"])
        self.run.citations = [{
            "id": "src-1", "path": "docs/guide.md", "start_line": 1, "end_line": 2,
            "source": "Guide\nDetails", "revision": "working-tree", "supports": "Read while answering",
        }]
        self.run.save(update_fields=["citations"])

    def test_agent_polling_hides_citations_for_managed_and_legacy_tokens(self):
        """Agent-kit answers omit markers and metadata, preserving stored evidence."""

        self.add_citation()
        web = APIClient()
        web.force_authenticate(self.user)
        issued = web.post("/api/v1/auth/agent/token", {}, format="json")
        self.assertEqual(issued.status_code, 200)
        managed = APIClient()
        managed.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.data['access']}")
        for client in (self.client, managed):
            with self.subTest(client=client):
                response = client.get(f"/api/lens/runs/{self.run.uuid}/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["answer"], "See docs/guide.md.\n\nDone.")
                self.assertEqual(response.data["citations"], [])
        self.message.refresh_from_db()
        self.run.refresh_from_db()
        self.assertIn("[[source:", self.message.content)
        self.assertEqual(len(self.run.citations), 1)

    def test_web_polling_retains_citations_even_for_api_request_source(self):
        """Presentation follows authenticated scope, not caller-supplied labels."""

        self.add_citation()
        web = APIClient()
        web.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")
        response = web.get(f"/api/lens/runs/{self.run.uuid}/?request_source=cli")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["answer"], self.message.content)
        self.assertEqual(response.data["citations"][0]["id"], "src-1")

    def test_agent_answer_strips_markers_before_truncating(self):
        """The answer limit cannot expose the beginning of a citation marker."""

        self.message.content = "x" * 49_995 + " [[SOURCE: docs/guide.md:1-2]] tail"
        self.message.save(update_fields=["content"])
        response = self.client.get(f"/api/lens/runs/{self.run.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["answer"], "x" * 49_995 + " tail")

    def test_agent_creation_response_hides_citations_on_reused_run(self):
        """An idempotent creation response applies the same scope as polling."""

        self.add_citation()
        with (
            patch("lens.views.sessions._session_assistant_is_runnable", return_value=True),
            patch("lens.views.sessions.RunCreateSerializer.save", return_value=self.run),
        ):
            response = self.client.post(
                f"/api/lens/sessions/{self.session.uuid}/runs/", {"question": "Report"}, format="json"
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["answer"], "See docs/guide.md.\n\nDone.")
        self.assertEqual(response.data["citations"], [])

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
