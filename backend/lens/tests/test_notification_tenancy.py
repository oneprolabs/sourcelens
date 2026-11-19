"""Cross-tenant notification API, delivery and immutable history boundaries."""

from datetime import timedelta
from unittest.mock import patch

from agentcore_notifier.adapters.django.models import NotificationChannel, NotificationRecord
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantDelegation, TenantMembership
from lens.alerts import _delivery_result, evaluate_run_alerts
from lens.messages import publish_message
from lens.models import (
    AlertDelivery,
    AlertEvent,
    AlertRule,
    AlertSubscription,
    Assistant,
    LensNode,
    NotificationChannelScope,
    Run,
    Session,
)
from lens.notification_scope import notification_record_scope
from lens.services import create_execution_run

ROOT = "/api/v1/admin/notifications/"
RULES = "/api/lens/admin/alert-rules/"


class NotificationTenancyTests(TestCase):
    """Keep queries, validation and actual sends in the same server-owned scope."""

    def setUp(self):
        self.node = LensNode.objects.create(name="Notification node", status="online", enrollment_status="approved")
        self.a = Tenant.objects.create(name="Notify A", slug="notify-a")
        self.b = Tenant.objects.create(name="Notify B", slug="notify-b")
        users = get_user_model()
        self.admin = users.objects.create_user(username="notify-a", email="a@example.com")
        self.other = users.objects.create_user(username="notify-b", email="b@example.com", is_staff=True)
        self.member = users.objects.create_user(username="notify-member")
        self.platform = users.objects.create_user(username="notify-platform")
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        for user, tenant, role in (
            (self.admin, self.a, "tenant_admin"),
            (self.other, self.b, "tenant_admin"),
            (self.member, self.a, "member"),
        ):
            TenantMembership.objects.create(user=user, tenant=tenant, role=role)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.ca = self.channel(self.a, "A")
        self.cb = self.channel(self.b, "B")
        self.cp = self.channel(None, "Platform")

    def channel(self, tenant, name):
        channel = NotificationChannel.objects.create(
            channel_type="email", name=name, config={"smtp_password": "credential"}
        )
        NotificationChannelScope.objects.create(channel=channel, tenant=tenant)
        return channel

    def rule(self, tenant, channel):
        rule = AlertRule.objects.create(tenant=tenant, name="Failure", events=["run_failed"])
        AlertSubscription.objects.create(rule=rule, channel_uuid=channel.uuid, email_recipients=["ops@example.com"])
        return rule

    def occurrence(self, tenant):
        assistant = Assistant.objects.create(
            tenant=tenant, lensnode=self.node, name="Private", slug="private-" + tenant.slug
        )
        session = Session.objects.create(tenant=tenant, assistant=assistant, user=self.admin)
        run = create_execution_run(session=session, question="Secret question", enqueue=False)
        run.status, run.finished_at = Run.Status.FAILED, timezone.now()
        run.save()
        evaluate_run_alerts(run.uuid)
        return run

    def test_nonstaff_admin_sees_only_own_channels_and_masks_credentials(self):
        response = self.client.get(ROOT + "channels/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["uuid"] for row in response.data["results"]], [str(self.ca.uuid)])
        self.assertNotIn("credential", str(response.data))

    def test_member_and_unscoped_staff_do_not_gain_admin_access(self):
        staff = get_user_model().objects.create_user(username="unscoped-staff", is_staff=True)
        for user in (self.member, staff):
            self.client.force_authenticate(user)
            for url in (ROOT + "channels/", ROOT + "notification-stats/", RULES):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_tenant_cannot_select_platform_scope_or_global_configuration(self):
        self.assertEqual(self.client.get(ROOT + "channels/", HTTP_X_NOTIFICATION_SCOPE="platform").status_code, 403)
        for suffix in ("global/", "silence-rules/"):
            self.assertEqual(self.client.get(ROOT + suffix).status_code, 403)
            self.assertEqual(self.client.put(ROOT + suffix, {"value": {}}, format="json").status_code, 403)

    def test_invalid_calendar_filters_return_validation_errors(self):
        for suffix in ("notification-records/", "notification-stats/"):
            for field in ("start_date", "end_date"):
                for value in ("2026-99-30", "2026-02-30", "invalid"):
                    with self.subTest(suffix=suffix, field=field, value=value):
                        response = self.client.get(ROOT + suffix, {field: value})
                        self.assertEqual(response.status_code, 400, response.data)
                        self.assertIn(field, response.data)

    def test_cross_scope_crud_validate_and_test_never_call_transport(self):
        with patch("core.views.notification_targets._validate_email_config") as validate, patch(
            "core.views.notification_targets.notification_test.send_test_message"
        ) as send:
            for channel in (self.cb, self.cp):
                url = ROOT + f"channels/{channel.uuid}/"
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.put(url, {"name": "Stolen"}, format="json").status_code, 404)
                self.assertEqual(self.client.post(url + "test/").status_code, 404)
                self.assertEqual(
                    self.client.post(
                        ROOT + "channels/validate/",
                        {"channel_type": "email", "channel_uuid": str(channel.uuid), "config": {}},
                        format="json",
                    ).status_code,
                    404,
                )
                self.assertEqual(self.client.delete(url).status_code, 404)
            validate.assert_not_called()
            send.assert_not_called()

    def test_bulk_delete_is_atomic_for_mixed_selection(self):
        response = self.client.post(
            ROOT + "channels/bulk-delete/", {"channel_ids": [str(self.ca.uuid), str(self.cb.uuid)]}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(NotificationChannel.objects.filter(pk=self.ca.pk).exists())

    def test_default_changes_and_ownership_injection_stay_local(self):
        self.cb.is_default = True
        self.cb.save()
        response = self.client.post(
            ROOT + "channels/",
            {"name": "New", "channel_type": "email", "is_default": True, "tenant_id": self.b.pk, "config": {}},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        channel = NotificationChannel.objects.get(uuid=response.data["uuid"])
        self.assertEqual(channel.ownership.tenant_id, self.a.pk)
        self.cb.refresh_from_db()
        self.assertTrue(self.cb.is_default)

    def test_foreign_channel_and_resources_cannot_be_bound_to_rule(self):
        foreign = Assistant.objects.create(tenant=self.b, name="Foreign", slug="foreign")
        for extra in (
            {"channel_uuid": str(self.cb.uuid)},
            {"assistants": [str(foreign.uuid)]},
            {"subscriptions": [{"channel_uuid": str(self.cb.uuid)}]},
        ):
            response = self.client.post(RULES, {"name": "No", "events": ["run_failed"], **extra}, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(AlertRule.objects.exists())

    def test_rule_event_and_message_queries_do_not_cross_scope(self):
        rule = self.rule(self.b, self.cb)
        event = AlertEvent.objects.create(
            tenant=self.b, rule=rule, event_type="run_failed", detail={"question": "private"}
        )
        self.assertEqual(self.client.get(RULES + f"{rule.uuid}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/lens/admin/alert-events/{event.uuid}/").status_code, 404)
        self.assertEqual(self.client.post(f"/api/lens/admin/alert-events/{event.uuid}/retry/").status_code, 404)
        publication, _ = publish_message(
            topic="release.ready",
            idempotency_key="same",
            title="Secret",
            body="B only",
            publisher=self.other,
            tenant=self.b,
        )
        self.assertEqual(self.client.get(f"/api/lens/admin/alert-messages/{publication.uuid}/").status_code, 404)

    def test_matching_and_actual_delivery_are_tenant_local(self):
        own = self.rule(self.a, self.ca)
        self.rule(self.b, self.cb)
        self.rule(None, self.cp)
        run = self.occurrence(self.a)
        self.assertEqual(list(AlertEvent.objects.values_list("rule_id", flat=True)), [own.pk])
        delivery = AlertDelivery.objects.get()
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            self.assertTrue(_delivery_result(delivery)["success"])
        self.assertEqual(send.call_args.kwargs["channel_uuid"], str(self.ca.uuid))
        self.assertEqual(delivery.event.tenant_id, run.tenant_id)

    def test_tampered_destination_cannot_send_and_has_no_default_fallback(self):
        rule = self.rule(self.a, self.cb)
        self.occurrence(self.a)
        delivery = AlertDelivery.objects.get()
        with patch("lens.alerts._send_notification") as send:
            self.assertTrue(_delivery_result(delivery)["skipped"])
            send.assert_not_called()
        rule.subscriptions.update(channel_uuid=self.ca.uuid)
        delivery.destination["channel_uuid"] = str(self.ca.uuid)
        delivery.save()
        NotificationChannelScope.objects.filter(channel=self.ca).update(tenant=self.b)
        with patch("lens.alerts._send_notification") as send:
            self.assertTrue(_delivery_result(delivery)["skipped"])
            send.assert_not_called()

    def test_tenant_suspension_stops_queued_send(self):
        self.rule(self.a, self.ca)
        self.occurrence(self.a)
        self.a.status = "suspended"
        self.a.save()
        with patch("lens.alerts._send_notification") as send:
            self.assertTrue(_delivery_result(AlertDelivery.objects.get())["skipped"])
            send.assert_not_called()

    def test_record_ownership_survives_channel_delete(self):
        record = NotificationRecord.objects.create(
            channel_link=self.ca, source_app="lens", provider_type="email", payload={"subject": "Own"}, status="success"
        )
        other = NotificationRecord.objects.create(
            channel_link=self.cb,
            source_app="lens",
            provider_type="email",
            payload={"subject": "Foreign"},
            status="failed",
        )
        self.ca.delete()
        response = self.client.get(ROOT + "notification-records/")
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["uuid"], str(record.uuid))
        self.assertEqual(self.client.get(ROOT + f"notification-records/{other.uuid}/").status_code, 404)
        self.assertEqual(self.client.get(ROOT + "notification-stats/").data["summary"]["total"], 1)

    def test_unsaved_validation_record_is_pinned_to_scope(self):
        with notification_record_scope(self.a):
            record = NotificationRecord.objects.create(
                source_app="validation", user=self.platform, provider_type="email", payload={}
            )
        self.assertEqual(record.ownership.tenant_id, self.a.pk)
        self.assertEqual(self.client.get(ROOT + "notification-records/").data["total"], 1)

    def test_unowned_channels_and_records_are_not_exposed(self):
        channel = NotificationChannel.objects.create(name="Unowned", channel_type="email")
        NotificationRecord.objects.create(channel_link=channel, source_app="unknown", provider_type="email", payload={})
        self.assertEqual(self.client.get(ROOT + "channels/").data["total"], 1)
        self.assertEqual(self.client.get(ROOT + "notification-records/").data["total"], 0)

    def test_message_idempotency_is_local_to_tenant(self):
        args = dict(topic="release.ready", idempotency_key="same", title="Ready", body="Body", publisher=self.admin)
        a, first = publish_message(**args, tenant=self.a)
        b, second = publish_message(**args, tenant=self.b)
        repeated, created = publish_message(**args, tenant=self.a)
        self.assertTrue(first and second)
        self.assertFalse(created)
        self.assertEqual(a.pk, repeated.pk)
        self.assertNotEqual(a.pk, b.pk)

    def test_platform_and_delegated_scopes_are_independent(self):
        self.client.force_authenticate(self.platform)
        self.assertEqual(self.client.get(ROOT + "channels/").data["results"][0]["uuid"], str(self.cp.uuid))
        delegation = TenantDelegation.objects.create(
            platform_user=self.platform, tenant=self.a, expires_at=timezone.now() + timedelta(hours=1)
        )
        response = self.client.get(ROOT + "channels/", HTTP_X_DELEGATION_ID=str(delegation.uuid))
        self.assertEqual(response.data["results"][0]["uuid"], str(self.ca.uuid))
        response = self.client.get(
            ROOT + "channels/", HTTP_X_DELEGATION_ID=str(delegation.uuid), HTTP_X_NOTIFICATION_SCOPE="platform"
        )
        self.assertEqual(response.data["results"][0]["uuid"], str(self.cp.uuid))

    def test_audit_never_contains_configuration_or_message_values(self):
        response = self.client.put(
            ROOT + f"channels/{self.ca.uuid}/",
            {"config": {"smtp_password": "new-credential", "url": "secret-url"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        audit = AuditLog.objects.get(action="notification_channel.update")
        self.assertEqual(audit.tenant_id, self.a.pk)
        self.assertNotIn("credential", str(audit.metadata))
        self.assertNotIn("secret-url", str(audit.metadata))

    def test_registration_challenge_cannot_be_polled_by_another_scope(self):
        state = {
            "device_code": "private-device",
            "verification_uri_complete": "https://example.com/qr",
            "interval": 2,
            "expires_in": 100,
        }
        with patch("core.views.notification_records.device_registration.begin_app_registration", return_value=state):
            self.assertEqual(self.client.post(ROOT + "channels/feishu-app/register/start/").status_code, 200)
        self.client.force_authenticate(self.other)
        with patch("core.views.notification_records.device_registration.poll_app_registration") as poll:
            response = self.client.get(ROOT + "channels/feishu-app/register/poll/", {"device_code": "private-device"})
            self.assertEqual(response.status_code, 404)
            poll.assert_not_called()

    def test_readonly_delegation_blocks_send_validate_and_poll(self):
        self.client.force_authenticate(self.platform)
        delegation = TenantDelegation.objects.create(
            platform_user=self.platform, tenant=self.a, mode="readonly", expires_at=timezone.now() + timedelta(hours=1)
        )
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        self.assertEqual(self.client.post(ROOT + f"channels/{self.ca.uuid}/test/", **headers).status_code, 403)
        self.assertEqual(self.client.post(ROOT + "channels/validate/", {}, format="json", **headers).status_code, 403)
        self.assertEqual(
            self.client.get(ROOT + "channels/feishu-app/register/poll/", {"device_code": "x"}, **headers).status_code,
            403,
        )

    def test_platform_config_changes_real_beat_schedule_atomically(self):
        from agentcore_notifier.adapters.django.services import notification_config
        from django_celery_beat.models import PeriodicTask

        from core.services.notification_config import CLEANUP_TASK

        self.client.force_authenticate(self.platform)
        response = self.client.put(
            ROOT + "global/",
            {"value": {"retention_days": 35, "cleanup_enabled": True, "cleanup_crontab": "7 3 * * *"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        task = PeriodicTask.objects.get(name=CLEANUP_TASK)
        self.assertTrue(task.enabled)
        self.assertEqual(task.crontab.minute, "7")
        self.assertEqual(task.crontab.hour, "3")
        task.last_run_at = timezone.now()
        task.queue, task.kwargs = "operator-queue", '{"keep": true}'
        task.save()
        self.assertEqual(
            self.client.put(ROOT + "global/", {"value": {"cleanup_enabled": False}}, format="json").status_code, 200
        )
        task.refresh_from_db()
        self.assertFalse(task.enabled)
        self.assertIsNone(task.last_run_at)
        self.assertEqual(task.queue, "operator-queue")
        response = self.client.put(
            ROOT + "global/", {"value": {"retention_days": 9, "cleanup_crontab": "99 99 * * *"}}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(notification_config.get_config("global")["retention_days"], 35)

    def test_foreign_user_and_group_recipients_are_rejected(self):
        from django.contrib.auth.models import Group

        from accounts.models import TenantGroup

        group = Group.objects.create(name="Other recipients")
        TenantGroup.objects.create(tenant=self.b, auth_group=group, name="Other")
        for field, value in (("users", self.other.pk), ("groups", group.pk)):
            response = self.client.post(
                RULES,
                {
                    "name": "No",
                    "events": ["run_failed"],
                    "subscriptions": [{"channel_uuid": str(self.ca.uuid), field: [value]}],
                },
                format="json",
            )
            self.assertEqual(response.status_code, 400)

    def test_previous_statistics_window_cannot_count_another_tenant(self):
        for channel in (self.ca, self.cb):
            record = NotificationRecord.objects.create(
                channel_link=channel, provider_type="email", source_app="lens", payload={}, status="success"
            )
            NotificationRecord.objects.filter(pk=record.pk).update(created_at=timezone.now() - timedelta(days=2))
        today = timezone.now().date()
        response = self.client.get(
            ROOT + "notification-stats/",
            {"start_date": str(today - timedelta(days=1)), "end_date": str(today), "granularity": "day"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["total"], 0)
        self.assertEqual(response.data["summary_prev"]["total"], 1)
        self.assertEqual(len(response.data["series"]), 24)

    def test_registration_completion_cannot_update_another_tenants_app(self):
        channel = NotificationChannel.objects.create(
            name="Foreign app", channel_type="feishu_app", config={"app_id": "original", "app_secret": "keep"}
        )
        NotificationChannelScope.objects.create(channel=channel, tenant=self.b)
        state = {
            "device_code": "owned-code",
            "verification_uri_complete": "https://example.com/qr",
            "interval": 2,
            "expires_in": 100,
        }
        with patch("core.views.notification_records.device_registration.begin_app_registration", return_value=state):
            self.client.post(ROOT + "channels/feishu-app/register/start/")
        with patch(
            "core.views.notification_records.device_registration.poll_app_registration",
            return_value={"status": "success", "client_id": "new-id", "client_secret": "new-secret"},
        ):
            response = self.client.get(ROOT + "channels/feishu-app/register/poll/", {"device_code": "owned-code"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("new-secret", str(response.data))
        new_channel = NotificationChannel.objects.get(uuid=response.data["channel"]["uuid"])
        self.assertEqual(new_channel.ownership.tenant_id, self.a.pk)
        channel.refresh_from_db()
        self.assertEqual(channel.config["app_secret"], "keep")

    def test_feishu_test_never_falls_back_to_another_tenants_credentials(self):
        foreign = NotificationChannel.objects.create(
            channel_type="feishu_app", name="Foreign app", config={"app_id": "foreign", "app_secret": "secret"}
        )
        NotificationChannelScope.objects.create(channel=foreign, tenant=self.b)
        personal = NotificationChannel.objects.create(
            channel_type="feishu_app", name="Personal", user=self.admin, config={"open_id": "person"}
        )
        NotificationChannelScope.objects.create(channel=personal, tenant=self.a)
        with patch("core.views.notification_targets.notification_test.send_test_message") as send:
            self.assertEqual(self.client.post(ROOT + f"channels/{personal.uuid}/test/").status_code, 400)
            send.assert_not_called()

    def test_backfill_assigns_legacy_channels_and_surviving_record_ownership(self):
        import importlib

        from django.apps import apps
        from django.db import connection

        from lens.models import NotificationRecordScope

        default, _ = Tenant.objects.get_or_create(slug="default", defaults={"name": "Default"})
        channel = NotificationChannel.objects.create(name="Legacy", channel_type="email")
        personal = NotificationChannel.objects.create(name="Personal legacy", channel_type="email", user=self.other)
        record = NotificationRecord.objects.create(source_app="legacy", provider_type="email", payload={})
        backfill = importlib.import_module("lens.migrations.0070_multitenancy").backfill_notification_ownership
        with connection.schema_editor(atomic=False) as editor:
            backfill(apps, editor)
            backfill(apps, editor)
        self.assertEqual(channel.ownership.tenant_id, default.pk)
        self.assertEqual(personal.ownership.tenant_id, self.b.pk)
        self.assertEqual(NotificationRecordScope.objects.get(record=record).tenant_id, default.pk)
