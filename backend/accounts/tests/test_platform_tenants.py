"""Platform tenant overview and delegation tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import (
    AuditLog,
    Profile,
    Tenant,
    TenantMembership,
)
from lens.models import Assistant


class PlatformTenantDelegationTests(TestCase):
    """Platform super admins see aggregates and delegate into one tenant."""

    def setUp(self):
        user_model = get_user_model()
        self.platform = user_model.objects.create_user(
            username="platform-admin",
            email="platform-admin@example.com",
            password="x",
            is_staff=True,
        )
        profile, _ = Profile.objects.get_or_create(user=self.platform)
        profile.platform_role = Profile.PlatformRole.SUPER_ADMIN
        profile.save(update_fields=["platform_role"])
        self.regular = user_model.objects.create_user(
            username="regular-user",
            email="regular-user@example.com",
            password="x",
        )
        self.tenant_a = Tenant.objects.create(name="Acme", slug="acme")
        self.tenant_b = Tenant.objects.create(name="Beta", slug="beta")
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.platform,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.regular,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.assistant_a = Assistant.objects.create(
            name="Acme assistant",
            slug="acme-platform-assistant",
            tenant=self.tenant_a,
        )
        self.assistant_b = Assistant.objects.create(
            name="Beta assistant",
            slug="beta-platform-assistant",
            tenant=self.tenant_b,
        )
        self.platform_client = APIClient()
        self.platform_client.force_authenticate(self.platform)
        self.regular_client = APIClient()
        self.regular_client.force_authenticate(self.regular)

    def test_platform_tenant_list_requires_super_admin(self):
        allowed = self.platform_client.get("/api/v1/platform/tenants")
        denied = self.regular_client.get("/api/v1/platform/tenants")

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        names = {row["name"] for row in allowed.data["results"]}
        self.assertIn("Acme", names)
        self.assertIn("Beta", names)

    def test_platform_tenant_list_has_aggregate_counts(self):
        response = self.platform_client.get("/api/v1/platform/tenants")
        acme = next(row for row in response.data["results"] if row["name"] == "Acme")

        self.assertEqual(acme["member_count"], 1)
        self.assertEqual(acme["assistant_count"], 1)

    def test_delegation_scopes_requests_to_target_tenant(self):
        start = self.platform_client.post(
            f"/api/v1/platform/tenants/{self.tenant_b.uuid}/delegate",
            {"reason": "Support ticket", "expires_in_minutes": 60},
            format="json",
        )
        self.assertEqual(start.status_code, 201, start.data)
        delegation_id = start.data["delegation_id"]

        delegated = APIClient()
        delegated.force_authenticate(self.platform)
        delegated.credentials(HTTP_X_DELEGATION_ID=delegation_id)

        tenant_info = delegated.get("/api/v1/tenant")
        assistants = delegated.get("/api/lens/assistants/", {"page_size": 100})

        self.assertEqual(tenant_info.status_code, 200)
        self.assertEqual(tenant_info.data["slug"], "beta")
        slugs = {row["slug"] for row in assistants.data["results"]}
        self.assertIn("beta-platform-assistant", slugs)
        self.assertNotIn("acme-platform-assistant", slugs)

    def test_delegation_cannot_manage_members_or_groups(self):
        start = self.platform_client.post(
            f"/api/v1/platform/tenants/{self.tenant_b.uuid}/delegate",
            {"reason": "Read only ops"},
            format="json",
        )
        delegated = APIClient()
        delegated.force_authenticate(self.platform)
        delegated.credentials(HTTP_X_DELEGATION_ID=start.data["delegation_id"])

        members = delegated.get("/api/v1/tenant/members")
        groups = delegated.get("/api/v1/tenant/groups")

        self.assertEqual(members.status_code, 403)
        self.assertEqual(groups.status_code, 403)

    def test_delegated_write_is_audited(self):
        start = self.platform_client.post(
            f"/api/v1/platform/tenants/{self.tenant_b.uuid}/delegate",
            {"reason": "Config help"},
            format="json",
        )
        delegated = APIClient()
        delegated.force_authenticate(self.platform)
        delegated.credentials(HTTP_X_DELEGATION_ID=start.data["delegation_id"])

        response = delegated.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Delegated datasource",
                "source_type": "git",
                "config": {"repo_url": "https://example.com/beta.git"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        audit = AuditLog.objects.get(action="delegation.operation")
        self.assertEqual(audit.tenant_id, self.tenant_b.uuid)
        self.assertEqual(audit.actor_user_id, self.platform.pk)
        self.assertEqual(str(audit.delegation_id), start.data["delegation_id"])
        self.assertEqual(audit.metadata["method"], "POST")

    def test_end_delegation_removes_scope(self):
        start = self.platform_client.post(
            f"/api/v1/platform/tenants/{self.tenant_b.uuid}/delegate",
            {"expires_in_minutes": 60},
            format="json",
        )
        delegation_id = start.data["delegation_id"]
        end = self.platform_client.post(f"/api/v1/platform/delegations/{delegation_id}/end")
        self.assertEqual(end.status_code, 200)

        delegated = APIClient()
        delegated.force_authenticate(self.platform)
        delegated.credentials(HTTP_X_DELEGATION_ID=delegation_id)
        response = delegated.get("/api/v1/tenant")

        self.assertEqual(response.status_code, 403)
