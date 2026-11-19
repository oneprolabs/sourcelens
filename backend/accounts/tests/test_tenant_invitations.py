"""Exercise invitation delivery, activation, tenancy, and token lifecycle."""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AuditLog, Tenant, TenantGroup, TenantMembership


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://lens.example.com",
    TENANT_INVITATION_TIMEOUT=259200,
)
class TenantInvitationTests(TestCase):
    """Only the current tenant's administrator may invite new members."""

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Acme", slug="invite-acme")
        self.other = Tenant.objects.create(name="Other", slug="invite-other")
        self.admin = get_user_model().objects.create_user(username="inviter", email="admin@example.com")
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role="tenant_admin")
        self.group = TenantGroup.objects.create(
            tenant=self.tenant, name="Engineering", auth_group=Group.objects.create(name="Invite Engineering")
        )
        self.foreign_group = TenantGroup.objects.create(
            tenant=self.other, name="Other", auth_group=Group.objects.create(name="Invite Other")
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.public = APIClient()
        self.url = "/api/v1/tenant/invitations"
        self.accept_url = "/api/v1/auth/invitations/accept"

    def invite(self, **values):
        """Submit one invitation using the supported tenant-facing payload."""

        return self.client.post(self.url, {"email": "new@example.com", **values}, format="json")

    def token(self):
        """Read a delivered token from the in-memory email, without logging it."""

        link = next(line for line in mail.outbox[-1].body.splitlines() if line.startswith("https://"))
        return parse_qs(urlsplit(link).fragment)["token"][0]

    def accept(self, token, **values):
        """Set a strong password with the invitation credential."""

        return self.public.post(
            self.accept_url,
            {
                "token": token,
                "new_password1": "Invite-Strong-Password-73!",
                "new_password2": "Invite-Strong-Password-73!",
                **values,
            },
            format="json",
        )

    def test_invitation_sends_email_and_creates_only_an_inactive_scoped_member(self):
        response = self.invite(email=" New@Example.com ", role="tenant_admin", group_ids=[self.group.auth_group_id])
        self.assertEqual(response.status_code, 201, response.data)
        user = get_user_model().objects.get(email="new@example.com")
        self.assertEqual(user.membership.tenant, self.tenant)
        self.assertEqual(user.membership.status, "invited")
        self.assertEqual(user.membership.role, "tenant_admin")
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(list(user.groups.all()), [self.group.auth_group])
        self.assertEqual(len(mail.outbox), 1)
        token = self.token()
        self.assertNotIn(token, str(response.data))
        self.assertNotIn(token, str(list(AuditLog.objects.values("metadata"))))
        self.assertIn("/accept-invitation#token=", mail.outbox[0].body)

    def test_acceptance_sets_password_confirms_email_and_is_single_use(self):
        from allauth.account.models import EmailAddress

        self.assertEqual(self.invite().status_code, 201)
        token = self.token()
        accepted = self.accept(token)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        user = get_user_model().objects.get(email="new@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("Invite-Strong-Password-73!"))
        self.assertTrue(user.profile.registration_completed)
        self.assertEqual(user.membership.status, "active")
        self.assertIsNotNone(user.membership.joined_at)
        self.assertTrue(EmailAddress.objects.filter(user=user, email=user.email, verified=True).exists())
        self.assertEqual(self.accept(token).status_code, 400)

    @override_settings(TURNSTILE_ENABLED=False)
    def test_invited_member_can_log_in_only_after_acceptance(self):
        """Mailbox acceptance unlocks the normal password login endpoint."""

        self.invite()
        credentials = {"email": "new@example.com", "password": "Invite-Strong-Password-73!"}
        response = self.public.post("/api/v1/auth/login", credentials, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access", response.data)
        self.assertEqual(self.accept(self.token()).status_code, 200)
        response = self.public.post("/api/v1/auth/login", credentials, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_retry_and_resend_reuse_the_pending_member_and_revoke_older_links(self):
        self.invite()
        token = self.token()
        user = get_user_model().objects.get(email="new@example.com")
        response = self.client.post(f"{self.url}/{user.pk}/resend", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get_user_model().objects.filter(email=user.email).count(), 1)
        self.assertEqual(self.accept(token).status_code, 400)
        self.assertEqual(self.accept(self.token()).status_code, 200)
        self.assertEqual(self.client.post(f"{self.url}/{user.pk}/resend", {}, format="json").status_code, 400)

    def test_failed_delivery_preserves_pending_member_and_retry_does_not_duplicate_it(self):
        with patch("accounts.services.tenant_invitations.send_invitation_email", side_effect=OSError("unavailable")):
            response = self.invite()
        self.assertEqual(response.status_code, 502, response.data)
        user = get_user_model().objects.get(email="new@example.com")
        self.assertEqual(user.membership.status, "invited")
        self.assertEqual(self.invite().status_code, 201)
        self.assertEqual(get_user_model().objects.filter(email=user.email).count(), 1)
        self.assertEqual(self.accept(self.token()).status_code, 200)

    def test_invalid_email_role_and_foreign_groups_do_not_create_users_or_send_email(self):
        for values in [
            {"email": "invalid"},
            {"role": "super_admin"},
            {"group_ids": [self.foreign_group.auth_group_id]},
            {"group_ids": [999999]},
            {"group_ids": "all"},
        ]:
            with self.subTest(values=values):
                self.assertEqual(self.invite(**values).status_code, 400)
        self.assertFalse(get_user_model().objects.filter(email="new@example.com").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_existing_accounts_in_other_tenants_or_without_membership_are_not_reassigned(self):
        user = get_user_model().objects.create_user(username="existing", email="new@example.com", password="x")
        self.assertEqual(self.invite().status_code, 400)
        TenantMembership.objects.create(user=user, tenant=self.other)
        self.assertEqual(self.invite().status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.membership.tenant, self.other)

    def test_only_active_tenant_admins_can_send_or_resend_invitations(self):
        self.invite()
        invitee = get_user_model().objects.get(email="new@example.com")
        self.client.force_authenticate(invitee)
        self.assertEqual(self.invite(email="second@example.com").status_code, 403)
        self.client.force_authenticate(self.admin)
        membership = self.admin.membership
        membership.role = "member"
        membership.save()
        self.admin.is_staff = True
        self.admin.save()
        self.assertEqual(self.invite(email="second@example.com").status_code, 403)
        self.assertEqual(self.client.post(f"{self.url}/{invitee.pk}/resend").status_code, 403)

    def test_suspended_tenant_and_disabled_members_cannot_accept(self):
        self.invite()
        token = self.token()
        self.tenant.status = "suspended"
        self.tenant.save()
        self.assertEqual(self.accept(token).status_code, 400)
        self.tenant.status = "active"
        self.tenant.save()
        user = get_user_model().objects.get(email="new@example.com")
        TenantMembership.objects.filter(user=user).update(status="disabled")
        self.assertEqual(self.accept(token).status_code, 400)

    def test_expired_tampered_and_weak_password_requests_do_not_activate(self):
        self.invite()
        token = self.token()
        self.assertEqual(self.accept(token + "tampered").status_code, 400)
        with patch("django.core.signing.time.time", return_value=(timezone.now() + timedelta(days=4)).timestamp()):
            self.assertEqual(self.accept(token).status_code, 400)
        self.assertEqual(self.accept(token, new_password1="weak", new_password2="weak").status_code, 400)
        self.assertEqual(self.accept(token, new_password2="different").status_code, 400)
        self.assertEqual(self.accept(token).status_code, 200)

    def test_invited_member_cannot_be_activated_by_the_normal_member_update(self):
        self.invite()
        user = get_user_model().objects.get(email="new@example.com")
        response = self.client.patch(f"/api/v1/tenant/members/{user.pk}", {"status": "active"}, format="json")
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_disabled_pending_invitation_cannot_be_reactivated_without_acceptance(self):
        self.invite()
        user = get_user_model().objects.get(email="new@example.com")
        url = f"/api/v1/tenant/members/{user.pk}"
        self.assertEqual(self.client.patch(url, {"status": "disabled"}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(url, {"status": "active"}, format="json").status_code, 400)
        self.assertEqual(self.accept(self.token()).status_code, 400)
        self.assertEqual(self.invite().status_code, 201)
        self.assertEqual(self.accept(self.token()).status_code, 200)

    def test_pending_member_update_cannot_attach_foreign_groups_or_partially_change_role(self):
        self.invite()
        user = get_user_model().objects.get(email="new@example.com")
        response = self.client.patch(
            f"/api/v1/tenant/members/{user.pk}",
            {
                "role": "tenant_admin",
                "group_ids": [self.foreign_group.auth_group_id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        user.membership.refresh_from_db()
        self.assertEqual(user.membership.role, "member")
        self.assertFalse(user.groups.exists())

    def test_delegation_cannot_send_or_resend_and_cross_tenant_resend_is_hidden(self):
        from accounts.models import Profile, TenantDelegation

        self.invite()
        invitee = get_user_model().objects.get(email="new@example.com")
        self.admin.profile.platform_role = Profile.PlatformRole.SUPER_ADMIN
        self.admin.profile.save()
        delegation = TenantDelegation.objects.create(
            platform_user=self.admin,
            tenant=self.other,
            reason="Verify invite isolation",
            mode="operational",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.pk)}
        self.assertEqual(self.client.post(self.url, {"email": "another@example.com"}, **headers).status_code, 403)
        self.assertEqual(self.client.post(f"{self.url}/{invitee.pk}/resend", **headers).status_code, 403)
        other_admin = get_user_model().objects.create_user(username="other-inviter")
        TenantMembership.objects.create(user=other_admin, tenant=self.other, role="tenant_admin")
        self.client.force_authenticate(other_admin)
        self.assertEqual(self.client.post(f"{self.url}/{invitee.pk}/resend").status_code, 404)

    def test_otp_cannot_issue_login_tokens_or_complete_pending_registration(self):
        self.invite()
        with patch("accounts.views.login_otp.otp.verify_code", return_value=(True, "")):
            response = self.public.post(
                "/api/v1/auth/login/verify-code",
                {
                    "email": "new@example.com",
                    "code": "123456",
                },
                format="json",
            )
        self.assertEqual(response.status_code, 403, response.data)
        user = get_user_model().objects.get(email="new@example.com")
        self.assertFalse(user.profile.registration_completed)
        self.assertNotIn("access", response.data)

    def test_self_registration_cannot_replace_an_invited_account(self):
        """A pending tenant member must finish through the invitation flow."""

        self.invite()
        user = get_user_model().objects.get(email="new@example.com")
        response = self.public.post("/api/v1/auth/register/send-email", {"email": user.email}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        user.profile.refresh_from_db()
        self.assertIsNone(user.profile.registration_token)
        self.assertEqual(len(mail.outbox), 1)

    def test_existing_registration_token_cannot_delete_a_tenant_membership(self):
        """Reject a stale registration credential before deleting any user data."""

        self.invite()
        user = get_user_model().objects.get(email="new@example.com")
        profile = user.profile
        profile.registration_token = "stale-registration-credential"
        profile.registration_token_expires = timezone.now() + timedelta(hours=1)
        profile.save()
        response = self.public.post(
            "/api/v1/auth/register/complete",
            {
                "token": profile.registration_token,
                "password": "Strong-Password-73!",
                "virtual_email_username": "replacement-member",
                "language": "en-US",
                "timezone": "UTC",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(TenantMembership.objects.filter(user_id=user.pk, tenant=self.tenant, status="invited").exists())

    def test_legacy_single_and_bulk_updates_cannot_enable_pending_members(self):
        """The legacy account API cannot manage tenant invitations."""

        self.invite()
        user = get_user_model().objects.get(email="new@example.com")
        legacy_admin = get_user_model().objects.create_user(username="legacy-admin", is_staff=True)
        self.client.force_authenticate(legacy_admin)
        for status in ["invited", "disabled"]:
            TenantMembership.objects.filter(user=user).update(status=status)
            response = self.client.patch(
                f"/api/v1/management/users/{user.pk}/",
                {"is_active": True, "username": "should-not-change"},
                format="json",
            )
            self.assertEqual(response.status_code, 404)
            response = self.client.post(
                "/api/v1/management/users/bulk-status/",
                {"user_ids": [user.pk], "is_active": True},
                format="json",
            )
            self.assertEqual(response.status_code, 400)
            user.refresh_from_db()
            self.assertFalse(user.is_active)
            self.assertEqual(user.username, user.email)

    def test_tenant_admin_cannot_use_legacy_global_member_endpoints(self):
        """Staff status does not grant tenant members global account access."""

        self.invite()
        user = get_user_model().objects.get(email="new@example.com")
        self.admin.is_staff = True
        self.admin.save()
        response = self.client.patch(
            f"/api/v1/management/users/{user.pk}/", {"is_active": True}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/api/v1/management/users/bulk-status/", {"user_ids": [user.pk], "is_active": True}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_invitation_email_escapes_tenant_markup(self):
        self.tenant.name = '<script>alert("tenant")</script>'
        self.tenant.save()
        self.invite()
        self.assertNotIn("<script>", mail.outbox[0].alternatives[0][0])
        self.assertIn("&lt;script&gt;", mail.outbox[0].alternatives[0][0])

    def test_accepted_member_can_be_disabled_and_reenabled_without_another_invitation(self):
        self.invite()
        self.assertEqual(self.accept(self.token()).status_code, 200)
        user = get_user_model().objects.get(email="new@example.com")
        url = f"/api/v1/tenant/members/{user.pk}"
        for status in ["disabled", "active"]:
            self.assertEqual(self.client.patch(url, {"status": status}, format="json").status_code, 200)

    def test_join_event_is_tenant_scoped_and_contains_no_member_identity(self):
        from accounts.services.tenant_overview import tenant_overview

        self.invite()
        self.accept(self.token())
        foreign = get_user_model().objects.create_user(username="foreign-joined")
        TenantMembership.objects.create(user=foreign, tenant=self.other, joined_at=timezone.now())
        events = [row for row in tenant_overview(self.tenant)["recent_activity"] if row["kind"] == "member_joined"]
        self.assertEqual(len(events), 1)
        self.assertEqual(set(events[0]), {"kind", "name", "at"})
        self.assertEqual(events[0]["name"], "")
