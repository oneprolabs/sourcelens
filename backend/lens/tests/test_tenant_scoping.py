"""Tenant scoping tests for assistants and datasources."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantDelegation, TenantMembership
from lens.models import (
    Assistant,
    Connection,
    DataSource,
    DataSourceCredential,
    EnvironmentVariableSet,
    LensNode,
    Run,
    Session,
    Skill,
)
from lens.services import create_execution_run


class AssistantDataSourceTenantScopeTests(TestCase):
    """A tenant must not read or mutate another tenant's core resources."""

    def setUp(self):
        user_model = get_user_model()
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.lensnode = LensNode.objects.create(
            name="Tenant scope node",
            workspace_path="/workspace",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )

        self.user_a = user_model.objects.create_user(
            username="scope-a",
            email="scope-a@example.com",
            password="x",
            is_staff=True,
        )
        self.user_b = user_model.objects.create_user(
            username="scope-b",
            email="scope-b@example.com",
            password="x",
            is_staff=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.user_a,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.user_b,
            role=TenantMembership.Role.TENANT_ADMIN,
        )

        self.assistant_a = Assistant.objects.create(
            name="Assistant A",
            slug="assistant-a",
            tenant=self.tenant_a,
            lensnode=self.lensnode,
            visibility=Assistant.Visibility.PRIVATE,
        )
        self.assistant_b = Assistant.objects.create(
            name="Assistant B",
            slug="assistant-b",
            tenant=self.tenant_b,
            lensnode=self.lensnode,
            visibility=Assistant.Visibility.PRIVATE,
        )
        self.public_b = Assistant.objects.create(
            name="Public B",
            slug="public-b",
            tenant=self.tenant_b,
            visibility=Assistant.Visibility.PUBLIC,
        )
        self.datasource_a = DataSource.objects.create(
            name="Datasource A",
            source_type=DataSource.SourceType.GIT,
            tenant=self.tenant_a,
        )
        self.datasource_b = DataSource.objects.create(
            name="Datasource B",
            source_type=DataSource.SourceType.GIT,
            tenant=self.tenant_b,
        )
        self.skill = Skill.objects.create(
            name="Tenant scope skill",
            enabled=True,
            tenant=self.tenant_a,
        )
        self.credential_a = DataSourceCredential.objects.create(
            name="Credential A",
            provider=DataSourceCredential.Provider.GITHUB,
            auth_type=DataSourceCredential.AuthType.HTTPS_TOKEN,
            tenant=self.tenant_a,
        )
        self.credential_b = DataSourceCredential.objects.create(
            name="Credential B",
            provider=DataSourceCredential.Provider.GITHUB,
            auth_type=DataSourceCredential.AuthType.HTTPS_TOKEN,
            tenant=self.tenant_b,
        )
        self.connection_a = Connection.objects.create(
            name="Connection A",
            plugin_key="github",
            endpoint="https://github.com",
            tenant=self.tenant_a,
        )
        self.connection_b = Connection.objects.create(
            name="Connection B",
            plugin_key="github",
            endpoint="https://github.com",
            tenant=self.tenant_b,
        )
        self.env_a = EnvironmentVariableSet.objects.create(
            name="Env A",
            tenant=self.tenant_a,
        )
        self.env_a.set_values({"TOKEN": "a-secret"})
        self.env_a.save(update_fields=["encrypted_values"])
        self.env_b = EnvironmentVariableSet.objects.create(
            name="Env B",
            tenant=self.tenant_b,
        )
        self.env_b.set_values({"TOKEN": "b-secret"})
        self.env_b.save(update_fields=["encrypted_values"])

        self.user_tenant_admin = user_model.objects.create_user(
            username="scope-tenant-admin",
            email="scope-tenant-admin@example.com",
            password="x",
        )
        self.user_tenant_member = user_model.objects.create_user(
            username="scope-tenant-member",
            email="scope-tenant-member@example.com",
            password="x",
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.user_tenant_admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.user_tenant_member,
            role=TenantMembership.Role.MEMBER,
        )

        self.client_a = APIClient()
        self.client_a.force_authenticate(self.user_a)
        self.client_admin = APIClient()
        self.client_admin.force_authenticate(self.user_tenant_admin)
        self.client_member = APIClient()
        self.client_member.force_authenticate(self.user_tenant_member)

    def _assistant_slugs(self, response):
        return {row["slug"] for row in response.data["results"]}

    def test_core_admin_collections_and_details_use_the_same_tenant_scope(self):
        """Legacy staff see only unowned rows; platform admins need delegation."""
        legacy = get_user_model().objects.create_user(username="scope-legacy-staff", is_staff=True)
        platform = get_user_model().objects.create_user(username="scope-platform-admin")
        Profile.objects.filter(user=platform).update(platform_role="super_admin")
        collections = (
            ("datasources", self.datasource_a, self.datasource_b),
            ("credentials", self.credential_a, self.credential_b),
            ("connections", self.connection_a, self.connection_b),
            ("environment-variable-sets", self.env_a, self.env_b),
        )
        for resource, own, foreign in collections:
            path = f"/api/lens/admin/{resource}/"
            detail = f"{path}{foreign.uuid}/"
            with self.subTest(resource=resource):
                self.assertEqual(self.client_admin.get(path).data["count"], 1)
                self.assertEqual(self.client_admin.get(f"{path}{own.uuid}/").status_code, 200)
                self.assertEqual(self.client_admin.get(detail).status_code, 404)
                client = APIClient()
                client.force_authenticate(legacy)
                self.assertEqual(client.get(path).data["count"], 0)
                self.assertEqual(client.get(detail).status_code, 404)
                client.force_authenticate(platform)
                self.assertEqual(client.get(path).status_code, 403)
                self.assertEqual(client.get(detail).status_code, 403)

    def test_core_admin_endpoints_reject_staff_members_and_inactive_tenants(self):
        """A Django staff flag does not replace an active tenant role."""
        self.user_tenant_member.is_staff = True
        self.user_tenant_member.save(update_fields=["is_staff"])
        self.tenant_a.status = Tenant.Status.SUSPENDED
        self.tenant_a.save(update_fields=["status"])
        for client in (self.client_member, self.client_admin):
            for resource in ("datasources", "credentials", "connections", "environment-variable-sets"):
                with self.subTest(resource=resource, client=client):
                    self.assertEqual(client.get(f"/api/lens/admin/{resource}/").status_code, 403)
            self.assertEqual(client.get("/api/v1/tenant").status_code, 403)

    def test_datasource_sync_statuses_only_include_current_tenant(self):
        """Batch polling applies the same tenant boundary as the main list."""
        response = self.client_admin.get(
            "/api/lens/admin/datasources/sync-statuses/",
            {"uuids": f"{self.datasource_a.uuid},{self.datasource_b.uuid}"},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["uuid"] for row in response.data], [str(self.datasource_a.uuid)])

    def test_readonly_delegation_can_read_metadata_but_cannot_reveal_or_edit(self):
        """A bound delegation grants only the selected tenant and read-only mode."""
        platform = get_user_model().objects.create_user(username="scope-readonly-platform")
        Profile.objects.filter(user=platform).update(platform_role="super_admin")
        delegation = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.tenant_a,
            mode="readonly",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        client = APIClient()
        client.force_authenticate(platform)
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        for resource, item in (("credentials", self.credential_a), ("environment-variable-sets", self.env_a)):
            with self.subTest(resource=resource):
                path = f"/api/lens/admin/{resource}/{item.uuid}/"
                self.assertEqual(client.get(path, **headers).status_code, 200)
                self.assertEqual(client.post(f"{path}reveal/", **headers).status_code, 403)
        self.assertEqual(client.get("/api/v1/tenant", **headers).status_code, 200)
        self.assertEqual(client.patch("/api/v1/tenant", {"name": "Edited"}, format="json", **headers).status_code, 403)
        self.assertEqual(client.get("/api/v1/tenant", HTTP_X_DELEGATION_ID="invalid").status_code, 403)

    def test_assistant_list_is_tenant_scoped_but_keeps_public(self):
        response = self.client_a.get(
            "/api/lens/assistants/",
            {"page_size": 100},
        )

        self.assertEqual(response.status_code, 200)
        slugs = self._assistant_slugs(response)
        self.assertIn("assistant-a", slugs)
        self.assertIn("public-b", slugs)
        self.assertNotIn("assistant-b", slugs)

    def test_assistant_detail_cannot_cross_tenant(self):
        response = self.client_a.get(
            f"/api/lens/assistants/{self.assistant_b.uuid}/"
        )

        self.assertEqual(response.status_code, 404)

    def test_managed_assistant_list_excludes_other_tenant_public_assistants(self):
        response = self.client_admin.get("/api/lens/assistants/", {"managed": "true", "page_size": 100})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._assistant_slugs(response), {"assistant-a"})

    def test_public_visibility_does_not_grant_cross_tenant_write_access(self):
        for client in (self.client_a, self.client_admin):
            with self.subTest(staff=client is self.client_a):
                self.assertEqual(
                    client.patch(
                        f"/api/lens/assistants/{self.public_b.uuid}/", {"name": "Hijacked"}, format="json"
                    ).status_code,
                    404,
                )
                self.assertEqual(client.post(f"/api/lens/assistants/{self.public_b.uuid}/archive/").status_code, 404)
                self.assertEqual(
                    client.post(
                        f"/api/lens/assistants/{self.public_b.uuid}/datasources/",
                        {"datasource_uuid": str(self.datasource_a.uuid), "mount_name": "hijacked"},
                        format="json",
                    ).status_code,
                    404,
                )
        self.public_b.refresh_from_db()
        self.assertEqual(self.public_b.name, "Public B")
        self.assertEqual(self.public_b.status, Assistant.Status.ACTIVE)

    def test_public_assistant_model_replacement_requires_tenant_ownership(self):
        from agentcore_metering.adapters.django.models import LLMConfig

        from lens.models import LLMConfigScope

        model = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL, provider="openai", config={"model": "foreign-model"}
        )
        LLMConfigScope.objects.create(config=model, tenant=self.tenant_b)
        response = self.client_admin.post(
            "/api/lens/assistants/replace-model/",
            {
                "model_field": "agent_model_ref",
                "target_model_ref": str(model.uuid),
                "assistant_models": [{"uuid": str(self.public_b.uuid), "model_ref": None}],
                "preview": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.public_b.refresh_from_db()
        self.assertIsNone(self.public_b.agent_model_ref)

    def test_assistant_update_and_delete_cannot_cross_tenant(self):
        patch = self.client_a.patch(
            f"/api/lens/assistants/{self.assistant_b.uuid}/",
            {"name": "Hijacked"},
            format="json",
        )
        archive = self.client_a.post(
            f"/api/lens/assistants/{self.assistant_b.uuid}/archive/"
        )

        self.assertEqual(patch.status_code, 404)
        self.assertEqual(archive.status_code, 404)
        self.assistant_b.refresh_from_db()
        self.assertEqual(self.assistant_b.name, "Assistant B")

    def test_assistant_create_assigns_current_tenant(self):
        response = self.client_a.post(
            "/api/lens/assistants/",
            {
                "name": "Created A",
                "slug": "created-a",
                "visibility": "private",
                "skill_bindings": [
                    {"skill_uuid": str(self.skill.uuid)},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        created = Assistant.objects.get(uuid=response.data["uuid"])
        self.assertEqual(created.tenant, self.tenant_a)
        self.assertEqual(created.owner_user, self.user_a)

    def test_assistant_cannot_bind_another_tenant_datasource(self):
        response = self.client_a.post(
            f"/api/lens/assistants/{self.assistant_a.uuid}/datasources/",
            {
                "datasource_uuid": str(self.datasource_b.uuid),
                "mount_name": "other",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_datasource_list_is_tenant_scoped(self):
        response = self.client_a.get(
            "/api/lens/admin/datasources/",
            {"page_size": 100},
        )

        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.data["results"]}
        self.assertIn("Datasource A", names)
        self.assertNotIn("Datasource B", names)

    def test_datasource_detail_cannot_cross_tenant(self):
        response = self.client_a.get(
            f"/api/lens/admin/datasources/{self.datasource_b.uuid}/"
        )

        self.assertEqual(response.status_code, 404)

    def test_datasource_create_assigns_current_tenant(self):
        response = self.client_a.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Created datasource",
                "source_type": "git",
                "config": {"repo_url": "https://example.com/repo.git"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        created = DataSource.objects.get(uuid=response.data["uuid"])
        self.assertEqual(created.tenant, self.tenant_a)

    def test_tenant_admin_can_create_core_resources(self):
        assistant = self.client_admin.post(
            "/api/lens/assistants/",
            {
                "name": "Admin created",
                "slug": "admin-created",
                "visibility": "private",
                "owner_user": self.user_b.pk,
                "skill_bindings": [
                    {"skill_uuid": str(self.skill.uuid)},
                ],
            },
            format="json",
        )
        datasource = self.client_admin.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Admin datasource",
                "source_type": "git",
                "config": {"repo_url": "https://example.com/admin.git"},
            },
            format="json",
        )

        self.assertEqual(assistant.status_code, 201, assistant.data)
        self.assertEqual(datasource.status_code, 201, datasource.data)
        self.assertEqual(
            Assistant.objects.get(uuid=assistant.data["uuid"]).tenant,
            self.tenant_a,
        )
        self.assertEqual(
            Assistant.objects.get(uuid=assistant.data["uuid"]).owner_user,
            self.user_tenant_admin,
        )
        self.assertEqual(
            DataSource.objects.get(uuid=datasource.data["uuid"]).tenant,
            self.tenant_a,
        )

    def test_tenant_member_cannot_create_core_resources(self):
        assistant = self.client_member.post(
            "/api/lens/assistants/",
            {
                "name": "Member created",
                "slug": "member-created",
                "visibility": "private",
                "skill_bindings": [
                    {"skill_uuid": str(self.skill.uuid)},
                ],
            },
            format="json",
        )
        datasource = self.client_member.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Member datasource",
                "source_type": "git",
                "config": {"repo_url": "https://example.com/member.git"},
            },
            format="json",
        )

        self.assertEqual(assistant.status_code, 403)
        self.assertEqual(datasource.status_code, 403)

    def test_credential_list_is_tenant_scoped(self):
        response = self.client_admin.get(
            "/api/lens/admin/credentials/",
            {"page_size": 100},
        )

        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.data["results"]}
        self.assertIn("Credential A", names)
        self.assertNotIn("Credential B", names)

    def test_datasource_cannot_bind_another_tenant_credential(self):
        response = self.client_admin.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Cross tenant credential",
                "source_type": "git",
                "config": {"repo_url": "https://example.com/cross.git"},
                "credential_uuid": str(self.credential_b.uuid),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("credential_uuid", response.data)

    def test_datasource_cannot_bind_another_tenant_connection(self):
        response = self.client_admin.post(
            "/api/lens/admin/datasources/",
            {
                "name": "Cross tenant connection",
                "source_type": "git",
                "config": {"repo_url": "https://example.com/cross.git"},
                "connection_uuid": str(self.connection_b.uuid),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("connection_uuid", response.data)

    def test_environment_variable_list_is_tenant_scoped(self):
        response = self.client_admin.get(
            "/api/lens/admin/environment-variable-sets/",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.data
        rows = (
            payload["results"]
            if isinstance(payload, dict) and "results" in payload
            else payload
        )
        names = {row["name"] for row in rows}
        self.assertIn("Env A", names)
        self.assertNotIn("Env B", names)

    def test_environment_variable_create_assigns_tenant(self):
        response = self.client_admin.post(
            "/api/lens/admin/environment-variable-sets/",
            {
                "name": "Env created",
                "values": [
                    {"key": "TOKEN", "value": "created-secret"},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        created = EnvironmentVariableSet.objects.get(
            uuid=response.data["uuid"]
        )
        self.assertEqual(created.tenant, self.tenant_a)

    def test_environment_variable_reveal_is_tenant_admin_only(self):
        member = self.client_member.post(
            f"/api/lens/admin/environment-variable-sets/{self.env_a.uuid}/reveal/"
        )
        admin = self.client_admin.post(
            f"/api/lens/admin/environment-variable-sets/{self.env_a.uuid}/reveal/"
        )

        self.assertEqual(member.status_code, 403)
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(admin.data["values"], [
            {"key": "TOKEN", "value": "a-secret"},
        ])
        self.assertEqual(admin["Cache-Control"], "no-store")

    def test_assistant_cannot_bind_another_tenant_environment_set(self):
        response = self.client_admin.post(
            "/api/lens/assistants/",
            {
                "name": "Cross env assistant",
                "slug": "cross-env-assistant",
                "visibility": "private",
                "skill_bindings": [
                    {
                        "skill_uuid": str(self.skill.uuid),
                        "environment_variable_set_uuid": str(
                            self.env_b.uuid
                        ),
                    },
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_session_and_run_inherit_tenant(self):
        session_response = self.client_admin.post(
            "/api/lens/sessions/",
            {"assistant_uuid": str(self.assistant_a.uuid)},
            format="json",
        )
        self.assertEqual(session_response.status_code, 201, session_response.data)
        session = Session.objects.get(uuid=session_response.data["uuid"])
        self.assertEqual(session.tenant, self.tenant_a)

        run = create_execution_run(session, "Tenant question", enqueue=False)
        self.assertEqual(run.tenant, self.tenant_a)

    def test_cross_tenant_session_and_run_are_hidden(self):
        session_b = Session.objects.create(
            tenant=self.tenant_b,
            assistant=self.assistant_b,
            user=self.user_b,
        )
        run_b = create_execution_run(
            session_b,
            "Other tenant question",
            enqueue=False,
        )

        session_response = self.client_a.get(
            f"/api/lens/sessions/{session_b.uuid}/"
        )
        run_response = self.client_a.get(
            f"/api/lens/runs/{run_b.uuid}/"
        )

        self.assertEqual(session_response.status_code, 404)
        self.assertEqual(run_response.status_code, 404)

    def test_admin_run_list_is_tenant_scoped(self):
        session_a = Session.objects.create(
            tenant=self.tenant_a,
            assistant=self.assistant_a,
            user=self.user_a,
        )
        session_b = Session.objects.create(
            tenant=self.tenant_b,
            assistant=self.assistant_b,
            user=self.user_b,
        )
        create_execution_run(session_a, "Run A", enqueue=False)
        create_execution_run(session_b, "Run B", enqueue=False)

        response = self.client_a.get(
            "/api/lens/admin/runs/",
            {"page": 1, "page_size": 100},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["question"], "Run A")

        run_b = Run.objects.filter(session=session_b).first()
        detail = self.client_a.get(
            f"/api/lens/admin/runs/{run_b.uuid}/"
        )
        trajectory = self.client_a.get(
            f"/api/lens/admin/runs/{run_b.uuid}/trajectory/"
        )
        diagnostics = self.client_a.get(
            f"/api/lens/admin/runs/{run_b.uuid}/diagnostics/"
        )
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(trajectory.status_code, 404)
        self.assertEqual(diagnostics.status_code, 404)
