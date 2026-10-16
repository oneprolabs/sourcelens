"""Tests for alert rule evaluation and notification dispatch."""

import uuid
from unittest.mock import patch

from agentcore_metering.adapters.django.models import LLMUsage
from agentcore_notifier.adapters.django.models import NotificationChannel
from agentcore_notifier.adapters.django.tasks.send import send_notification
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from lens.alerts import evaluate_run_alerts
from lens.models import (
    AlertEvent,
    AlertRule,
    Assistant,
    LensNode,
    Run,
    RunTraceEvent,
    Session,
)
from lens.services import create_execution_run, finish_lensnode_run

User = get_user_model()


class AlertEvaluationTests(TestCase):
    """Exercise alert evaluation against terminal Runs."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alert-user",
            email="alert-user@example.com",
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
        )
        self.other_assistant = Assistant.objects.create(
            name="Other Advisor",
            slug="other-advisor",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
        )
        self.session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        self.channel = NotificationChannel.objects.create(
            channel_type=NotificationChannel.TYPE_WEBHOOK,
            name="Ops Feishu",
            config={
                "provider_type": "feishu",
                "url": "https://example.com/hook",
            },
        )
        self._sequence = 0

    def _run(self, status=Run.Status.DONE):
        run = create_execution_run(
            session=self.session,
            question="Why did it fail?",
            enqueue=False,
        )
        Run.objects.filter(pk=run.pk).update(
            status=status,
            finished_at=timezone.now(),
        )
        run.refresh_from_db()
        return run

    def _usage(self, run, total_tokens):
        LLMUsage.objects.create(
            model="test-model",
            total_tokens=total_tokens,
            metadata={"run_uuid": str(run.uuid)},
        )

    def _turn(self, run, turn):
        self._sequence += 1
        RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=self._sequence,
            event_type="model.call",
            timestamp=timezone.now(),
            turn=turn,
        )

    def _rule(self, **overrides):
        defaults = {
            "name": "Failure watch",
            "events": [AlertRule.Event.RUN_FAILED],
            "channel_uuid": self.channel.uuid,
        }
        defaults.update(overrides)
        return AlertRule.objects.create(**defaults)

    def test_failed_run_dispatches_once(self):
        rule = self._rule()
        run = self._run(status=Run.Status.FAILED)

        with patch.object(send_notification, "delay") as delay:
            evaluate_run_alerts(run.uuid)
            evaluate_run_alerts(run.uuid)

        event = AlertEvent.objects.get(run=run)
        self.assertEqual(event.event_type, AlertEvent.EventType.RUN_FAILED)
        self.assertEqual(event.status, AlertEvent.Status.DISPATCHED)
        self.assertEqual(AlertEvent.objects.filter(rule=rule).count(), 1)
        delay.assert_called_once()
        self.assertEqual(
            delay.call_args.kwargs["channel_uuid"],
            str(self.channel.uuid),
        )

    def test_done_run_does_not_fire_failure(self):
        self._rule()
        run = self._run(status=Run.Status.DONE)

        with patch.object(send_notification, "delay") as delay:
            evaluate_run_alerts(run.uuid)

        self.assertFalse(AlertEvent.objects.filter(run=run).exists())
        delay.assert_not_called()

    def test_token_threshold_triggers(self):
        self._rule(
            name="Token watch",
            events=[AlertRule.Event.TOKEN_EXCEEDED],
            token_threshold=100,
        )
        run = self._run()
        self._usage(run, 150)

        with patch.object(send_notification, "delay"):
            evaluate_run_alerts(run.uuid)

        event = AlertEvent.objects.get(run=run)
        self.assertEqual(event.event_type, AlertEvent.EventType.TOKEN_EXCEEDED)
        self.assertEqual(event.detail["total_tokens"], 150)

    def test_token_threshold_not_reached(self):
        self._rule(
            name="Token watch",
            events=[AlertRule.Event.TOKEN_EXCEEDED],
            token_threshold=100,
        )
        run = self._run()
        self._usage(run, 80)

        with patch.object(send_notification, "delay"):
            evaluate_run_alerts(run.uuid)

        self.assertFalse(AlertEvent.objects.filter(run=run).exists())

    def test_rounds_threshold_triggers(self):
        self._rule(
            name="Rounds watch",
            events=[AlertRule.Event.ROUNDS_EXCEEDED],
            rounds_threshold=3,
        )
        run = self._run()
        self._turn(run, 2)
        self._turn(run, 5)

        with patch.object(send_notification, "delay"):
            evaluate_run_alerts(run.uuid)

        event = AlertEvent.objects.get(run=run)
        self.assertEqual(event.event_type, AlertEvent.EventType.ROUNDS_EXCEEDED)
        self.assertEqual(event.detail["rounds"], 5)

    def test_assistant_scope_limits_rule(self):
        rule = self._rule()
        rule.assistants.add(self.other_assistant)
        run = self._run(status=Run.Status.FAILED)

        with patch.object(send_notification, "delay") as delay:
            evaluate_run_alerts(run.uuid)

        self.assertFalse(AlertEvent.objects.filter(run=run).exists())
        delay.assert_not_called()

    def test_disabled_rule_is_ignored(self):
        self._rule(enabled=False)
        run = self._run(status=Run.Status.FAILED)

        with patch.object(send_notification, "delay") as delay:
            evaluate_run_alerts(run.uuid)

        self.assertFalse(AlertEvent.objects.filter(run=run).exists())
        delay.assert_not_called()

    def test_missing_channel_records_skipped(self):
        rule = self._rule(channel_uuid=None)
        run = self._run(status=Run.Status.FAILED)

        with patch.object(send_notification, "delay") as delay:
            evaluate_run_alerts(run.uuid)

        event = AlertEvent.objects.get(rule=rule)
        self.assertEqual(event.status, AlertEvent.Status.SKIPPED)
        delay.assert_not_called()

    def test_email_channel_without_recipients_is_skipped(self):
        email_channel = NotificationChannel.objects.create(
            channel_type=NotificationChannel.TYPE_EMAIL,
            name="Ops Email",
            config={"smtp_host": "smtp.example.com", "from_email": "a@b.com"},
        )
        rule = self._rule(channel_uuid=email_channel.uuid)
        run = self._run(status=Run.Status.FAILED)

        with patch.object(send_notification, "delay") as delay:
            evaluate_run_alerts(run.uuid)

        event = AlertEvent.objects.get(rule=rule)
        self.assertEqual(event.status, AlertEvent.Status.SKIPPED)
        delay.assert_not_called()

    def test_unsupported_channel_type_is_skipped(self):
        app_channel = NotificationChannel.objects.create(
            channel_type=NotificationChannel.TYPE_SMS,
            name="Ops SMS",
            config={},
        )
        rule = self._rule(channel_uuid=app_channel.uuid)
        run = self._run(status=Run.Status.FAILED)

        with patch.object(send_notification, "delay") as delay:
            evaluate_run_alerts(run.uuid)

        event = AlertEvent.objects.get(rule=rule)
        self.assertEqual(event.status, AlertEvent.Status.SKIPPED)
        self.assertIn("not supported", event.error_message)
        delay.assert_not_called()

    def test_finish_hook_evaluates_alerts(self):
        self._rule()
        run = create_execution_run(
            session=self.session,
            question="q",
            enqueue=False,
        )

        with patch("lens.alerts.evaluate_run_alerts") as evaluate:
            with self.captureOnCommitCallbacks(execute=True):
                finish_lensnode_run(run.uuid, Run.Status.DONE)

        evaluate.assert_called_once_with(run.uuid)
