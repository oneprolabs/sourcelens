"""Platform batch control and tenant administrator notice contracts."""

import threading
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantApiKey, TenantMembership


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_HOST_USER="")
class PlatformKeyControlTests(TestCase):
    """Control batches across tenants without exposing their notification recipients."""

    def setUp(self):
        """Create two tenants with independent administrators and hashed credentials."""
        self.platform = get_user_model().objects.create_user(username="control-platform")
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        self.tenants, self.admins, self.keys = [], [], []
        for i in range(2):
            tenant = Tenant.objects.create(name=f"Tenant {i}", slug=f"control-{i}")
            admin = get_user_model().objects.create_user(username=f"control-{i}", email=f"admin{i}@example.com")
            TenantMembership.objects.create(tenant=tenant, user=admin, role="tenant_admin")
            key, _ = TenantApiKey.issue(
                tenant=tenant,
                created_by=admin,
                name=f"Integration {i}",
                scope="all",
                assistant_uuids=[],
                expires_at=None,
            )
            self.tenants.append(tenant)
            self.admins.append(admin)
            self.keys.append(key)
        self.client = APIClient()
        self.client.force_authenticate(self.platform)

    def bulk(self, action="disable", keys=None):
        """Send the exact list versions reviewed by the platform operator."""
        return self.client.post(
            "/api/v1/platform/api-keys/bulk",
            {
                "action": action,
                "keys": [{"uuid": str(k.uuid), "updated_at": k.updated_at.isoformat()} for k in (keys or self.keys)],
            },
            format="json",
        )

    def test_bulk_control_is_atomic_audited_and_creates_one_notice_per_tenant(self):
        """The response contains operational metadata without secrets or recipient identities."""
        response = self.bulk()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["changed"], response.data["skipped"]), (2, 0))
        self.assertEqual(len(response.data["notices"]), 2)
        for key in self.keys:
            key.refresh_from_db()
            self.assertEqual(key.status, "disabled")
            self.assertTrue(
                AuditLog.objects.filter(
                    action="api_key.force_disable",
                    tenant=key.tenant,
                    actor_user=self.platform,
                    resource_id=str(key.uuid),
                ).exists()
            )
        self.assertEqual({row["status"] for row in response.data["notices"]}, {"pending"})
        self.assertNotIn("admin0@example.com", str(response.data))
        self.assertNotIn(self.keys[0].key_hash, str(response.data))

    def test_only_disable_and_revoke_are_supported_and_skips_are_explicit(self):
        """Platform control never enables, extends or rotates tenant credentials."""
        for action in ("enable", "extend", "rotate"):
            self.assertEqual(self.bulk(action).status_code, 400)
        self.keys[0].status = "revoked"
        self.keys[0].save()
        self.keys[1].status = "disabled"
        self.keys[1].save()
        response = self.bulk()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["changed"], 0)
        self.assertEqual({r["reason"] for r in response.data["skipped_items"]}, {"revoked", "unchanged"})
        self.assertEqual(response.data["notices"], [])

    def test_stale_or_missing_key_rejects_the_whole_selection(self):
        """No other tenant is changed or notified after a selection mismatch."""
        TenantApiKey.objects.filter(pk=self.keys[0].pk).update(updated_at=timezone.now())
        self.assertEqual(self.bulk().status_code, 409)
        TenantApiKey.objects.filter(pk=self.keys[0].pk).delete()
        self.assertEqual(self.bulk().status_code, 404)
        self.keys[1].refresh_from_db()
        self.assertEqual(self.keys[1].status, "active")
        self.assertFalse(AuditLog.objects.filter(action="api_key.force_disable").exists())

    def test_tenant_admin_staff_member_and_api_key_cannot_force_control(self):
        """A tenant operator cannot enter platform batch or notice endpoints."""
        for actor, token in [(self.admins[0], None), (self.platform, {"principal_type": "tenant_api_key"})]:
            self.client.force_authenticate(actor, token=token)
            self.assertEqual(self.bulk().status_code, 403)
        self.admins[0].is_staff = True
        self.admins[0].save()
        self.client.force_authenticate(self.admins[0])
        self.assertEqual(self.bulk().status_code, 403)

    def test_single_action_persists_notice_and_enqueues_only_after_commit(self):
        """Broker payloads contain only notice UUIDs and cannot run before commit."""
        with patch("accounts.tasks.deliver_api_key_notice_task.delay") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(f"/api/v1/platform/api-keys/{self.keys[0].uuid}/disable")
                self.assertEqual(response.status_code, 200, response.data)
                enqueue.assert_not_called()
            enqueue.assert_called_once_with(response.data["notification"]["uuid"])
        self.assertEqual(response.data["notification"]["status"], "pending")

    def test_rollback_discards_both_notice_and_enqueue(self):
        """A rolled-back action never sends a notice about a change that did not happen."""
        from accounts.models import ApiKeyControlNotice

        with patch("accounts.tasks.deliver_api_key_notice_task.delay") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(ValueError), transaction.atomic():
                    self.client.post(f"/api/v1/platform/api-keys/{self.keys[0].uuid}/disable")
                    raise ValueError("rollback")
            enqueue.assert_not_called()
        self.assertFalse(ApiKeyControlNotice.objects.exists())
        self.keys[0].refresh_from_db()
        self.assertEqual(self.keys[0].status, "active")

    def test_delivery_is_isolated_localized_and_idempotent_after_success(self):
        """Each tenant receives only its own key summary; secrets are never mailed."""
        from accounts.models import ApiKeyControlNotice
        from accounts.services.api_key_notices import deliver_notice

        response = self.bulk("revoke")
        self.assertEqual(response.status_code, 200, response.data)
        notices = list(ApiKeyControlNotice.objects.order_by("tenant_id"))
        for notice in notices:
            deliver_notice(notice.pk)
            deliver_notice(notice.pk)
        self.assertEqual(len(mail.outbox), 2)
        messages = {m.to[0]: m for m in mail.outbox}
        for i, admin in enumerate(self.admins):
            body = messages[admin.email].body
            self.assertIn(self.keys[i].name, body)
            self.assertIn(self.keys[i].key_display, body)
            self.assertNotIn(self.keys[1 - i].name, body)
            self.assertNotIn(self.keys[i].key_hash, body)
        self.assertEqual(set(ApiKeyControlNotice.objects.values_list("status", flat=True)), {"sent"})

    def test_failure_does_not_restore_key_and_can_retry_without_resending_successes(self):
        """Retry only failed recipients after an explicit platform request."""
        from accounts.models import ApiKeyControlNotice
        from accounts.services.api_key_notices import deliver_notice

        extra = get_user_model().objects.create_user(username="second-admin", email="second@example.com")
        TenantMembership.objects.create(tenant=self.tenants[0], user=extra, role="tenant_admin")
        response = self.bulk("revoke", [self.keys[0]])
        notice = ApiKeyControlNotice.objects.get(uuid=response.data["notices"][0]["uuid"])
        from django.core.mail import EmailMultiAlternatives

        original_send = EmailMultiAlternatives.send

        def fail_second(message, *args, **kwargs):
            """Simulate one rejected recipient without failing another tenant or actor."""
            if message.to == [extra.email]:
                raise OSError("private SMTP error")
            return original_send(message, *args, **kwargs)

        with patch.object(EmailMultiAlternatives, "send", fail_second):
            deliver_notice(notice.pk)
        notice.refresh_from_db()
        self.assertEqual(notice.status, "failed")
        self.keys[0].refresh_from_db()
        self.assertEqual(self.keys[0].status, "revoked")
        self.assertEqual(len(mail.outbox), 1)
        detail = self.client.get(f"/api/v1/platform/api-key-notices/{notice.pk}")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertNotIn("private SMTP error", str(detail.data))
        with patch("accounts.tasks.deliver_api_key_notice_task.delay"):
            retried = self.client.post(f"/api/v1/platform/api-key-notices/{notice.pk}/retry")
        self.assertEqual(retried.status_code, 200, retried.data)
        deliver_notice(notice.pk)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[-1].to, [extra.email])
        self.client.force_authenticate(self.admins[0])
        self.assertEqual(self.client.get(f"/api/v1/platform/api-key-notices/{notice.pk}").status_code, 403)

    def test_email_backend_initialization_failure_is_persisted_for_explicit_retry(self):
        """Transport initialization errors cannot strand a notice in a misleading pending state."""
        from accounts.models import ApiKeyControlNotice
        from accounts.services.api_key_notices import deliver_notice

        response = self.bulk("revoke", [self.keys[0]])
        notice = ApiKeyControlNotice.objects.get(pk=response.data["notices"][0]["uuid"])
        with patch("accounts.services.api_key_notices.get_connection", side_effect=OSError("private backend error")):
            deliver_notice(notice.pk)
        notice.refresh_from_db()
        self.assertEqual((notice.status, notice.attempts, notice.error_code), ("failed", 1, "DELIVERY_FAILED"))
        self.assertEqual(mail.outbox, [])
        self.keys[0].refresh_from_db()
        self.assertEqual(self.keys[0].status, "revoked")
        detail = self.client.get(f"/api/v1/platform/api-key-notices/{notice.pk}")
        self.assertNotIn("private backend error", str(detail.data))
        with patch("accounts.tasks.deliver_api_key_notice_task.delay") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                retried = self.client.post(f"/api/v1/platform/api-key-notices/{notice.pk}/retry")
            enqueue.assert_called_once_with(str(notice.pk))
        self.assertEqual(retried.status_code, 200, retried.data)
        deliver_notice(notice.pk)
        self.assertEqual(len(mail.outbox), 1)
        notice.refresh_from_db()
        self.assertEqual((notice.status, notice.attempts), ("sent", 2))

    def test_delivery_ignores_inactive_nonadmin_foreign_and_empty_email_members(self):
        """Recipient lookup uses live membership and does not infer the key creator as recipient."""
        from accounts.models import ApiKeyControlNotice
        from accounts.services.api_key_notices import deliver_notice

        self.admins[0].is_active = False
        self.admins[0].save()
        response = self.bulk("disable", [self.keys[0]])
        notice = ApiKeyControlNotice.objects.get(uuid=response.data["notices"][0]["uuid"])
        deliver_notice(notice.pk)
        notice.refresh_from_db()
        self.assertEqual(notice.status, "failed")
        self.assertEqual(notice.error_code, "NO_ACTIVE_ADMINS")
        self.assertEqual(mail.outbox, [])

    def test_broker_failure_preserves_notice_for_bounded_periodic_recovery(self):
        """The recovery task dispatches old pending rows but never resends sent or failed rows."""
        from accounts.models import ApiKeyControlNotice
        from accounts.tasks import dispatch_pending_api_key_notices_task

        with patch("accounts.tasks.deliver_api_key_notice_task.delay", side_effect=OSError("broker unavailable")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.bulk()
        self.assertEqual(response.status_code, 200)
        notices = list(ApiKeyControlNotice.objects.all())
        self.assertEqual(len(notices), 2)
        ApiKeyControlNotice.objects.filter(pk=notices[0].pk).update(updated_at=timezone.now() - timedelta(minutes=3))
        with patch("accounts.tasks.deliver_api_key_notice_task.delay") as enqueue:
            dispatch_pending_api_key_notices_task()
        enqueue.assert_called_once_with(str(notices[0].pk))
        ApiKeyControlNotice.objects.filter(pk=notices[0].pk).update(status="sent")
        with patch("accounts.tasks.deliver_api_key_notice_task.delay") as enqueue:
            dispatch_pending_api_key_notices_task()
        enqueue.assert_not_called()

    def test_invalid_selection_and_notice_methods_are_bounded(self):
        """Malformed batches and retries of pending notices cannot mutate credentials."""
        item = {"uuid": str(self.keys[0].pk), "updated_at": self.keys[0].updated_at.isoformat()}
        for rows in [[], [item, item], [item] * 101, [{"uuid": "invalid"}]]:
            response = self.client.post(
                "/api/v1/platform/api-keys/bulk", {"action": "disable", "keys": rows}, format="json"
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/v1/platform/api-key-notices").status_code, 405)
        response = self.bulk()
        notice = response.data["notices"][0]
        self.assertEqual(self.client.post(f"/api/v1/platform/api-key-notices/{notice['uuid']}/retry").status_code, 409)
        listed = self.client.get("/api/v1/platform/api-key-notices")
        self.assertEqual(len(listed.data["results"]), 2)
        self.assertNotIn("delivered_user_ids", str(listed.data))
        self.assertNotIn("keys", str(listed.data))
        first = self.client.get("/api/v1/platform/api-key-notices", {"page_size": 1})
        second = self.client.get("/api/v1/platform/api-key-notices", {"page_size": 1, "page": 2})
        self.assertEqual(first.data["count"], 2)
        self.assertEqual(len(first.data["results"]), 1)
        self.assertNotEqual(first.data["results"][0]["uuid"], second.data["results"][0]["uuid"])

    def test_delivery_uses_live_recipients_without_exposing_other_members(self):
        """A demoted creator is not notified, and a new admin receives the tenant's notice."""
        from accounts.models import ApiKeyControlNotice
        from accounts.services.api_key_notices import deliver_notice

        response = self.bulk("revoke", [self.keys[0]])
        notice_uuid = response.data["notices"][0]["uuid"]
        TenantMembership.objects.filter(user=self.admins[0]).update(role="member")
        replacement = get_user_model().objects.create_user(
            username="replacement-admin", email="replacement@example.com"
        )
        TenantMembership.objects.create(tenant=self.tenants[0], user=replacement, role="tenant_admin")
        deliver_notice(notice_uuid)
        self.assertEqual([m.to for m in mail.outbox], [[replacement.email]])
        self.assertEqual(ApiKeyControlNotice.objects.get(pk=notice_uuid).status, "sent")

    def test_single_noop_does_not_duplicate_notices(self):
        """Repeated disable requests never flood tenant administrators."""
        url = f"/api/v1/platform/api-keys/{self.keys[0].uuid}/disable"
        first = self.client.post(url)
        self.assertIsNotNone(first.data["notification"])
        second = self.client.post(url)
        self.assertIsNone(second.data["notification"])

    def test_notice_localization_and_header_safety(self):
        """Tenant-authored labels remain literal text and cannot create message headers."""
        from accounts.services.api_key_notices import deliver_notice

        self.tenants[0].default_language = "es"
        self.tenants[0].name = "Tenant\r\nBcc: attacker@example.com"
        self.tenants[0].save()
        response = self.bulk("revoke", [self.keys[0]])
        deliver_notice(response.data["notices"][0]["uuid"])
        self.assertEqual(mail.outbox[0].to, [self.admins[0].email])
        self.assertNotIn("\n", mail.outbox[0].subject)
        self.assertIn("Control de claves", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].bcc, [])

    def test_periodic_registration_keeps_existing_operator_configuration(self):
        """The new recovery schedule uses the central registry and does not overwrite operations settings."""
        from django_celery_beat.models import PeriodicTask

        from accounts.periodic_tasks import register_periodic_tasks
        from core.periodic_registry import TASK_REGISTRY, apply_registry

        TASK_REGISTRY.clear()
        self.addCleanup(TASK_REGISTRY.clear)
        register_periodic_tasks()
        apply_registry()
        task = PeriodicTask.objects.get(name="accounts-dispatch-pending-api-key-notices")
        self.assertEqual(task.interval.every, 60)
        self.assertEqual(task.queue, "backend")
        task.enabled = False
        task.save()
        register_periodic_tasks()
        apply_registry()
        task.refresh_from_db()
        self.assertFalse(task.enabled)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_HOST_USER="")
class ControlNoticeConcurrencyTests(TransactionTestCase):
    """Duplicate worker deliveries serialize without mailing successful notices twice."""

    def test_concurrent_deliveries_send_once(self):
        """A second worker waits for the first delivery's committed state."""
        from django.core.mail import EmailMultiAlternatives

        from accounts.models import ApiKeyControlNotice
        from accounts.services.api_key_notices import deliver_notice

        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks")
        tenant = Tenant.objects.create(name="Notice race", slug="notice-race")
        admin = get_user_model().objects.create_user(username="notice-race", email="notice-race@example.com")
        TenantMembership.objects.create(tenant=tenant, user=admin, role="tenant_admin")
        notice = ApiKeyControlNotice.objects.create(tenant=tenant, actor=admin, action="disable", keys=[])
        entered, release, started, finished = [threading.Event() for _ in range(4)]
        errors = []
        original_send = EmailMultiAlternatives.send

        def paused_send(message, *args, **kwargs):
            """Hold the delivery while it owns the notice lock."""
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Notice delivery race was not released")
            return original_send(message, *args, **kwargs)

        def deliver(second=False):
            """Run the actual service on a separate worker database connection."""
            close_old_connections()
            try:
                if second:
                    started.set()
                deliver_notice(notice.pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                if second:
                    finished.set()
                close_old_connections()

        with patch.object(EmailMultiAlternatives, "send", paused_send):
            first = threading.Thread(target=deliver)
            second = threading.Thread(target=deliver, args=(True,))
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                second.start()
                self.assertTrue(started.wait(5))
                overtook = finished.wait(0.25)
            finally:
                release.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        if errors:
            raise errors[0]
        self.assertFalse(overtook)
        self.assertEqual(len(mail.outbox), 1)
        notice.refresh_from_db()
        self.assertEqual((notice.status, notice.attempts), ("sent", 1))
