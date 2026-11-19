"""Prototype key identification and optional integration purpose."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import F
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import ApiKeyControlNotice, AuditLog, Profile, Tenant, TenantApiKey, TenantMembership


class ApiKeyMetadataTests(TestCase):
    """Keep identification truthful across new, rotated and legacy credentials."""

    def setUp(self):
        """Create a human administrator in a distinct tenant."""
        self.tenant = Tenant.objects.create(name="Metadata", slug="key-metadata")
        self.admin = get_user_model().objects.create_user(username="key-metadata-admin")
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role="tenant_admin")
        self.platform = get_user_model().objects.create_user(username="key-metadata-platform")
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def create(self, **values):
        """Use the public management endpoint without disclosing test secrets."""
        return self.client.post("/api/v1/tenant/api-keys", {"name": "CI integration", **values}, format="json")

    def test_creation_and_rotation_show_the_real_suffix_without_revealing_the_secret(self):
        """Display follows the currently issued secret and keeps the purpose on rotation."""
        with patch("accounts.models.secrets.token_urlsafe", return_value="test-only-random-material-X7Qz"):
            created = self.create(description="  Generates change summaries\nfor CI.  ")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["key_suffix"], created.data["key"][-4:])
        self.assertEqual(created.data["key_display"], "slk_••••X7Qz")
        self.assertEqual(created.data["description"], "Generates change summaries\nfor CI.")
        key_uuid = created.data["uuid"]
        detail = self.client.get(f"/api/v1/tenant/api-keys/{key_uuid}")
        self.assertEqual(detail.data["key_display"], created.data["key_display"])
        self.assertNotIn(created.data["key"], str(detail.data))
        with patch("accounts.models.secrets.token_urlsafe", return_value="other-test-only-random-value-n8Rv"):
            rotated = self.client.post(f"/api/v1/tenant/api-keys/{key_uuid}")
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.assertEqual(rotated.data["key_display"], "slk_••••n8Rv")
        self.assertEqual(rotated.data["description"], created.data["description"])
        self.assertNotIn("Generates change summaries", str(list(AuditLog.objects.values("metadata"))))

    def test_tenant_and_platform_search_suffix_and_preserve_legacy_prefix_search(self):
        """Partial suffix search is tenant scoped and platform identification has the same value."""
        with patch("accounts.models.secrets.token_urlsafe", return_value="test-only-random-material-X7Qz"):
            created = self.create()
        self.assertEqual(created.status_code, 201, created.data)
        listing = self.client.get("/api/v1/tenant/api-keys", {"search": "7qz"})
        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(listing.data["results"][0]["uuid"], created.data["uuid"])
        other = Tenant.objects.create(name="Foreign", slug="key-metadata-foreign")
        TenantApiKey.objects.filter(pk=created.data["uuid"]).update(tenant=other)
        self.assertEqual(self.client.get("/api/v1/tenant/api-keys", {"search": "7qz"}).data["count"], 0)
        self.client.force_authenticate(self.platform)
        platform = self.client.get("/api/v1/platform/api-keys", {"search": "7qz"})
        self.assertEqual(platform.data["count"], 1)
        self.assertEqual(platform.data["results"][0]["key_display"], "slk_••••X7Qz")
        self.assertNotIn("description", platform.data["results"][0])

    def test_legacy_key_uses_known_prefix_until_rotation(self):
        """Hash-only legacy records cannot claim a recovered suffix."""
        key = TenantApiKey.objects.create(
            tenant=self.tenant,
            created_by=self.admin,
            name="Legacy",
            key_prefix="slk_legacy12",
            key_hash=TenantApiKey.digest("legacy-test-secret"),
        )
        detail = self.client.get(f"/api/v1/tenant/api-keys/{key.pk}")
        self.assertEqual(detail.data["key_suffix"], "")
        self.assertEqual(detail.data["key_display"], "slk_legacy12••••")
        self.assertEqual(detail.data["description"], "")
        self.assertEqual(self.client.get("/api/v1/tenant/api-keys", {"search": "legacy12"}).data["count"], 1)
        rotated = self.client.post(f"/api/v1/tenant/api-keys/{key.pk}")
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.assertEqual(rotated.data["key_suffix"], rotated.data["key"][-4:])

    def test_rotation_by_older_code_cannot_leave_a_false_suffix_or_search_hit(self):
        """A rolling-upgrade old writer changes version without knowing suffix metadata."""
        with patch("accounts.models.secrets.token_urlsafe", return_value="test-only-random-material-X7Qz"):
            created = self.create()
        TenantApiKey.objects.filter(pk=created.data["uuid"]).update(
            key_prefix="slk_newolder1",
            key_hash=TenantApiKey.digest("older-code-test-key"),
            key_version=F("key_version") + 1,
        )
        detail = self.client.get(f"/api/v1/tenant/api-keys/{created.data['uuid']}")
        self.assertEqual(detail.data["key_suffix"], "")
        self.assertEqual(detail.data["key_display"], "slk_newolder1••••")
        self.assertEqual(self.client.get("/api/v1/tenant/api-keys", {"search": "7Qz"}).data["count"], 0)
        self.client.force_authenticate(self.platform)
        self.assertEqual(self.client.get("/api/v1/platform/api-keys", {"search": "7Qz"}).data["count"], 0)

    def test_purpose_is_optional_bounded_text_and_invalid_input_creates_nothing(self):
        """Reject invalid user input before any write or one-time secret issuance."""
        for value in (None, True, ["wrong"], {"wrong": "shape"}, "x" * 1001):
            with self.subTest(value_type=type(value).__name__):
                response = self.create(description=value)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(TenantApiKey.objects.count(), 0)
        valid = self.create(description="x" * 1000)
        self.assertEqual(valid.status_code, 201, valid.data)
        self.assertEqual(len(valid.data["description"]), 1000)

    def test_purpose_updates_preserve_omitted_values_and_reject_revoked_keys(self):
        """Existing metadata mutation obeys normal tenant and terminal-state gates."""
        created = self.create(description="Original", expires_in_days=90)
        url = f"/api/v1/tenant/api-keys/{created.data['uuid']}"
        self.assertEqual(self.client.patch(url, {"name": "New"}, format="json").data["description"], "Original")
        self.assertEqual(self.client.patch(url, {"description": None}, format="json").status_code, 400)
        self.assertEqual(self.client.get(url).data["description"], "Original")
        self.assertEqual(self.client.patch(url, {"description": ""}, format="json").data["description"], "")
        self.client.delete(url)
        self.assertEqual(self.client.patch(url, {"description": "After revoke"}, format="json").status_code, 409)

    def test_pending_legacy_control_notices_remain_renderable(self):
        """A pre-upgrade pending message has only prefix metadata and remains truthful."""
        from accounts.services.api_key_notices import _mail_content

        notice = ApiKeyControlNotice.objects.create(
            tenant=self.tenant,
            actor=self.platform,
            action="disable",
            keys=[{"uuid": "test-reference", "name": "Legacy", "key_prefix": "slk_legacy12"}],
        )
        _subject, body = _mail_content(notice)
        self.assertIn("slk_legacy12••••", body)
        self.assertNotIn("None", body)

    def test_settings_use_the_reviewed_version_and_never_rotate_the_secret(self):
        """Reject stale edits before applying scope or purpose, including races with platform control."""
        from lens.models import Assistant

        created = self.create(description="Original", expires_in_days=90)
        key = TenantApiKey.objects.get(pk=created.data["uuid"])
        original_hash = key.key_hash
        original_version = key.key_version
        original_expiry = key.expires_at
        target = Assistant.objects.create(name="Target", slug="key-metadata-target", tenant=self.tenant)
        url = f"/api/v1/tenant/api-keys/{key.pk}"
        payload = {
            "name": "Narrowed CI",
            "description": "Updated",
            "scope": "selected",
            "assistant_uuids": [str(target.uuid)],
            "updated_at": created.data["updated_at"],
        }
        changed = self.client.patch(url, payload, format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        key.refresh_from_db()
        self.assertEqual(key.assistant_uuids, [str(target.uuid)])
        self.assertEqual(key.key_hash, original_hash)
        self.assertEqual(key.key_version, original_version)
        self.assertEqual(key.expires_at, original_expiry)
        conflict = self.client.patch(url, {**payload, "description": "Stale"}, format="json")
        self.assertEqual(conflict.status_code, 409, conflict.data)
        key.refresh_from_db()
        self.assertEqual(key.description, "Updated")
        invalid = self.client.patch(url, {"updated_at": "invalid", "description": "Bad"}, format="json")
        self.assertEqual(invalid.status_code, 400, invalid.data)

    def test_scope_settings_immediately_restrict_existing_machine_credentials(self):
        """Narrowing applies on the next real Key request and keeps metadata tenant scoped."""
        from lens.models import Assistant, Session

        first = Assistant.objects.create(name="First", slug="key-settings-first", tenant=self.tenant)
        second = Assistant.objects.create(name="Second", slug="key-settings-second", tenant=self.tenant)
        created = self.create()
        target = Session.objects.create(tenant=self.tenant, api_key_id=created.data["uuid"], assistant=second)
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {created.data['key']}")
        target_url = f"/api/lens/sessions/{target.uuid}/runs/"
        self.assertEqual(external.post(target_url, {}, format="json").status_code, 400)
        url = f"/api/v1/tenant/api-keys/{created.data['uuid']}"
        response = self.client.patch(
            url,
            {"scope": "selected", "assistant_uuids": [str(first.uuid)], "updated_at": created.data["updated_at"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(external.post(target_url, {}, format="json").status_code, 404)
        other = Tenant.objects.create(name="Other settings", slug="key-settings-other")
        TenantApiKey.objects.filter(pk=created.data["uuid"]).update(tenant=other)
        denied = self.client.patch(url, {"description": "Do not write"}, format="json")
        self.assertEqual(denied.status_code, 404)

    def test_metadata_name_limits_return_validation_errors(self):
        """A crafted settings request must not surface a database length error."""
        response = self.create(name="x" * 151)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TenantApiKey.objects.count(), 0)
        created = self.create(name="x" * 150)
        self.assertEqual(created.status_code, 201, created.data)
        url = f"/api/v1/tenant/api-keys/{created.data['uuid']}"
        response = self.client.patch(url, {"name": "x" * 151}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(url).data["name"], "x" * 150)
