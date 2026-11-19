"""Platform API key overview tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import (
    AuditLog,
    Profile,
    Tenant,
    TenantApiKey,
    TenantMembership,
)


class PlatformApiKeyOverviewTests(TestCase):
    """Platform super admins can inspect and force-control tenant keys."""

    def setUp(self):
        user_model = get_user_model()
        self.platform = user_model.objects.create_user(
            username="key-platform",
            email="key-platform@example.com",
            password="x",
            is_staff=True,
        )
        profile, _ = Profile.objects.get_or_create(user=self.platform)
        profile.platform_role = Profile.PlatformRole.SUPER_ADMIN
        profile.save(update_fields=["platform_role"])
        self.admin = user_model.objects.create_user(
            username="key-admin",
            email="key-admin@example.com",
            password="x",
        )
        self.tenant_a = Tenant.objects.create(name="Acme", slug="acme")
        self.tenant_b = Tenant.objects.create(name="Beta", slug="beta")
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.key_a = TenantApiKey.objects.create(
            tenant=self.tenant_a,
            name="CI pipeline",
            key_prefix="slk_aaaa1111",
            key_hash="hash-a",
            created_by=self.admin,
        )
        self.key_b = TenantApiKey.objects.create(
            tenant=self.tenant_b,
            name="Production",
            key_prefix="slk_bbbb2222",
            key_hash="hash-b",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.platform)
        self.regular = APIClient()
        self.regular.force_authenticate(self.admin)

    def test_requires_super_admin(self):
        response = self.regular.get("/api/v1/platform/api-keys")

        self.assertEqual(response.status_code, 403)

    def test_lists_keys_across_tenants_without_secrets(self):
        response = self.client.get("/api/v1/platform/api-keys")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        rows = {row["name"]: row for row in response.data["results"]}
        self.assertEqual(rows["CI pipeline"]["tenant"]["slug"], "acme")
        self.assertEqual(rows["CI pipeline"]["created_by"], "key-admin")
        self.assertNotIn("key_hash", rows["CI pipeline"])
        self.assertNotIn("key", rows["CI pipeline"])

    def test_search_by_tenant_prefix_and_creator(self):
        by_tenant = self.client.get(
            "/api/v1/platform/api-keys",
            {"search": "Beta"},
        )
        by_prefix = self.client.get(
            "/api/v1/platform/api-keys",
            {"search": "slk_aaaa"},
        )
        by_creator = self.client.get(
            "/api/v1/platform/api-keys",
            {"search": "key-admin"},
        )

        self.assertEqual(by_tenant.data["count"], 1)
        self.assertEqual(by_prefix.data["count"], 1)
        self.assertEqual(by_creator.data["count"], 1)

    def test_force_disable_and_revoke_are_audited(self):
        disabled = self.client.post(f"/api/v1/platform/api-keys/{self.key_a.uuid}/disable")
        revoked = self.client.post(f"/api/v1/platform/api-keys/{self.key_b.uuid}/revoke")

        self.assertEqual(disabled.status_code, 200, disabled.data)
        self.assertEqual(disabled.data["status"], "disabled")
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertEqual(revoked.data["status"], "revoked")
        self.assertTrue(
            AuditLog.objects.filter(
                action="api_key.force_disable",
                resource_id=str(self.key_a.uuid),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="api_key.force_revoke",
                resource_id=str(self.key_b.uuid),
            ).exists()
        )

    def test_revoked_key_cannot_change_state(self):
        self.key_a.status = TenantApiKey.Status.REVOKED
        self.key_a.save(update_fields=["status"])

        response = self.client.post(f"/api/v1/platform/api-keys/{self.key_a.uuid}/disable")

        self.assertEqual(response.status_code, 409)
