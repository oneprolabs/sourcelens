"""Keep tenant member identities and explicit assistant grants outside delegation."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Profile, Tenant, TenantDelegation, TenantGroup, TenantMembership
from lens.models import Assistant, AssistantAccess, AssistantSkill, Message, Run, Session, Skill


class AssistantDelegationAccessTests(TestCase):
    """Delegated resource configuration must preserve tenant-owned member grants."""

    def setUp(self):
        """Create a usable assistant with user and group grants owned by a tenant."""
        self.tenant = Tenant.objects.create(name="Delegated tenant", slug="delegated-access")
        self.admin = get_user_model().objects.create_user(username="grant-admin")
        self.member = get_user_model().objects.create_user(username="grant-member", email="private@example.invalid")
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role="tenant_admin")
        TenantMembership.objects.create(tenant=self.tenant, user=self.member)
        self.group = Group.objects.create(name="private-grant-group")
        TenantGroup.objects.create(tenant=self.tenant, auth_group=self.group, name="Private team")
        self.skill = Skill.objects.create(tenant=self.tenant, name="Working skill", definition={})
        self.assistant = Assistant.objects.create(
            tenant=self.tenant, name="Delegated assistant", slug="delegated-assistant", visibility="private"
        )
        AssistantSkill.objects.create(assistant=self.assistant, skill=self.skill)
        self.user_grant = AssistantAccess.objects.create(
            assistant=self.assistant, user=self.member, granted_by=self.admin
        )
        self.group_grant = AssistantAccess.objects.create(
            assistant=self.assistant, group=self.group, granted_by=self.admin
        )
        self.platform = get_user_model().objects.create_user(username="grant-platform")
        Profile.objects.filter(user=self.platform).update(platform_role="super_admin")
        self.delegation = TenantDelegation.objects.create(
            platform_user=self.platform,
            tenant=self.tenant,
            mode="operational",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.platform)
        self.headers = {"HTTP_X_DELEGATION_ID": str(self.delegation.uuid)}
        self.url = f"/api/lens/assistants/{self.assistant.uuid}/"

    def assert_grants_preserved(self):
        """Check record identity and attribution as well as the selected principals."""
        self.assertEqual(
            set(self.assistant.access_grants.values_list("pk", "user_id", "group_id", "granted_by_id")),
            {
                (self.user_grant.pk, self.member.pk, None, self.admin.pk),
                (self.group_grant.pk, None, self.group.pk, self.admin.pk),
            },
        )

    def test_delegation_details_omit_member_grants_in_both_modes(self):
        """Read access to configuration does not reveal tenant member identities."""
        for mode in ("operational", "readonly"):
            with self.subTest(mode=mode):
                self.delegation.mode = mode
                self.delegation.save(update_fields=["mode"])
                response = self.client.get(self.url, **self.headers)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertIn("workspace_guide", response.data)
                self.assertEqual(response.data["visibility"], "private")
                self.assertNotIn("access_grants", response.data)
                for identity in (self.member.username, self.member.email, self.group.name, "Private team"):
                    self.assertNotIn(identity, str(response.data))

    def test_delegation_cannot_enumerate_authorization_subjects(self):
        """The assistant-specific selector cannot bypass hidden membership APIs."""
        for mode in ("operational", "readonly"):
            self.delegation.mode = mode
            self.delegation.save(update_fields=["mode"])
            response = self.client.get("/api/lens/assistants/access-subjects/", **self.headers)
            self.assertEqual(response.status_code, 403, response.data)
            self.assertNotIn(self.member.username, str(response.data))

    def test_legacy_subject_details_cannot_bypass_delegation_boundary(self):
        """Direct member and group detail URLs remain hidden in delegation."""
        for mode in ("operational", "readonly"):
            self.delegation.mode = mode
            self.delegation.save(update_fields=["mode"])
            for path in (f"users/{self.member.pk}", f"groups/{self.group.pk}"):
                response = self.client.get(f"/api/lens/admin/access/{path}/", **self.headers)
                self.assertEqual(response.status_code, 403, response.data)

    def test_legacy_subject_details_reject_foreign_tenant_identities(self):
        """A tenant administrator cannot query a foreign user or group by ID."""
        foreign = Tenant.objects.create(name="Foreign", slug="delegated-foreign")
        stranger = get_user_model().objects.create_user(username="foreign-grant-member")
        TenantMembership.objects.create(tenant=foreign, user=stranger)
        group = Group.objects.create(name="foreign-grant-group")
        TenantGroup.objects.create(tenant=foreign, auth_group=group, name="Foreign team")
        self.client.force_authenticate(self.admin)
        for path in (f"users/{stranger.pk}", f"groups/{group.pk}"):
            response = self.client.get(f"/api/lens/admin/access/{path}/")
            self.assertEqual(response.status_code, 404, response.data)

    def test_tenant_subject_detail_filters_related_groups_resources_and_history(self):
        """Legacy cross-links must not expand identity or activity projections."""
        foreign = Tenant.objects.create(name="Foreign", slug="delegated-related-foreign")
        stranger = get_user_model().objects.create_user(username="related-foreign-member")
        TenantMembership.objects.create(tenant=foreign, user=stranger)
        foreign_group = Group.objects.create(name="related-foreign-group")
        TenantGroup.objects.create(tenant=foreign, auth_group=foreign_group, name="Hidden related team")
        self.member.groups.add(self.group, foreign_group)
        stranger.groups.add(self.group)
        foreign_assistant = Assistant.objects.create(
            tenant=foreign, name="Hidden assistant", slug="hidden-related-assistant", visibility="public"
        )
        AssistantAccess.objects.create(assistant=foreign_assistant, group=self.group)
        tenant_wide = Assistant.objects.create(
            tenant=self.tenant, name="Tenant wide", slug="related-wide", visibility="tenant"
        )
        own = Session.objects.create(tenant=self.tenant, assistant=self.assistant, user=self.member)
        invalid = Session.objects.create(tenant=foreign, assistant=self.assistant, user=self.member)
        hidden = Session.objects.create(tenant=foreign, assistant=foreign_assistant, user=self.member)
        for sequence, (session, tenant) in enumerate(
            ((own, self.tenant), (own, foreign), (invalid, foreign), (hidden, foreign)), start=1
        ):
            message = Message.objects.create(session=session, role="user", content="Scoped question", sequence=sequence)
            Run.objects.create(tenant=tenant, session=session, input_message=message)
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/lens/admin/access/users/{self.member.pk}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["subject"]["groups"], [{"id": self.group.pk, "name": "Private team"}])
        self.assertEqual(response.data["stats"]["conversations"], 1)
        self.assertEqual(response.data["stats"]["qa_records"], 1)
        rows = {row["uuid"]: row for row in response.data["assistants"]}
        self.assertEqual(set(rows), {str(self.assistant.uuid), str(tenant_wide.uuid)})
        self.assertEqual(rows[str(self.assistant.uuid)]["qa_records"], 1)
        self.assertEqual(rows[str(tenant_wide.uuid)]["access_sources"], ["tenant"])
        response = self.client.get(f"/api/lens/admin/access/groups/{self.group.pk}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["stats"]["members"], 1)
        self.assertEqual([row["id"] for row in response.data["members"]["results"]], [self.member.pk])
        self.assertEqual([row["uuid"] for row in response.data["assistants"]], [str(self.assistant.uuid)])
        self.assertEqual(response.data["assistants"][0]["qa_records"], 1)

    def test_staff_member_cannot_use_legacy_subject_detail_routes(self):
        """The built-in tenant role must override the Django staff flag."""
        self.member.is_staff = True
        self.member.save(update_fields=["is_staff"])
        self.client.force_authenticate(self.member)
        for path in (f"users/{self.admin.pk}", f"groups/{self.group.pk}"):
            self.assertEqual(self.client.get(f"/api/lens/admin/access/{path}/").status_code, 403)

    def test_explicit_grant_updates_are_rejected_without_changing_configuration(self):
        """Empty, repeated and malformed grants cannot silently clear tenant assignments."""
        for grants in ([], [{"type": "user", "id": self.member.pk}], None, "invalid"):
            with self.subTest(grants=grants):
                response = self.client.patch(
                    self.url, {"description": "must not save", "access_grants": grants}, format="json", **self.headers
                )
                self.assertEqual(response.status_code, 403, response.data)
                self.assistant.refresh_from_db()
                self.assertEqual(self.assistant.description, "")
                self.assert_grants_preserved()

    def test_configuration_updates_preserve_hidden_grants_and_redact_responses(self):
        """Normal partial and full editing remain usable without reading grants."""
        for method in (self.client.patch, self.client.put):
            response = method(
                self.url,
                {
                    "name": self.assistant.name,
                    "slug": self.assistant.slug,
                    "description": "Updated",
                    "visibility": "private",
                },
                format="json",
                **self.headers,
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn("access_grants", response.data)
            self.assertEqual(response.data["description"], "Updated")
            self.assert_grants_preserved()

    def test_create_with_explicit_grants_is_rejected_before_writing(self):
        """Creation cannot assign or probe members through the resource endpoint."""
        response = self.client.post(
            "/api/lens/assistants/",
            {"name": "Rejected", "slug": "rejected-grants", "access_grants": []},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(Assistant.objects.filter(slug="rejected-grants").exists())

    def test_delegation_can_create_configuration_without_member_assignments(self):
        """Default private creation remains possible for the tenant to authorize later."""
        response = self.client.post(
            "/api/lens/assistants/",
            {
                "name": "New configuration",
                "slug": "delegated-new",
                "skill_bindings": [{"skill_uuid": str(self.skill.uuid)}],
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("access_grants", response.data)
        assistant = Assistant.objects.get(uuid=response.data["uuid"])
        self.assertEqual(assistant.tenant_id, self.tenant.pk)
        self.assertEqual(assistant.visibility, "private")
        self.assertFalse(assistant.access_grants.exists())
        self.assertIsNone(assistant.owner_user_id)

    def test_delegation_bulk_cannot_replace_member_grants(self):
        """Batch assignment cannot bypass the single-resource boundary."""
        response = self.client.post(
            "/api/lens/assistants/bulk-visibility/",
            {
                "assistants": [{"uuid": str(self.assistant.uuid), "updated_at": self.assistant.updated_at.isoformat()}],
                "visibility": "private",
                "access_grants": [{"type": "user", "id": self.admin.pk}],
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_grants_preserved()

    def test_delegation_visibility_only_batch_preserves_member_grants(self):
        """The prototype's visibility configuration remains available without subjects."""
        for visibility in ("tenant", "public", "private", "private"):
            with self.subTest(visibility=visibility):
                response = self.client.post(
                    "/api/lens/assistants/bulk-visibility/",
                    {
                        "assistants": [
                            {"uuid": str(self.assistant.uuid), "updated_at": self.assistant.updated_at.isoformat()}
                        ],
                        "visibility": visibility,
                    },
                    format="json",
                    **self.headers,
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assistant.refresh_from_db()
                self.assertEqual(self.assistant.visibility, visibility)
                self.assert_grants_preserved()
        self.assertEqual(response.data, {"updated": 0, "skipped": 1})

    def test_lifecycle_responses_do_not_reveal_hidden_grants(self):
        """Archive and restore must use the same projection as detail and editing."""
        for action in ("archive", "restore"):
            response = self.client.post(f"{self.url}{action}/", **self.headers)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn("access_grants", response.data)
            self.assert_grants_preserved()

    def test_tenant_administrator_retains_member_grant_management(self):
        """The boundary must not block the tenant that owns membership administration."""
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(len(response.data["access_grants"]), 2)
        self.assertEqual(self.client.get("/api/lens/assistants/access-subjects/").status_code, 200)
        response = self.client.patch(
            self.url, {"access_grants": [{"type": "user", "id": self.member.pk}]}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["access_grants"]), 1)
