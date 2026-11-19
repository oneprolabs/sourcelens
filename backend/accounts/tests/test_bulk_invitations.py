"""Bulk invitations preserve per-row feedback without claiming existing identities."""

import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, Tenant, TenantGroup, TenantMembership


class BulkInvitationTests(TestCase):
    """Exercise real HTTP validation, tenant authority and delivery boundaries."""

    def setUp(self):
        """Create a tenant administrator and two isolated group scopes."""
        cache.clear()
        self.tenant = Tenant.objects.create(name="Bulk invitations", slug="bulk-invitations")
        self.other = Tenant.objects.create(name="Other", slug="bulk-invitations-other")
        self.admin = get_user_model().objects.create_user(username="bulk-inviter", email="admin@example.test")
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role="tenant_admin")
        self.group = TenantGroup.objects.create(
            tenant=self.tenant, name="Engineering", auth_group=Group.objects.create(name="Bulk invite engineering")
        )
        self.foreign_group = TenantGroup.objects.create(
            tenant=self.other, name="Private", auth_group=Group.objects.create(name="Bulk invite private")
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = "/api/v1/tenant/invitations/bulk"

    def row(self, email, **values):
        """Build one explicit recipient configuration."""
        return {"email": email, "role": "member", "group_ids": [], **values}

    def preview(self, entries):
        """Obtain the confirmation for the exact reviewed input."""
        response = self.client.post(self.url + "/preview", {"entries": entries}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def send(self, entries, preview=None):
        """Submit the frozen confirmation without changing the reviewed rows."""
        preview = preview or self.preview(entries)
        return self.client.post(self.url, {"entries": entries, "confirmation": preview["confirmation"]}, format="json")

    def test_preview_groups_errors_duplicates_and_existing_without_mutation(self):
        """Mixed input reports row groups and hides existing account identity or tenant."""
        existing = get_user_model().objects.create_user(username="hidden", email="EXISTING@example.test")
        TenantMembership.objects.create(tenant=self.other, user=existing)
        entries = [
            self.row(" New@Example.test "),
            self.row("bad@local"),
            self.row("existing@example.test"),
            self.row("new@example.test"),
            self.row("groups@example.test", group_ids=[self.foreign_group.auth_group_id]),
            self.row("role@example.test", role="super_admin"),
        ]
        count = get_user_model().objects.count()
        result = self.preview(entries)
        self.assertEqual(result["counts"], {"valid": 1, "invalid": 3, "existing": 1, "duplicate": 1})
        self.assertEqual(result["items"][0]["email"], "new@example.test")
        self.assertNotIn("member_id", str(result))
        self.assertNotIn("hidden", str(result))
        self.assertNotIn(str(self.other.pk), str(result))
        self.assertEqual(get_user_model().objects.count(), count)

    def test_send_processes_only_valid_new_rows_and_preserves_per_row_configuration(self):
        """Invalid or duplicate recipients do not block valid invitations."""
        entries = [
            self.row("one@example.test", role="tenant_admin", group_ids=[self.group.auth_group_id]),
            self.row("bad@local"),
            self.row("one@example.test"),
            self.row("two@example.test"),
        ]
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.send(entries)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["sent"], response.data["skipped"], response.data["failed"]), (2, 2, 0))
        self.assertEqual(send.call_count, 2)
        user = get_user_model().objects.get(email="one@example.test")
        self.assertFalse(user.is_active)
        self.assertEqual(
            (user.membership.tenant, user.membership.role, user.membership.status),
            (self.tenant, "tenant_admin", "invited"),
        )
        self.assertEqual(list(user.groups.all()), [self.group.auth_group])
        self.assertNotIn("token", str(response.data))
        self.assertEqual(AuditLog.objects.filter(action="member.bulk_invite").count(), 2)

    def test_replaying_confirmation_does_not_resend_or_rotate_existing_pending(self):
        """A response lost in transit can be retried without repeating accepted deliveries."""
        entries = [self.row("once@example.test")]
        preview = self.preview(entries)
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            self.assertEqual(self.send(entries, preview).data["sent"], 1)
            member = TenantMembership.objects.get(user__email="once@example.test")
            version = member.auth_version
            response = self.send(entries, preview)
        self.assertEqual((response.data["sent"], response.data["skipped"]), (0, 1))
        self.assertEqual(send.call_count, 1)
        member.refresh_from_db()
        self.assertEqual(member.auth_version, version)

    def test_smtp_failure_keeps_pending_and_continues_with_later_recipients(self):
        """Delivery failures are recoverable through the existing resend flow."""
        entries = [self.row("failed@example.test"), self.row("sent@example.test")]
        with patch("accounts.services.tenant_invitations.send_invitation_email", side_effect=[OSError("smtp"), None]):
            response = self.send(entries)
        self.assertEqual((response.data["sent"], response.data["failed"]), (1, 1))
        failed = response.data["items"][0]
        self.assertEqual(failed["reason"], "delivery_failed")
        self.assertEqual(TenantMembership.objects.get(user_id=failed["member_id"]).status, "invited")
        self.assertEqual(AuditLog.objects.filter(action="member.bulk_invite", result="failed").count(), 1)

    def test_confirmation_is_bound_to_input_actor_tenant_and_expiry(self):
        """Changed configuration and tenant moves require renewed review."""
        entries = [self.row("proof@example.test")]
        preview = self.preview(entries)
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            changed = self.send([self.row("proof@example.test", role="tenant_admin")], preview)
            self.assertEqual(changed.status_code, 409, changed.data)
            with patch("django.core.signing.time.time", return_value=1):
                old = self.preview(entries)
            self.assertEqual(self.send(entries, old).status_code, 409)
            TenantMembership.objects.filter(user=self.admin).update(tenant=self.other)
            self.assertEqual(self.send(entries, preview).status_code, 409)
            send.assert_not_called()

    def test_malformed_rows_remain_individual_validation_errors(self):
        """JSON scalars and malformed recipient values do not crash or prevent a valid row."""
        entries = [None, True, 7, [], {"email": None}, self.row("good@example.test")]
        preview = self.preview(entries)
        self.assertEqual(preview["counts"], {"valid": 1, "invalid": 5, "existing": 0, "duplicate": 0})
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.send(entries, preview)
        self.assertEqual((response.data["sent"], response.data["skipped"]), (1, 5))
        self.assertEqual(send.call_count, 1)

    def test_confirmation_cannot_be_reused_by_another_admin(self):
        """The same tenant does not make another caller the reviewed actor."""
        entries = [self.row("bound@example.test")]
        preview = self.preview(entries)
        other_admin = get_user_model().objects.create_user(username="other-inviter")
        TenantMembership.objects.create(tenant=self.tenant, user=other_admin, role="tenant_admin")
        self.client.force_authenticate(other_admin)
        self.assertEqual(self.send(entries, preview).status_code, 409)
        self.assertFalse(get_user_model().objects.filter(email="bound@example.test").exists())

    def test_full_size_confirmation_is_accepted_without_truncating_recipients(self):
        """A valid maximum-size envelope can be reviewed and processed without hidden truncation."""
        from accounts.views.tenant_invitations import InvitationSendThrottle

        entries = [self.row(f"full-{index}@example.test") for index in range(100)]
        preview = self.preview(entries)
        self.assertEqual(preview["counts"]["valid"], 100)
        throttle = InvitationSendThrottle()
        key = throttle.cache_format % {"scope": throttle.scope, "ident": self.admin.pk}
        throttle.cache.set(key, [throttle.timer()] * 30, throttle.duration)
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.send(entries, preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["failed"], len(response.data["items"])), (100, 100))
        send.assert_not_called()

    def test_bad_envelopes_and_missing_confirmation_never_create_accounts(self):
        """Both preview and send enforce bounded input independently of the browser."""
        count = get_user_model().objects.count()
        for entries in ([], [self.row(f"n{i}@example.test") for i in range(101)], "a@example.test"):
            response = self.client.post(self.url + "/preview", {"entries": entries}, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(self.url, {"entries": [self.row("proof@example.test")]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_user_model().objects.count(), count)

    def test_rate_budget_is_per_recipient_and_shared_with_single_send(self):
        """A batch cannot turn the remaining single-send budget into multiple emails."""
        from accounts.views.tenant_invitations import InvitationSendThrottle

        entries = [self.row("limited-a@example.test"), self.row("limited-b@example.test")]
        throttle = InvitationSendThrottle()
        key = throttle.cache_format % {"scope": throttle.scope, "ident": self.admin.pk}
        throttle.cache.set(key, [throttle.timer()] * 29, throttle.duration)
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.send(entries)
            self.assertEqual((response.data["sent"], response.data["failed"]), (1, 1))
            self.assertEqual(send.call_count, 1)
            limited = response.data["items"][1]
            self.assertEqual(limited["reason"], "rate_limited")
            self.assertFalse(get_user_model().objects.filter(email="limited-b@example.test").exists())
            self.assertEqual(
                self.client.post(
                    "/api/v1/tenant/invitations", self.row("third@example.test"), format="json"
                ).status_code,
                429,
            )

    def test_revoked_actor_stops_preparation_between_recipients(self):
        """No further account or token is created after a caller loses management authority."""
        entries = [self.row("first@example.test"), self.row("later@example.test")]

        def revoke(*args):
            """Revoke the caller while the first SMTP delivery occurs."""
            TenantMembership.objects.filter(user=self.admin).update(role="member")

        with patch("accounts.services.tenant_invitations.send_invitation_email", side_effect=revoke) as send:
            response = self.send(entries)
        self.assertEqual((response.data["sent"], response.data["failed"]), (1, 1))
        self.assertEqual(send.call_count, 1)
        self.assertFalse(get_user_model().objects.filter(email="later@example.test").exists())

    def test_only_preview_eligible_rows_can_become_new_invitations(self):
        """Removing an existing user after preview cannot silently expand the send selection."""
        user = get_user_model().objects.create_user(username="review-existing", email="existing@example.test")
        entries = [self.row("existing@example.test"), self.row("new@example.test")]
        preview = self.preview(entries)
        user.delete()
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.send(entries, preview)
        self.assertEqual((response.data["sent"], response.data["skipped"]), (1, 1))
        self.assertEqual(send.call_count, 1)
        self.assertFalse(get_user_model().objects.filter(email="existing@example.test").exists())

    def test_existing_pending_and_disabled_invites_are_not_reconfigured_by_batch(self):
        """Bulk paste is not a bypass for explicitly reviewed resend or reinvite actions."""
        for index, status in enumerate(("invited", "disabled")):
            user = get_user_model().objects.create_user(
                username=f"pending-{index}", email=f"pending-{index}@example.test", is_active=False
            )
            TenantMembership.objects.create(tenant=self.tenant, user=user, status=status, role="member")
            user.groups.add(self.group.auth_group)
        entries = [self.row(f"pending-{index}@example.test", role="tenant_admin") for index in range(2)]
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.send(entries)
        self.assertEqual((response.data["sent"], response.data["skipped"]), (0, 2))
        send.assert_not_called()
        for member in TenantMembership.objects.filter(user__username__startswith="pending-"):
            self.assertEqual((member.role, member.auth_version), ("member", 1))
            self.assertEqual(list(member.user.groups.all()), [self.group.auth_group])

    def test_preview_and_submission_reject_non_human_admin_authority(self):
        """Staff, machine, platform-only and delegated requests cannot enumerate or invite."""
        from accounts.models import TenantApiKey

        payload = {"entries": [self.row("hidden@example.test")]}
        staff = get_user_model().objects.create_user(username="invitation-staff", is_staff=True)
        TenantMembership.objects.create(tenant=self.tenant, user=staff)
        platform = get_user_model().objects.create_superuser("invitation-platform", "p@example.test", "password")
        for user in (staff, platform):
            self.client.force_authenticate(user)
            for url in (self.url, self.url + "/preview"):
                self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        self.client.force_authenticate(self.admin)
        for url in (self.url, self.url + "/preview"):
            self.assertEqual(
                self.client.post(url, payload, format="json", HTTP_X_DELEGATION_ID="support").status_code, 403
            )
        _, secret = TenantApiKey.issue(
            tenant=self.tenant, created_by=self.admin, name="Machine", scope="all", assistant_uuids=[], expires_at=None
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        for url in (self.url, self.url + "/preview"):
            self.assertIn(client.post(url, payload, format="json").status_code, (401, 403))

    def test_deleted_group_after_preview_skips_the_row_without_creating_an_account(self):
        """Group availability is rechecked on submission and never silently removed."""
        entries = [self.row("group-gone@example.test", group_ids=[self.group.auth_group_id])]
        preview = self.preview(entries)
        self.group.delete()
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.send(entries, preview)
        self.assertEqual((response.data["sent"], response.data["skipped"]), (0, 1))
        self.assertEqual(response.data["items"][0]["reason"], "invalid_groups")
        send.assert_not_called()
        self.assertFalse(get_user_model().objects.filter(email="group-gone@example.test").exists())

    def test_revoked_invitation_can_be_restored_only_with_current_target(self):
        """A stale role, group or membership state cannot be overwritten by a reviewed reinvite."""
        from django.utils import timezone

        user = get_user_model().objects.create_user(
            username="revoked-invited", email="revoked@example.test", is_active=False
        )
        member = TenantMembership.objects.create(
            tenant=self.tenant, user=user, status="disabled", invited_at=timezone.now()
        )
        rows = self.client.get("/api/v1/tenant/members").data["results"]
        row = next(row for row in rows if row["id"] == user.pk)
        payload = self.row(user.email, target_id=user.pk, version=row["version"], role="tenant_admin")
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.client.post("/api/v1/tenant/invitations", payload, format="json")
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(send.call_count, 1)
            denied = self.client.post("/api/v1/tenant/invitations", payload, format="json")
            self.assertEqual(denied.status_code, 409, denied.data)
            self.assertEqual(send.call_count, 1)
        member.refresh_from_db()
        self.assertEqual((member.status, member.role), ("invited", "tenant_admin"))
        self.assertFalse(user.is_active)

    def test_target_id_cannot_be_replaced_by_another_email(self):
        """The explicit member target must match the exact recipient being restored."""
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.client.post(
                "/api/v1/tenant/invitations",
                self.row("other@example.test", target_id=self.admin.pk, version="old"),
                format="json",
            )
        self.assertEqual(response.status_code, 409)
        send.assert_not_called()


class BulkInvitationRaceTests(TransactionTestCase):
    """Invitation creation is serialized while delivery does not keep database locks open."""

    def test_replay_during_delivery_does_not_duplicate_an_account_or_email(self):
        """The second request can finish while SMTP is blocked and only skips the pending account."""
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock concurrency requires PostgreSQL")
        tenant = Tenant.objects.create(name="Invite race", slug="bulk-invite-race")
        admin = get_user_model().objects.create_user(username="invite-race-admin")
        TenantMembership.objects.create(tenant=tenant, user=admin, role="tenant_admin")
        client = APIClient()
        client.force_authenticate(admin)
        url = "/api/v1/tenant/invitations/bulk"
        entries = [{"email": "race@example.test", "role": "member", "group_ids": []}]
        proof = client.post(url + "/preview", {"entries": entries}, format="json").data["confirmation"]
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        responses, errors, transactions = {}, [], []

        def deliver(*args):
            """Observe the real connection state before simulating a slow mail server."""
            transactions.append(connection.in_atomic_block)
            entered.set()
            if not release.wait(8):
                raise TimeoutError("Invitation race was not released")

        def request(first):
            """Use independent connections for concurrent retries of one reviewed batch."""
            close_old_connections()
            try:
                caller = APIClient()
                caller.force_authenticate(get_user_model().objects.get(pk=admin.pk))
                responses[first] = caller.post(url, {"entries": entries, "confirmation": proof}, format="json")
            except Exception as exc:
                errors.append(exc)
            finally:
                if not first:
                    finished.set()
                close_old_connections()

        with patch("accounts.services.tenant_invitations.send_invitation_email", side_effect=deliver) as send:
            first = threading.Thread(target=request, args=(True,))
            second = threading.Thread(target=request, args=(False,))
            first.start()
            self.assertTrue(entered.wait(8))
            second.start()
            try:
                self.assertTrue(finished.wait(8), "SMTP must not hold the tenant transaction open")
            finally:
                release.set()
                first.join(10)
                second.join(10)
        self.assertEqual(errors, [])
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(transactions, [False])
        self.assertEqual(send.call_count, 1)
        self.assertEqual((responses[True].data["sent"], responses[False].data["skipped"]), (1, 1))
        self.assertEqual(get_user_model().objects.filter(email="race@example.test").count(), 1)
