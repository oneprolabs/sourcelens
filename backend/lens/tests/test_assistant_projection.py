"""Separate assistant usage metadata from tenant administration configuration."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantDelegation, TenantMembership
from lens.models import Assistant, AssistantAccess, AssistantDataSourceBinding, DataSource, LensNode, Session


class AssistantProjectionTests(TestCase):
    """Using an assistant does not grant access to its configuration or grants."""

    usage_fields = {
        "uuid",
        "name",
        "description",
        "slug",
        "capability",
        "mode",
        "routing_mode",
        "status",
        "is_enabled",
        "visibility",
        "collaboration_members",
        "supports_document_attachments",
        "can_process_images",
        "show_citations",
    }

    def setUp(self):
        """Configure a tenant assistant with content that belongs only in management."""
        self.tenant = Tenant.objects.create(name="Projection", slug="projection-a")
        self.foreign = Tenant.objects.create(name="Foreign", slug="projection-b")
        self.admin = self.member("admin", self.tenant, "tenant_admin")
        self.peer = self.member("peer", self.tenant)
        self.other = self.member("other", self.foreign, "tenant_admin")
        self.node = LensNode.objects.create(name="Internal execution node")
        self.assistant = Assistant.objects.create(
            tenant=self.tenant,
            name="Usable assistant",
            slug="projection-assistant",
            description="Display summary",
            visibility="tenant",
            lensnode=self.node,
            settings={"internal_policy": "management-policy", "features": {"citations": False}},
            workspace_guide={"custom_instructions": "private workspace instructions"},
            decision_gates={"private-gate": True},
        )
        self.source = DataSource.objects.create(
            tenant=self.tenant, name="Confidential source name", source_type="upload"
        )
        AssistantDataSourceBinding.objects.create(
            assistant=self.assistant, datasource=self.source, mount_name="internal"
        )
        AssistantAccess.objects.create(assistant=self.assistant, user=self.peer)
        self.client = APIClient()
        self.client.force_authenticate(self.peer)

    def member(self, name, tenant, role="member"):
        """Create a user with explicit tenant authority."""
        user = get_user_model().objects.create_user(username=f"projection-{name}", email=f"{name}@projection.invalid")
        TenantMembership.objects.create(user=user, tenant=tenant, role=role)
        return user

    def detail(self, **params):
        """Read the assistant through the same route used by management details."""
        return self.client.get(f"/api/lens/assistants/{self.assistant.uuid}/", params)

    def assert_usage(self, data):
        """Check an allowlist so future configuration fields cannot silently leak."""
        self.assertEqual(set(data), self.usage_fields)
        self.assertEqual(data["name"], self.assistant.name)
        self.assertIs(data["show_citations"], False)
        for private in (
            "management-policy",
            "private workspace instructions",
            self.peer.email,
            self.node.name,
            self.source.name,
        ):
            self.assertNotIn(private, str(data))

    def test_member_list_and_detail_return_only_usage_fields(self):
        """A tenant-wide use grant carries no management metadata."""
        response = self.client.get("/api/lens/assistants/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_usage(response.data["results"][0])
        self.assert_usage(self.detail().data)

    def test_anonymous_metadata_requires_a_public_assistant_in_an_active_tenant(self):
        """An anonymous preview must not disclose nonpublic or suspended tenant assistants."""
        client = APIClient()
        path = f"/api/lens/public/assistants/{self.assistant.slug}/"
        for visibility in (Assistant.Visibility.TENANT, Assistant.Visibility.PRIVATE):
            with self.subTest(visibility=visibility):
                Assistant.objects.filter(pk=self.assistant.pk).update(visibility=visibility)
                self.assertEqual(client.get(path).status_code, 404)

        Assistant.objects.filter(pk=self.assistant.pk).update(visibility=Assistant.Visibility.PUBLIC)
        self.assertEqual(client.get(path).status_code, 200)
        Tenant.objects.filter(pk=self.tenant.pk).update(status=Tenant.Status.SUSPENDED)
        self.assertEqual(client.get(path).status_code, 404)

    def test_internal_coordinator_requires_the_smart_session_flow(self):
        """Direct conversations cannot target a hidden system assistant."""
        Assistant.objects.filter(pk=self.assistant.pk).update(is_system=True)
        response = self.client.post(
            "/api/lens/sessions/", {"assistant_uuid": str(self.assistant.uuid)}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Session.objects.filter(assistant=self.assistant).exists())

    def test_session_update_keeps_the_assistant_and_routing_mode_read_only(self):
        """Editing a session never changes how it was created, even with an accessible assistant."""
        other = Assistant.objects.create(
            tenant=self.tenant, name="Another assistant", slug="projection-alternate", visibility="tenant"
        )
        session = Session.objects.create(tenant=self.tenant, assistant=self.assistant, user=self.peer)
        response = self.client.patch(
            f"/api/lens/sessions/{session.uuid}/",
            {"title": "New title", "assistant": other.pk, "routing_mode": Session.RoutingMode.SMART},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        session.refresh_from_db()
        self.assertEqual(session.title, "New title")
        self.assertEqual(session.assistant_id, self.assistant.pk)
        self.assertEqual(session.routing_mode, Session.RoutingMode.DIRECT)

    def test_smart_session_can_update_its_title_and_accessible_range(self):
        """Session editing still supports the user-selected Smart Collaboration range."""
        coordinator = Assistant.objects.create(
            tenant=self.tenant, name="Coordinator", slug="projection-coordinator", is_system=True
        )
        session = Session.objects.create(
            tenant=self.tenant, assistant=coordinator, user=self.peer, routing_mode=Session.RoutingMode.SMART
        )
        response = self.client.patch(
            f"/api/lens/sessions/{session.uuid}/",
            {"title": "New title", "allowed_assistant_uuids": [str(self.assistant.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["title"], "New title")
        self.assertEqual(response.data["allowed_assistant_uuids"], [str(self.assistant.uuid)])

    def test_query_parameters_cannot_elevate_a_member(self):
        """Explicit management selection is checked before returning any configuration."""
        self.assertEqual(self.client.get("/api/lens/assistants/", {"managed": "true"}).status_code, 403)
        response = self.detail(managed="true")
        self.assertIn(response.status_code, (200, 403))
        if response.status_code == 200:
            self.assert_usage(response.data)

    def test_tenant_administrator_keeps_own_configuration_contract(self):
        """The editing and inspection surfaces still receive their full configuration."""
        self.client.force_authenticate(self.admin)
        response = self.detail()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["settings"]["internal_policy"], "management-policy")
        self.assertEqual(response.data["access_grants"][0]["email"], self.peer.email)
        rows = self.client.get("/api/lens/assistants/", {"managed": "true"}).data["results"]
        self.assertIn("datasource_bindings", rows[0])
        self.assertEqual(self.client.get(f"/api/lens/assistants/{self.assistant.uuid}/datasources/").status_code, 200)

    def test_foreign_public_assistant_never_exposes_configuration_to_other_admin(self):
        """An administrator's authority remains bound to their own tenant per row."""
        self.assistant.visibility = "public"
        self.assistant.save()
        self.client.force_authenticate(self.other)
        self.assert_usage(self.detail().data)
        self.assert_usage(self.client.get("/api/lens/assistants/").data["results"][0])

    def test_datasource_configuration_is_not_a_usage_endpoint(self):
        """The related-resource action cannot bypass the detail projection."""
        response = self.client.get(f"/api/lens/assistants/{self.assistant.uuid}/datasources/")
        self.assertEqual(response.status_code, 403, response.data)

    def test_member_with_django_staff_flag_still_has_only_tenant_member_authority(self):
        """Django staff must not override the built-in tenant role."""
        self.peer.is_staff = True
        self.peer.save()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.peer.pk))
        self.assert_usage(self.detail().data)
        self.assertEqual(
            self.client.patch(
                f"/api/lens/assistants/{self.assistant.uuid}/", {"description": "unauthorized"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(self.client.post(f"/api/lens/assistants/{self.assistant.uuid}/archive/").status_code, 403)

    def test_api_key_does_not_inherit_its_creator_configuration_access(self):
        """The independently authenticated principal only receives usage metadata."""
        self.client.force_authenticate(self.admin, token={"principal_type": "tenant_api_key", "scope": "all"})
        self.assert_usage(self.detail().data)
        self.assert_usage(self.client.get("/api/lens/assistants/").data["results"][0])

    def test_collaboration_members_include_only_assistants_the_viewer_can_access(self):
        """A visible coordinator must not reveal the identity of an inaccessible member."""
        hidden = Assistant.objects.create(
            tenant=self.tenant, name="Hidden collaborator", slug="projection-hidden", visibility="private"
        )
        Assistant.objects.filter(pk=self.assistant.pk).update(routing_mode="smart")
        self.assistant.collaboration_members.add(hidden)
        data = self.detail().data
        self.assertEqual(data["collaboration_members"], [])
        AssistantAccess.objects.create(assistant=hidden, user=self.peer)
        data = self.detail().data
        self.assertEqual([row["uuid"] for row in data["collaboration_members"]], [str(hidden.uuid)])
        self.assertEqual(set(data["collaboration_members"][0]), {"uuid", "name", "capability", "status", "is_enabled"})

    def test_readonly_delegation_can_inspect_configuration_but_cannot_update(self):
        """Operational help preserves the existing readonly boundary."""
        platform = get_user_model().objects.create_user(username="projection-platform")
        Profile.objects.filter(user=platform).update(platform_role="super_admin")
        delegation = TenantDelegation.objects.create(
            platform_user=platform,
            tenant=self.tenant,
            mode="readonly",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        self.client.force_authenticate(platform)
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        url = f"/api/lens/assistants/{self.assistant.uuid}/"
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("workspace_guide", response.data)
        self.assertEqual(
            self.client.patch(url, {"description": "unauthorized"}, format="json", **headers).status_code, 403
        )

    def test_platform_and_legacy_staff_cannot_read_or_mutate_tenant_public_configuration(self):
        """The unowned compatibility path never expands into an existing tenant."""
        self.assistant.visibility = "public"
        self.assistant.save()
        for platform_role in ("", "super_admin"):
            with self.subTest(platform_role=platform_role):
                user = get_user_model().objects.create_user(
                    username=f"projection-unowned-{platform_role or 'staff'}", is_staff=True
                )
                Profile.objects.filter(user=user).update(platform_role=platform_role)
                self.client.force_authenticate(user)
                self.assert_usage(self.detail().data)
                self.assertEqual(
                    self.client.patch(
                        f"/api/lens/assistants/{self.assistant.uuid}/", {"description": "unauthorized"}, format="json"
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    self.client.get(f"/api/lens/assistants/{self.assistant.uuid}/datasources/").status_code, 404
                )
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.description, "Display summary")

    def test_mixed_admin_collection_projects_each_assistant_by_its_owner(self):
        """One list can contain own configurations and public usage metadata safely."""
        other = Assistant.objects.create(
            tenant=self.foreign,
            name="Public foreign",
            slug="projection-public-foreign",
            visibility="public",
            settings={"private": "foreign configuration"},
        )
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/lens/assistants/")
        by_uuid = {row["uuid"]: row for row in response.data["results"]}
        self.assertIn("decision_gates", by_uuid[str(self.assistant.uuid)])
        self.assertEqual(set(by_uuid[str(other.uuid)]), self.usage_fields)
        self.assertNotIn("foreign configuration", str(response.data))

    def test_legacy_staff_cannot_replace_models_in_a_public_tenant_assistant(self):
        """A known model identity cannot expand unowned administration into a tenant."""
        from agentcore_metering.adapters.django.models import LLMConfig

        from lens.models import LLMConfigScope

        model = LLMConfig.objects.create(scope="global", provider="openai", config={"model": "tenant-chat"})
        LLMConfigScope.objects.create(config=model, tenant=self.tenant)
        self.assistant.visibility = "public"
        self.assistant.save()
        staff = get_user_model().objects.create_user(username="projection-model-staff", is_staff=True)
        self.client.force_authenticate(staff)
        response = self.client.post(
            "/api/lens/assistants/replace-model/",
            {
                "model_field": "agent_model_ref",
                "target_model_ref": str(model.uuid),
                "assistant_models": [{"uuid": str(self.assistant.uuid), "model_ref": None}],
                "preview": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assistant.refresh_from_db()
        self.assertIsNone(self.assistant.agent_model_ref)

    def test_real_selected_api_key_receives_only_usage_metadata_and_scoped_members(self):
        """Exercise authentication so the administrator owner cannot leak through."""
        from accounts.models import TenantApiKey

        self.assistant.routing_mode = "smart"
        self.assistant.save()
        member = Assistant.objects.create(
            tenant=self.tenant, name="Key-excluded member", slug="projection-key-member", visibility="tenant"
        )
        self.assistant.collaboration_members.add(member)
        _, raw = TenantApiKey.issue(
            tenant=self.tenant,
            name="Usage only",
            scope="selected",
            assistant_uuids=[str(self.assistant.uuid)],
            created_by=self.admin,
            expires_at=None,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        response = client.get(f"/api/lens/assistants/{self.assistant.uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_usage(response.data)
        self.assertEqual(response.data["collaboration_members"], [])
        self.assertEqual(client.get(f"/api/lens/assistants/{self.assistant.uuid}/datasources/").status_code, 403)
        self.assertEqual(client.get(f"/api/lens/assistants/{member.uuid}/").status_code, 404)

    def test_disabled_history_metadata_keeps_the_same_safe_projection(self):
        """History access cannot reintroduce hidden configuration fields."""
        Assistant.objects.filter(pk=self.assistant.pk).update(is_enabled=False)
        self.assertEqual(self.client.get("/api/lens/assistants/").data["count"], 0)
        response = self.client.get("/api/lens/assistants/", {"include_disabled": "true"})
        self.assert_usage(response.data["results"][0])
        self.assertIs(response.data["results"][0]["is_enabled"], False)
        self.assert_usage(self.detail().data)

    def test_historical_collaboration_range_does_not_disclose_revoked_member_names(self):
        """The session route cannot bypass the assistant-member projection."""
        from lens.models import Session

        hidden = Assistant.objects.create(
            tenant=self.tenant, name="Revoked collaborator", slug="projection-revoked", visibility="private"
        )
        session = Session.objects.create(
            tenant=self.tenant,
            assistant=self.assistant,
            user=self.peer,
            routing_mode="smart",
            allowed_assistant_uuids=[str(hidden.uuid)],
        )
        response = self.client.get(f"/api/lens/sessions/{session.uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["routing_assistants"], [])
        self.assertNotIn(hidden.name, str(response.data))
        AssistantAccess.objects.create(assistant=hidden, user=self.peer)
        response = self.client.get(f"/api/lens/sessions/{session.uuid}/")
        self.assertEqual(response.data["routing_assistants"][0]["name"], hidden.name)

    def test_usage_capabilities_still_allow_session_creation_without_configuration_fields(self):
        """Clients can choose an assistant and start a conversation from usage metadata."""
        self.assistant.routing_mode = "smart"
        self.assistant.save()
        member = Assistant.objects.create(
            tenant=self.tenant, name="Accessible member", slug="projection-usable-member", visibility="tenant"
        )
        self.assistant.collaboration_members.add(member)
        response = self.client.post("/api/lens/sessions/", {"assistant_uuid": str(self.assistant.uuid)}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["routing_assistants"][0]["uuid"], str(member.uuid))
        self.assertEqual(response.data["assistant_slug"], self.assistant.slug)
