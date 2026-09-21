from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from pypdf import PdfReader
from rest_framework.test import APIClient

from lens.models import Assistant, LensNode, Message, Run, Session, SharedQA
from lens.qa_pdf import (
    build_qa_pdf_filename,
    build_qa_pdf_html,
    render_qa_pdf,
)

User = get_user_model()


class QAPdfRenderingTests(TestCase):
    """Verify Q&A exports remain styled, searchable text PDFs."""

    def test_filename_prefers_summary_and_sanitizes_unsafe_characters(self):
        filename = build_qa_pdf_filename(
            "  July orders: status / follow-up?  ",
            "Fallback question",
        )

        self.assertEqual(filename, "July orders status follow-up.pdf")
        self.assertEqual(
            build_qa_pdf_filename("", "Deployment overview"),
            "Deployment overview.pdf",
        )
        self.assertEqual(
            build_qa_pdf_filename("", ""),
            "SourceLens-conversation.pdf",
        )

    def test_html_preserves_layout_and_sanitizes_markdown(self):
        html = build_qa_pdf_html(
            title="订单汇总",
            question="订单总数是多少？",
            answer=(
                "## 结论\n\n总数是 **38**。\n\n"
                "| 月份 | 数量 |\n| --- | ---: |\n| 7月 | 38 |\n\n"
                "<script>alert('unsafe')</script>\n\n"
                "![remote](https://example.com/image.png)\n\n"
                "[unsafe](javascript:alert(1))"
            ),
            assistant_name="订单助手",
            language_code="zh-hans",
        )

        self.assertIn("margin: 18mm 28mm", html)
        self.assertIn("SourceLens · AI Agent 问答", html)
        self.assertIn("#f3f4f6", html)
        self.assertIn("border-radius: 8px", html)
        self.assertIn("letter-spacing: -.015em", html)
        self.assertIn("<strong>38</strong>", html)
        self.assertIn("<table>", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("javascript:", html)

    def test_html_drops_inline_source_markers(self):
        html = build_qa_pdf_html(
            title="Report",
            question="How did revenue move?",
            answer=(
                "Revenue grew. [[source: hosted_demo/report.xlsx]] "
                "Then it dipped."
            ),
            language_code="en",
        )

        self.assertIn("Revenue grew.", html)
        self.assertIn("Then it dipped.", html)
        self.assertNotIn("[[source", html)
        self.assertNotIn("hosted_demo/report.xlsx", html)

    def test_pdf_contains_selectable_text_instead_of_page_image(self):
        pdf = render_qa_pdf(
            title="订单汇总",
            question="订单总数是多少？",
            answer="## 结论\n\n订单总数是 **38**。",
            assistant_name="订单助手",
            language_code="zh-hans",
        )

        self.assertTrue(pdf.startswith(b"%PDF"))
        reader = PdfReader(BytesIO(pdf))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("订单总数是多少", text)
        self.assertIn("订单总数是 38", text)
        self.assertTrue(reader.trailer["/Root"]["/MarkInfo"]["/Marked"])
        self.assertNotIn(b"/Subtype /Image", pdf)

    def test_long_answer_paginates_without_losing_tail_content(self):
        paragraphs = []
        for number in range(1, 181):
            paragraphs.append(f"第 {number} 条分页验证内容。")

        pdf = render_qa_pdf(
            title="分页验证",
            question="请导出完整内容。",
            answer="\n\n".join(paragraphs),
            language_code="zh-hans",
        )

        pages = PdfReader(BytesIO(pdf)).pages
        text = "\n".join(page.extract_text() or "" for page in pages)
        self.assertGreater(len(pages), 1)
        self.assertIn("第 1 条分页验证内容", text)
        self.assertIn("第 180 条分页验证内容", text)


class QAPdfApiTests(TestCase):
    """Verify PDF endpoints reuse existing Q&A authorization boundaries."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username="pdf-owner",
            password="pass12345",
        )
        self.other = User.objects.create_user(
            username="pdf-other",
            password="pass12345",
        )
        node = LensNode.objects.create(
            name="PDF node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.assistant = Assistant.objects.create(
            name="订单助手",
            slug="pdf-helper",
            lensnode=node,
            selected_task="qa",
            status=Assistant.Status.ACTIVE,
            visibility=Assistant.Visibility.PUBLIC,
        )
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.owner,
            title="七月订单汇总",
        )
        question = Message.objects.create(
            session=session,
            role=Message.Role.USER,
            content="订单总数是多少？",
            sequence=1,
        )
        self.run = Run.objects.create(
            session=session,
            status=Run.Status.DONE,
            input_message=question,
        )
        answer = Message.objects.create(
            session=session,
            role=Message.Role.ASSISTANT,
            content="订单总数是 **38**。",
            run=self.run,
            sequence=2,
        )
        self.run.output_message = answer
        self.run.save(update_fields=["output_message"])
        self.share = SharedQA.objects.create(
            token="pdf-share-token",
            run=self.run,
            assistant=self.assistant,
            assistant_name=self.assistant.name,
            assistant_slug=self.assistant.slug,
            question=question.content,
            answer=answer.content,
            title="七月订单汇总",
            published_by=self.owner,
            published_at=timezone.now(),
        )

    @patch("lens.views.sessions.render_qa_pdf", return_value=b"%PDF-test")
    def test_owner_can_download_run_pdf(self, render_pdf):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/lens/runs/{self.run.uuid}/pdf/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("utf-8''", response["Content-Disposition"])
        render_pdf.assert_called_once()

    @patch("lens.views.sessions.render_qa_pdf", return_value=b"%PDF-test")
    def test_other_user_cannot_download_run_pdf(self, render_pdf):
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/lens/runs/{self.run.uuid}/pdf/")

        self.assertEqual(response.status_code, 404)
        render_pdf.assert_not_called()

    @patch("lens.views.shares.render_qa_pdf", return_value=b"%PDF-test")
    def test_authenticated_user_can_download_shared_pdf(self, render_pdf):
        self.client.force_authenticate(self.other)

        response = self.client.get(
            f"/api/lens/public/qa/{self.share.token}/pdf/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(
            SharedQA.objects.get(pk=self.share.pk).view_count,
            0,
        )
        render_pdf.assert_called_once()

    @patch("lens.views.shares.render_qa_pdf", return_value=b"%PDF-test")
    def test_anonymous_user_cannot_download_shared_pdf(self, render_pdf):
        response = self.client.get(
            f"/api/lens/public/qa/{self.share.token}/pdf/"
        )

        self.assertEqual(response.status_code, 403)
        render_pdf.assert_not_called()
