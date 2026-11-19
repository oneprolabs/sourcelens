"""Platform member directory tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import (
    Profile,
    Tenant,
    TenantApiKey,
    TenantMembership,
)


class PlatformMemberDirectoryTests(TestCase):
    """Platform super admins can look up membership ownership read-only."""

    def setUp(self):
        user_model = get_user_model()
        self.platform = user_model.objects.create_user(
            username="member-platform",
            email="member-platform@example.com",
            password="x",
            is_staff=True,
        )
        profile, _ = Profile.objects.get_or_create(user=self.platform)
        profile.platform_role = Profile.PlatformRole.SUPER_ADMIN
        profile.save(update_fields=["platform_role"])
        self.admin = user_model.objects.create_user(
            username="acme-admin",
            email="acme-admin@example.com",
            password="x",
            first_name="Acme",
            last_name="Admin",
        )
        self.member = user_model.objects.create_user(
            username="beta-member",
            email="beta-member@example.com",
            password="x",
        )
        self.tenant_a = Tenant.objects.create(name="Acme", slug="acme")
        self.tenant_b = Tenant.objects.create(name="Beta", slug="beta")
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.member,
            role=TenantMembership.Role.MEMBER,
            status=TenantMembership.Status.DISABLED,
        )
        TenantApiKey.objects.create(
            tenant=self.tenant_a,
            name="CI key",
            key_prefix="slk_aaaa",
            key_hash="hash-1",
            created_by=self.admin,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.platform)
        self.regular = APIClient()
        self.regular.force_authenticate(self.member)

    def test_requires_super_admin(self):
        response = self.regular.get("/api/v1/platform/members")

        self.assertEqual(response.status_code, 403)

    def test_lists_members_with_tenant_and_key_count(self):
        response = self.client.get("/api/v1/platform/members")

        self.assertEqual(response.status_code, 200, response.data)
        rows = {row["username"]: row for row in response.data["results"]}
        self.assertEqual(rows["acme-admin"]["tenant"]["slug"], "acme")
        self.assertEqual(rows["acme-admin"]["role"], "tenant_admin")
        self.assertEqual(rows["acme-admin"]["api_key_count"], 1)
        self.assertEqual(rows["beta-member"]["api_key_count"], 0)
        self.assertEqual(rows["beta-member"]["status"], "disabled")
        self.assertEqual(response.data["counts"]["active"], 1)
        self.assertEqual(response.data["counts"]["disabled"], 1)

    def test_search_by_email_and_name(self):
        by_email = self.client.get(
            "/api/v1/platform/members",
            {"search": "acme-admin@example.com"},
        )
        by_name = self.client.get(
            "/api/v1/platform/members",
            {"search": "Acme Admin"},
        )

        self.assertEqual(by_email.data["count"], 1)
        self.assertEqual(by_name.data["count"], 1)
        self.assertEqual(by_name.data["results"][0]["username"], "acme-admin")

    def test_filters_by_tenant_role_and_status(self):
        by_tenant = self.client.get(
            "/api/v1/platform/members",
            {"tenant": str(self.tenant_b.uuid)},
        )
        by_role = self.client.get(
            "/api/v1/platform/members",
            {"role": "member"},
        )
        by_status = self.client.get(
            "/api/v1/platform/members",
            {"status": "disabled"},
        )

        self.assertEqual(by_tenant.data["count"], 1)
        self.assertEqual(by_role.data["count"], 1)
        self.assertEqual(by_status.data["count"], 1)
        self.assertEqual(by_status.data["results"][0]["username"], "beta-member")
