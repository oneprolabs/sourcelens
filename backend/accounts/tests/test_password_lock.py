"""Password retry state matches the prototype without revealing registered emails."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import PasswordLoginState, Tenant, TenantApiKey, TenantMembership


@override_settings(TURNSTILE_ENABLED=False)
class PasswordLockTests(TestCase):
    """Exercise real authentication, member status and tenant-local recovery."""

    def setUp(self):
        """Keep the password login account separate from its tenant administrator."""
        self.tenant = Tenant.objects.create(name="Login tenant", slug="password-lock")
        self.admin = get_user_model().objects.create_user(
            username="lock-admin", email="admin@lock.test", password="Admin8Pass"
        )
        self.user = get_user_model().objects.create_user(
            username="lock-user", email="member@lock.test", password="Member8Pass"
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role="tenant_admin")
        TenantMembership.objects.create(tenant=self.tenant, user=self.user)
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.public = APIClient()

    def login(self, password="Incorrect8Pass", email=None):
        """Send a real password request through the existing Turnstile boundary."""
        return self.public.post(
            "/api/v1/auth/login", {"email": email or self.user.email, "password": password}, format="json"
        )

    def row(self):
        """Read the actual serialized member version and effective status."""
        response = self.admin_client.get("/api/v1/tenant/members", {"search": self.user.email})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"][0]

    def test_five_failures_lock_for_fifteen_minutes_without_disabling_membership(self):
        """Correct passwords cannot bypass a live lock and expiry permits a new login."""
        now = timezone.now()
        with patch("accounts.services.password_login.timezone.now", return_value=now):
            for remaining in range(4, -1, -1):
                response = self.login()
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(int(response.data["remaining_attempts"]), remaining)
            locked = self.login("Member8Pass")
            self.assertEqual(locked.data["error_code"], "PASSWORD_LOGIN_LOCKED")
            self.assertEqual(locked.data["locked_until"], (now + timedelta(minutes=15)).isoformat())
            row = self.row()
            self.assertEqual(row["status"], "locked")
            self.assertEqual(row["membership_status"], "active")
            self.assertEqual(row["locked_until"], (now + timedelta(minutes=15)).isoformat())
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(TenantMembership.objects.get(user=self.user).status, "active")
        with patch("accounts.services.password_login.timezone.now", return_value=now + timedelta(minutes=15)):
            self.assertEqual(self.row()["status"], "active")
            success = self.login("Member8Pass")
            self.assertEqual(success.status_code, 200, success.data)
            self.assertIn("access", success.data)

    def test_unknown_and_registered_emails_have_identical_public_failures(self):
        """Counts and locked responses cannot be used as an account-existence oracle."""
        with patch("accounts.services.password_login.timezone.now", return_value=timezone.now()):
            for _ in range(6):
                registered = self.login()
                unknown = self.login(email="unknown@lock.test")
                self.assertEqual(registered.status_code, unknown.status_code)
                self.assertEqual(registered.data, unknown.data)

    def test_success_resets_consecutive_failures_and_case_variants_share_one_lock(self):
        """The counter follows normalized email and clears after successful authentication."""
        self.login()
        self.login()
        self.assertEqual(self.login("Member8Pass").status_code, 200)
        for _ in range(4):
            response = self.login(email=self.user.email.upper())
            self.assertNotEqual(response.data["error_code"], "PASSWORD_LOGIN_LOCKED")
        self.assertEqual(self.login().data["error_code"], "PASSWORD_LOGIN_LOCKED")

    def test_admin_unlocks_reviewed_member_without_enabling_a_disabled_account(self):
        """Unlock is versioned, audited and distinct from account enablement."""
        for _ in range(5):
            self.login()
        row = self.row()
        url = f"/api/v1/tenant/members/{self.user.pk}/unlock"
        response = self.admin_client.post(url, {"version": row["version"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "active")
        self.assertEqual(self.login("Member8Pass").status_code, 200)
        from accounts.models import AuditLog

        self.assertTrue(
            AuditLog.objects.filter(tenant=self.tenant, action="member.unlock", resource_id=str(self.user.pk)).exists()
        )
        self.admin_client.patch(f"/api/v1/tenant/members/{self.user.pk}", {"status": "disabled"}, format="json")
        response = self.admin_client.post(url, {"version": self.row()["version"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_expired_selection_and_foreign_or_unprivileged_requests_cannot_unlock(self):
        """A fresh lock cannot be cleared with a previously reviewed row."""
        old = self.row()
        for _ in range(5):
            self.login()
        url = f"/api/v1/tenant/members/{self.user.pk}/unlock"
        stale = self.admin_client.post(url, {"version": old["version"]}, format="json")
        self.assertEqual(stale.status_code, 409, stale.data)
        self.public.force_authenticate(self.user)
        denied = self.public.post(url, {"version": self.row()["version"]}, format="json")
        self.assertEqual(denied.status_code, 403, denied.data)
        other = Tenant.objects.create(name="Other lock", slug="other-lock")
        foreign = get_user_model().objects.create_user(username="lock-foreign", email="foreign@lock.test")
        TenantMembership.objects.create(tenant=other, user=foreign)
        missing = self.admin_client.post(
            f"/api/v1/tenant/members/{foreign.pk}/unlock", {"version": old["version"]}, format="json"
        )
        self.assertEqual(missing.status_code, 404, missing.data)

    def test_locked_filter_runs_before_pagination_and_enable_does_not_unlock(self):
        """Administrative status edits and bulk enable are not password recovery shortcuts."""
        for _ in range(5):
            self.login()
        response = self.admin_client.get("/api/v1/tenant/members", {"status": "locked", "page_size": 1})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.user.pk)
        active = self.admin_client.get("/api/v1/tenant/members", {"status": "active"})
        self.assertNotIn(self.user.pk, [row["id"] for row in active.data["results"]])
        self.admin_client.patch(f"/api/v1/tenant/members/{self.user.pk}", {"status": "active"}, format="json")
        row = self.row()
        enabled = self.admin_client.post(
            "/api/v1/tenant/members/bulk",
            {"action": "enable", "members": [{"id": row["id"], "version": row["version"]}]},
            format="json",
        )
        self.assertEqual(enabled.status_code, 200, enabled.data)
        self.assertEqual(enabled.data["changed"], 0)
        self.assertEqual(self.login("Member8Pass").data["error_code"], "PASSWORD_LOGIN_LOCKED")

    def test_existing_jwt_email_code_and_api_key_remain_usable(self):
        """A password attack does not revoke stronger recovery or independent credentials."""
        from rest_framework_simplejwt.tokens import RefreshToken

        access = str(RefreshToken.for_user(self.user).access_token)
        _, raw = TenantApiKey.issue(
            tenant=self.tenant,
            created_by=self.user,
            name="Independent",
            scope="all",
            assistant_uuids=[],
            expires_at=None,
        )
        for _ in range(5):
            self.login()
        session = APIClient()
        session.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(session.get("/api/v1/auth/user").status_code, 200)
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(external.get("/api/lens/assistants/").status_code, 200)
        with patch("accounts.views.login_otp.otp.verify_code", return_value=(True, "")):
            recovered = self.public.post(
                "/api/v1/auth/login/verify-code", {"email": self.user.email, "code": "123456"}, format="json"
            )
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertIn("access", recovered.data)
        self.assertEqual(self.row()["status"], "locked")

    def test_django_and_allauth_password_entries_cannot_bypass_the_rest_lock(self):
        """Username-admin and direct email authentication share the same retry row."""
        from django.contrib.auth import authenticate

        for _ in range(4):
            self.assertIsNone(authenticate(username=self.user.username, password="Wrong8Pass"))
        self.assertEqual(self.login().data["error_code"], "PASSWORD_LOGIN_LOCKED")
        self.assertIsNone(authenticate(email=self.user.email, password="Member8Pass"))
        self.assertIsNone(authenticate(username=self.user.username, password="Member8Pass"))
        with patch(
            "accounts.services.password_login.timezone.now", return_value=timezone.now() + timedelta(minutes=16)
        ):
            self.assertEqual(authenticate(username=self.user.username, password="Member8Pass").pk, self.user.pk)

    def test_verified_aliases_cannot_start_a_separate_account_retry_counter(self):
        """Allauth secondary email credentials obey the same account lock."""
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(user=self.user, email="alias@lock.test", verified=True)
        for _ in range(4):
            self.login(email="alias@lock.test")
        self.assertEqual(self.login().data["error_code"], "PASSWORD_LOGIN_LOCKED")
        self.assertEqual(self.login("Member8Pass", email="alias@lock.test").data["error_code"], "PASSWORD_LOGIN_LOCKED")

    def test_headless_password_entry_obeys_lock_and_existing_cookie_session_survives(self):
        """Legacy session backend paths remain readable without enabling password fallback."""
        from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY

        session = APIClient()
        session.force_login(self.user, backend="django.contrib.auth.backends.ModelBackend")
        for _ in range(5):
            self.login()
        self.assertEqual(session.get("/api/v1/auth/user").status_code, 200)
        self.assertEqual(session.session[BACKEND_SESSION_KEY], "django.contrib.auth.backends.ModelBackend")
        response = self.public.post(
            "/_allauth/app/v1/auth/login", {"email": self.user.email, "password": "Member8Pass"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertNotIn(SESSION_KEY, self.public.session)

    def test_rejected_turnstile_or_malformed_requests_do_not_consume_retries(self):
        """Do not let unverified or invalid envelopes create retry state."""
        with patch("accounts.urls.turnstile.verify_token", return_value=(False, ["invalid"])):
            self.assertEqual(self.login().data["error_code"], "TURNSTILE_FAILED")
        self.public.post("/api/v1/auth/login", {"email": "invalid", "password": "x"}, format="json")
        self.public.post("/api/v1/auth/login", {"email": self.user.email}, format="json")
        self.assertFalse(PasswordLoginState.objects.exists())

    def test_expiry_starts_a_fresh_counter_and_locked_retries_do_not_extend_deadline(self):
        """Locked retries cannot indefinitely postpone recovery."""
        now = timezone.now()
        PasswordLoginState.objects.create(
            email=self.user.email, failed_attempts=5, locked_until=now + timedelta(minutes=1)
        )
        first = self.login()
        with patch("accounts.services.password_login.timezone.now", return_value=now + timedelta(seconds=30)):
            self.assertEqual(self.login().data["locked_until"], first.data["locked_until"])
        with patch("accounts.services.password_login.timezone.now", return_value=now + timedelta(minutes=1)):
            self.assertEqual(int(self.login().data["remaining_attempts"]), 4)

    def test_staff_machine_delegation_and_inactive_tenants_cannot_unlock(self):
        """Each recovery request must have current explicit tenant administrator authority."""
        from accounts.models import Profile

        PasswordLoginState.objects.create(
            email=self.user.email, failed_attempts=5, locked_until=timezone.now() + timedelta(minutes=15)
        )
        url = f"/api/v1/tenant/members/{self.user.pk}/unlock"
        payload = {"version": self.row()["version"]}
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        Profile.objects.filter(user=self.user).update(platform_role="super_admin")
        self.public.force_authenticate(self.user)
        self.assertEqual(self.public.post(url, payload, format="json").status_code, 403)
        self.public.force_authenticate(None)
        _, raw = TenantApiKey.issue(
            tenant=self.tenant,
            created_by=self.admin,
            name="Recovery denied",
            scope="all",
            assistant_uuids=[],
            expires_at=None,
        )
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(self.public.post(url, payload, format="json").status_code, 403)
        with patch("accounts.services.member_management.is_delegated_request", return_value=True):
            self.assertEqual(self.admin_client.post(url, payload, format="json").status_code, 403)
        Tenant.objects.filter(pk=self.tenant.pk).update(status="suspended")
        self.assertEqual(self.admin_client.post(url, payload, format="json").status_code, 403)
        self.assertIsNotNone(PasswordLoginState.objects.get(email=self.user.email).locked_until)


class PasswordLockConcurrencyTests(TransactionTestCase):
    """Independent PostgreSQL connections serialize one identifier's attempts."""

    def test_concurrent_attempts_share_one_five_failure_lock(self):
        """The first counter creation and later increments cannot lose concurrent failures."""
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks")
        from rest_framework.exceptions import ValidationError

        from accounts.services.password_login import PasswordLoginRejected, authenticate_password

        barrier = threading.Barrier(6)
        calls = []

        def attempt():
            """Use a separate connection and report the real committed public outcome."""
            close_old_connections()
            try:
                barrier.wait(timeout=10)

                def reject():
                    """Only requests before the committed lock reach password verification."""
                    calls.append(1)
                    raise ValidationError("Invalid credentials")

                try:
                    authenticate_password(email="parallel@lock.test", authenticate=reject)
                except PasswordLoginRejected as error:
                    return str(error.detail["error_code"])
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: attempt(), range(6)))
        self.assertEqual(results.count("PASSWORD_LOGIN_INVALID"), 4)
        self.assertEqual(results.count("PASSWORD_LOGIN_LOCKED"), 2)
        self.assertEqual(len(calls), 5)
        state = PasswordLoginState.objects.get(email="parallel@lock.test")
        self.assertEqual(state.failed_attempts, 5)
        self.assertIsNotNone(state.locked_until)
