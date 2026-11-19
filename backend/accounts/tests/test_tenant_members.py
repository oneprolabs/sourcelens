"""Tests for tenant-scoped member management."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Tenant, TenantMembership


class TenantMemberApiTests(TestCase):
    """Tenant administrators manage only members of their own tenant."""

    def setUp(self):
        user_model = get_user_model()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Other", slug="other")
        self.admin = user_model.objects.create_user(
            username="member-admin",
            email="member-admin@example.com",
            password="x",
        )
        self.member = user_model.objects.create_user(
            username="member-one",
            email="member-one@example.com",
            password="x",
        )
        self.other = user_model.objects.create_user(
            username="member-other",
            email="member-other@example.com",
            password="x",
        )
        self.other_tenant_user = user_model.objects.create_user(
            username="member-foreign",
            email="member-foreign@example.com",
            password="x",
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.member,
            role=TenantMembership.Role.MEMBER,
        )
        self.group = Group.objects.create(name="Acme Engineering")
        self.member.groups.add(self.group)
        TenantMembership.objects.create(
            tenant=self.other_tenant,
            user=self.other_tenant_user,
            role=TenantMembership.Role.MEMBER,
        )
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.member_client = APIClient()
        self.member_client.force_authenticate(self.member)

    def test_member_list_is_tenant_scoped(self):
        response = self.admin_client.get(
            "/api/v1/tenant/members",
            {"page_size": 100},
        )

        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.admin.pk, ids)
        self.assertIn(self.member.pk, ids)
        self.assertNotIn(self.other_tenant_user.pk, ids)
        member_row = next(row for row in response.data["results"] if row["id"] == self.member.pk)
        self.assertEqual(member_row["role"], "member")
        self.assertEqual(member_row["groups"][0]["name"], "Acme Engineering")
        self.assertFalse(member_row["is_self"])

    def test_member_search_and_role_filter(self):
        response = self.admin_client.get(
            "/api/v1/tenant/members",
            {"search": "member-one", "role": "member"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.member.pk)

    def test_group_filter(self):
        response = self.admin_client.get(
            "/api/v1/tenant/members",
            {"group": self.group.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.member.pk)

    def test_admin_can_change_role_and_groups(self):
        response = self.admin_client.patch(
            f"/api/v1/tenant/members/{self.member.pk}",
            {
                "role": "tenant_admin",
                "group_ids": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.member.membership.refresh_from_db()
        self.assertEqual(self.member.membership.role, "tenant_admin")
        self.assertFalse(self.member.groups.exists())

    def test_admin_can_disable_and_enable_member(self):
        disable = self.admin_client.patch(
            f"/api/v1/tenant/members/{self.member.pk}",
            {"status": "disabled"},
            format="json",
        )
        self.assertEqual(disable.status_code, 200)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)

        enable = self.admin_client.patch(
            f"/api/v1/tenant/members/{self.member.pk}",
            {"status": "active"},
            format="json",
        )
        self.assertEqual(enable.status_code, 200)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

    def test_cannot_disable_self(self):
        response = self.admin_client.patch(
            f"/api/v1/tenant/members/{self.admin.pk}",
            {"status": "disabled"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.data)

    def test_cannot_remove_last_tenant_admin(self):
        response = self.admin_client.patch(
            f"/api/v1/tenant/members/{self.admin.pk}",
            {"role": "member"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("role", response.data)

    def test_member_cannot_manage_members(self):
        read = self.member_client.get("/api/v1/tenant/members")
        write = self.member_client.patch(
            f"/api/v1/tenant/members/{self.member.pk}",
            {"role": "tenant_admin"},
            format="json",
        )

        self.assertEqual(read.status_code, 403)
        self.assertEqual(write.status_code, 403)

    def test_cannot_update_other_tenant_member(self):
        response = self.admin_client.patch(
            f"/api/v1/tenant/members/{self.other_tenant_user.pk}",
            {"role": "tenant_admin"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_disabling_member_preserves_keys_unless_explicitly_selected(self):
        """The two prototype actions have different, tenant-local credential outcomes."""
        from accounts.models import AuditLog, TenantApiKey

        def issue(tenant, creator, state="active"):
            key, raw = TenantApiKey.issue(
                tenant=tenant,
                created_by=creator,
                name="Member integration",
                scope="all",
                assistant_uuids=[],
                expires_at=None,
            )
            TenantApiKey.objects.filter(pk=key.pk).update(status=state)
            return key, raw

        key, raw = issue(self.tenant, self.member)
        foreign, _ = issue(self.other_tenant, self.member)
        other, _ = issue(self.tenant, self.admin)
        revoked, _ = issue(self.tenant, self.member, "revoked")
        url = f"/api/v1/tenant/members/{self.member.pk}"
        response = self.admin_client.patch(url, {"status": "disabled"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(external.get("/api/lens/assistants/").status_code, 200)
        response = self.admin_client.patch(url, {"status": "disabled", "disable_api_keys": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(external.get("/api/lens/assistants/").status_code, 401)
        for record, expected in ((key, "disabled"), (foreign, "active"), (other, "active"), (revoked, "revoked")):
            record.refresh_from_db()
            self.assertEqual(record.status, expected)
        self.assertEqual(
            AuditLog.objects.filter(action="api_key.disable_with_member", resource_id=str(key.pk)).count(), 1
        )
        self.admin_client.patch(url, {"status": "active"}, format="json")
        key.refresh_from_db()
        self.assertEqual(key.status, "disabled")

    def test_invalid_combined_disable_does_not_change_membership(self):
        """Reject coercion and prevent credential changes on unrelated member edits."""
        url = f"/api/v1/tenant/members/{self.member.pk}"
        for payload in (
            {"status": "disabled", "disable_api_keys": "false"},
            {"status": "active", "disable_api_keys": True},
        ):
            response = self.admin_client.patch(url, payload, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(TenantMembership.objects.get(user=self.member).status, "active")

    def test_status_tab_counts_remain_tenant_wide_when_filtered(self):
        """Tabs show real whole-tenant counts independent of the current search and page."""
        TenantMembership.objects.filter(user=self.member).update(status="invited")
        response = self.admin_client.get(
            "/api/v1/tenant/members", {"search": "member-one", "status": "invited", "page_size": 1}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["status_counts"], {"all": 2, "active": 1, "invited": 1})

    def test_member_groups_use_tenant_display_names_and_hide_foreign_groups(self):
        """Project business names without leaking a foreign group's metadata."""
        from accounts.models import TenantGroup

        TenantGroup.objects.create(tenant=self.tenant, auth_group=self.group, name="Engineering")
        foreign = Group.objects.create(name="foreign-internal-id")
        TenantGroup.objects.create(tenant=self.other_tenant, auth_group=foreign, name="Foreign secret")
        self.member.groups.add(foreign)
        response = self.admin_client.get("/api/v1/tenant/members")
        row = next(row for row in response.data["results"] if row["id"] == self.member.pk)
        self.assertEqual(row["groups"], [{"id": self.group.pk, "name": "Engineering"}])
