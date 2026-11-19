"""Tests for transient document attachments backed by Redis metadata."""

import io
import tempfile
import uuid
import zipfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import default_storage, storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from lens.attachments import store_message_attachment
from lens.document_attachments import (
    DocumentAttachmentError,
    bind_document_attachments_to_run,
    cleanup_expired_document_files,
    delete_document_attachment,
    get_document_attachment,
    get_run_document_attachments,
    get_run_document_expectation,
    store_document_attachment,
)
from lens.execution import execute_answer_run
from lens.lensnode_auth import issue_lensnode_token
from lens.models import (
    Assistant,
    LensNode,
    MessageAttachment,
    Run,
    RunExecution,
    Session,
)
from lens.serializers import AssistantSerializer, MessageSerializer
from lens.services import (
    LensNodeDispatchError,
    create_execution_run,
    dispatch_run_to_lensnode,
    finish_lensnode_run,
)
from lens.tasks import execute_answer_run as execute_answer_run_task
from PIL import Image
from pypdf import PdfWriter
from rest_framework.test import APIClient

User = get_user_model()

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "document-attachment-tests",
    }
}
TEST_MEDIA_ROOT = tempfile.mkdtemp()
TEST_DOCUMENT_ATTACHMENT_ROOT = tempfile.mkdtemp()
TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": TEST_MEDIA_ROOT},
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
    "document_attachments": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": TEST_DOCUMENT_ATTACHMENT_ROOT},
    },
}


def _pdf_upload(name="tender.pdf", page_count=1):
    """Return a small structurally valid PDF upload."""

    buffer = io.BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type="application/pdf",
    )


def _png_upload(name="diagram.png"):
    """Return a small valid image upload."""

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (120, 200, 80)).save(
        buffer,
        format="PNG",
    )
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type="image/png",
    )


def _docx_upload(name="requirements.docx"):
    """Return a minimal OOXML ZIP with the DOCX package marker."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )


def _text_upload(
    name="notes.txt",
    content="Production incident details",
):
    """Return one UTF-8 plain-text or Markdown upload."""

    content_type = "text/markdown" if name.endswith(".md") else "text/plain"
    return SimpleUploadedFile(
        name,
        content.encode("utf-8"),
        content_type=content_type,
    )


def _compressed_docx_upload(name="compressed.docx"):
    """Return an OOXML archive with an unsafe expansion ratio."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", b"A" * (2 * 1024 * 1024))
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )


@override_settings(
    CACHES=TEST_CACHES,
    STORAGES=TEST_STORAGES,
    DOCUMENT_ATTACHMENT_TTL_SECONDS=86400,
)
class DocumentAttachmentTests(TestCase):
    """Exercise the Redis metadata and shared-file lifecycle."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="document-user",
            email="document-user@example.com",
            password="pass12345",
        )
        self.lensnode = LensNode.objects.create(
            name="Local LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            labels={"run_document_attachments": True},
        )
        self.assistant = Assistant.objects.create(
            name="Document Advisor",
            slug="document-advisor",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[{"path": "/workspace/reference"}],
            visibility=Assistant.Visibility.PUBLIC,
        )
        self.session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        self.client = APIClient()

    def test_store_uses_cache_metadata_without_database_row(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )

        cached = get_document_attachment(metadata["uuid"])
        self.assertEqual(cached["kind"], "document")
        self.assertEqual(cached["session_uuid"], str(self.session.uuid))
        self.assertEqual(cached["original_name"], "tender.pdf")
        self.assertEqual(len(cached["content_hash"]), 64)
        document_storage = storages["document_attachments"]
        self.assertTrue(document_storage.exists(cached["storage_name"]))
        self.assertFalse(default_storage.exists(cached["storage_name"]))
        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_upload_accepts_utf8_text_and_markdown_documents(self):
        self.client.force_authenticate(self.user)

        for name, expected_mime in (
            ("incident.txt", "text/plain"),
            ("analysis.md", "text/markdown"),
        ):
            with self.subTest(name=name):
                response = self.client.post(
                    (
                        f"/api/lens/sessions/{self.session.uuid}/"
                        "attachments/"
                    ),
                    {"file": _text_upload(name)},
                    format="multipart",
                )

                self.assertEqual(response.status_code, 201, response.content)
                self.assertEqual(response.data["kind"], "document")
                self.assertEqual(response.data["mime_type"], expected_mime)

    def test_upload_rejects_non_utf8_text_document(self):
        self.client.force_authenticate(self.user)
        upload = SimpleUploadedFile(
            "binary.txt",
            b"\xff\xfe\x00\x01",
            content_type="text/plain",
        )

        response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/attachments/",
            {"file": upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("ATTACHMENT_UNSUPPORTED_TYPE", str(response.data))

    def test_store_validates_document_before_locking_assistant(self):
        events = []

        def validate_document(*args):
            events.append("validate")
            return True

        def lock_assistant(*args):
            events.append("lock")

        with (
            patch(
                "lens.document_attachments._valid_document_bytes",
                side_effect=validate_document,
            ),
            patch(
                "lens.document_attachments.lock_assistant_for_new_work",
                side_effect=lock_assistant,
            ),
        ):
            metadata = store_document_attachment(
                self.session,
                self.user,
                _pdf_upload(),
            )

        self.assertEqual(events, ["validate", "lock"])
        delete_document_attachment(metadata["uuid"])

    def test_upload_rejects_lensnode_without_document_capability(self):
        self.lensnode.labels = {}
        self.lensnode.save(update_fields=["labels"])
        self.client.force_authenticate(self.user)

        with patch(
            "lens.views.sessions.store_document_attachment"
        ) as store_document:
            response = self.client.post(
                f"/api/lens/sessions/{self.session.uuid}/attachments/",
                {"file": _pdf_upload()},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "DOCUMENT_ATTACHMENTS_UNSUPPORTED_BY_LENSNODE",
            str(response.data),
        )
        store_document.assert_not_called()

    def test_archived_session_rejects_document_upload(self):
        self.session.status = Session.Status.ARCHIVED
        self.session.save(update_fields=["status"])
        self.client.force_authenticate(self.user)

        with patch(
            "lens.document_attachments.document_attachment_storage"
        ) as attachment_storage:
            response = self.client.post(
                f"/api/lens/sessions/{self.session.uuid}/attachments/",
                {"file": _pdf_upload()},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("SESSION_ARCHIVED", str(response.data))
        attachment_storage.assert_not_called()

    def test_assistant_payload_reports_document_capability(self):
        payload = AssistantSerializer(self.assistant).data

        self.assertIs(payload["supports_document_attachments"], True)

        self.lensnode.labels = {}
        self.lensnode.save(update_fields=["labels"])
        payload = AssistantSerializer(self.assistant).data

        self.assertIs(payload["supports_document_attachments"], False)

    def test_unbound_assistant_reports_dynamic_document_capability(self):
        self.assistant.lensnode = None
        self.assistant.save(update_fields=["lensnode"])
        self.lensnode.tasks = [{"name": self.assistant.capability}]
        self.lensnode.save(update_fields=["tasks"])

        payload = AssistantSerializer(self.assistant).data

        self.assertIs(payload["supports_document_attachments"], True)

    def test_unbound_assistant_routes_oversized_text_to_capable_node(self):
        self.assistant.lensnode = None
        self.assistant.save(update_fields=["lensnode"])
        self.lensnode.tasks = [{"name": self.assistant.capability}]
        self.lensnode.labels = {}
        self.lensnode.save(update_fields=["labels", "tasks"])
        capable_node = LensNode.objects.create(
            name="Document-capable LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            tasks=[{"name": self.assistant.capability}],
            labels={"run_document_attachments": True},
        )
        self.client.force_authenticate(self.user)

        upload = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/attachments/",
            {"file": _text_upload(content="x" * 8001)},
            format="multipart",
        )

        self.assertEqual(upload.status_code, 201, upload.content)
        self.assertEqual(upload.data["kind"], "document")
        self.assertEqual(upload.data["byte_size"], 8001)

        response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/runs/",
            {
                "question": "",
                "enqueue": False,
                "attachment_uuids": [upload.data["uuid"]],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        run = self.session.run_set.get(uuid=response.data["uuid"])
        self.assertEqual(run.lensnode, capable_node)

    def test_store_rejects_spoofed_document_extension(self):
        upload = SimpleUploadedFile(
            "tender.pdf",
            b"not a pdf",
            content_type="application/pdf",
        )

        with self.assertRaisesRegex(
            DocumentAttachmentError,
            "ATTACHMENT_UNSUPPORTED_TYPE",
        ):
            store_document_attachment(self.session, self.user, upload)

    def test_store_rejects_malformed_pdf_with_valid_header(self):
        upload = SimpleUploadedFile(
            "tender.pdf",
            b"%PDF-not-a-document",
            content_type="application/pdf",
        )

        with self.assertRaisesRegex(
            DocumentAttachmentError,
            "ATTACHMENT_UNSUPPORTED_TYPE",
        ):
            store_document_attachment(self.session, self.user, upload)

    def test_store_rejects_pdf_over_page_limit(self):
        with (
            patch("lens.document_attachments.DOCUMENT_PDF_MAX_PAGES", 1),
            self.assertRaisesRegex(
                DocumentAttachmentError,
                "ATTACHMENT_UNSUPPORTED_TYPE",
            ),
        ):
            store_document_attachment(
                self.session,
                self.user,
                _pdf_upload(page_count=2),
            )

    def test_store_rejects_pdf_over_object_limit(self):
        with (
            patch("lens.document_attachments.DOCUMENT_PDF_MAX_OBJECTS", 3),
            self.assertRaisesRegex(
                DocumentAttachmentError,
                "ATTACHMENT_UNSUPPORTED_TYPE",
            ),
        ):
            store_document_attachment(
                self.session,
                self.user,
                _pdf_upload(),
            )

    def test_store_rejects_ooxml_with_unsafe_expansion_ratio(self):
        with self.assertRaisesRegex(
            DocumentAttachmentError,
            "ATTACHMENT_UNSUPPORTED_TYPE",
        ):
            store_document_attachment(
                self.session,
                self.user,
                _compressed_docx_upload(),
            )

    def test_store_rejects_ooxml_without_canonical_main_part(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/not-document.xml", "<document />")
        upload = SimpleUploadedFile(
            "requirements.docx",
            buffer.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )

        with self.assertRaisesRegex(
            DocumentAttachmentError,
            "ATTACHMENT_UNSUPPORTED_TYPE",
        ):
            store_document_attachment(self.session, self.user, upload)

    def test_store_rejects_ooxml_with_corrupt_member_crc(self):
        upload = _docx_upload()
        data = bytearray(upload.read())
        offset = data.index(b"<document />")
        data[offset] ^= 1
        corrupted = SimpleUploadedFile(
            "requirements.docx",
            bytes(data),
            content_type=upload.content_type,
        )

        with self.assertRaisesRegex(
            DocumentAttachmentError,
            "ATTACHMENT_UNSUPPORTED_TYPE",
        ):
            store_document_attachment(self.session, self.user, corrupted)

    def test_bind_scopes_document_to_one_run(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _docx_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
        )

        bound = bind_document_attachments_to_run(
            self.session,
            run,
            [metadata["uuid"]],
        )

        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]["run_uuid"], str(run.uuid))
        self.assertEqual(
            get_run_document_attachments(run.uuid)[0]["uuid"],
            metadata["uuid"],
        )
        run.input_message.refresh_from_db()
        serialized = MessageSerializer(run.input_message).data
        self.assertEqual(serialized["attachments"][0]["kind"], "document")

    def test_run_index_uses_latest_document_expiry(self):
        first = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload("first.pdf"),
        )
        second = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload("second.pdf"),
        )
        first["expires_at"] = (
            timezone.now() + timedelta(minutes=5)
        ).isoformat()
        second["expires_at"] = (
            timezone.now() + timedelta(hours=2)
        ).isoformat()
        cache.set(f"lens:document_attachment:{first['uuid']}", first, 300)
        cache.set(f"lens:document_attachment:{second['uuid']}", second, 7200)
        run = create_execution_run(
            session=self.session,
            question="Compare them",
            enqueue=False,
        )

        with patch(
            "lens.document_attachments.cache.set_many",
            wraps=cache.set_many,
        ) as set_many:
            bind_document_attachments_to_run(
                self.session,
                run,
                [first["uuid"], second["uuid"]],
            )

        timeout = set_many.call_args.kwargs["timeout"]
        self.assertGreater(timeout, 60 * 60)
        cache.delete(f"lens:document_attachment:{first['uuid']}")
        self.assertEqual(
            [item["uuid"] for item in get_run_document_attachments(run.uuid)],
            [second["uuid"]],
        )

    def test_document_can_be_reused_by_another_run_in_same_session(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        first = create_execution_run(
            session=self.session,
            question="First",
            enqueue=False,
        )
        second = create_execution_run(
            session=self.session,
            question="Second",
            enqueue=False,
        )
        bind_document_attachments_to_run(
            self.session,
            first,
            [metadata["uuid"]],
        )

        rebound = bind_document_attachments_to_run(
            self.session,
            second,
            [metadata["uuid"]],
        )

        self.assertEqual(len(rebound), 1)
        self.assertEqual(
            get_run_document_attachments(second.uuid)[0]["uuid"],
            metadata["uuid"],
        )
        self.assertEqual(
            get_run_document_attachments(first.uuid)[0]["uuid"],
            metadata["uuid"],
        )

    def test_follow_up_reuses_previous_document(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload("contract.pdf"),
        )
        create_execution_run(
            session=self.session,
            question="Summarize this PDF",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )
        run = create_execution_run(
            session=self.session,
            question="What is the deadline in the previous PDF?",
            enqueue=False,
        )

        self.assertEqual(
            get_run_document_attachments(run.uuid)[0]["uuid"],
            metadata["uuid"],
        )

    def test_delete_removes_cache_metadata_and_original_file(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )

        deleted = delete_document_attachment(
            metadata["uuid"],
            session_uuid=self.session.uuid,
            user_id=self.user.id,
        )

        self.assertTrue(deleted)
        self.assertIsNone(get_document_attachment(metadata["uuid"]))
        self.assertEqual(
            cache.get(
                f"lens:session_document_attachments:{self.session.uuid}"
            ),
            None,
        )
        self.assertFalse(
            storages["document_attachments"].exists(metadata["storage_name"])
        )

    def test_user_quota_is_enforced_before_writing_another_file(self):
        storage = storages["document_attachments"]
        with patch(
            "lens.document_attachments.DOCUMENT_ATTACHMENT_MAX_PER_USER",
            1,
        ):
            store_document_attachment(
                self.session,
                self.user,
                _pdf_upload("first.pdf"),
            )
            with (
                patch.object(storage, "save", wraps=storage.save) as save,
                self.assertRaisesRegex(
                    DocumentAttachmentError,
                    "ATTACHMENT_TOO_MANY",
                ),
            ):
                store_document_attachment(
                    self.session,
                    self.user,
                    _pdf_upload("second.pdf"),
                )

        save.assert_not_called()

    def test_deleting_document_releases_user_quota(self):
        with patch(
            "lens.document_attachments.DOCUMENT_ATTACHMENT_MAX_PER_USER",
            1,
        ):
            first = store_document_attachment(
                self.session,
                self.user,
                _pdf_upload("first.pdf"),
            )
            delete_document_attachment(first["uuid"])
            second = store_document_attachment(
                self.session,
                self.user,
                _pdf_upload("second.pdf"),
            )

        self.assertEqual(second["original_name"], "second.pdf")

    def test_legacy_single_argument_task_seeds_zero_document_expectation(self):
        run = create_execution_run(
            session=self.session,
            question="Legacy question",
            enqueue=False,
        )
        cache.delete(
            f"lens:run_document_attachment_expectation:{run.uuid}"
        )

        with patch("lens.execution.execute_answer_run"):
            execute_answer_run_task.run(str(run.uuid))

        self.assertEqual(get_run_document_expectation(run.uuid), 0)

    def test_single_argument_task_reads_document_count_header(self):
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
        )
        cache.delete(
            f"lens:run_document_attachment_expectation:{run.uuid}"
        )

        with patch("lens.execution.execute_answer_run") as execute:
            execute_answer_run_task.apply(
                args=[str(run.uuid)],
                headers={"sourcelens_expected_document_count": 1},
            ).get()

        execute.assert_called_once_with(
            run,
            expected_document_count=1,
        )
        self.assertEqual(get_run_document_expectation(run.uuid), 1)

    def test_cleanup_removes_files_older_than_fixed_ttl(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        future = timezone.now() + timedelta(days=2)

        deleted = cleanup_expired_document_files(now=future)

        self.assertGreaterEqual(deleted, 1)
        self.assertFalse(
            storages["document_attachments"].exists(metadata["storage_name"])
        )

    def test_upload_and_run_dispatch_without_database_attachment(self):
        self.client.force_authenticate(self.user)
        upload = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/attachments/",
            {"file": _pdf_upload()},
            format="multipart",
        )
        self.assertEqual(upload.status_code, 201, upload.content)
        self.assertEqual(upload.data["kind"], "document")
        self.assertEqual(MessageAttachment.objects.count(), 0)

        run_response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/runs/",
            {
                "question": "",
                "enqueue": False,
                "attachment_uuids": [upload.data["uuid"]],
            },
            format="json",
        )
        self.assertEqual(run_response.status_code, 201, run_response.content)

        run = self.session.run_set.get(uuid=run_response.data["uuid"])
        with (
            patch("lens.services.get_channel_layer"),
            patch("lens.services.async_to_sync") as async_to_sync,
        ):
            sender = async_to_sync.return_value
            dispatch_run_to_lensnode(run, "Analyze the document")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(
            payload["subject_documents"][0]["uuid"],
            upload.data["uuid"],
        )
        self.assertEqual(
            payload["subject_documents"][0]["original_name"],
            "tender.pdf",
        )
        self.assertEqual(payload["answer_language"], "zh-CN")

    def test_dispatch_includes_features_from_assistant_settings(self):
        self.assistant.settings = {"features": {"codegraph": False}}
        self.assistant.save(update_fields=["settings"])
        run = create_execution_run(
            session=self.session,
            question="Analyze the codebase",
            enqueue=False,
        )

        with (
            patch("lens.services.get_channel_layer"),
            patch("lens.services.async_to_sync") as async_to_sync,
        ):
            sender = async_to_sync.return_value
            dispatch_run_to_lensnode(run, "Analyze the codebase")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(payload["features"], {"codegraph": False})

    def test_dispatch_features_default_to_empty_when_unset(self):
        run = create_execution_run(
            session=self.session,
            question="Analyze the codebase",
            enqueue=False,
        )

        with (
            patch("lens.services.get_channel_layer"),
            patch("lens.services.async_to_sync") as async_to_sync,
        ):
            sender = async_to_sync.return_value
            dispatch_run_to_lensnode(run, "Analyze the codebase")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(payload["features"], {})

    def test_document_only_run_dispatches_with_analysis_prompt(self):
        self.user.profile.language = "zh-CN"
        self.user.profile.save(update_fields=["language"])
        self.assistant.preprocess_model_ref = uuid.uuid4()
        self.assistant.save(update_fields=["preprocess_model_ref"])
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )

        with (
            patch("lens.execution.rewrite_query") as rewrite_query,
            patch("lens.execution.validate_run_dispatch"),
            patch("lens.execution.dispatch_run_to_lensnode") as dispatch,
        ):
            execute_answer_run(run, dispatch=True)

        rewrite_query.assert_not_called()
        self.assertEqual(dispatch.call_args.args[1], "请分析所附文档")
        run.execution.refresh_from_db()
        self.assertEqual(
            run.execution.status,
            RunExecution.Status.DISPATCHED,
        )

    def test_english_document_only_run_uses_english_prompt(self):
        self.user.profile.language = "en-US"
        self.user.profile.save(update_fields=["language"])
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )

        with (
            patch("lens.execution.validate_run_dispatch"),
            patch("lens.execution.dispatch_run_to_lensnode") as dispatch,
        ):
            execute_answer_run(run, dispatch=True)

        self.assertEqual(
            dispatch.call_args.args[1],
            "Analyze the attached document.",
        )

    def test_run_pdf_lists_transient_document(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )
        run.status = Run.Status.DONE
        run.output_message.content = "Done"
        run.output_message.save(update_fields=["content"])
        run.save(update_fields=["status"])
        self.client.force_authenticate(self.user)

        with patch(
            "lens.views.sessions.render_qa_pdf",
            return_value=b"pdf",
        ) as render_pdf:
            response = self.client.get(f"/api/lens/runs/{run.uuid}/pdf/")

        self.assertEqual(response.status_code, 200)
        input_files = render_pdf.call_args.kwargs["input_files"]
        self.assertEqual(
            [item["original_name"] for item in input_files],
            ["tender.pdf"],
        )

    def test_dispatch_fails_when_expected_document_metadata_is_missing(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )
        delete_document_attachment(metadata["uuid"])

        with (
            patch("lens.execution.validate_run_dispatch"),
            patch("lens.execution.dispatch_run_to_lensnode") as dispatch,
            self.assertRaisesRegex(
                LensNodeDispatchError,
                "DOCUMENT_ATTACHMENT_UNAVAILABLE",
            ),
        ):
            execute_answer_run(
                run,
                dispatch=True,
                expected_document_count=1,
            )

        dispatch.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.FAILED)

    def test_enqueued_run_carries_expected_document_count(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )

        with (
            patch("lens.services._enqueue_answer_run") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            run = create_execution_run(
                session=self.session,
                question="Analyze it",
                enqueue=True,
                attachment_uuids=[metadata["uuid"]],
            )

        enqueue.assert_called_once_with(run.uuid, 1)
        self.assertEqual(get_run_document_expectation(run.uuid), 1)

    def test_busy_requeue_restores_expected_document_count(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )
        run.status = Run.Status.RUNNING
        run.save(update_fields=["status"])

        with patch("lens.tasks.execute_answer_run.apply_async") as apply_async:
            finish_lensnode_run(
                run.uuid,
                Run.Status.FAILED,
                error="LENSNODE_BUSY",
            )

        self.assertEqual(
            apply_async.call_args.kwargs["args"],
            [str(run.uuid)],
        )
        self.assertEqual(
            apply_async.call_args.kwargs["headers"],
            {"sourcelens_expected_document_count": 1},
        )

    def test_busy_requeue_fails_closed_without_expectation(self):
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
        )
        run.status = Run.Status.RUNNING
        run.save(update_fields=["status"])

        with (
            patch(
                "lens.services.get_run_document_expectation",
                return_value=None,
            ),
            patch("lens.tasks.execute_answer_run.apply_async") as apply_async,
        ):
            finish_lensnode_run(
                run.uuid,
                Run.Status.FAILED,
                error="LENSNODE_BUSY",
            )

        self.assertEqual(
            apply_async.call_args.kwargs["args"],
            [str(run.uuid)],
        )
        self.assertEqual(
            apply_async.call_args.kwargs["headers"],
            {"sourcelens_expected_document_count": -1},
        )

    def test_queue_promotion_restores_expected_document_count(self):
        active_run = create_execution_run(
            session=self.session,
            question="First",
            enqueue=False,
        )
        active_run.status = Run.Status.RUNNING
        active_run.save(update_fields=["status"])
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        queued_run = create_execution_run(
            session=self.session,
            question="Second",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )

        with patch("lens.tasks.execute_answer_run.apply_async") as apply_async:
            finish_lensnode_run(active_run.uuid, Run.Status.DONE)

        apply_async.assert_called_once_with(
            args=[str(queued_run.uuid)],
            headers={"sourcelens_expected_document_count": 1},
        )

    def test_queue_promotion_fails_closed_without_expectation(self):
        active_run = create_execution_run(
            session=self.session,
            question="First",
            enqueue=False,
        )
        active_run.status = Run.Status.RUNNING
        active_run.save(update_fields=["status"])
        queued_run = create_execution_run(
            session=self.session,
            question="Second",
            enqueue=False,
        )

        with (
            patch(
                "lens.services.get_run_document_expectation",
                return_value=None,
            ),
            patch("lens.tasks.execute_answer_run.apply_async") as apply_async,
        ):
            finish_lensnode_run(active_run.uuid, Run.Status.DONE)

        apply_async.assert_called_once_with(
            args=[str(queued_run.uuid)],
            headers={"sourcelens_expected_document_count": -1},
        )

    def test_dispatch_fails_when_document_expectation_state_is_missing(self):
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
        )

        with (
            patch("lens.execution.validate_run_dispatch"),
            patch("lens.execution.dispatch_run_to_lensnode") as dispatch,
            self.assertRaisesRegex(
                LensNodeDispatchError,
                "DOCUMENT_ATTACHMENT_STATE_UNAVAILABLE",
            ),
        ):
            execute_answer_run(
                run,
                dispatch=True,
                expected_document_count=-1,
            )

        dispatch.assert_not_called()

    def test_message_attachments_keep_mixed_request_order(self):
        document = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        image = store_message_attachment(
            self.session,
            self.user,
            _png_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze both",
            enqueue=False,
            attachment_uuids=[document["uuid"], str(image.uuid)],
        )

        serialized = MessageSerializer(run.input_message).data
        attachment_uuids = [
            str(item["uuid"]) for item in serialized["attachments"]
        ]

        self.assertEqual(
            attachment_uuids,
            [document["uuid"], str(image.uuid)],
        )
        image.refresh_from_db()
        self.assertEqual(image.order, 1)
        self.assertEqual(
            get_run_document_attachments(run.uuid)[0]["order"],
            0,
        )

    def test_run_rejects_missing_or_expired_document_metadata(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/runs/",
            {
                "question": "Analyze it",
                "enqueue": False,
                "attachment_uuids": [str(uuid.uuid4())],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Run.objects.count(), 0)

    def test_run_rejects_lensnode_without_document_capability(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        self.lensnode.labels = {}
        self.lensnode.save(update_fields=["labels"])
        self.client.force_authenticate(self.user)

        response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/runs/",
            {
                "question": "Analyze it",
                "enqueue": False,
                "attachment_uuids": [metadata["uuid"]],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "DOCUMENT_ATTACHMENTS_UNSUPPORTED_BY_LENSNODE",
            str(response.data),
        )
        self.assertEqual(Run.objects.count(), 0)
        self.assertEqual(
            get_document_attachment(metadata["uuid"])["run_uuid"],
            "",
        )

    def test_run_rejects_duplicate_document_uuids(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        self.client.force_authenticate(self.user)

        response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/runs/",
            {
                "question": "Analyze it",
                "enqueue": False,
                "attachment_uuids": [
                    metadata["uuid"],
                    metadata["uuid"],
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Run.objects.count(), 0)
        self.assertEqual(
            get_document_attachment(metadata["uuid"])["run_uuid"],
            "",
        )

    def test_message_list_batches_document_cache_reads(self):
        for question in ("First", "Second"):
            metadata = store_document_attachment(
                self.session,
                self.user,
                _pdf_upload(f"{question}.pdf"),
            )
            create_execution_run(
                session=self.session,
                question=question,
                enqueue=False,
                attachment_uuids=[metadata["uuid"]],
            )
        self.client.force_authenticate(self.user)

        with patch(
            "lens.document_attachments.cache.get_many",
            wraps=cache.get_many,
        ) as get_many:
            response = self.client.get(
                f"/api/lens/sessions/{self.session.uuid}/messages/"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_many.call_count, 2)

    def test_lensnode_fetch_requires_matching_bound_run(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )
        unbound = store_document_attachment(
            self.session,
            self.user,
            _docx_upload(),
        )
        token = issue_lensnode_token(self.lensnode)
        self.client.force_authenticate(user=None)

        response = self.client.get(
            (
                f"/api/lens/lensnode/runs/{run.uuid}/attachments/"
                f"{metadata['uuid']}/"
            ),
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        denied = self.client.get(
            (
                f"/api/lens/lensnode/runs/{run.uuid}/attachments/"
                f"{unbound['uuid']}/"
            ),
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["X-Attachment-Hash"],
            metadata["content_hash"],
        )
        self.assertEqual(denied.status_code, 404)

    def test_owner_can_delete_uploaded_document(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        self.client.force_authenticate(self.user)

        response = self.client.delete(
            f"/api/lens/attachments/{metadata['uuid']}/"
        )

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(get_document_attachment(metadata["uuid"]))

    def test_owner_cannot_delete_document_bound_to_active_run(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )
        self.client.force_authenticate(self.user)

        response = self.client.delete(
            f"/api/lens/attachments/{metadata['uuid']}/"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "ATTACHMENT_IN_USE")
        self.assertIsNotNone(get_document_attachment(metadata["uuid"]))

    def test_owner_cannot_delete_document_bound_to_awaiting_run(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )
        run.status = Run.Status.RUNNING
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["status", "resume_by"])
        self.client.force_authenticate(self.user)

        response = self.client.delete(
            f"/api/lens/attachments/{metadata['uuid']}/"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "ATTACHMENT_IN_USE")
        self.assertIsNotNone(get_document_attachment(metadata["uuid"]))

    def test_owner_can_delete_document_after_run_finishes(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze it",
            enqueue=False,
            attachment_uuids=[metadata["uuid"]],
        )
        run.status = Run.Status.DONE
        run.save(update_fields=["status"])
        self.client.force_authenticate(self.user)

        response = self.client.delete(
            f"/api/lens/attachments/{metadata['uuid']}/"
        )

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(get_document_attachment(metadata["uuid"]))

    def test_deleting_session_removes_temporary_document(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        self.client.force_authenticate(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                f"/api/lens/sessions/{self.session.uuid}/"
            )
            self.assertIsNotNone(get_document_attachment(metadata["uuid"]))
            self.assertTrue(
                storages["document_attachments"].exists(metadata["storage_name"])
            )

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(get_document_attachment(metadata["uuid"]))
        self.assertFalse(
            storages["document_attachments"].exists(metadata["storage_name"])
        )

    def test_general_chat_accepts_document_upload(self):
        self.assistant.selected_task = "general_chat"
        self.assistant.save(update_fields=["selected_task"])
        self.client.force_authenticate(self.user)

        response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/attachments/",
            {"file": _pdf_upload()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["kind"], "document")

    def test_general_chat_accepts_previously_uploaded_document(self):
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        self.assistant.selected_task = "general_chat"
        self.assistant.save(update_fields=["selected_task"])
        self.client.force_authenticate(self.user)

        response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/runs/",
            {
                "question": "Analyze it",
                "enqueue": False,
                "attachment_uuids": [metadata["uuid"]],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Run.objects.count(), 1)
        self.assertEqual(
            get_document_attachment(metadata["uuid"])["run_uuid"],
            str(response.data["uuid"]),
        )

    def test_general_chat_rejects_document_without_lensnode_capability(self):
        self.assistant.selected_task = "general_chat"
        self.assistant.save(update_fields=["selected_task"])
        self.lensnode.labels = {}
        self.lensnode.save(update_fields=["labels"])
        metadata = store_document_attachment(
            self.session,
            self.user,
            _pdf_upload(),
        )
        self.client.force_authenticate(self.user)

        response = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/runs/",
            {
                "question": "Analyze it",
                "enqueue": False,
                "attachment_uuids": [metadata["uuid"]],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Run.objects.count(), 0)
