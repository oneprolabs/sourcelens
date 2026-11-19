"""Tests for tenant-scoped API key management."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Tenant, TenantApiKey, TenantMembership
from lens.models import Assistant


class TenantApiKeyApiTests(TestCase):
    """Tenant administrators manage keys for their own tenant only."""

    def setUp(self):
        user_model = get_user_model()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Other", slug="other")
        self.admin = user_model.objects.create_user(
            username="key-admin",
            email="key-admin@example.com",
            password="x",
        )
        self.member = user_model.objects.create_user(
            username="key-member",
            email="key-member@example.com",
            password="x",
        )
        self.foreign = user_model.objects.create_user(
            username="key-foreign",
            email="key-foreign@example.com",
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
        TenantMembership.objects.create(
            tenant=self.other_tenant,
            user=self.foreign,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.assistant = Assistant.objects.create(
            name="Acme assistant",
            slug="acme-key-assistant",
            tenant=self.tenant,
        )
        self.foreign_assistant = Assistant.objects.create(
            name="Foreign assistant",
            slug="foreign-key-assistant",
            tenant=self.other_tenant,
        )
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.member_client = APIClient()
        self.member_client.force_authenticate(self.member)
        self.foreign_client = APIClient()
        self.foreign_client.force_authenticate(self.foreign)

    def test_callable_count_excludes_paused_assistants_without_clearing_selected_scope(self):
        """Paused assistants remain selected so re-enabling restores the same grant."""
        for scope in ("all", "selected"):
            response = self.admin_client.post(
                "/api/v1/tenant/api-keys",
                {
                    "name": scope,
                    "scope": scope,
                    "assistant_uuids": [str(self.assistant.uuid)] if scope == "selected" else [],
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data["assistant_count"], 1)
        Assistant.objects.filter(pk=self.assistant.pk).update(is_enabled=False)
        response = self.admin_client.get("/api/v1/tenant/api-keys")
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        self.assertEqual([row["assistant_count"] for row in rows], [0, 0])
        selected = next(row for row in rows if row["scope"] == "selected")
        self.assertEqual(selected["assistant_uuids"], [str(self.assistant.uuid)])

    def test_create_key_returns_secret_once(self):
        response = self.admin_client.post(
            "/api/v1/tenant/api-keys",
            {
                "name": "CI pipeline",
                "scope": "all",
                "expires_in_days": 30,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["key"].startswith("slk_"))
        record = TenantApiKey.objects.get(uuid=response.data["uuid"])
        self.assertEqual(record.tenant, self.tenant)
        self.assertEqual(record.assistant_uuids, [])
        self.assertNotEqual(record.key_hash, response.data["key"])
        self.assertEqual(
            TenantApiKey.digest(response.data["key"]),
            record.key_hash,
        )

    def test_create_selected_scope_validates_tenant_assistants(self):
        response = self.admin_client.post(
            "/api/v1/tenant/api-keys",
            {
                "name": "Selected",
                "scope": "selected",
                "assistant_uuids": [str(self.assistant.uuid)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        record = TenantApiKey.objects.get(uuid=response.data["uuid"])
        self.assertEqual(
            record.assistant_uuids,
            [str(self.assistant.uuid)],
        )

        invalid = self.admin_client.post(
            "/api/v1/tenant/api-keys",
            {
                "name": "Invalid",
                "scope": "selected",
                "assistant_uuids": [str(self.foreign_assistant.uuid)],
            },
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)

    def test_key_list_is_tenant_scoped(self):
        TenantApiKey.issue(
            tenant=self.tenant,
            name="Acme key",
            scope="all",
            assistant_uuids=[],
            created_by=self.admin,
            expires_at=None,
        )
        TenantApiKey.issue(
            tenant=self.other_tenant,
            name="Foreign key",
            scope="all",
            assistant_uuids=[],
            created_by=self.foreign,
            expires_at=None,
        )

        response = self.admin_client.get("/api/v1/tenant/api-keys")

        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"Acme key"})
        self.assertNotIn("Foreign key", names)

    def test_key_disable_enable_and_expiry(self):
        record, _ = TenantApiKey.issue(
            tenant=self.tenant,
            name="Mutable",
            scope="all",
            assistant_uuids=[],
            created_by=self.admin,
            expires_at=None,
        )
        disabled = self.admin_client.patch(
            f"/api/v1/tenant/api-keys/{record.uuid}",
            {"status": "disabled"},
            format="json",
        )
        self.assertEqual(disabled.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.status, "disabled")

        expired = self.admin_client.patch(
            f"/api/v1/tenant/api-keys/{record.uuid}",
            {"status": "active", "expires_in_days": 1},
            format="json",
        )
        self.assertEqual(expired.status_code, 200)
        record.refresh_from_db()
        self.assertGreater(record.expires_at, timezone.now())

        record.expires_at = timezone.now() - timedelta(seconds=1)
        record.save(update_fields=["expires_at"])
        payload = self.admin_client.get("/api/v1/tenant/api-keys").data
        self.assertEqual(payload["results"][0]["status"], "expired")

    def test_key_rotate_changes_secret_and_version(self):
        record, old_secret = TenantApiKey.issue(
            tenant=self.tenant,
            name="Rotate",
            scope="all",
            assistant_uuids=[],
            created_by=self.admin,
            expires_at=None,
        )
        response = self.admin_client.post(
            f"/api/v1/tenant/api-keys/{record.uuid}"
        )

        self.assertEqual(response.status_code, 200, response.data)
        record.refresh_from_db()
        self.assertEqual(record.key_version, 2)
        self.assertNotEqual(record.key_hash, TenantApiKey.digest(old_secret))
        self.assertEqual(
            record.key_hash,
            TenantApiKey.digest(response.data["key"]),
        )

    def test_key_delete_revokes(self):
        record, _ = TenantApiKey.issue(
            tenant=self.tenant,
            name="Revoke",
            scope="all",
            assistant_uuids=[],
            created_by=self.admin,
            expires_at=None,
        )
        response = self.admin_client.delete(
            f"/api/v1/tenant/api-keys/{record.uuid}"
        )

        self.assertEqual(response.status_code, 204)
        record.refresh_from_db()
        self.assertEqual(record.status, "revoked")
        self.assertIsNotNone(record.revoked_at)

    def test_member_cannot_manage_keys(self):
        response = self.member_client.get("/api/v1/tenant/api-keys")
        create = self.member_client.post(
            "/api/v1/tenant/api-keys",
            {"name": "Nope"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(create.status_code, 403)

    def test_cannot_access_other_tenant_key(self):
        record, _ = TenantApiKey.issue(
            tenant=self.other_tenant,
            name="Foreign",
            scope="all",
            assistant_uuids=[],
            created_by=self.foreign,
            expires_at=None,
        )
        response = self.admin_client.patch(
            f"/api/v1/tenant/api-keys/{record.uuid}",
            {"status": "disabled"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
