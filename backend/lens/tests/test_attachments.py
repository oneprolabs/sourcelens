import io
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from agentcore_metering.adapters.django.models import LLMConfig, LLMUsage
from lens.attachments import (
    ATTACHMENT_MAX_ASPECT_RATIO,
    ATTACHMENT_MAX_PIXELS,
    AttachmentError,
    bind_attachments_to_message,
    store_message_attachment,
)
from lens.execution import execute_answer_run
from lens.llm import LensLLMResult, _multimodal_messages
from lens.models import (
    Assistant,
    LensNode,
    Message,
    MessageAttachment,
    Session,
)
from lens.serializers import RunCreateSerializer
from lens.services import (
    MultimodalPreprocessingError,
    analyze_multimodal_intent,
    create_execution_run,
    select_session_attachment_context,
)

User = get_user_model()


def _png_upload(name="shot.png", size=(2000, 1200), color=(120, 200, 80)):
    """Return a SimpleUploadedFile holding a real PNG image."""

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(
        name, buffer.getvalue(), content_type="image/png"
    )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AttachmentServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="img-user",
            email="img-user@example.com",
            password="pass12345",
        )
        self.lensnode = LensNode.objects.create(
            name="Local LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
        )
        self.assistant = Assistant.objects.create(
            name="Code Advisor",
            slug="code-advisor",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[{"path": "/workspace/repo"}],
            visibility=Assistant.Visibility.PUBLIC,
            multimodal_model_ref=uuid.uuid4(),
        )
        self.session = Session.objects.create(
            assistant=self.assistant, user=self.user, title=""
        )

    def test_store_downscales_and_strips_metadata(self):
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )

        self.assertEqual(attachment.session, self.session)
        self.assertEqual(attachment.uploaded_by, self.user)
        self.assertEqual(attachment.mime_type, "image/png")
        self.assertLessEqual(max(attachment.width, attachment.height), 1600)
        self.assertGreater(attachment.byte_size, 0)
        self.assertIsNone(attachment.message_id)

    def test_store_rejects_oversized(self):
        big = _png_upload()
        big.size = 11 * 1024 * 1024
        with self.assertRaises(AttachmentError):
            store_message_attachment(self.session, self.user, big)

    def test_store_rejects_non_image(self):
        bad = SimpleUploadedFile(
            "notes.txt", b"hello not an image", content_type="text/plain"
        )
        with self.assertRaises(AttachmentError):
            store_message_attachment(self.session, self.user, bad)

    def test_store_rejects_extreme_aspect_ratio(self):
        with self.assertRaisesRegex(
            AttachmentError,
            "ATTACHMENT_ASPECT_UNSUPPORTED",
        ):
            store_message_attachment(
                self.session,
                self.user,
                _png_upload(size=(3001, 1000)),
            )

        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_store_rejects_excessive_pixel_count(self):
        with self.assertRaisesRegex(
            AttachmentError,
            "ATTACHMENT_DIMENSIONS_TOO_LARGE",
        ):
            store_message_attachment(
                self.session,
                self.user,
                _png_upload(size=(2501, 2000)),
            )

        self.assertEqual(MessageAttachment.objects.count(), 0)

    def test_store_accepts_dimension_boundaries(self):
        aspect_boundary = store_message_attachment(
            self.session,
            self.user,
            _png_upload(
                size=(int(1000 * ATTACHMENT_MAX_ASPECT_RATIO), 1000)
            ),
        )
        pixel_boundary = store_message_attachment(
            self.session,
            self.user,
            _png_upload(size=(2500, ATTACHMENT_MAX_PIXELS // 2500)),
        )

        self.assertIsNotNone(aspect_boundary.pk)
        self.assertIsNotNone(pixel_boundary.pk)

    def test_bind_links_in_order_and_ignores_foreign(self):
        first = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        second = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        other_session = Session.objects.create(
            assistant=self.assistant, user=self.user
        )
        foreign = store_message_attachment(
            other_session, self.user, _png_upload()
        )
        message = Message.objects.create(
            session=self.session,
            role=Message.Role.USER,
            content="why?",
            sequence=1,
        )

        bind_attachments_to_message(
            self.session,
            message,
            [str(second.uuid), str(first.uuid), str(foreign.uuid)],
        )

        first.refresh_from_db()
        second.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(second.order, 0)
        self.assertEqual(first.order, 1)
        self.assertIsNone(foreign.message_id)

    def test_create_execution_run_binds_attachments(self):
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )

        run = create_execution_run(
            session=self.session,
            question="why this error?",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        attachment.refresh_from_db()
        self.assertEqual(attachment.message_id, run.input_message_id)
        self.assertEqual(run.input_message.attachments.count(), 1)
        self.assertEqual(
            run.execution.runtime_snapshot["direct_attachment_uuids"],
            [str(attachment.uuid)],
        )

    def test_explicit_attachment_does_not_reuse_historical_images(self):
        historical = store_message_attachment(
            self.session, self.user, _png_upload("historical.png")
        )
        current = store_message_attachment(
            self.session, self.user, _png_upload("current.png")
        )
        create_execution_run(
            session=self.session,
            question="Describe the historical image",
            enqueue=False,
            attachment_uuids=[str(historical.uuid)],
        )

        run = create_execution_run(
            session=self.session,
            question="请描述这张图片",
            enqueue=False,
            attachment_uuids=[str(current.uuid)],
        )

        self.assertEqual(
            run.execution.runtime_snapshot["session_attachment_uuids"],
            [str(current.uuid)],
        )

    def test_unrelated_follow_up_does_not_reuse_single_image(self):
        attachment = store_message_attachment(
            self.session, self.user, _png_upload("historical.png")
        )
        create_execution_run(
            session=self.session,
            question="Describe this image",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        run = create_execution_run(
            session=self.session,
            question="debian 可以支持agent模式吗",
            enqueue=False,
        )

        self.assertEqual(
            run.execution.runtime_snapshot["session_attachment_uuids"],
            [],
        )

    @patch(
        "lens.services.get_session_document_attachments",
        return_value=[
            {
                "uuid": "document-1",
                "kind": "document",
                "original_name": "notes.txt",
                "created_at": "2026-09-14T06:00:00+00:00",
            }
        ],
    )
    def test_follow_up_reuses_latest_document_without_keyword_matching(
        self, mock_documents
    ):
        """Follow-ups reuse the latest document without language keywords."""

        selected = select_session_attachment_context(
            self.session,
            "Please turn this into a checklist; the user may mistype "
            "references.",
        )

        self.assertEqual(
            [item["uuid"] for item in selected],
            ["document-1"],
        )
        mock_documents.assert_called_once_with(self.session.uuid)

    @patch("lens.services.get_session_document_attachments", return_value=[])
    def test_document_reference_never_falls_back_to_historical_image(
        self, mock_documents
    ):
        """A missing document must not be replaced with an old image."""

        image = store_message_attachment(
            self.session, self.user, _png_upload("historical.png")
        )
        create_execution_run(
            session=self.session,
            question="Describe this image",
            enqueue=False,
            attachment_uuids=[str(image.uuid)],
        )

        with self.assertRaisesRegex(AttachmentError, "ATTACHMENT_NOT_FOUND"):
            create_execution_run(
                session=self.session,
                question="Analyze the previous PDF",
                enqueue=False,
            )
        mock_documents.assert_called()

    @patch("lens.services.model_supports_vision", return_value=True)
    def test_follow_up_reuses_previous_image(self, mock_support):
        attachment = store_message_attachment(
            self.session, self.user, _png_upload("diagram.png")
        )
        first = create_execution_run(
            session=self.session,
            question="Describe this image",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )
        second = create_execution_run(
            session=self.session,
            question="What color is the previous image?",
            enqueue=False,
        )

        selected = second.execution.runtime_snapshot[
            "session_attachment_uuids"
        ]
        self.assertEqual(selected, [str(attachment.uuid)])
        with patch("lens.services.run_completion_multimodal") as call:
            call.return_value = LensLLMResult(
                content="green",
                usage={},
                metered=True,
            )
            result = analyze_multimodal_intent(second)

        self.assertEqual(result["image_count"], 1)
        self.assertNotEqual(first.uuid, second.uuid)

    def test_multiple_previous_attachments_are_not_all_reused(self):
        first = store_message_attachment(
            self.session, self.user, _png_upload("first.png")
        )
        second = store_message_attachment(
            self.session, self.user, _png_upload("second.png")
        )
        create_execution_run(
            session=self.session,
            question="Describe the first image",
            enqueue=False,
            attachment_uuids=[str(first.uuid)],
        )
        run = create_execution_run(
            session=self.session,
            question="Tell me something unrelated",
            enqueue=False,
        )

        self.assertEqual(
            run.execution.runtime_snapshot["session_attachment_uuids"],
            [],
        )
        second.refresh_from_db()
        self.assertIsNone(second.message_id)

    @patch("lens.services.model_supports_vision", return_value=True)
    @patch("lens.services.run_completion_multimodal")
    def test_analyze_multimodal_intent_folds_image_and_text(
        self, mock_call, mock_support
    ):
        mock_call.return_value = LensLLMResult(
            content="KeyError missing 'token' in auth middleware",
            usage={
                "total_tokens": 1234,
                "prompt_tokens": 1000,
                "completion_tokens": 234,
                "cost": 0.012,
            },
            metered=True,
        )
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        run = create_execution_run(
            session=self.session,
            question="为什么报错",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        result = analyze_multimodal_intent(run)

        self.assertTrue(mock_call.called)
        self.assertEqual(result["image_count"], 1)
        self.assertTrue(result["rewritten"])
        self.assertIn("KeyError", result["question"])
        self.assertEqual(result["usage"]["total_tokens"], 1234)
        self.assertEqual(result["status"], "succeeded")
        mock_call.assert_called_once()
        call = mock_call.call_args.kwargs
        self.assertIn("为什么报错", call["user_text"])
        self.assertEqual(len(call["image_data_urls"]), 1)

    @patch("lens.services.model_supports_vision", return_value=True)
    @patch("lens.services._recent_history_context")
    @patch("lens.services.run_completion_multimodal")
    def test_analyze_multimodal_intent_does_not_replay_history(
        self, mock_call, mock_history, mock_support
    ):
        mock_call.return_value = LensLLMResult(
            content="The image contains a console error.",
            usage={},
            metered=True,
        )
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        run = create_execution_run(
            session=self.session,
            question="Describe this image.",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        result = analyze_multimodal_intent(run)

        self.assertEqual(result["status"], "succeeded")
        mock_history.assert_not_called()
        self.assertNotIn("There is no image", mock_call.call_args.kwargs[
            "user_text"
        ])

    def test_multimodal_messages_include_text_and_image_content_blocks(self):
        messages = _multimodal_messages(
            "system prompt",
            "user question",
            ["data:image/png;base64,encoded-image"],
        )

        self.assertEqual(messages[0].content, "system prompt")
        self.assertEqual(
            messages[1].content,
            [
                {"type": "text", "text": "user question"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,encoded-image"
                    },
                },
            ],
        )

    @patch("lens.services.run_completion_multimodal")
    def test_non_vision_model_fails_before_multimodal_call(self, mock_call):
        config = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL,
            model_type=LLMConfig.MODEL_TYPE_LLM,
            provider="deepseek",
            config={
                "api_key": "test",
                "model": "deepseek-v4-pro",
            },
            is_active=True,
        )
        self.assistant.multimodal_model_ref = config.uuid
        self.assistant.save(update_fields=["multimodal_model_ref"])
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        run = create_execution_run(
            session=self.session,
            question="Describe this image.",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        with self.assertRaises(MultimodalPreprocessingError) as context:
            analyze_multimodal_intent(run)

        self.assertEqual(context.exception.reason, "MODEL_NOT_VISION_CAPABLE")
        mock_call.assert_not_called()

    @patch("lens.services.run_completion_multimodal")
    @patch("litellm.utils.supports_vision", return_value=False)
    def test_custom_gateway_vision_config_allows_unknown_model(
        self, mock_support, mock_call
    ):
        config = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL,
            model_type=LLMConfig.MODEL_TYPE_LLM,
            provider="openai_compatible",
            config={
                "api_key": "test",
                "api_base": "https://gateway.example/v1",
                "model": "custom-vision-model",
                "vision": True,
            },
            is_active=True,
        )
        self.assistant.multimodal_model_ref = config.uuid
        self.assistant.save(update_fields=["multimodal_model_ref"])
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        run = create_execution_run(
            session=self.session,
            question="Describe this image.",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )
        mock_call.return_value = LensLLMResult(
            content="A green image.", usage={}, metered=True
        )

        result = analyze_multimodal_intent(run)

        self.assertEqual(result["status"], "succeeded")
        mock_support.assert_not_called()
        mock_call.assert_called_once()

    @patch("lens.services.model_supports_vision", return_value="unknown")
    @patch("lens.services.run_completion_multimodal")
    def test_unknown_vision_capability_blocks_multimodal_call(
        self, mock_call, mock_support
    ):
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        run = create_execution_run(
            session=self.session,
            question="Describe this image.",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        with self.assertRaises(MultimodalPreprocessingError) as context:
            analyze_multimodal_intent(run)

        self.assertEqual(context.exception.reason, "MODEL_NOT_VISION_CAPABLE")
        mock_support.assert_called_once()
        mock_call.assert_not_called()


    def test_admin_run_detail_marks_inherited_attachment_context(self):
        from lens.views.admin_runs import _admin_run_detail

        attachment = store_message_attachment(
            self.session, self.user, _png_upload("diagram.png")
        )
        direct = create_execution_run(
            session=self.session,
            question="Describe this image",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )
        inherited = create_execution_run(
            session=self.session,
            question="What color is the previous image?",
            enqueue=False,
        )

        self.assertEqual(
            _admin_run_detail(direct)["attachments"][0]["source"],
            "direct",
        )
        self.assertEqual(
            _admin_run_detail(inherited)["attachments"][0]["source"],
            "inherited",
        )

    @patch("lens.services.model_supports_vision", return_value=True)
    @patch("lens.services.run_completion_multimodal")
    def test_multimodal_failure_does_not_fallback_to_workspace_search(
        self, mock_call, mock_support
    ):
        mock_call.side_effect = RuntimeError("provider secret must not leak")
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        run = create_execution_run(
            session=self.session,
            question="为什么报错",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        with self.assertRaises(MultimodalPreprocessingError):
            execute_answer_run(run, dispatch=False)

        run.refresh_from_db()
        step = run.steps.get(step_type="multimodal")
        self.assertEqual(run.status, run.Status.FAILED)
        self.assertEqual(run.error, "IMAGE_PREPROCESSING_FAILED")
        self.assertEqual(step.status, step.Status.FAILED)
        self.assertEqual(step.detail["status"], "failed")
        self.assertEqual(
            step.detail["error"], "IMAGE_PREPROCESSING_FAILED"
        )
        self.assertEqual(step.detail["reason"], "MODEL_REQUEST_FAILED")
        self.assertNotIn("provider secret", step.detail)

    def test_admin_run_step_counts_includes_preprocess_usage(self):
        from lens.models import RunStep
        from lens.views import _admin_run_step_counts

        run = create_execution_run(
            session=self.session, question="q", enqueue=False
        )
        RunStep.objects.create(
            run=run,
            sequence=0,
            step_type=RunStep.StepType.MULTIMODAL,
            status=RunStep.Status.DONE,
            detail={
                "usage": {
                    "total_tokens": 500,
                    "prompt_tokens": 400,
                    "completion_tokens": 100,
                    "cost": 0.02,
                }
            },
        )

        counts = _admin_run_step_counts(run)

        self.assertEqual(counts["llm_calls"], 1)
        self.assertEqual(counts["total_tokens"], 500)
        self.assertEqual(counts["prompt_tokens"], 400)
        self.assertEqual(counts["completion_tokens"], 100)
        self.assertEqual(counts["total_cost"], 0.02)

    def test_admin_run_step_counts(self):
        from lens.models import RunStep
        from lens.views import _admin_run_step_counts
        from lens.views.admin_runs import _admin_run_detail

        run = create_execution_run(
            session=self.session,
            question="q",
            enqueue=False,
        )
        RunStep.objects.create(
            run=run,
            sequence=0,
            step_type=RunStep.StepType.GENERAL_CHAT,
            status=RunStep.Status.DONE,
            detail={
                "events": [
                    {
                        "agent_event": (
                            "tool.run_skill_script.start"
                        )
                    },
                    {
                        "agent_event": (
                            "tool.run_skill_script.done"
                        )
                    },
                    {
                        "agent_event": (
                            "tool.analyze_structured_output.start"
                        ),
                        "operation": "count",
                        "max_calls": 6,
                    },
                    {
                        "agent_event": (
                            "tool.analyze_structured_output.budget_exceeded"
                        ),
                        "operation": "count",
                        "max_calls": 6,
                    },
                    {
                        "agent_event": (
                            "tool.analyze_structured_output.start"
                        ),
                        "operation": "validate_records",
                        "max_calls": 1,
                    },
                    {
                        "agent_event": (
                            "tool.analyze_structured_output.budget_exceeded"
                        ),
                        "operation": "validate_records",
                        "max_calls": 1,
                    },
                    {
                        "agent_event": "tool.run_skill_transform.start"
                    },
                    {
                        "agent_event": (
                            "tool.run_skill_transform.budget_exceeded"
                        )
                    },
                    {"agent_event": "tool.task.invoke"},
                    {"agent_event": "tool.task.denied"},
                ]
            },
        )

        counts = _admin_run_step_counts(run)

        self.assertEqual(counts["structured_analysis_calls"], 1)
        self.assertNotIn("structured_analysis_limit_hits", counts)
        self.assertNotIn("structured_analysis_max_calls", counts)
        self.assertEqual(counts["structured_validation_calls"], 1)
        self.assertNotIn("structured_validation_limit_hits", counts)
        self.assertNotIn("structured_validation_max_calls", counts)
        self.assertEqual(counts["transform_calls"], 1)
        self.assertNotIn("transform_call_limit_hits", counts)
        self.assertEqual(counts["subagent_count"], 0)
        self.assertEqual(counts["subagent_denied_count"], 1)

        detail = _admin_run_detail(run)

        self.assertEqual(detail["structured_analysis_calls"], 1)
        self.assertNotIn("structured_analysis_limit_hits", detail)
        self.assertNotIn("structured_analysis_max_calls", detail)
        self.assertEqual(detail["structured_validation_calls"], 1)
        self.assertNotIn("structured_validation_limit_hits", detail)
        self.assertNotIn("structured_validation_max_calls", detail)
        self.assertEqual(detail["transform_calls"], 1)
        self.assertNotIn("transform_call_limit_hits", detail)
        self.assertEqual(detail["subagent_denied_count"], 1)

    def test_admin_run_detail_uses_metered_calls_including_subagents(self):
        from lens.views.admin_runs import _admin_run_detail

        run = create_execution_run(
            session=self.session,
            question="q",
            enqueue=False,
        )
        LLMUsage.objects.create(
            user=self.user,
            model="deepseek-v4-flash",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cached_tokens=80,
            reasoning_tokens=5,
            metadata={
                "run_uuid": str(run.uuid),
                "is_subagent": False,
                "source_type": "lensnode_agent",
            },
        )
        LLMUsage.objects.create(
            user=self.user,
            model="deepseek-v4-flash",
            prompt_tokens=200,
            completion_tokens=30,
            total_tokens=230,
            cached_tokens=160,
            reasoning_tokens=10,
            metadata={
                "run_uuid": str(run.uuid),
                "is_subagent": True,
                "source_type": "lensnode_agent",
            },
        )

        detail = _admin_run_detail(run)

        self.assertEqual(detail["llm_calls"], 2)
        self.assertEqual(detail["subagent_model_calls"], 1)
        self.assertEqual(detail["total_tokens"], 350)
        self.assertEqual(detail["cached_tokens"], 240)
        self.assertEqual(detail["reasoning_tokens"], 15)
        self.assertEqual(detail["models_used"], ["deepseek-v4-flash"])
        self.assertEqual(len(detail["model_calls"]), 2)
        self.assertFalse(detail["model_calls"][0]["is_subagent"])
        self.assertTrue(detail["model_calls"][1]["is_subagent"])

    def test_admin_run_detail_deduplicates_models_in_call_order(self):
        from lens.views.admin_runs import _admin_run_detail

        run = create_execution_run(
            session=self.session,
            question="q",
            enqueue=False,
        )
        for model in (
            "deepseek-v4-flash",
            "qwen3-coder-plus",
            "deepseek-v4-flash",
        ):
            LLMUsage.objects.create(
                user=self.user,
                model=model,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                metadata={"run_uuid": str(run.uuid)},
            )

        detail = _admin_run_detail(run)

        self.assertEqual(
            detail["models_used"],
            ["deepseek-v4-flash", "qwen3-coder-plus"],
        )

    def test_analyze_multimodal_intent_requires_model(self):
        self.assistant.multimodal_model_ref = None
        self.assistant.save(update_fields=["multimodal_model_ref"])
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        run = create_execution_run(
            session=self.session,
            question="原始问题",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        with self.assertRaises(MultimodalPreprocessingError) as context:
            analyze_multimodal_intent(run)

        self.assertEqual(context.exception.code, "VISION_MODEL_NOT_CONFIGURED")

    def test_run_create_serializer_requires_text_or_image(self):
        serializer = RunCreateSerializer(
            data={"question": "   "},
            context={"session": self.session},
        )
        self.assertFalse(serializer.is_valid())

    def test_run_create_serializer_accepts_image_only(self):
        attachment = store_message_attachment(
            self.session, self.user, _png_upload()
        )
        serializer = RunCreateSerializer(
            data={
                "question": "",
                "enqueue": False,
                "attachment_uuids": [str(attachment.uuid)],
            },
            context={"session": self.session},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        run = serializer.save()
        attachment.refresh_from_db()
        self.assertEqual(attachment.message_id, run.input_message_id)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AttachmentEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="api-user",
            email="api-user@example.com",
            password="pass12345",
        )
        self.other = User.objects.create_user(
            username="api-other",
            email="api-other@example.com",
            password="pass12345",
        )
        self.admin = User.objects.create_user(
            username="api-admin",
            email="api-admin@example.com",
            password="pass12345",
            is_staff=True,
        )
        self.lensnode = LensNode.objects.create(
            name="Local LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
        )
        self.assistant = Assistant.objects.create(
            name="Code Advisor",
            slug="code-advisor",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            visibility=Assistant.Visibility.PUBLIC,
            multimodal_model_ref=uuid.uuid4(),
        )
        self.session = Session.objects.create(
            assistant=self.assistant, user=self.user
        )
        self.client = APIClient()

    def test_upload_then_owner_can_fetch_and_others_cannot(self):
        self.client.force_authenticate(self.user)
        upload = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/attachments/",
            {"file": _png_upload()},
            format="multipart",
        )
        self.assertEqual(upload.status_code, 201, upload.content)
        att_uuid = upload.data["uuid"]
        self.assertTrue(
            MessageAttachment.objects.filter(uuid=att_uuid).exists()
        )

        fetch = self.client.get(f"/api/lens/attachments/{att_uuid}/")
        self.assertEqual(fetch.status_code, 200)

        self.client.force_authenticate(self.other)
        denied = self.client.get(f"/api/lens/attachments/{att_uuid}/")
        self.assertEqual(denied.status_code, 403)

        # A staff admin may view any user's attachment for observability.
        self.client.force_authenticate(self.admin)
        admin_fetch = self.client.get(f"/api/lens/attachments/{att_uuid}/")
        self.assertEqual(admin_fetch.status_code, 200)

    def test_upload_rejected_when_assistant_not_multimodal(self):
        self.assistant.multimodal_model_ref = None
        self.assistant.save(update_fields=["multimodal_model_ref"])
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            f"/api/lens/sessions/{self.session.uuid}/attachments/",
            {"file": _png_upload()},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)

    def test_upload_rejects_unsupported_image_dimensions(self):
        self.client.force_authenticate(self.user)
        cases = (
            ((3001, 1000), "ATTACHMENT_ASPECT_UNSUPPORTED"),
            ((2501, 2000), "ATTACHMENT_DIMENSIONS_TOO_LARGE"),
        )

        for size, error_code in cases:
            with self.subTest(error_code=error_code):
                response = self.client.post(
                    f"/api/lens/sessions/{self.session.uuid}/attachments/",
                    {"file": _png_upload(size=size)},
                    format="multipart",
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn(error_code.encode(), response.content)

        self.assertEqual(MessageAttachment.objects.count(), 0)
