"""Tenant administrators request mailbox-owned password recovery without changing credentials."""

import threading
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, PasswordLoginState, Profile, Tenant, TenantApiKey, TenantMembership
from accounts.services.member_management import require_member_authority


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class MemberPasswordResetTests(TestCase):
    """Use real member versions, tenant authorization and the existing reset confirmation API."""

    def setUp(self):
        """Create an accepted member and a separate explicit tenant administrator."""
        cache.clear()
        self.tenant = Tenant.objects.create(name="Reset tenant", slug="member-reset")
        self.admin = self.member("reset-admin", role="tenant_admin")
        self.target = self.member("reset-target")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = f"/api/v1/tenant/members/{self.target.pk}/password-reset"

    def member(self, name, role="member", tenant=None):
        """Keep completed profile and accepted membership state consistent."""
        user = get_user_model().objects.create_user(
            username=name, email=f"{name}@example.test", password="Original8Pass"
        )
        Profile.objects.filter(user=user).update(registration_completed=True, language="es")
        TenantMembership.objects.create(tenant=tenant or self.tenant, user=user, role=role, joined_at=timezone.now())
        return user

    def row(self):
        """Read the same live confirmation snapshot as the member page."""
        response = self.client.get("/api/v1/tenant/members", {"search": self.target.email})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"][0]

    def send(self, version=None, **extra):
        """Never accept a caller-supplied recipient as authority."""
        return self.client.post(self.url, {"version": version or self.row()["version"], **extra}, format="json")

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email", return_value=True
    )
    def test_real_reset_link_is_sent_to_the_member_and_used_only_by_its_owner(self, send):
        """Requesting mail does not change a password; normal link confirmation does."""
        row = self.row()
        self.assertTrue(row["can_reset_password"])
        original = self.target.password
        response = self.send(row["version"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"status": "sent", "member_id": self.target.pk})
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, original)
        delivery = send.call_args.kwargs
        self.assertEqual(delivery["email"], self.target.email)
        self.assertEqual(delivery["language"], "es")
        self.assertNotIn(delivery["token"], str(response.data))
        public = APIClient()
        payload = {
            "uid": delivery["uid"],
            "token": delivery["token"],
            "new_password1": "Changed9Pass",
            "new_password2": "Changed9Pass",
        }
        confirmed = public.post("/api/v1/auth/password/reset/confirm", payload, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(public.post("/api/v1/auth/password/reset/confirm", payload, format="json").status_code, 400)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("Changed9Pass"))
        audit = AuditLog.objects.get(action="member.password_reset", resource_id=str(self.target.pk))
        self.assertEqual(audit.tenant, self.tenant)
        self.assertNotIn(delivery["token"], str(audit.metadata))

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email", return_value=True
    )
    def test_recipient_override_and_old_member_versions_are_rejected(self, send):
        """A stale row cannot issue a recovery link to an edited account."""
        version = self.row()["version"]
        override = self.send(version, email="attacker@example.test")
        self.assertEqual(override.status_code, 400, override.data)
        get_user_model().objects.filter(pk=self.target.pk).update(email="changed@example.test")
        self.assertEqual(self.send(version).status_code, 409)
        send.assert_not_called()

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email",
        return_value=False,
    )
    def test_delivery_failures_are_explicit_and_retries_are_rate_limited(self, send):
        """Unlike public recovery, an authorized administrative action reports real rejection."""
        response = self.send()
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(self.send().status_code, 429)
        self.assertEqual(send.call_count, 1)
        audit = AuditLog.objects.get(action="member.password_reset")
        self.assertEqual(audit.result, "failed")
        self.assertFalse(self.target.check_password("Changed9Pass"))

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email",
        side_effect=OSError("private delivery detail"),
    )
    def test_unexpected_mail_error_is_not_returned_or_audited(self, send):
        """Transport internals and recovery credentials stay out of public results and audit."""
        response = self.send()
        self.assertEqual(response.status_code, 503, response.data)
        self.assertNotIn("private delivery detail", str(response.data))
        self.assertNotIn("private delivery detail", str(AuditLog.objects.get(action="member.password_reset").metadata))

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email", return_value=True
    )
    def test_invitation_and_incomplete_registration_cannot_use_password_recovery(self, send):
        """Mailbox invitation acceptance remains the only way to accept an invited membership."""
        TenantMembership.objects.filter(user=self.target).update(
            status="invited", invited_at=timezone.now(), joined_at=None
        )
        self.assertFalse(self.row()["can_reset_password"])
        self.assertEqual(self.send().status_code, 400)
        TenantMembership.objects.filter(user=self.target).update(status="active", invited_at=None)
        Profile.objects.filter(user=self.target).update(registration_completed=False)
        self.assertFalse(self.row()["can_reset_password"])
        self.assertEqual(self.send().status_code, 400)
        send.assert_not_called()

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email", return_value=True
    )
    def test_disabled_and_locked_accounts_are_not_reenabled_or_unlocked(self, send):
        """Sending mail leaves administrative and temporary credential restrictions intact."""
        until = timezone.now() + timedelta(minutes=15)
        PasswordLoginState.objects.create(email=self.target.email, failed_attempts=5, locked_until=until)
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(PasswordLoginState.objects.get(email=self.target.email).locked_until, until)
        cache.clear()
        TenantMembership.objects.filter(user=self.target).update(status="disabled")
        get_user_model().objects.filter(pk=self.target.pk).update(is_active=False)
        self.assertEqual(self.send().status_code, 200)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertEqual(TenantMembership.objects.get(user=self.target).status, "disabled")

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email", return_value=True
    )
    def test_cross_tenant_and_staff_member_or_machine_cannot_send(self, send):
        """Do not inherit authority from staff, platform role, API key creator or foreign targets."""
        reviewed = {"version": self.row()["version"]}
        other = Tenant.objects.create(name="Other reset", slug="other-reset")
        foreign = self.member("foreign-reset", tenant=other)
        self.assertEqual(
            self.client.post(
                f"/api/v1/tenant/members/{foreign.pk}/password-reset", reviewed, format="json"
            ).status_code,
            404,
        )
        self.target.is_staff = True
        self.target.save(update_fields=["is_staff"])
        Profile.objects.filter(user=self.target).update(platform_role="super_admin")
        self.client.force_authenticate(self.target)
        self.assertEqual(self.client.post(self.url, reviewed, format="json").status_code, 403)
        self.client.force_authenticate(None)
        _, raw = TenantApiKey.issue(
            tenant=self.tenant,
            created_by=self.admin,
            name="No recovery",
            scope="all",
            assistant_uuids=[],
            expires_at=None,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(self.client.post(self.url, reviewed, format="json").status_code, 403)
        send.assert_not_called()

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email", return_value=True
    )
    def test_delegation_suspended_tenant_and_revoked_administrator_cannot_send(self, send):
        """Check authority both before and after acquiring the tenant lock."""
        version = self.row()["version"]
        response = self.client.post(self.url, {"version": version}, HTTP_X_DELEGATION_ID="support", format="json")
        self.assertEqual(response.status_code, 403)
        Tenant.objects.filter(pk=self.tenant.pk).update(status="suspended")
        self.assertEqual(self.client.post(self.url, {"version": version}, format="json").status_code, 403)
        Tenant.objects.filter(pk=self.tenant.pk).update(status="active")

        def revoke(request, tenant):
            """Simulate authority changing after the view permission check."""
            TenantMembership.objects.filter(user=self.admin).update(role="member")
            require_member_authority(request, tenant)

        with patch("accounts.services.member_password_reset.require_member_authority", side_effect=revoke):
            self.assertEqual(self.client.post(self.url, {"version": version}, format="json").status_code, 403)
        send.assert_not_called()

    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email", return_value=True
    )
    def test_target_cooldown_is_shared_by_administrators(self, send):
        """Changing the sender cannot bypass the member's delivery cooldown."""
        version = self.row()["version"]
        self.assertEqual(self.send(version).status_code, 200)
        other_admin = self.member("reset-other-admin", role="tenant_admin")
        self.client.force_authenticate(other_admin)
        self.assertEqual(self.send(version).status_code, 429)
        self.assertEqual(send.call_count, 1)

    @patch("accounts.services.member_password_reset.cache.add", return_value=True)
    @patch(
        "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email", return_value=True
    )
    def test_actor_has_an_hourly_budget_in_addition_to_target_cooldown(self, send, reserve):
        """Even distinct eligible reservations cannot exceed the human actor's mail budget."""
        version = self.row()["version"]
        for _ in range(30):
            self.assertEqual(self.send(version).status_code, 200)
        self.assertEqual(self.send(version).status_code, 429)
        self.assertEqual(send.call_count, 30)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class MemberPasswordResetRaceTests(TransactionTestCase):
    """Use independent PostgreSQL connections for recovery delivery races."""

    def test_concurrent_retry_finishes_while_smtp_is_blocked_without_sending_again(self):
        """SMTP holds no tenant locks, and a competing request observes the reserved cooldown."""
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock concurrency requires PostgreSQL")
        cache.clear()
        tenant = Tenant.objects.create(name="Reset race", slug="member-reset-race")
        admin = get_user_model().objects.create_user(username="reset-race-admin")
        target = get_user_model().objects.create_user(username="reset-race-target", email="race@example.test")
        Profile.objects.filter(user=target).update(registration_completed=True)
        TenantMembership.objects.create(tenant=tenant, user=admin, role="tenant_admin")
        TenantMembership.objects.create(tenant=tenant, user=target, joined_at=timezone.now())
        client = APIClient()
        client.force_authenticate(admin)
        version = client.get("/api/v1/tenant/members", {"search": target.email}).data["results"][0]["version"]
        url = f"/api/v1/tenant/members/{target.pk}/password-reset"
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        responses, errors, transactions = {}, [], []

        def deliver(**kwargs):
            """Block the delivery transport after observing transaction state."""
            transactions.append(connection.in_atomic_block)
            entered.set()
            if not release.wait(8):
                raise TimeoutError("Reset delivery was not released")
            return True

        def request(first):
            """Give each request its own database connection."""
            close_old_connections()
            try:
                caller = APIClient()
                caller.force_authenticate(get_user_model().objects.get(pk=admin.pk))
                responses[first] = caller.post(url, {"version": version}, format="json")
            except Exception as exc:
                errors.append(exc)
            finally:
                if not first:
                    finished.set()
                close_old_connections()

        with patch(
            "accounts.services.member_password_reset.PasswordResetEmailService.send_password_reset_email",
            side_effect=deliver,
        ) as send:
            first = threading.Thread(target=request, args=(True,))
            second = threading.Thread(target=request, args=(False,))
            first.start()
            try:
                self.assertTrue(entered.wait(8))
                second.start()
                self.assertTrue(finished.wait(8), "SMTP must release database locks before delivery")
            finally:
                release.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)
        self.assertEqual(errors, [])
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(transactions, [False])
        self.assertEqual(send.call_count, 1)
        self.assertEqual((responses[True].status_code, responses[False].status_code), (200, 429))
