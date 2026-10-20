"""Notification context, bounded question excerpts and navigable Run links."""

from datetime import datetime, timezone
from unittest.mock import Mock, patch

from agentcore_notifier.adapters.django.models import NotificationChannel
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.services.notification_targets import build_payload
from lens.alert_content import notification_card_content, render_notification, run_detail_url, run_link_label
from lens.alerts import evaluate_run_alerts, send_alert_delivery
from lens.models import AlertDelivery, AlertDiagnosis, AlertRule, AlertSubscription, Assistant, LensNode, Run, Session
from lens.services import create_execution_run


@override_settings(FRONTEND_URL="https://lens.example.test/portal/")
class AlertContentTests(TestCase):
    """Render actual persisted occurrences without sending external notifications."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice", first_name="Alice", last_name="Chen")
        node = LensNode.objects.create(name="Notification node")
        assistant = Assistant.objects.create(name="Engineering assistant", slug="content-test", lensnode=node)
        session = Session.objects.create(assistant=assistant, user=self.user)
        rule = AlertRule.objects.create(name="Failed answers", events=["run_failed"])
        AlertRule.objects.filter(pk=rule.pk).update(created_at=datetime(2026, 10, 18, tzinfo=timezone.utc))
        self.channel = NotificationChannel.objects.create(name="Ops", channel_type="email", config={})
        AlertSubscription.objects.create(
            rule=rule, channel_uuid=self.channel.uuid, email_recipients=["ops@example.test"]
        )
        self.run = create_execution_run(session=session, question="Why did yesterday's sync fail?", enqueue=False)
        self.run.status = Run.Status.FAILED
        self.run.started_at = datetime(2026, 10, 19, 7, 30, tzinfo=timezone.utc)
        self.run.finished_at = datetime(2026, 10, 19, 7, 35, tzinfo=timezone.utc)
        self.run.error = "RUN_TIMEOUT"
        self.run.save()
        evaluate_run_alerts(self.run.uuid)
        self.delivery = AlertDelivery.objects.get()

    def test_run_notification_contains_request_context_and_absolute_detail_link(self):
        title, body = render_notification(self.delivery, "zh-CN")
        self.assertIn("问答失败", title)
        for value in (
            "助手: Engineering assistant",
            "用户: Alice Chen (alice)",
            "问答任务时间: 2026-10-19 07:30:00 UTC ～ 2026-10-19 07:35:00 UTC",
            "用户问题:\nWhy did yesterday's sync fail?",
            "RUN_TIMEOUT",
            f"https://lens.example.test/portal/management/lens/runs?run={self.run.uuid}",
        ):
            self.assertIn(value, body)
        self.assertNotIn("发生时间:", body)
        self.assertNotIn("开始时间:", body)
        self.assertNotIn("Run: ", body)
        content = notification_card_content(self.delivery, "zh-CN")
        self.assertIn(
            ("问答任务时间", "2026-10-19 07:30:00 UTC ～ 2026-10-19 07:35:00 UTC"), content["fields"]
        )

    def test_running_alert_interval_ends_at_snapshot_time_in_both_formats(self):
        self.delivery.event.detail.update(
            status="running", started_at="2026-10-19T15:30:00+08:00", occurred_at="2026-10-19T15:32:00+08:00"
        )
        expected = "2026-10-19 07:30:00 UTC ～ 2026-10-19 07:32:00 UTC （截至告警触发）"
        _, body = render_notification(self.delivery, "zh-CN")
        self.assertIn(f"问答任务时间: {expected}", body)
        content = notification_card_content(self.delivery, "zh-CN")
        self.assertIn(("问答任务时间", expected), content["fields"])

    def test_question_is_bounded_at_capture_and_link_survives_a_long_excerpt(self):
        self.run.input_message.content = "长问题" * 10000
        self.run.input_message.save()
        evaluate_run_alerts(self.run.uuid)
        from lens.models import AlertOutbox

        question = AlertOutbox.objects.get().payload["question"]
        self.assertLessEqual(len(question), 2001)
        self.assertTrue(question.endswith("…"))
        self.delivery.event.detail = {**self.delivery.event.detail, "question": question}
        _, body = render_notification(self.delivery, "zh-CN")
        self.assertTrue(body.endswith(f"management/lens/runs?run={self.run.uuid}"))

    def test_long_question_preserves_failure_and_diagnosis_in_wecom_notification(self):
        self.delivery.event.detail["question"] = "长问题" * 667
        self.delivery.phase = "diagnosis"
        self.delivery.diagnosis = AlertDiagnosis(
            status="completed",
            language="zh-CN",
            result={
                "summary": "请求在工具执行阶段超时。",
                "root_cause": {"description": "工具响应超过时限。"},
                "recommendations": [{"action": "检查工具状态后重试。"}],
            },
        )
        title, body = render_notification(self.delivery, "zh-CN")
        payload = build_payload(
            "wecom",
            title,
            body,
            action_url=run_detail_url(self.delivery.event),
            action_label=run_link_label("zh-CN"),
        )
        text = payload["markdown"]["content"]
        for value in (
            r"RUN\_TIMEOUT",
            "请求在工具执行阶段超时。",
            "工具响应超过时限。",
            "检查工具状态后重试。",
            "Alice Chen",
            "长问题",
            f"management/lens/runs?run={self.run.uuid}",
        ):
            self.assertIn(value, text)
        self.assertLess(text.index("用户问题:"), text.index("诊断结果:"))
        self.assertIn("长问题" * 66 + "长问…", text)
        self.assertLessEqual(len(text.encode("utf-8")), 4096)

    def test_feishu_diagnosis_is_not_displaced_by_a_long_question(self):
        self.delivery.event.detail["question"] = "长问题" * 667
        self.delivery.phase = "diagnosis"
        self.delivery.diagnosis = AlertDiagnosis(
            status="completed",
            language="zh-CN",
            result={
                "summary": "诊断" * 480,
                "root_cause": {"description": "原因" * 450},
                "recommendations": [{"action": "检查工具状态后重试。"}],
            },
        )
        payload = build_payload(
            "feishu",
            "问答失败",
            "Plain text fallback",
            card_content=notification_card_content(self.delivery, "zh-CN"),
        )
        text = "\n".join(
            element["content"] for element in payload["card"]["body"]["elements"] if element["tag"] == "markdown"
        )
        for value in ("诊断" * 480, "原因" * 450, "检查工具状态后重试。", "长问题"):
            self.assertIn(value, text)
        self.assertLess(text.index("用户问题"), text.index("诊断结果"))
        self.assertIn("长问题" * 66 + "长问…", text)

    def test_question_precedes_diagnosis_fallback_in_both_formats(self):
        self.delivery.phase = "diagnosis"
        for status in ("failed", "timed_out"):
            with self.subTest(status=status):
                self.delivery.diagnosis = AlertDiagnosis(status=status, language="zh-CN")
                _, body = render_notification(self.delivery, "zh-CN")
                self.assertLess(body.index("\n用户问题:"), body.index("\n诊断结果:"))
                sections = notification_card_content(self.delivery, "zh-CN")["sections"]
                labels = [label for label, value in sections]
                self.assertLess(labels.index("用户问题"), labels.index("诊断结果"))

    def test_personal_notification_links_to_the_owned_conversation(self):
        self.user.email = "alice@example.test"
        self.user.save()
        self.run.session.assistant.visibility = "public"
        self.run.session.assistant.save()
        subscription = self.delivery.subscription
        rule = subscription.rule
        rule.personal_subscription_enabled = True
        rule.personal_channel_uuid = self.channel.uuid
        rule.save()
        subscription.personal = True
        subscription.user = self.user
        subscription.channel_uuid = None
        subscription.save()
        self.delivery.destination["recipients"] = [self.user.email]
        self.delivery.save(update_fields=["destination"])
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(self.delivery.uuid)
        body = send.call_args.kwargs["params"]["body"]
        expected = (
            f"https://lens.example.test/portal/lens/assistants/{self.run.session.assistant.slug}"
            f"/history?session={self.run.session.uuid}"
        )
        self.assertIn(expected, body)
        self.assertIn("查看会话:", body)
        self.assertNotIn("/management/", body)
        self.delivery.event.run = None
        self.assertEqual(run_detail_url(self.delivery.event, personal=True), "")

    def test_legacy_event_renders_without_question_or_user_snapshot(self):
        self.delivery.event.detail = {"assistant_name": "Legacy assistant", "matched_events": ["run_failed"]}
        _, body = render_notification(self.delivery, "en-US")
        self.assertIn("Legacy assistant", body)
        self.assertIn(f"management/lens/runs?run={self.run.uuid}", body)
        self.assertNotIn("None", body)
        self.assertIn("Q&A task time: - ～ ", body)

    def test_actual_feishu_delivery_contains_context_and_a_detail_button(self):
        self.channel.channel_type = "feishu"
        self.channel.config = {"url": "https://open.feishu.cn/open-apis/bot/v2/hook/content-test"}
        self.channel.save()
        self.delivery.destination["channel_type"] = "feishu"
        self.delivery.save()
        with patch("core.services.notification_targets.requests.post") as post:
            post.return_value = Mock(status_code=200, json=Mock(return_value={"code": 0}))
            send_alert_delivery(self.delivery.uuid)
        card = post.call_args.kwargs["json"]["card"]
        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(card["header"]["template"], "red")
        elements = card["body"]["elements"]
        self.assertTrue(any(item["tag"] == "column_set" for item in elements))
        self.assertTrue(any(item["tag"] == "hr" for item in elements))
        self.assertIn("Alice Chen", str(elements))
        self.assertIn("alice", str(elements))
        self.assertIn("Why did yesterday's sync fail?", str(elements))
        self.assertNotIn("SourceLens · Run:", str(elements))
        self.assertNotIn(str(self.run.uuid), str([item for item in elements if item["tag"] != "button"]))
        self.assertEqual(elements[-1]["tag"], "button")
        action = next(item for item in elements if item["tag"] == "button")
        self.assertEqual(
            action["behaviors"][0]["default_url"],
            f"https://lens.example.test/portal/management/lens/runs?run={self.run.uuid}",
        )
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, "sent")

    def test_labels_follow_the_recipient_language(self):
        for language, labels in (
            ("en-US", ("Assistant:", "User:", "Question:", "View Run details:", "Q&A task time:")),
            ("es", ("Asistente:", "Usuario:", "Pregunta:", "Ver detalles del Run:", "Periodo de la consulta:")),
        ):
            with self.subTest(language=language):
                _, body = render_notification(self.delivery, language)
                for label in labels:
                    self.assertIn(label, body)

    def test_card_uses_warning_colors_and_pairs_threshold_with_actual_usage(self):
        self.delivery.event.event_type = "token_exceeded"
        self.delivery.event.detail.update(total_tokens=12500, token_threshold=10000)
        content = notification_card_content(self.delivery, "zh-CN")
        self.assertEqual(content["color"], "orange")
        self.assertIn(("Token 用量", "12500"), content["metrics"])
        self.assertIn(("触发阈值", "10000"), content["metrics"])

    def test_business_message_card_keeps_localized_message_in_a_separate_section(self):
        self.delivery.event.subject = "message"
        self.delivery.event.detail = {
            "topic": "release.ready",
            "body": "English body",
            "translations": {"zh-CN": {"body": "发布已完成，请检查结果。"}},
        }
        content = notification_card_content(self.delivery, "zh-CN")
        self.assertEqual(content["color"], "blue")
        self.assertIn(("消息主题", "release.ready"), content["fields"])
        self.assertTrue(any(label == "发生时间" for label, value in content["fields"]))
        self.assertFalse(any(label == "问答任务时间" for label, value in content["fields"]))
        self.assertEqual(content["sections"], [("", "发布已完成，请检查结果。")])

    @override_settings(FRONTEND_URL="javascript:alert(1)")
    def test_invalid_frontend_url_never_produces_a_navigation_link(self):
        _, body = render_notification(self.delivery, "en-US")
        self.assertNotIn("javascript:", body)
        self.assertNotIn("View Run details:", body)
