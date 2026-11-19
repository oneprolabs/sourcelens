"""Tenant overview and setup progress must use the caller's current scope."""

from datetime import timedelta

from agentcore_metering.adapters.django.models import LLMConfig
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantDelegation, TenantMembership
from lens.models import (
    Assistant,
    AssistantDataSourceBinding,
    Connection,
    DataSource,
    LLMConfigScope,
    Message,
    Run,
    ScheduledTask,
    Session,
)


class TenantOverviewTests(TestCase):
    """Exercise real aggregation, lifecycle and delegation boundaries."""

    def setUp(self):
        self.a = Tenant.objects.create(name="Overview A", slug="tenant-overview-a")
        self.b = Tenant.objects.create(name="Private B", slug="tenant-overview-b")
        self.admin = get_user_model().objects.create_user(username="tenant-overview-admin")
        TenantMembership.objects.create(user=self.admin, tenant=self.a, role="tenant_admin")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def overview(self, **headers):
        """Read the actual tenant summary endpoint."""

        response = self.client.get("/api/v1/tenant/overview", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def model(self, tenant, **fields):
        """Create a model with explicit ownership."""

        config = LLMConfig.objects.create(provider="openai", config={"api_key": "private-secret"}, **fields)
        LLMConfigScope.objects.create(config=config, tenant=tenant)
        return config

    def test_empty_progress_has_no_fabricated_completion(self):
        data = self.overview()
        self.assertEqual(data["member_count"], 1)
        self.assertEqual(data["assistant_count"], 0)
        self.assertEqual(data["run_count_7d"], 0)
        self.assertEqual(len(data["question_series_7d"]), 7)
        self.assertEqual(sum(row["count"] for row in data["question_series_7d"]), 0)
        self.assertFalse(data["setup"]["complete"])
        self.assertFalse(data["setup"]["model_configured"])

    def test_nonstaff_tenant_admin_can_load_assistant_decision_form_metadata(self):
        response = self.client.get("/api/lens/admin/decision-catalog/")
        self.assertEqual(response.status_code, 200, response.data)
        membership = TenantMembership.objects.get(user=self.admin)
        membership.role = "member"
        membership.save()
        self.admin.is_staff = True
        self.admin.save()
        self.admin.refresh_from_db()
        self.admin.__dict__.pop("_membership_cache", None)
        self.assertEqual(self.client.get("/api/lens/admin/decision-catalog/").status_code, 403)

    def test_other_tenant_platform_disabled_and_embedding_models_do_not_complete_setup(self):
        self.model(self.b)
        self.model(None)
        self.model(self.a, is_active=False)
        self.model(self.a, model_type="embedding")
        Assistant.objects.create(tenant=self.b, name="Private assistant", slug="tenant-overview-private")
        DataSource.objects.create(tenant=self.b, name="Private source", source_type="git")
        data = self.overview()
        self.assertFalse(data["setup"]["model_configured"])
        self.assertEqual(data["assistant_count"], 0)
        self.assertEqual(data["datasource_count"], 0)
        self.assertNotIn("Private", str(data))
        self.assertNotIn("private-secret", str(data))

    def test_setup_summaries_are_bounded_and_do_not_expose_provider_config(self):
        for index in range(7):
            self.model(self.a)
            Assistant.objects.create(tenant=self.a, name=f"Assistant {index}", slug=f"tenant-summary-{index}")
        data = self.overview()
        self.assertEqual(len(data["setup"]["models"]), 5)
        self.assertEqual(len(data["setup"]["assistants"]), 5)
        self.assertEqual(data["setup"]["model_count"], 7)
        self.assertNotIn("private-secret", str(data))

    def test_connection_summary_is_scoped_bounded_and_never_returns_configuration(self):
        for index in range(7):
            Connection.objects.create(
                tenant=self.a,
                name=f"Connection {index}",
                plugin_key="github",
                endpoint="https://private.example.com",
                config={"private": "do-not-return"},
                allowed_scope={"repositories": ["private/repository"]},
                status="disabled" if index == 0 else "active",
            )
        other = Connection.objects.create(tenant=self.b, name="Other tenant", plugin_key="github")
        Connection.objects.create(tenant=self.a, name="Retired", plugin_key="typesafe")
        Connection.objects.create(
            tenant=self.a, name="Internal anonymous", plugin_key="github", allowed_scope={"repositories": ["*"]}
        )
        data = self.overview()
        self.assertEqual(data["setup"]["connection_count"], 7)
        self.assertEqual(len(data["setup"]["connections"]), 5)
        self.assertEqual(data["setup"]["connections"][0]["status"], "disabled")
        for row in data["setup"]["connections"]:
            self.assertEqual(set(row), {"uuid", "name", "plugin_key", "status"})
        for private in ("Other tenant", "Retired", "Internal anonymous", "do-not-return", "private/repository"):
            self.assertNotIn(private, str(data))
        self.assertEqual(self.client.get(f"/api/lens/admin/connections/{other.uuid}/").status_code, 404)
        self.assertEqual(self.client.post(f"/api/lens/admin/connections/{other.uuid}/validate/").status_code, 404)

    def test_general_chat_can_skip_knowledge_but_knowledge_assistant_cannot(self):
        model = self.model(self.a)
        assistant = Assistant.objects.create(
            tenant=self.a, name="Chat", slug="tenant-overview-chat", agent_model_ref=model.uuid
        )
        data = self.overview()
        self.assertTrue(data["setup"]["complete"])
        self.assertFalse(data["setup"]["knowledge_required"])
        assistant.capability = "knowledge_qa"
        assistant.save()
        self.assertFalse(self.overview()["setup"]["complete"])
        datasource = DataSource.objects.create(tenant=self.a, name="Docs", source_type="git")
        self.assertFalse(self.overview()["setup"]["complete"])
        AssistantDataSourceBinding.objects.create(assistant=assistant, datasource=datasource, mount_name="docs")
        self.assertTrue(self.overview()["setup"]["complete"])

    def test_disabled_assistant_does_not_complete_setup_but_still_counts_as_managed(self):
        """Paused configuration stays in the inventory without indicating readiness."""
        model = self.model(self.a)
        Assistant.objects.create(
            tenant=self.a, name="Paused", slug="tenant-overview-paused", agent_model_ref=model.uuid, is_enabled=False
        )
        data = self.overview()
        self.assertEqual(data["assistant_count"], 1)
        self.assertFalse(data["setup"]["assistant_configured"])
        self.assertFalse(data["setup"]["complete"])

    def test_unbound_or_unavailable_model_does_not_mark_assistant_configured(self):
        model = self.model(self.a)
        assistant = Assistant.objects.create(tenant=self.a, name="Unbound", slug="tenant-overview-unbound")
        self.assertFalse(self.overview()["setup"]["assistant_configured"])
        assistant.agent_model_ref = model.uuid
        assistant.save()
        self.assertTrue(self.overview()["setup"]["complete"])
        model.is_active = False
        model.save()
        data = self.overview()
        self.assertFalse(data["setup"]["complete"])
        self.assertEqual(data["attention"]["assistant_attention_count"], 1)

    def test_questions_use_immutable_run_tenant_and_exclude_evaluation_future(self):
        assistant = Assistant.objects.create(tenant=self.a, name="A", slug="tenant-overview-runs")
        session = Session.objects.create(tenant=self.a, assistant=assistant, user=self.admin)
        message = Message.objects.create(session=session, role="user", content="Private prompt", sequence=1)
        for tenant, source, age in (
            (self.a, "user", 1),
            (self.a, "user", 8),
            (self.a, "evaluation", 1),
            (self.a, "user", -1),
            (self.b, "user", 1),
        ):
            run = Run.objects.create(tenant=tenant, source=source, session=session, input_message=message)
            Run.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=age))
        data = self.overview()
        self.assertEqual(data["run_count_7d"], 1)
        self.assertEqual(data["run_count_previous_7d"], 1)
        self.assertEqual(data["run_change_percent"], 0)
        self.assertNotIn("Private prompt", str(data))

    def test_datasource_health_includes_waiting_disabled_and_real_sync_failures(self):
        failed = DataSource.objects.create(tenant=self.a, name="Failed", source_type="git")
        ScheduledTask.objects.create(
            name="Failed sync",
            task_type="source_sync",
            target_type="datasource",
            target_id=failed.uuid,
            last_status="failed",
            last_error="private endpoint credential",
        )
        DataSource.objects.create(tenant=self.a, name="Waiting", source_type="git")
        DataSource.objects.create(tenant=self.a, name="Disabled", source_type="git", status="disabled")
        DataSource.objects.create(tenant=self.b, name="Private failed", source_type="git", last_error="other")
        data = self.overview()
        self.assertEqual(
            data["datasource_health"], {"failed": 1, "waiting": 1, "disabled": 1, "synced": 0, "running": 0}
        )
        self.assertEqual(data["attention"]["failed_datasource_count"], 1)
        self.assertNotIn("private endpoint", str(data))

    def test_member_staff_disabled_membership_and_inactive_tenant_are_denied(self):
        membership = TenantMembership.objects.get(user=self.admin)
        for role, state, tenant_state in (
            ("member", "active", "active"),
            ("tenant_admin", "disabled", "active"),
            ("tenant_admin", "active", "suspended"),
        ):
            membership.role, membership.status = role, state
            membership.save()
            self.a.status = tenant_state
            self.a.save()
            self.admin.is_staff = True
            self.admin.save()
            self.admin.refresh_from_db()
            self.admin.__dict__.pop("_membership_cache", None)
            self.assertEqual(self.client.get("/api/v1/tenant/overview").status_code, 403)

    def test_platform_requires_delegation_and_readonly_delegation_can_read(self):
        platform = get_user_model().objects.create_user(username="tenant-overview-platform")
        Profile.objects.update_or_create(user=platform, defaults={"platform_role": "super_admin"})
        self.client.force_authenticate(platform)
        self.assertEqual(self.client.get("/api/v1/tenant/overview").status_code, 403)
        delegation = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.a,
            mode="readonly",
            reason="Acceptance",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        data = self.overview(HTTP_X_DELEGATION_ID=str(delegation.uuid))
        self.assertEqual(data["tenant"]["uuid"], str(self.a.uuid))
        self.assertTrue(data["delegated"])
        response = self.client.post(
            "/api/lens/admin/connections/",
            {"name": "Readonly connection", "plugin_key": "github"},
            format="json",
            HTTP_X_DELEGATION_ID=str(delegation.uuid),
        )
        self.assertEqual(response.status_code, 403)
        connection = Connection.objects.create(tenant=self.a, name="Delegated connection", plugin_key="github")
        for method, path in (
            ("patch", f"/api/lens/admin/connections/{connection.uuid}/"),
            ("delete", f"/api/lens/admin/connections/{connection.uuid}/"),
            ("post", f"/api/lens/admin/connections/{connection.uuid}/validate/"),
            ("post", "/api/lens/admin/connections/resource-preview/"),
        ):
            response = getattr(self.client, method)(path, {}, format="json", HTTP_X_DELEGATION_ID=str(delegation.uuid))
            self.assertEqual(response.status_code, 403, path)
        self.assertTrue(Connection.objects.filter(pk=connection.pk).exists())
        delegation.mode = "operational"
        delegation.save()
        response = self.client.post(
            "/api/lens/admin/connections/",
            {
                "name": "Operational connection",
                "plugin_key": "github",
                "allowed_scope": {"repositories": ["acme/docs"]},
            },
            format="json",
            HTTP_X_DELEGATION_ID=str(delegation.uuid),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Connection.objects.get(uuid=response.data["uuid"]).tenant_id, self.a.pk)
        delegation.status = "ended"
        delegation.save()
        self.assertEqual(
            self.client.get("/api/v1/tenant/overview", HTTP_X_DELEGATION_ID=str(delegation.uuid)).status_code, 403
        )
