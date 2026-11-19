"""Catalog publication, private ownership and runtime enablement boundaries."""

import io
import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantMembership
from lens.models import Assistant, AssistantMCP, AssistantSkill, MCPServer, Skill, TenantSkill
from lens.services import build_loaded_mcps, build_loaded_plugins, build_loaded_skills


class CatalogTenancyTests(TestCase):
    """Tenant admins consume official templates without editing other tenants."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Catalog A", slug="catalog-a")
        self.other = Tenant.objects.create(name="Catalog B", slug="catalog-b")
        self.admin = get_user_model().objects.create_user(username="catalog-admin")
        TenantMembership.objects.create(user=self.admin, tenant=self.tenant, role="tenant_admin")
        self.platform = get_user_model().objects.create_user(username="catalog-platform")
        Profile.objects.update_or_create(user=self.platform, defaults={"platform_role": "super_admin"})
        self.skill = Skill.objects.create(name="Official")
        self.mcp = MCPServer.objects.create(name="Official MCP", transport="url", endpoint="https://example.com/mcp")
        self.private = Skill.objects.create(name="Foreign", tenant=self.other)
        self.assistant = Assistant.objects.create(name="Catalog", slug="catalog-test", tenant=self.tenant)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_official_discovery_and_private_resources_are_separate(self):
        response = self.client.get("/api/lens/admin/skills/", {"catalog": "official"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["name"] for row in response.data["results"]], ["Official"])
        self.assertFalse(response.data["results"][0]["tenant_enabled"])
        self.assertEqual(self.client.get(f"/api/lens/admin/skills/{self.private.uuid}/").status_code, 404)

    def test_tenant_cannot_edit_or_delete_official_resource(self):
        for path in [f"skills/{self.skill.uuid}", f"mcp-servers/{self.mcp.uuid}"]:
            with self.subTest(path=path):
                url = f"/api/lens/admin/{path}/"
                self.assertEqual(self.client.patch(url, {"enabled": False}, format="json").status_code, 403)
                self.assertEqual(self.client.delete(url).status_code, 403)

    def test_private_creation_ignores_claimed_owner(self):
        response = self.client.post(
            "/api/lens/admin/skills/", {"name": "Mine", "tenant": str(self.other.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Skill.objects.get(uuid=response.data["uuid"]).tenant, self.tenant)

    def test_platform_can_publish_without_legacy_staff(self):
        self.client.force_authenticate(self.platform)
        response = self.client.post("/api/lens/admin/skills/", {"name": "New official"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(Skill.objects.get(uuid=response.data["uuid"]).tenant_id)
        self.assertEqual(self.client.get(f"/api/lens/admin/skills/{self.private.uuid}/").status_code, 404)
        self.assertTrue(AuditLog.objects.filter(action="catalog.skill.create").exists())

    def test_tenant_enablement_controls_runtime_without_changing_template(self):
        AssistantSkill.objects.create(assistant=self.assistant, skill=self.skill)
        AssistantMCP.objects.create(assistant=self.assistant, mcp=self.mcp)
        self.assertEqual(build_loaded_skills(self.assistant), [])
        self.assertEqual(build_loaded_mcps(self.assistant), [])
        for path in [f"skills/{self.skill.uuid}", f"mcp-servers/{self.mcp.uuid}"]:
            response = self.client.post(f"/api/lens/admin/{path}/enablement/", {"enabled": True}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(build_loaded_skills(self.assistant)), 1)
        self.assertEqual(len(build_loaded_mcps(self.assistant)), 1)
        self.skill.refresh_from_db()
        self.assertTrue(self.skill.enabled)
        TenantSkill.objects.filter(tenant=self.tenant).update(enabled=False)
        self.mcp.enabled = False
        self.mcp.save(update_fields=["enabled"])
        self.assertEqual(build_loaded_skills(self.assistant), [])
        self.assertEqual(build_loaded_mcps(self.assistant), [])

    def test_foreign_bindings_never_reach_dispatch(self):
        AssistantSkill.objects.create(assistant=self.assistant, skill=self.private)
        foreign_mcp = MCPServer.objects.create(name="Foreign", tenant=self.other, transport="url")
        AssistantMCP.objects.create(assistant=self.assistant, mcp=foreign_mcp)
        self.assertEqual(build_loaded_skills(self.assistant), [])
        self.assertEqual(build_loaded_mcps(self.assistant), [])

    def test_disabled_or_foreign_resources_cannot_be_bound(self):
        url = f"/api/lens/assistants/{self.assistant.uuid}/"
        for payload in [
            {"skill_bindings": [{"skill_uuid": str(self.private.uuid)}]},
            {"skill_bindings": [{"skill_uuid": str(self.skill.uuid)}]},
            {"mcp_bindings": [{"mcp_uuid": str(self.mcp.uuid)}]},
        ]:
            with self.subTest(payload=payload):
                response = self.client.patch(url, payload, format="json")
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(next(iter(payload)), response.data)

    def test_enablement_rejects_bad_inputs_and_private_resources(self):
        url = f"/api/lens/admin/skills/{self.skill.uuid}/enablement/"
        self.assertEqual(self.client.post(url, {"enabled": "false"}, format="json").status_code, 400)
        self.assertEqual(
            self.client.post(f"/api/lens/admin/skills/{self.private.uuid}/enablement/", {"enabled": True}).status_code,
            404,
        )

    def test_official_mcp_requires_environment_references_for_secrets(self):
        self.client.force_authenticate(self.platform)
        url = "/api/lens/admin/mcp-servers/"
        payload = {
            "name": "Template",
            "transport": "url",
            "config": {"headers": {"Authorization": "Bearer shared-secret"}},
        }
        denied = self.client.post(url, payload, format="json")
        self.assertEqual(denied.status_code, 400, denied.data)
        payload["config"]["headers"]["Authorization"] = "Bearer ${API_TOKEN}"
        payload["environment"] = [{"name": "API_TOKEN", "secret": True, "required": True}]
        allowed = self.client.post(url, payload, format="json")
        self.assertEqual(allowed.status_code, 201, allowed.data)

    def test_private_mcp_rejects_foreign_connection(self):
        from lens.models import Connection

        connection = Connection.objects.create(name="Foreign", tenant=self.other, plugin_key="github")
        response = self.client.post(
            "/api/lens/admin/mcp-servers/",
            {"name": "Bad adapter", "transport": "plugin", "connection_uuid": str(connection.uuid), "tools": []},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("connection_uuid", response.data)

    def test_platform_delete_impact_is_aggregate_only(self):
        AssistantSkill.objects.create(assistant=self.assistant, skill=self.skill)
        self.client.force_authenticate(self.platform)
        response = self.client.get(f"/api/lens/admin/skills/{self.skill.uuid}/delete-impact/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["bound_count"], 1)
        self.assertEqual(response.data["bound_assistants"], [])

    def test_connector_registry_is_readable_by_nonstaff_tenant_admin(self):
        response = self.client.get("/api/lens/admin/plugins/")
        self.assertEqual(response.status_code, 200, response.data)

    def test_manual_skill_file_preview_does_not_read_server_files(self):
        response = self.client.get(f"/api/lens/admin/skills/{self.skill.uuid}/file-preview/", {"path": "manage.py"})
        self.assertEqual(response.status_code, 404, response.data)

    def test_scope_header_does_not_promote_tenant_admin(self):
        response = self.client.post(
            "/api/lens/admin/skills/",
            {"name": "Still private"},
            format="json",
            HTTP_X_CATALOG_SCOPE="official",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Skill.objects.get(uuid=response.data["uuid"]).tenant, self.tenant)
        response = self.client.patch(
            f"/api/lens/admin/skills/{self.skill.uuid}/",
            {"name": "Hijacked"},
            format="json",
            HTTP_X_CATALOG_SCOPE="official",
        )
        self.assertEqual(response.status_code, 403, response.data)

    def test_delegation_keeps_official_writes_explicit(self):
        self.client.force_authenticate(self.platform)
        start = self.client.post(
            f"/api/v1/platform/tenants/{self.other.uuid}/delegate",
            {"reason": "Catalog support", "expires_in_minutes": 60},
            format="json",
        )
        self.assertEqual(start.status_code, 201, start.data)
        self.client.credentials(HTTP_X_DELEGATION_ID=start.data["delegation_id"])
        self.assertEqual(self.client.get(f"/api/lens/admin/skills/{self.private.uuid}/").status_code, 200)
        self.assertEqual(
            self.client.patch(f"/api/lens/admin/skills/{self.skill.uuid}/", {"enabled": False}).status_code, 403
        )
        response = self.client.patch(
            f"/api/lens/admin/skills/{self.skill.uuid}/",
            {"enabled": False},
            format="json",
            HTTP_X_CATALOG_SCOPE="official",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            self.client.get(
                f"/api/lens/admin/skills/{self.private.uuid}/", HTTP_X_CATALOG_SCOPE="official"
            ).status_code,
            404,
        )

    def test_unassigned_staff_cannot_import_catalog_resources(self):
        user = get_user_model().objects.create_user(username="unassigned-catalog", is_staff=True)
        self.client.force_authenticate(user)
        for action in ["upload", "import-github", "check-updates"]:
            response = self.client.post(f"/api/lens/admin/skills/{action}/", {}, format="json")
            self.assertEqual(response.status_code, 403, response.data)

    def test_identical_package_names_are_private_to_each_owner(self):
        from lens.skill_packages import import_skill_zip

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("SKILL.md", "---\nname: same-name\ndescription: Tenant package\n---\n# Instructions\n")
        with tempfile.TemporaryDirectory() as storage, self.settings(STORAGE_ROOT=storage):
            first = import_skill_zip(file_obj=io.BytesIO(archive.getvalue()), tenant=self.tenant)
            second = import_skill_zip(file_obj=io.BytesIO(archive.getvalue()), tenant=self.other)
            repeated = import_skill_zip(file_obj=io.BytesIO(archive.getvalue()), tenant=self.tenant)
            self.assertEqual(first.pk, repeated.pk)
            self.assertNotEqual(first.pk, second.pk)
            self.assertNotEqual(first.package_path, second.package_path)
            self.assertTrue(Path(first.package_path).is_dir())
            self.assertTrue(Path(second.package_path).is_dir())

    def test_official_secret_cannot_hide_literal_value_beside_placeholder(self):
        self.client.force_authenticate(self.platform)
        for value in ["shared-secret-${API_TOKEN}", "Bearer ${API_TOKEN} shared-secret", ["${API_TOKEN}", "shared"]]:
            response = self.client.post(
                "/api/lens/admin/mcp-servers/",
                {
                    "name": "Unsafe template",
                    "transport": "url",
                    "config": {"headers": {"Authorization": value}},
                    "environment": [{"name": "API_TOKEN", "secret": True}],
                },
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn("config", response.data)

    def test_official_endpoint_checks_every_duplicate_credential_parameter(self):
        self.client.force_authenticate(self.platform)
        response = self.client.post(
            "/api/lens/admin/mcp-servers/",
            {
                "name": "Unsafe endpoint",
                "transport": "url",
                "endpoint": "https://example.com/mcp?token=shared&token=${API_TOKEN}",
                "environment": [{"name": "API_TOKEN", "secret": True}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("config", response.data)

    def test_legacy_catalog_backfill_preserves_private_ownership(self):
        from importlib import import_module

        from django.apps import apps
        from lens.models import Connection

        default, _ = Tenant.objects.get_or_create(slug="default", defaults={"name": "Default"})
        connection = Connection.objects.create(name="Existing", tenant=self.other, plugin_key="github")
        adapter = MCPServer.objects.create(name="Existing adapter", transport="plugin", connection=connection)
        migration = import_module("lens.migrations.0070_multitenancy")
        migration.backfill_catalog_ownership(apps, None)
        for resource, expected in [
            (self.skill, default),
            (self.mcp, default),
            (adapter, self.other),
            (self.private, self.other),
        ]:
            resource.refresh_from_db()
            self.assertEqual(resource.tenant_id, expected.pk)
        self.assertFalse(Skill.objects.filter(tenant__isnull=True).exists())
        self.assertFalse(MCPServer.objects.filter(tenant__isnull=True).exists())

    def test_private_adapter_cannot_dispatch_a_foreign_connection(self):
        from lens.models import Connection

        connection = Connection.objects.create(name="Foreign", tenant=self.other, plugin_key="github")
        adapter = MCPServer.objects.create(
            name="Invalid legacy adapter", tenant=self.tenant, transport="plugin", connection=connection
        )
        AssistantMCP.objects.create(assistant=self.assistant, mcp=adapter)
        self.assertEqual(build_loaded_plugins(self.assistant), [])
