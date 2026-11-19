"""Platform overview and audit log tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantMembership
from lens.models import Assistant, LensNode


class PlatformAuditTests(TestCase):
    """Platform super admins can read aggregates and audit records."""

    def setUp(self):
        user_model = get_user_model()
        self.platform = user_model.objects.create_user(
            username="audit-platform",
            email="audit-platform@example.com",
            password="x",
            is_staff=True,
        )
        profile, _ = Profile.objects.get_or_create(user=self.platform)
        profile.platform_role = Profile.PlatformRole.SUPER_ADMIN
        profile.save(update_fields=["platform_role"])
        self.regular = user_model.objects.create_user(
            username="audit-regular",
            email="audit-regular@example.com",
            password="x",
        )
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.platform,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        Assistant.objects.create(
            name="Acme assistant",
            slug="audit-acme-assistant",
            tenant=self.tenant,
        )
        LensNode.objects.create(
            name="Audit node",
            workspace_path="/workspace",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.platform_client = APIClient()
        self.platform_client.force_authenticate(self.platform)
        self.regular_client = APIClient()
        self.regular_client.force_authenticate(self.regular)

    def test_overview_requires_super_admin_and_returns_counts(self):
        allowed = self.platform_client.get("/api/v1/platform/overview")
        denied = self.regular_client.get("/api/v1/platform/overview")

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        self.assertGreaterEqual(allowed.data["tenant_count"], 1)
        self.assertGreaterEqual(allowed.data["assistant_count"], 1)
        self.assertGreaterEqual(allowed.data["node_online_count"], 1)

    def test_delegation_writes_audit_record(self):
        start = self.platform_client.post(
            f"/api/v1/platform/tenants/{self.tenant.uuid}/delegate",
            {"reason": "Support", "expires_in_minutes": 60},
            format="json",
        )
        self.assertEqual(start.status_code, 201, start.data)

        audit = self.platform_client.get(
            "/api/v1/platform/audit",
            {"action": "delegation.start"},
        )

        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.data["count"], 1)
        row = audit.data["results"][0]
        self.assertEqual(row["tenant"]["slug"], "acme")
        self.assertEqual(row["actor"], "audit-platform")
        self.assertEqual(row["metadata"]["reason"], "Support")

    def test_audit_requires_super_admin(self):
        response = self.regular_client.get("/api/v1/platform/audit")

        self.assertEqual(response.status_code, 403)

    def test_audit_filters_and_search(self):
        self.platform_client.post(
            f"/api/v1/platform/tenants/{self.tenant.uuid}/delegate",
            {"reason": "Filter me"},
            format="json",
        )

        by_tenant = self.platform_client.get(
            "/api/v1/platform/audit",
            {"tenant": str(self.tenant.uuid)},
        )
        by_search = self.platform_client.get(
            "/api/v1/platform/audit",
            {"search": "audit-platform"},
        )

        self.assertEqual(by_tenant.data["count"], 1)
        self.assertEqual(by_search.data["count"], 1)
