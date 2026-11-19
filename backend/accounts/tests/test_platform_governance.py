"""Platform operators govern nodes and settings independently of staff."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantMembership
from lens.models import DataSource, DataSourceCredential, GlobalSetting, LensNode


class PlatformGovernanceTests(TestCase):
    """Keep platform configuration out of tenant administration."""

    def setUp(self):
        self.platform = get_user_model().objects.create_user(username="governance-platform")
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        self.admin = get_user_model().objects.create_user(username="governance-tenant", is_staff=True)
        self.tenant = Tenant.objects.create(name="Governance", slug="governance")
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role="tenant_admin")
        self.node = LensNode.objects.create(name="Shared", workspace_path="/workspace", enrollment_status="approved")
        GlobalSetting.objects.create(key="private.config", value={"secret": "hidden"})
        GlobalSetting.objects.create(key="public_base_url", value="https://lens.example.com")
        self.client = APIClient()

    def test_platform_role_can_manage_nodes_without_staff(self):
        self.client.force_authenticate(self.platform)
        response = self.client.post("/api/lens/admin/lensnodes/", {"name": "New shared node"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("token", response.data)
        self.assertTrue(AuditLog.objects.filter(action="node.create", actor_user=self.platform).exists())
        self.assertEqual(self.client.get("/api/lens/admin/lensnodes/available/").status_code, 200)
        self.assertEqual(self.client.get("/api/lens/admin/global-settings/public-config/").status_code, 200)

    def test_tenant_staff_cannot_govern_platform(self):
        self.client.force_authenticate(self.admin)
        for url in ["/api/lens/admin/lensnodes/", "/api/lens/admin/global-settings/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {"name": "Denied"}).status_code, 403)

    def test_tenant_node_options_exclude_operational_details(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/lens/admin/lensnodes/available/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(str(response.data[0]["uuid"]), str(self.node.uuid))
        self.assertEqual(set(response.data[0]), {"uuid", "name", "status", "workspace_path", "pool"})

    def test_tenant_can_read_only_the_public_url(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/lens/admin/global-settings/public-config/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, [{"key": "public_base_url", "value": "https://lens.example.com"}])

    def test_platform_setting_change_is_audited_without_values(self):
        self.client.force_authenticate(self.platform)
        response = self.client.patch(
            "/api/lens/admin/global-settings/lensnode.defaults.idle_timeout/", {"value": 360}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(GlobalSetting.objects.get(key="lensnode.defaults.idle_timeout").value, 360)
        audit = AuditLog.objects.get(action="global_setting.update")
        self.assertEqual(audit.resource_id, "lensnode.defaults.idle_timeout")
        self.assertNotIn("360", str(audit.metadata))

    @patch("lens.views.lensnodes.test_datasource_connection")
    def test_tenant_can_test_its_own_connection(self, dispatch):
        dispatch.return_value = {"status": "success"}
        self.admin.is_staff = False
        self.admin.save(update_fields=["is_staff"])
        credential = DataSourceCredential.objects.create(name="Owned", tenant=self.tenant)
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/lens/admin/lensnodes/{self.node.uuid}/test-datasource-connection/",
            {"credential_uuid": str(credential.uuid), "config": {}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(dispatch.call_args.kwargs["credential_uuid"], str(credential.uuid))

    @patch("lens.views.lensnodes.test_datasource_connection")
    def test_tenant_cannot_test_foreign_resources(self, dispatch):
        foreign = Tenant.objects.create(name="Foreign", slug="foreign-governance")
        credential = DataSourceCredential.objects.create(name="Foreign", tenant=foreign)
        datasource = DataSource.objects.create(name="Foreign", tenant=foreign, lensnode=self.node)
        self.client.force_authenticate(self.admin)
        for payload in [
            {"credential_uuid": str(credential.uuid)},
            {"datasource_uuid": str(datasource.uuid)},
            {"credential_uuid": "invalid-uuid"},
        ]:
            with self.subTest(payload=payload):
                response = self.client.post(
                    f"/api/lens/admin/lensnodes/{self.node.uuid}/test-datasource-connection/",
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, 404, response.data)
        dispatch.assert_not_called()

    def test_tenant_cannot_probe_shared_workspace(self):
        self.client.force_authenticate(self.admin)
        for action in ["list-dirs", "check-datasource-path"]:
            response = self.client.post(f"/api/lens/admin/lensnodes/{self.node.uuid}/{action}/", {}, format="json")
            self.assertEqual(response.status_code, 403)

    @patch("lens.views.lensnodes.test_datasource_connection")
    def test_platform_needs_delegation_to_use_tenant_credentials(self, dispatch):
        dispatch.return_value = {"status": "success"}
        credential = DataSourceCredential.objects.create(name="Tenant", tenant=self.tenant)
        self.client.force_authenticate(self.platform)
        url = f"/api/lens/admin/lensnodes/{self.node.uuid}/test-datasource-connection/"
        payload = {"credential_uuid": str(credential.uuid)}
        denied = self.client.post(url, payload, format="json")
        self.assertEqual(denied.status_code, 404, denied.data)
        dispatch.assert_not_called()
        delegation = self.client.post(
            f"/api/v1/platform/tenants/{self.tenant.uuid}/delegate",
            {"reason": "Connection support", "expires_in_minutes": 60},
            format="json",
        )
        self.assertEqual(delegation.status_code, 201, delegation.data)
        allowed = self.client.post(url, payload, format="json", HTTP_X_DELEGATION_ID=delegation.data["delegation_id"])
        self.assertEqual(allowed.status_code, 200, allowed.data)
        dispatch.assert_called_once()

    def test_platform_setting_deletion_is_audited(self):
        self.client.force_authenticate(self.platform)
        response = self.client.delete("/api/lens/admin/global-settings/private.config/")
        self.assertEqual(response.status_code, 204)
        self.assertTrue(AuditLog.objects.filter(action="global_setting.delete", resource_id="private.config").exists())

    def test_platform_schedule_toggle_is_audited(self):
        self.client.force_authenticate(self.platform)
        response = self.client.patch(
            "/api/lens/admin/global-settings/system-health/",
            {"task_type": "lensnode_health", "enabled": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            AuditLog.objects.filter(action="global_setting.schedule_update", resource_id="lensnode_health").exists()
        )
