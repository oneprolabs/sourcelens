"""Tests for the current-tenant profile API."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Tenant, TenantMembership


class CurrentTenantApiTests(TestCase):
    """Tenant administrators can edit only their own tenant profile."""

    def setUp(self):
        user_model = get_user_model()
        self.tenant = Tenant.objects.create(
            name="Acme",
            slug="acme",
            description="Acme tenant",
            contact_email="admin@acme.example",
        )
        self.other_tenant = Tenant.objects.create(
            name="Other",
            slug="other",
        )
        self.admin = user_model.objects.create_user(
            username="tenant-profile-admin",
            email="tenant-profile-admin@example.com",
            password="x",
        )
        self.member = user_model.objects.create_user(
            username="tenant-profile-member",
            email="tenant-profile-member@example.com",
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
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.member_client = APIClient()
        self.member_client.force_authenticate(self.member)

    def test_tenant_admin_can_read_current_tenant(self):
        response = self.admin_client.get("/api/v1/tenant")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["slug"], "acme")
        self.assertEqual(response.data["name"], "Acme")
        self.assertEqual(response.data["description"], "Acme tenant")
        self.assertEqual(response.data["member_count"], 2)
        self.assertEqual(response.data["status"], "active")

    def test_tenant_admin_can_update_editable_fields(self):
        response = self.admin_client.patch(
            "/api/v1/tenant",
            {
                "name": "Acme Updated",
                "description": "Updated description",
                "default_language": "en-US",
                "timezone": "UTC",
                "contact_email": "ops@acme.example",
                "slug": "hijacked",
                "status": "suspended",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, "Acme Updated")
        self.assertEqual(self.tenant.default_language, "en-US")
        self.assertEqual(self.tenant.timezone, "UTC")
        self.assertEqual(self.tenant.contact_email, "ops@acme.example")
        self.assertEqual(self.tenant.slug, "acme")
        self.assertEqual(self.tenant.status, "active")

    def test_blank_tenant_name_is_rejected(self):
        response = self.admin_client.patch(
            "/api/v1/tenant",
            {"name": "   "},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)

    def test_member_cannot_read_or_update_tenant_profile(self):
        read = self.member_client.get("/api/v1/tenant")
        write = self.member_client.patch(
            "/api/v1/tenant",
            {"name": "Nope"},
            format="json",
        )

        self.assertEqual(read.status_code, 403)
        self.assertEqual(write.status_code, 403)

    def test_logo_upload_updates_tenant_logo(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile(
            "logo.png",
            b"\x89PNG\r\n\x1a\nfake",
            content_type="image/png",
        )
        with patch(
            "accounts.views.tenant.default_storage.save",
            return_value="tenant-logos/acme/logo.png",
        ), patch(
            "accounts.views.tenant.default_storage.url",
            return_value="/media/tenant-logos/acme/logo.png",
        ):
            response = self.admin_client.post(
                "/api/v1/tenant/logo",
                {"logo": upload},
                format="multipart",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.tenant.refresh_from_db()
        self.assertEqual(
            self.tenant.logo_url,
            "/media/tenant-logos/acme/logo.png",
        )

    def test_logo_rejects_unsupported_type(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile(
            "logo.txt",
            b"not an image",
            content_type="text/plain",
        )
        response = self.admin_client.post(
            "/api/v1/tenant/logo",
            {"logo": upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("logo", response.data)
