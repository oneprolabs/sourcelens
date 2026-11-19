"""Prototype bulk member actions use reviewed tenant-local selections."""

import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, Tenant, TenantApiKey, TenantGroup, TenantMembership


class TenantMemberBulkTests(TestCase):
    """Exercise batch outcomes and rollback through the actual HTTP boundary."""

    def setUp(self):
        """Create separate tenants and an explicit administrator without staff privileges."""
        self.tenant = Tenant.objects.create(name="Bulk tenant", slug="bulk-members")
        self.other = Tenant.objects.create(name="Other", slug="bulk-members-other")
        self.admin = self.member("bulk-admin", role="tenant_admin")
        self.first = self.member("bulk-first")
        self.second = self.member("bulk-second")
        self.foreign = self.member("bulk-foreign", tenant=self.other)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = "/api/v1/tenant/members/bulk"

    def member(self, name, *, role="member", tenant=None, status="active"):
        """Create a real membership with status-consistent authentication state."""
        user = get_user_model().objects.create_user(
            username=name, email=f"{name}@example.test", is_active=status == "active"
        )
        TenantMembership.objects.create(tenant=tenant or self.tenant, user=user, role=role, status=status)
        return user

    def selection(self, *users):
        """Freeze the version returned by the live member collection."""
        rows = self.client.get("/api/v1/tenant/members", {"page_size": 100}).data["results"]
        indexed = {row["id"]: row for row in rows}
        return [{"id": user.pk, "version": indexed[user.pk]["version"]} for user in users]

    def call(self, action, *users, **values):
        """Apply one reviewed batch."""
        return self.client.post(
            self.url, {"action": action, "members": self.selection(*users), **values}, format="json"
        )

    def test_role_change_skips_unchanged_and_keeps_last_administrator(self):
        """Demoting all live admins rejects the whole batch before any member changes."""
        response = self.call("role", self.first, self.second, role="tenant_admin")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["changed"], 2)
        response = self.call("role", self.first, self.second, role="tenant_admin")
        self.assertEqual(response.data["skipped"], 2)
        denied = self.call("role", self.admin, self.first, self.second, role="member")
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertEqual(TenantMembership.objects.filter(tenant=self.tenant, role="tenant_admin").count(), 3)

    def test_mixed_foreign_and_stale_selection_is_atomic(self):
        """Neither missing scope nor a concurrent edit allows a partially applied selection."""
        selected = self.selection(self.first)
        for tail in ({"id": self.foreign.pk, "version": "unknown"}, {"id": 99999999, "version": "unknown"}):
            response = self.client.post(self.url, {"action": "disable", "members": selected + [tail]}, format="json")
            self.assertEqual(response.status_code, 404, response.data)
        TenantMembership.objects.filter(user=self.first).update(role="tenant_admin")
        response = self.client.post(self.url, {"action": "disable", "members": selected}, format="json")
        self.assertEqual(response.status_code, 409, response.data)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_active)

    def test_enable_skips_pending_invitation_and_disable_protects_self(self):
        """Bulk enable cannot bypass mailbox acceptance or disable the requesting admin."""
        invited = self.member("bulk-invited", status="invited")
        disabled = self.member("bulk-disabled", status="disabled")
        response = self.call("enable", self.first, invited, disabled)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["changed"], response.data["skipped"]), (1, 2))
        invited.refresh_from_db()
        self.assertFalse(invited.is_active)
        denied = self.call("disable", self.admin, self.first)
        self.assertEqual(denied.status_code, 400, denied.data)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_active)

    def test_group_addition_preserves_existing_groups_and_rejects_foreign_group(self):
        """The batch adds memberships rather than replacing personal or other local groups."""
        existing = Group.objects.create(name="Existing")
        added = Group.objects.create(name="Added")
        foreign = Group.objects.create(name="Foreign")
        TenantGroup.objects.create(tenant=self.tenant, auth_group=added, name="Added")
        TenantGroup.objects.create(tenant=self.other, auth_group=foreign, name="Foreign")
        self.first.groups.add(existing)
        response = self.call("groups", self.first, self.second, group_ids=[added.pk])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(self.first.groups.values_list("pk", flat=True)), {existing.pk, added.pk})
        denied = self.call("groups", self.first, self.second, group_ids=[foreign.pk])
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertFalse(self.first.groups.filter(pk=foreign.pk).exists())

    def test_disable_preview_and_execution_use_real_creator_keys(self):
        """A demoted creator's actual keys remain visible to the disable choice."""
        key, _ = TenantApiKey.issue(
            tenant=self.tenant, created_by=self.first, name="Live", scope="all", assistant_uuids=[], expires_at=None
        )
        other, _ = TenantApiKey.issue(
            tenant=self.other, created_by=self.first, name="Other", scope="all", assistant_uuids=[], expires_at=None
        )
        response = self.call("disable", self.first, self.second, preview=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["key_summary"]["active"], 1)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_active)
        response = self.call("disable", self.first, self.second, disable_api_keys=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["disabled_keys"], 1)
        key.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((key.status, other.status), ("disabled", "active"))
        self.assertEqual(AuditLog.objects.filter(action="member.bulk_disable").count(), 2)

    def test_staff_member_and_inactive_tenant_cannot_manage_members(self):
        """Staff flags and cached membership cannot replace an active explicit tenant administrator."""
        selected = self.selection(self.first)
        self.second.is_staff = True
        self.second.save()
        self.client.force_authenticate(self.second)
        for url in (self.url, f"/api/v1/tenant/members/{self.first.pk}"):
            method = self.client.post if url == self.url else self.client.patch
            response = method(url, {"action": "disable", "members": selected, "status": "disabled"}, format="json")
            self.assertEqual(response.status_code, 403, response.data)
        self.client.force_authenticate(self.admin)
        Tenant.objects.filter(pk=self.tenant.pk).update(status="suspended")
        response = self.client.post(self.url, {"action": "disable", "members": selected}, format="json")
        self.assertEqual(response.status_code, 403, response.data)

    def test_invalid_batches_never_mutate_members(self):
        """Bound selection size and require action-specific parameters."""
        selected = self.selection(self.first)
        for payload in (
            {"action": "disable", "members": []},
            {"action": "disable", "members": selected * 2},
            {"action": "disable", "members": [{"id": n, "version": "v"} for n in range(1, 102)]},
            {"action": "disable", "members": selected, "disable_api_keys": "false"},
            {"action": "role", "members": selected},
            {"action": "groups", "members": selected, "group_ids": []},
            {"action": "enable", "members": selected, "role": "member"},
        ):
            response = self.client.post(self.url, payload, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_active)

    def test_group_and_account_changes_invalidate_confirmation(self):
        """Group routes and account changes cannot silently change a reviewed target."""
        selected = self.selection(self.first)
        self.first.groups.add(Group.objects.create(name="Concurrent group"))
        response = self.client.post(self.url, {"action": "disable", "members": selected}, format="json")
        self.assertEqual(response.status_code, 409)
        selected = self.selection(self.first)
        get_user_model().objects.filter(pk=self.first.pk).update(email="changed@example.test")
        response = self.client.post(self.url, {"action": "disable", "members": selected}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_mutation_failure_rolls_back_the_entire_batch(self):
        """A later write failure must undo prior member changes."""
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory, force_authenticate

        from accounts.services.member_management import MemberBulkInput, mutate_members

        raw = APIRequestFactory().post(self.url)
        force_authenticate(raw, user=self.admin)
        data = MemberBulkInput(data={"action": "disable", "members": self.selection(self.first, self.second)})
        data.is_valid(raise_exception=True)
        original = TenantMembership.save

        def fail_second(member, *args, **kwargs):
            """Fail only after the first member has been changed."""
            if member.user_id == self.second.pk:
                raise RuntimeError("write unavailable")
            return original(member, *args, **kwargs)

        with patch.object(TenantMembership, "save", fail_second), self.assertRaises(RuntimeError):
            mutate_members(request=Request(raw), data=data.validated_data)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_active)
        self.assertFalse(AuditLog.objects.filter(action="member.bulk_disable").exists())

    def test_resend_reports_real_delivery_and_skips_active_members(self):
        """SMTP failure remains pending and does not mask other successful recipients."""
        first = self.member("bulk-pending-a", status="invited")
        second = self.member("bulk-pending-b", status="invited")
        with patch(
            "accounts.services.tenant_invitations.send_invitation_email", side_effect=[None, OSError("smtp")]
        ) as send:
            response = self.call("resend", first, second, self.first)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["changed"], response.data["skipped"], response.data["failed"]), (1, 1, 1))
        self.assertEqual(send.call_count, 2)
        second.refresh_from_db()
        self.assertFalse(second.is_active)
        self.assertEqual(TenantMembership.objects.get(user=second).status, "invited")
        self.assertNotIn("token", str(response.data))
        self.assertEqual(AuditLog.objects.filter(action="member.bulk_resend", result="failed").count(), 1)

    def test_resend_prevalidates_scope_and_version_before_any_email(self):
        """An invalid tail never allows a partial send or credential rotation."""
        first = self.member("bulk-pending", status="invited")
        selected = self.selection(first)
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.client.post(
                self.url,
                {"action": "resend", "members": selected + [{"id": self.foreign.pk, "version": "other"}]},
                format="json",
            )
            self.assertEqual(response.status_code, 404)
            TenantMembership.objects.filter(user=first).update(auth_version=10)
            response = self.client.post(self.url, {"action": "resend", "members": selected}, format="json")
            self.assertEqual(response.status_code, 409)
            send.assert_not_called()

    def test_resend_shares_per_recipient_single_invitation_budget(self):
        """One batch cannot turn one request budget unit into multiple email deliveries."""
        first = self.member("bulk-budget-a", status="invited")
        second = self.member("bulk-budget-b", status="invited")
        with patch(
            "accounts.views.tenant_invitations.InvitationSendThrottle.allow_request", side_effect=[True, False]
        ) as budget:
            with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
                response = self.call("resend", first, second, self.first)
        self.assertEqual((response.data["changed"], response.data["failed"], response.data["skipped"]), (1, 1, 1))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(budget.call_count, 2)
        self.assertEqual(TenantMembership.objects.get(user=second).auth_version, 1)
        self.assertEqual(next(i for i in response.data["items"] if i["id"] == second.pk)["reason"], "rate_limited")

    def test_real_throttle_budget_is_shared_with_single_resend(self):
        """Thirty allowed deliveries are shared across batch and single endpoints."""
        from accounts.views.tenant_invitations import InvitationSendThrottle

        first = self.member("bulk-real-budget-a", status="invited")
        second = self.member("bulk-real-budget-b", status="invited")
        throttle = InvitationSendThrottle()
        key = throttle.cache_format % {"scope": throttle.scope, "ident": self.admin.pk}
        throttle.cache.set(key, [throttle.timer()] * 29, throttle.duration)
        try:
            with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
                response = self.call("resend", first, second)
                self.assertEqual((response.data["changed"], response.data["failed"]), (1, 1))
                self.assertEqual(send.call_count, 1)
                response = self.client.post(f"/api/v1/tenant/invitations/{second.pk}/resend")
                self.assertEqual(response.status_code, 429, response.data)
                self.assertEqual(send.call_count, 1)
        finally:
            throttle.cache.delete(key)

    def test_resend_rechecks_actor_between_recipients(self):
        """Revoking the caller during delivery stops all subsequent rotations and sends."""
        first = self.member("bulk-revoke-a", status="invited")
        second = self.member("bulk-revoke-b", status="invited")

        def revoke(*args):
            """Remove administrative authority while the first email is delivered."""
            TenantMembership.objects.filter(user=self.admin).update(role="member")

        with patch("accounts.services.tenant_invitations.send_invitation_email", side_effect=revoke) as send:
            response = self.call("resend", first, second)
        self.assertEqual((response.data["changed"], response.data["failed"]), (1, 1))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(TenantMembership.objects.get(user=second).auth_version, 1)

    def test_resend_preview_never_sends_or_rotates(self):
        """Confirmation previews have no credential or delivery side effects."""
        pending = self.member("bulk-preview-pending", status="invited")
        with patch("accounts.services.tenant_invitations.send_invitation_email") as send:
            response = self.call("resend", pending, self.first, preview=True)
        self.assertEqual((response.data["changed"], response.data["skipped"]), (1, 1))
        send.assert_not_called()
        self.assertEqual(TenantMembership.objects.get(user=pending).auth_version, 1)

    def test_cached_old_membership_does_not_control_the_member_list_scope(self):
        """A moved caller cannot use an old authenticated User cache to read the previous tenant."""
        selected = self.selection(self.first)
        self.assertEqual(self.admin.membership.tenant_id, self.tenant.pk)
        TenantMembership.objects.filter(user=self.admin).update(tenant=self.other)
        response = self.client.get("/api/v1/tenant/members")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(self.first.pk, [row["id"] for row in response.data["results"]])
        self.assertIn(self.foreign.pk, [row["id"] for row in response.data["results"]])
        response = self.client.post(self.url, {"action": "disable", "members": selected}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_machine_delegation_and_platform_credentials_cannot_manage_members(self):
        """Neither a machine identity nor platform access grants member administration."""
        selected = self.selection(self.first)
        key, secret = TenantApiKey.issue(
            tenant=self.tenant, created_by=self.admin, name="Machine", scope="all", assistant_uuids=[], expires_at=None
        )
        machine = APIClient()
        machine.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
        response = machine.post(self.url, {"action": "disable", "members": selected}, format="json")
        self.assertIn(response.status_code, (401, 403))
        response = self.client.post(
            self.url, {"action": "disable", "members": selected}, HTTP_X_DELEGATION_ID="support-context", format="json"
        )
        self.assertEqual(response.status_code, 403)
        platform = get_user_model().objects.create_superuser("bulk-platform", "platform@example.test", "password")
        self.client.force_authenticate(platform)
        response = self.client.post(self.url, {"action": "disable", "members": selected}, format="json")
        self.assertEqual(response.status_code, 403)


class TenantMemberBulkRaceTests(TransactionTestCase):
    """Exercise tenant serialization through separate PostgreSQL connections."""

    def test_concurrent_demotion_preserves_admin_and_revoked_actor_cannot_write(self):
        """Blocked requests recheck both the remaining admin and their own authority."""
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock concurrency requires PostgreSQL")
        for revoke_actor in (False, True):
            with self.subTest(revoke_actor=revoke_actor):
                tenant = Tenant.objects.create(name="Race", slug=f"member-race-{revoke_actor}")
                users = [
                    get_user_model().objects.create_user(username=f"member-race-{revoke_actor}-{i}") for i in range(3)
                ]
                for i, user in enumerate(users):
                    TenantMembership.objects.create(
                        tenant=tenant, user=user, role="tenant_admin" if i < 2 else "member"
                    )
                client = APIClient()
                client.force_authenticate(users[0])
                rows = {r["id"]: r for r in client.get("/api/v1/tenant/members").data["results"]}
                entered, release, started = threading.Event(), threading.Event(), threading.Event()
                responses, errors = {}, []
                original = TenantMembership.save

                def paused_save(member, *args, **kwargs):
                    """Hold the first request after it owns the authoritative tenant lock."""
                    if threading.current_thread().name == "member-first":
                        entered.set()
                        if not release.wait(8):
                            raise TimeoutError("Member race was not released")
                    return original(member, *args, **kwargs)

                def request(first):
                    """Authenticate independent callers before they compete for the lock."""
                    close_old_connections()
                    try:
                        client = APIClient()
                        client.force_authenticate(get_user_model().objects.get(pk=users[0 if first else 1].pk))
                        target = users[1 if revoke_actor else 0] if first else users[2 if revoke_actor else 1]
                        if not first:
                            started.set()
                        responses[first] = client.post(
                            "/api/v1/tenant/members/bulk",
                            {
                                "action": "role",
                                "role": "tenant_admin" if revoke_actor and not first else "member",
                                "members": [{"id": target.pk, "version": rows[target.pk]["version"]}],
                            },
                            format="json",
                        )
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        close_old_connections()

                with patch.object(TenantMembership, "save", paused_save):
                    first = threading.Thread(target=request, args=(True,), name="member-first")
                    second = threading.Thread(target=request, args=(False,), name="member-second")
                    first.start()
                    self.assertTrue(entered.wait(8))
                    second.start()
                    self.assertTrue(started.wait(8))
                    release.set()
                    first.join(10)
                    second.join(10)
                self.assertFalse(first.is_alive() or second.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(responses[True].status_code, 200, responses[True].data)
                self.assertEqual(responses[False].status_code, 403 if revoke_actor else 400, responses[False].data)
                self.assertEqual(TenantMembership.objects.filter(tenant=tenant, role="tenant_admin").count(), 1)
                if revoke_actor:
                    self.assertEqual(TenantMembership.objects.get(user=users[2]).role, "member")
