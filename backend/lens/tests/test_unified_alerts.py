"""Behavioral coverage for unified alerts, durable delivery and permissions."""

from datetime import timedelta
from unittest.mock import patch

from agentcore_notifier.adapters.django.models import NotificationChannel
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lens.alerts import capture_task_alert, evaluate_run_alerts, process_alert_outbox, send_alert_delivery
from lens.models import (
    AlertDelivery,
    AlertEvent,
    AlertOutbox,
    AlertRule,
    AlertSubscription,
    Assistant,
    LensNode,
    Run,
    Session,
)
from lens.services import create_execution_run


class UnifiedAlertTests(TestCase):
    """Exercise real persisted matches and deliveries with external sends stubbed."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="subscriber", email="subscriber@example.com")
        self.admin = get_user_model().objects.create_user(username="operator", is_staff=True)
        self.node = LensNode.objects.create(name="node")
        self.assistant = Assistant.objects.create(name="Assistant", slug="test", lensnode=self.node)
        self.session = Session.objects.create(assistant=self.assistant, user=self.user)
        self.channel = NotificationChannel.objects.create(name="Email", channel_type="email", config={})
        self.rule = AlertRule.objects.create(name="Timeout", events=["run_failed", "run_timeout"])
        self.subscription = AlertSubscription.objects.create(
            rule=self.rule, channel_uuid=self.channel.uuid, email_recipients=["ops@example.com"]
        )

    def run_event(self, **kwargs):
        defaults = dict(session=self.session, status="failed", finished_at=timezone.now(), error="RUN_TIMEOUT")
        defaults.update(kwargs)
        session = defaults.pop("session")
        run = create_execution_run(session=session, question="Test alert", enqueue=False)
        for key, value in defaults.items():
            setattr(run, key, value)
        run.save()
        return run

    def test_timeout_and_failure_produce_one_event_and_delivery(self):
        run = self.run_event(termination_detail={"reason": "soft_deadline"})
        evaluate_run_alerts(run.uuid)
        evaluate_run_alerts(run.uuid)
        event = AlertEvent.objects.get(rule=self.rule)
        self.assertEqual(event.event_type, "run_timeout")
        self.assertEqual(set(event.detail["matched_events"]), {"run_failed", "run_timeout"})
        self.assertEqual(event.deliveries.count(), 1)

    def test_partial_answer_is_not_a_failure(self):
        self.rule.events = ["run_failed", "run_partial"]
        self.rule.save()
        run = self.run_event(status="done", outcome="partial", error="")
        evaluate_run_alerts(run.uuid)
        self.assertEqual(AlertEvent.objects.get().event_type, "run_partial")

    def test_budget_termination_does_not_require_exceeding_numeric_threshold(self):
        self.rule.events = ["run_token_limit"]
        self.rule.save()
        run = self.run_event(status="done", outcome="partial", termination_detail={"reason": "token_budget_wrapup"})
        evaluate_run_alerts(run.uuid)
        self.assertEqual(AlertEvent.objects.get().event_type, "run_token_limit")

    def test_step_limit_termination_is_a_round_limit_alert(self):
        self.rule.events = ["run_round_limit"]
        self.rule.save()
        run = self.run_event(
            status="done",
            outcome="partial",
            error="",
            termination_detail={"reason": "step_limit", "trigger": "step_limit"},
        )
        evaluate_run_alerts(run.uuid)
        self.assertEqual(AlertEvent.objects.get().event_type, "run_round_limit")

    def test_terminal_run_writes_outbox_before_commit_and_rollback_removes_it(self):
        with transaction.atomic():
            run = self.run_event()
            self.assertTrue(AlertOutbox.objects.filter(source_uuid=str(run.uuid)).exists())
            transaction.set_rollback(True)
        self.assertFalse(AlertOutbox.objects.exists())

    def test_provider_failure_is_retryable_and_success_is_recorded(self):
        run = self.run_event()
        evaluate_run_alerts(run.uuid)
        delivery = AlertDelivery.objects.get()
        with patch("lens.alerts._send_notification", return_value={"success": False, "error": "unavailable"}):
            send_alert_delivery(delivery.uuid)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "pending")
        self.assertEqual(delivery.attempts, 1)
        delivery.next_attempt_at = timezone.now()
        delivery.save()
        with patch("lens.alerts._send_notification", return_value={"success": True}):
            send_alert_delivery(delivery.uuid)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "sent")

    def test_disabled_subscription_is_not_sent(self):
        evaluate_run_alerts(self.run_event().uuid)
        self.subscription.enabled = False
        self.subscription.save()
        with patch("lens.alerts._send_notification") as send:
            send_alert_delivery(AlertDelivery.objects.get().uuid)
        send.assert_not_called()
        self.assertEqual(AlertDelivery.objects.get().status, "skipped")

    def test_personal_subscription_does_not_receive_other_users_runs(self):
        self.subscription.user = self.user
        self.subscription.personal = True
        self.subscription.save()
        other = get_user_model().objects.create_user(username="other")
        session = Session.objects.create(assistant=self.assistant, user=other)
        evaluate_run_alerts(self.run_event(session=session).uuid)
        self.assertFalse(AlertDelivery.objects.exists())

    def test_partial_processing_result_matches_even_when_task_succeeded(self):
        from agentcore_task.adapters.django.models import TaskExecution

        self.rule.subject = "processing"
        self.rule.events = ["processing_partial"]
        self.rule.save()
        task = TaskExecution.objects.create(
            task_id="processing-one",
            task_name="processing",
            module="lens_datasource_conversion",
            status="SUCCESS",
            finished_at=timezone.now(),
            result={"conversion_summary": {"failed": 2}},
        )
        capture_task_alert(task)
        process_alert_outbox()
        self.assertEqual(AlertEvent.objects.get().event_type, "processing_partial")

    def test_regular_user_cannot_manage_admin_rules(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get("/api/lens/admin/alert-rules/").status_code, 403)

    def test_personal_api_rejects_arbitrary_recipients(self):
        client = APIClient()
        client.force_authenticate(self.user)
        result = client.post(
            "/api/lens/alert-subscriptions/",
            {"rule_uuid": str(self.rule.uuid), "email_recipients": ["outside@example.com"]},
            format="json",
        )
        self.assertEqual(result.status_code, 400)

    def publish_personal(self):
        """Publish an email rule and replace the managed subscription with a personal one."""

        self.rule.personal_subscription_enabled = True
        self.rule.personal_channel_uuid = self.channel.uuid
        self.rule.save()
        self.subscription.personal = True
        self.subscription.user = self.user
        self.subscription.channel_uuid = None
        self.subscription.save()

    def test_personal_api_uses_account_email_and_enforces_ownership(self):
        self.publish_personal()
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get("/api/lens/alert-subscriptions/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("email_recipients", response.data["results"][0])
        other = get_user_model().objects.create_user(username="unrelated")
        client.force_authenticate(other)
        self.assertEqual(
            client.patch(f"/api/lens/alert-subscriptions/{self.subscription.uuid}/", {"enabled": False}).status_code,
            404,
        )

    def test_personal_subscription_receives_only_current_visible_owned_runs(self):
        self.publish_personal()
        self.assistant.visibility = "public"
        self.assistant.save()
        evaluate_run_alerts(self.run_event().uuid)
        delivery = AlertDelivery.objects.get()
        self.assertEqual(delivery.destination["recipients"], [self.user.email])
        self.assistant.visibility = "private"
        self.assistant.save()
        with patch("lens.alerts._send_notification") as send:
            send_alert_delivery(delivery.uuid)
        send.assert_not_called()
        self.assertEqual(AlertDelivery.objects.get().status, "skipped")

    def test_personal_cannot_subscribe_to_unpublished_or_private_rules(self):
        client = APIClient()
        client.force_authenticate(self.user)
        data = {"rule_uuid": str(self.rule.uuid)}
        self.assertEqual(client.post("/api/lens/alert-subscriptions/", data, format="json").status_code, 400)
        self.rule.personal_subscription_enabled = True
        self.rule.personal_channel_uuid = self.channel.uuid
        self.rule.save()
        self.rule.assistants.add(self.assistant)
        self.assistant.visibility = "private"
        self.assistant.save()
        self.assertEqual(client.post("/api/lens/alert-subscriptions/", data, format="json").status_code, 400)

    def test_multiple_destinations_have_independent_outcomes(self):
        AlertSubscription.objects.create(
            rule=self.rule, channel_uuid=self.channel.uuid, email_recipients=["second@example.com"]
        )
        evaluate_run_alerts(self.run_event().uuid)
        deliveries = list(AlertDelivery.objects.all())
        self.assertEqual(len(deliveries), 2)
        with patch("lens.alerts._send_notification", side_effect=[{"success": True}, {"success": False}]):
            for delivery in deliveries:
                send_alert_delivery(delivery.uuid)
        self.assertEqual(set(AlertDelivery.objects.values_list("status", flat=True)), {"sent", "pending"})
        self.assertEqual(AlertEvent.objects.get().status, "pending")

    def test_duplicate_delivery_job_does_not_send_success_again(self):
        evaluate_run_alerts(self.run_event().uuid)
        delivery = AlertDelivery.objects.get()
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(delivery.uuid)
            send_alert_delivery(delivery.uuid)
        send.assert_called_once()

    def test_delivery_retries_are_bounded(self):
        evaluate_run_alerts(self.run_event().uuid)
        delivery = AlertDelivery.objects.get()
        with patch("lens.alerts._send_notification", return_value={"success": False}) as send:
            for _ in range(6):
                AlertDelivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now())
                send_alert_delivery(delivery.uuid)
        self.assertEqual(send.call_count, 5)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "failed")

    def task_event(self, identifier, status="FAILURE", **metadata):
        """Create one sync attempt with a stable source identity."""

        from agentcore_task.adapters.django.models import TaskExecution

        return TaskExecution.objects.create(
            task_id=identifier,
            task_name="Sync",
            module="lens_datasource",
            status=status,
            finished_at=timezone.now(),
            error=metadata.pop("error", "LENS_SOURCE_SYNC_FAILED"),
            metadata=metadata,
        )

    def test_task_callback_enqueues_and_retries_do_not_alert_early(self):
        self.rule.subject, self.rule.events = "sync", ["sync_failed"]
        self.rule.save()
        task = self.task_event("retrying", status="RETRY")
        self.assertFalse(AlertOutbox.objects.filter(source_uuid=task.task_id).exists())
        task.status = "FAILURE"
        task.save()
        self.assertTrue(AlertOutbox.objects.filter(source_uuid=task.task_id).exists())
        process_alert_outbox()
        self.assertEqual(AlertEvent.objects.get().event_type, "sync_failed")

    def test_user_cancellation_does_not_trigger_failure(self):
        self.rule.subject, self.rule.events = "sync", ["sync_failed", "sync_timeout"]
        self.rule.save()
        self.task_event("cancelled", status="REVOKED", error="DATASOURCE_SYNC_CANCELLED")
        process_alert_outbox()
        self.assertFalse(AlertEvent.objects.exists())

    def test_timeout_cancellation_keeps_its_timeout_classification(self):
        self.rule.subject, self.rule.events = "sync", ["sync_timeout"]
        self.rule.save()
        self.task_event(
            "timeout",
            status="REVOKED",
            error="DATASOURCE_SYNC_CANCELLED",
            timeout_cancel_requested_at=timezone.now().isoformat(),
        )
        process_alert_outbox()
        self.assertEqual(AlertEvent.objects.get().event_type, "sync_timeout")

    def test_running_threshold_uses_metered_usage_and_only_fires_once(self):
        from agentcore_metering.adapters.django.models import LLMUsage

        from lens.alerts import reconcile_alert_sources

        self.rule.events, self.rule.evaluation_mode, self.rule.token_threshold = ["token_exceeded"], "running", 10
        self.rule.save()
        run = self.run_event(status="running", finished_at=None)
        LLMUsage.objects.create(model="test", total_tokens=15, metadata={"run_uuid": str(run.uuid)})
        reconcile_alert_sources()
        process_alert_outbox()
        reconcile_alert_sources()
        self.assertEqual(AlertEvent.objects.count(), 1)
        self.assertEqual(AlertEvent.objects.get().detail["total_tokens"], 15)

    def test_terminal_threshold_does_not_fire_from_running_outbox(self):
        from lens.alerts import reconcile_alert_sources

        self.rule.events, self.rule.duration_threshold = ["run_slow"], 1
        self.rule.save()
        self.run_event(status="running", finished_at=None, started_at=timezone.now() - timedelta(minutes=2))
        reconcile_alert_sources()
        process_alert_outbox()
        self.assertFalse(AlertEvent.objects.exists())

    def test_broker_failure_keeps_committed_outbox_for_recovery(self):
        from lens.alerts import _wake_dispatcher

        with patch("lens.tasks.dispatch_alerts_task.delay", side_effect=RuntimeError("broker offline")):
            _wake_dispatcher()
        self.run_event()
        process_alert_outbox()
        self.assertEqual(AlertDelivery.objects.count(), 1)

    def test_bulk_terminal_updates_are_reconciled(self):
        from lens.alerts import reconcile_alert_sources

        run = self.run_event(status="running", finished_at=None)
        Run.objects.filter(pk=run.pk).update(status="failed", finished_at=timezone.now())
        reconcile_alert_sources()
        process_alert_outbox()
        self.assertTrue(AlertEvent.objects.filter(source_uuid=str(run.uuid)).exists())

    def test_rule_api_rejects_subject_event_mismatch(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(
            "/api/lens/admin/alert-rules/",
            {"name": "Invalid", "subject": "sync", "events": ["run_failed"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_rule_api_creates_multiple_destinations_atomically(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(
            "/api/lens/admin/alert-rules/",
            {
                "name": "Sync",
                "subject": "sync",
                "events": ["sync_failed"],
                "subscriptions": [
                    {"channel_uuid": str(self.channel.uuid), "email_recipients": ["one@example.com"]},
                    {"channel_uuid": str(self.channel.uuid), "email_recipients": ["two@example.com"]},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["subscriptions"]), 2)

    def test_scheduled_repeats_are_suppressed_but_manual_attempts_are_not(self):
        self.rule.subject, self.rule.events, self.rule.cooldown_seconds = "sync", ["sync_failed"], 300
        self.rule.save()
        from lens.models import DataSource

        source = DataSource.objects.create(name="Scheduled source", source_type="git", lensnode=self.node)
        self.task_event("scheduled-first", trigger="scheduled", datasource_uuid=str(source.uuid))
        process_alert_outbox()
        self.task_event("scheduled-second", trigger="scheduled", datasource_uuid=str(source.uuid))
        self.task_event("manual-third", trigger="manual", datasource_uuid=str(source.uuid))
        process_alert_outbox()
        self.assertEqual(AlertDelivery.objects.filter(status="suppressed").count(), 1)
        self.assertEqual(AlertDelivery.objects.filter(status="pending").count(), 2)

    def test_datasource_scopes_exclude_other_resources(self):
        from lens.models import DataSource

        self.rule.subject, self.rule.events = "sync", ["sync_failed"]
        self.rule.save()
        source = DataSource.objects.create(name="Private source", source_type="git", lensnode=self.node)
        self.rule.datasources.add(source)
        self.task_event("unrelated-source")
        process_alert_outbox()
        self.assertFalse(AlertEvent.objects.exists())
        self.task_event("matching-source", datasource_uuid=str(source.uuid))
        process_alert_outbox()
        self.assertEqual(AlertEvent.objects.get().detail["datasource_name"], source.name)

    def test_late_partial_task_summary_is_evaluated(self):
        self.rule.subject, self.rule.events = "processing", ["processing_partial"]
        self.rule.save()
        from agentcore_task.adapters.django.models import TaskExecution

        task = TaskExecution.objects.create(
            task_id="late-summary",
            task_name="Processing",
            module="lens_datasource_conversion",
            status="SUCCESS",
            finished_at=timezone.now(),
        )
        process_alert_outbox()
        self.assertFalse(AlertEvent.objects.exists())
        task.metadata = {"conversion_summary": {"failed": 1}}
        task.save()
        process_alert_outbox()
        self.assertEqual(AlertEvent.objects.get().event_type, "processing_partial")

    def test_retired_run_keeps_alert_history(self):
        run = self.run_event()
        evaluate_run_alerts(run.uuid)
        event = AlertEvent.objects.get()
        run.delete()
        event.refresh_from_db()
        self.assertIsNone(event.run_id)
        self.rule.delete()
        event.refresh_from_db()
        self.assertEqual(event.rule_snapshot["name"], "Timeout")
        self.assertTrue(event.deliveries.exists())

    def test_admin_can_retry_only_failed_destinations(self):
        evaluate_run_alerts(self.run_event().uuid)
        delivery = AlertDelivery.objects.get()
        AlertDelivery.objects.filter(pk=delivery.pk).update(status="failed", attempts=5)
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(f"/api/lens/admin/alert-events/{delivery.event.uuid}/retry/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["retried"], 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempts, 0)
        self.assertEqual(delivery.status, "pending")

    def test_task_completion_service_captures_partial_sync(self):
        from lens.models import DataSource
        from lens.tasks import complete_datasource_sync_task

        self.rule.subject, self.rule.events = "sync", ["sync_partial"]
        self.rule.save()
        source = DataSource.objects.create(name="Docs", source_type="git", lensnode=self.node)
        task = self.task_event("actual-sync", status="STARTED", datasource_uuid=str(source.uuid))
        complete_datasource_sync_task(
            task.task_id, {"status": "success", "synced": 1, "partial_success": True, "failed": 1}
        )
        process_alert_outbox()
        self.assertEqual(AlertEvent.objects.get().event_type, "sync_partial")

    def test_new_rule_does_not_replay_old_business_completion(self):
        from lens.alerts import reconcile_alert_sources

        run = self.run_event(status="running", finished_at=None)
        Run.objects.filter(pk=run.pk).update(status="failed", finished_at=self.rule.created_at - timedelta(minutes=1))
        reconcile_alert_sources()
        process_alert_outbox()
        self.assertFalse(AlertEvent.objects.exists())

    def test_running_scan_rotates_through_more_than_one_batch(self):
        from lens.alerts import reconcile_alert_sources

        self.rule.events, self.rule.evaluation_mode, self.rule.duration_threshold = ["run_slow"], "running", 1
        self.rule.save()
        for _ in range(3):
            self.run_event(status="running", finished_at=None, started_at=timezone.now() - timedelta(minutes=2))
        for _ in range(3):
            reconcile_alert_sources(limit=1)
        self.assertEqual(AlertEvent.objects.count(), 3)

    def test_late_metering_reopens_completed_threshold_evaluation(self):
        from agentcore_metering.adapters.django.models import LLMUsage

        from lens.alerts import reconcile_alert_sources

        self.rule.events, self.rule.token_threshold = ["token_exceeded"], 10
        self.rule.save()
        run = self.run_event(status="done", error="")
        process_alert_outbox()
        self.assertFalse(AlertEvent.objects.exists())
        LLMUsage.objects.create(model="test", total_tokens=20, metadata={"run_uuid": str(run.uuid)})
        reconcile_alert_sources()
        process_alert_outbox()
        self.assertEqual(AlertEvent.objects.get().detail["total_tokens"], 20)

    def test_missing_resource_does_not_group_unrelated_scheduled_attempts(self):
        self.rule.subject, self.rule.events, self.rule.cooldown_seconds = "sync", ["sync_failed"], 300
        self.rule.save()
        self.task_event("missing-first", trigger="scheduled")
        process_alert_outbox()
        self.task_event("missing-second", trigger="scheduled")
        process_alert_outbox()
        self.assertEqual(AlertDelivery.objects.filter(status="pending").count(), 2)

    def test_legacy_api_can_clear_migrated_destination(self):
        self.rule.channel_uuid = self.channel.uuid
        self.rule.email_recipients = ["legacy@example.com"]
        self.rule.save()
        self.subscription.name = "Legacy destination"
        self.subscription.save()
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.patch(f"/api/lens/admin/alert-rules/{self.rule.uuid}/", {"channel_uuid": None}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(self.rule.subscriptions.exists())
        evaluate_run_alerts(self.run_event().uuid)
        self.assertFalse(AlertDelivery.objects.exists())

    def test_notifier_adapter_passes_real_email_arguments_and_records_acceptance(self):
        self.channel.config = {"smtp_host": "smtp.example.invalid", "from_email": "local@example.test"}
        self.channel.save()
        evaluate_run_alerts(self.run_event().uuid)
        delivery = AlertDelivery.objects.get()
        with patch(
            "agentcore_notifier.adapters.django.tasks.send.EmailService.send", return_value={"success": True}
        ) as send:
            send_alert_delivery(delivery.uuid)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["to"], ["ops@example.com"])
        self.assertEqual(send.call_args.kwargs["source_id"], str(delivery.uuid))
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "sent")

    def test_notifier_adapter_uses_webhook_payload_and_preserves_rejections(self):
        webhook = NotificationChannel.objects.create(
            name="Webhook",
            channel_type="webhook",
            config={
                "url": "https://example.invalid/hook",
                "provider_type": "feishu",
            },
        )
        self.subscription.channel_uuid = webhook.uuid
        self.subscription.save()
        evaluate_run_alerts(self.run_event().uuid)
        delivery = AlertDelivery.objects.get()
        with patch(
            "agentcore_notifier.adapters.django.tasks.send.WebhookService.send", return_value={"success": False}
        ) as send:
            send_alert_delivery(delivery.uuid)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["payload"]["msg_type"], "interactive")
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "pending")
        self.assertEqual(delivery.attempts, 1)

    def test_native_target_delivery_records_localized_content_and_remains_idempotent(
        self,
    ):
        from unittest.mock import Mock

        from agentcore_notifier.adapters.django.models import NotificationRecord

        for provider, url in (
            ("feishu", "https://open.feishu.cn/open-apis/bot/v2/hook/test"),
            ("wecom", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"),
            ("dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=test"),
        ):
            channel = NotificationChannel.objects.create(name=provider, channel_type=provider, config={"url": url})
            self.subscription.channel_uuid = channel.uuid
            self.subscription.language = "zh-CN"
            self.subscription.save()
            run = self.run_event()
            evaluate_run_alerts(run.uuid)
            delivery = AlertDelivery.objects.get(event__source_uuid=str(run.uuid))
            with patch("core.services.notification_targets.requests.post") as post:
                post.return_value = Mock(status_code=200, json=Mock(return_value={"code": 0, "errcode": 0}))
                send_alert_delivery(delivery.uuid)
                send_alert_delivery(delivery.uuid)
                post.assert_called_once()
                self.assertIn("问答超时", str(post.call_args.kwargs["json"]))
            delivery.refresh_from_db()
            self.assertEqual(delivery.status, "sent")
            record = NotificationRecord.objects.get(source_id=str(delivery.uuid))
            self.assertEqual(record.locale, "zh-CN")
            self.assertEqual(record.channel_link, channel)

    def test_publish_api_is_idempotent_and_rejects_changed_content(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        payload = {
            "topic": "release.ready",
            "idempotency_key": "release-123",
            "title": "Release ready",
            "body": "Ready for review",
        }
        first = client.post("/api/lens/admin/alert-messages/", payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        repeated = client.post("/api/lens/admin/alert-messages/", payload, format="json")
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(first.data["uuid"], repeated.data["uuid"])
        self.assertFalse(first.data["deduplicated"])
        self.assertTrue(repeated.data["deduplicated"])
        changed = client.post("/api/lens/admin/alert-messages/", {**payload, "body": "Changed"}, format="json")
        self.assertEqual(changed.status_code, 409, changed.data)

    def test_regular_user_cannot_publish_or_read_publication_records(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get("/api/lens/admin/alert-messages/").status_code, 403)
        self.assertEqual(client.post("/api/lens/admin/alert-messages/", {}, format="json").status_code, 403)

    def test_same_destination_across_rules_has_only_one_sendable_delivery(self):
        other = AlertRule.objects.create(name="Duplicate rule", events=["run_failed", "run_timeout"])
        AlertSubscription.objects.create(
            rule=other, channel_uuid=self.channel.uuid, email_recipients=["ops@example.com"]
        )
        evaluate_run_alerts(self.run_event().uuid)
        self.assertEqual(AlertDelivery.objects.filter(status="pending").count(), 1)
        self.assertEqual(AlertDelivery.objects.filter(status="suppressed").count(), 1)

    def test_revoked_duplicate_owner_preserves_an_enabled_subscription(self):
        for action in ("disable_subscription", "delete_subscription", "disable_rule", "delete_rule"):
            with self.subTest(action=action), transaction.atomic():
                other = AlertRule.objects.create(name="Duplicate rule", events=["run_failed"])
                AlertSubscription.objects.create(
                    rule=other, channel_uuid=self.channel.uuid, email_recipients=["ops@example.com"]
                )
                evaluate_run_alerts(self.run_event().uuid)
                original = AlertDelivery.objects.get(status="pending")
                subscription = original.subscription
                if action == "disable_subscription":
                    subscription.enabled = False
                    subscription.save()
                elif action == "delete_subscription":
                    subscription.delete()
                elif action == "disable_rule":
                    subscription.rule.enabled = False
                    subscription.rule.save()
                else:
                    subscription.rule.delete()
                with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
                    send_alert_delivery(original.uuid)
                    for delivery in AlertDelivery.objects.filter(status="pending"):
                        send_alert_delivery(delivery.uuid)
                    for delivery in AlertDelivery.objects.all():
                        send_alert_delivery(delivery.uuid)
                send.assert_called_once()
                self.assertEqual(AlertDelivery.objects.filter(status="sent").count(), 1)
                self.assertEqual(AlertEvent.objects.get(rule=self.rule).status, "sent")
                transaction.set_rollback(True)

    def test_diagnosing_scheduled_failure_honors_cooldown(self):
        from lens.models import AlertDiagnosis, DataSource

        self.rule.subject = "sync"
        self.rule.events = ["sync_failed"]
        self.rule.cooldown_seconds = 3600
        self.rule.diagnosis_mode = "diagnose_first"
        self.rule.save()
        source = DataSource.objects.create(name="Scheduled source", source_type="git", lensnode=self.node)
        self.task_event("diagnosing-first", trigger="scheduled", datasource_uuid=str(source.uuid))
        process_alert_outbox()
        self.task_event("diagnosing-second", trigger="scheduled", datasource_uuid=str(source.uuid))
        process_alert_outbox()
        self.assertEqual(AlertDelivery.objects.filter(status="diagnosing").count(), 1)
        self.assertEqual(AlertDelivery.objects.filter(status="suppressed").count(), 1)
        self.assertEqual(AlertDiagnosis.objects.count(), 1)

    def test_role_operator_can_select_paginated_channels_without_secrets(self):
        from django.contrib.auth.models import Group

        from accounts.models import Role

        group = Group.objects.create(name="Alert operators")
        role = Role.objects.create(name="Alert operator", visible_features=["admin_console"])
        role.groups.add(group)
        self.user.groups.add(group)
        native = NotificationChannel.objects.create(
            name="Native", channel_type="feishu", config={"url": "private-url", "sign_secret": "private-secret"}
        )
        NotificationChannel.objects.create(name="Disabled", channel_type="email", is_active=False, config={})
        NotificationChannel.objects.create(name="Unsupported", channel_type="sms", config={})
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get("/api/lens/admin/alert-rules/channels/", {"page_size": 1})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertIsNotNone(response.data["next"])
        second = client.get("/api/lens/admin/alert-rules/channels/", {"page_size": 1, "page": 2})
        channels = response.data["results"] + second.data["results"]
        self.assertEqual({row["uuid"] for row in channels}, {str(self.channel.uuid), str(native.uuid)})
        for channel in channels:
            self.assertEqual(set(channel), {"uuid", "name", "channel_type", "is_active"})
        self.assertEqual(client.get("/api/v1/admin/notifications/channels/").status_code, 403)
        client.force_authenticate(get_user_model().objects.create_user(username="no-alert-role"))
        self.assertEqual(client.get("/api/lens/admin/alert-rules/channels/").status_code, 403)

    def publish_message(self, **overrides):
        from lens.messages import publish_message

        payload = dict(
            topic="release.ready",
            idempotency_key="release-123",
            title="Ready",
            body="Review the release",
            publisher=self.admin,
        )
        return publish_message(**{**payload, **overrides})

    def message_rule(self, **overrides):
        rule = AlertRule.objects.create(
            name="Release news", subject="message", topic="release.ready", events=["message_published"], **overrides
        )
        return AlertSubscription.objects.create(
            rule=rule, channel_uuid=self.channel.uuid, email_recipients=["release@example.com"]
        )

    def test_publication_matches_only_subscribed_topic_and_is_not_replayed(self):
        subscription = self.message_rule()
        other = AlertRule.objects.create(
            name="Other topic", subject="message", topic="release.failed", events=["message_published"]
        )
        AlertSubscription.objects.create(
            rule=other, channel_uuid=self.channel.uuid, email_recipients=["other@example.com"]
        )
        publication, _ = self.publish_message()
        process_alert_outbox()
        self.publish_message()
        process_alert_outbox()
        event = AlertEvent.objects.get(subject="message")
        self.assertEqual(event.rule_id, subscription.rule_id)
        self.assertEqual(event.detail["body"], "Review the release")
        self.assertEqual(AlertDelivery.objects.count(), 1)
        publication.refresh_from_db()
        self.assertIsNotNone(publication.processed_at)

    def test_unsubscribed_publications_remain_recorded_without_late_replay(self):
        publication, _ = self.publish_message()
        process_alert_outbox()
        self.message_rule()
        self.publish_message()
        process_alert_outbox()
        self.assertTrue(AlertOutbox.objects.filter(pk=publication.pk).exists())
        self.assertFalse(AlertEvent.objects.exists())

    def test_same_business_key_can_be_used_in_different_topics(self):
        first, _ = self.publish_message()
        other, _ = self.publish_message(topic="release.failed")
        self.assertNotEqual(first.pk, other.pk)

    def test_publication_rollback_does_not_leave_a_message(self):
        with transaction.atomic():
            self.publish_message()
            transaction.set_rollback(True)
        self.assertFalse(AlertOutbox.objects.filter(subject="message").exists())

    def test_published_message_topics_support_personal_opt_in(self):
        subscription = self.message_rule(personal_subscription_enabled=True, personal_channel_uuid=self.channel.uuid)
        client = APIClient()
        client.force_authenticate(self.user)
        rules = client.get("/api/lens/alert-subscriptions/rules/").data["results"]
        published = next(rule for rule in rules if rule["uuid"] == str(subscription.rule.uuid))
        self.assertEqual((published["subject"], published["topic"]), ("message", "release.ready"))
        response = client.post(
            "/api/lens/alert-subscriptions/", {"rule_uuid": str(subscription.rule.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.publish_message()
        process_alert_outbox()
        delivery = AlertDelivery.objects.get(subscription__user=self.user)
        self.assertEqual(delivery.destination["recipients"], [self.user.email])
        subscription.rule.personal_subscription_enabled = False
        subscription.rule.save()
        with patch("lens.alerts._send_notification") as send:
            send_alert_delivery(delivery.uuid)
        send.assert_not_called()
        self.assertEqual(AlertDelivery.objects.get(pk=delivery.pk).status, "skipped")

    def test_changed_topic_does_not_receive_already_queued_messages(self):
        subscription = self.message_rule()
        self.publish_message()
        process_alert_outbox()
        delivery = AlertDelivery.objects.get(subscription=subscription)
        subscription.rule.topic = "release.failed"
        subscription.rule.save()
        with patch("lens.alerts._send_notification") as send:
            send_alert_delivery(delivery.uuid)
        send.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "skipped")

    def test_message_subject_change_requires_clearing_existing_node_scope(self):
        self.rule.lensnodes.add(self.node)
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.patch(
            f"/api/lens/admin/alert-rules/{self.rule.uuid}/",
            {"subject": "message", "topic": "release.ready", "events": ["message_published"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("lensnodes", response.data)

    def test_malformed_external_metadata_does_not_break_task_completion(self):
        from agentcore_task.adapters.django.models import TaskExecution

        task = TaskExecution.objects.create(
            task_id="malformed-metadata",
            task_name="processing",
            module="lens_datasource_conversion",
            status="FAILURE",
            finished_at=timezone.now(),
            metadata={"datasource_uuid": "invalid", "conversion_summary": {"failed": {"invalid": "count"}}},
        )
        occurrence = capture_task_alert(task)
        self.assertEqual(occurrence.payload["datasource_uuid"], "")
        self.assertEqual(occurrence.payload["failed_files"], 0)

    def test_publication_contract_rejects_invalid_topics_and_oversized_content(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        payload = {"topic": "invalid topic", "idempotency_key": "key", "title": "Title", "body": "Body"}
        self.assertEqual(client.post("/api/lens/admin/alert-messages/", payload, format="json").status_code, 400)
        self.assertEqual(
            client.post(
                "/api/lens/admin/alert-messages/", {**payload, "topic": "valid", "body": "a" * 12001}, format="json"
            ).status_code,
            400,
        )
