"""Tests for tenant-scoped group management."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Tenant, TenantGroup, TenantMembership
from lens.models import Assistant, AssistantAccess


class TenantGroupApiTests(TestCase):
    """Tenant administrators manage groups inside their own tenant only."""

    def setUp(self):
        user_model = get_user_model()
        self.tenant = Tenant.objects.create(name="Acme", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Other", slug="other")
        self.admin = user_model.objects.create_user(
            username="group-admin",
            email="group-admin@example.com",
            password="x",
        )
        self.member = user_model.objects.create_user(
            username="group-member",
            email="group-member@example.com",
            password="x",
        )
        self.foreign = user_model.objects.create_user(
            username="group-foreign",
            email="group-foreign@example.com",
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
            role=TenantMembership.Role.MEMBER,
        )
        self.auth_group = Group.objects.create(name="Acme Engineering")
        self.group = TenantGroup.objects.create(
            tenant=self.tenant,
            auth_group=self.auth_group,
            name="研发部",
            description="Engineering",
            created_by=self.admin,
        )
        self.assistant = Assistant.objects.create(
            name="Acme assistant",
            slug="acme-assistant",
            tenant=self.tenant,
            visibility=Assistant.Visibility.PRIVATE,
        )
        AssistantAccess.objects.create(
            assistant=self.assistant,
            group=self.auth_group,
        )
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.member_client = APIClient()
        self.member_client.force_authenticate(self.member)

    def test_group_list_is_tenant_scoped_and_counts_usage(self):
        response = self.admin_client.get("/api/v1/tenant/groups")

        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "研发部")
        self.assertEqual(rows[0]["assistant_count"], 1)

    def test_group_create_and_duplicate_name(self):
        response = self.admin_client.post(
            "/api/v1/tenant/groups",
            {"name": "产品部", "description": "Product"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        created = TenantGroup.objects.get(uuid=response.data["uuid"])
        self.assertEqual(created.tenant, self.tenant)
        self.assertEqual(created.name, "产品部")

        duplicate = self.admin_client.post(
            "/api/v1/tenant/groups",
            {"name": "产品部"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_group_detail_lists_and_adds_members(self):
        detail = self.admin_client.get(f"/api/v1/tenant/groups/{self.group.uuid}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["members"], [])

        add = self.admin_client.post(
            f"/api/v1/tenant/groups/{self.group.uuid}/members",
            {"user_id": self.member.pk},
            format="json",
        )
        self.assertEqual(add.status_code, 200, add.data)
        self.assertTrue(self.auth_group.user_set.filter(pk=self.member.pk).exists())

        detail = self.admin_client.get(f"/api/v1/tenant/groups/{self.group.uuid}")
        self.assertEqual(detail.data["members"][0]["id"], self.member.pk)

    def test_group_member_must_belong_to_tenant(self):
        response = self.admin_client.post(
            f"/api/v1/tenant/groups/{self.group.uuid}/members",
            {"user_id": self.foreign.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.auth_group.user_set.filter(pk=self.foreign.pk).exists())

    def test_group_remove_member(self):
        self.auth_group.user_set.add(self.member)
        response = self.admin_client.delete(f"/api/v1/tenant/groups/{self.group.uuid}/members/{self.member.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.auth_group.user_set.filter(pk=self.member.pk).exists())

    def test_group_rename(self):
        response = self.admin_client.patch(
            f"/api/v1/tenant/groups/{self.group.uuid}",
            {"name": "研发与平台", "description": "Updated"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "研发与平台")
        self.assertEqual(self.group.description, "Updated")

    def test_group_delete_cannot_bypass_review(self):
        """The legacy single-delete path requires an impact proof too."""
        response = self.admin_client.delete(f"/api/v1/tenant/groups/{self.group.uuid}")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(TenantGroup.objects.filter(pk=self.group.pk).exists())

    def test_member_cannot_manage_groups(self):
        read = self.member_client.get("/api/v1/tenant/groups")
        write = self.member_client.post(
            "/api/v1/tenant/groups",
            {"name": "Nope"},
            format="json",
        )

        self.assertEqual(read.status_code, 403)
        self.assertEqual(write.status_code, 403)

    def test_cannot_access_other_tenant_group(self):
        other_auth = Group.objects.create(name="Other group")
        other_group = TenantGroup.objects.create(
            tenant=self.other_tenant,
            auth_group=other_auth,
            name="Other group",
        )
        response = self.admin_client.get(f"/api/v1/tenant/groups/{other_group.uuid}")

        self.assertEqual(response.status_code, 404)

    def selection(self, *groups):
        """Bind requests to current group evidence."""
        rows = self.admin_client.get("/api/v1/tenant/groups").data["results"]
        indexed = {row["uuid"]: row for row in rows}
        return [{"uuid": str(group.pk), "version": indexed[str(group.pk)]["version"]} for group in groups]

    def preview(self, *groups):
        """Obtain the reviewed impact through the real endpoint."""
        self.selected = self.selection(*(groups or [self.group]))
        response = self.admin_client.post(
            "/api/v1/tenant/groups/delete", {"groups": self.selected, "preview": True}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def confirm(self, preview, client=None):
        """Confirm the same frozen selection and proof."""
        return (client or self.admin_client).post(
            "/api/v1/tenant/groups/delete",
            {"groups": self.selected, "preview": False, "confirmation": preview["confirmation"]},
            format="json",
        )

    def test_review_is_read_only_and_deletion_preserves_members_assistants_and_direct_grants(self):
        """Only the reviewed group's associations are removed."""
        from accounts.models import AuditLog

        self.auth_group.user_set.add(self.member)
        direct = AssistantAccess.objects.create(assistant=self.assistant, user=self.member)
        preview = self.preview()
        self.assertEqual(preview["groups"][0]["member_count"], 1)
        self.assertEqual(
            preview["groups"][0]["assistants"], [{"uuid": str(self.assistant.uuid), "name": self.assistant.name}]
        )
        self.assertTrue(TenantGroup.objects.filter(pk=self.group.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="group.delete").exists())
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["deleted"], 1)
        self.assertTrue(Assistant.objects.filter(pk=self.assistant.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.member.pk).exists())
        self.assertTrue(AssistantAccess.objects.filter(pk=direct.pk).exists())
        self.assertFalse(Group.objects.filter(pk=self.auth_group.pk).exists())
        self.assertEqual(AuditLog.objects.filter(action="group.delete", tenant=self.tenant).count(), 1)

    def test_association_changes_invalidate_review(self):
        """A new member cannot silently be included after the impact review."""
        preview = self.preview()
        self.auth_group.user_set.add(self.member)
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertTrue(TenantGroup.objects.filter(pk=self.group.pk).exists())

    def test_foreign_associations_are_not_disclosed_or_deleted(self):
        """Historical contaminated groups are skipped without exposing foreign identity."""
        foreign = Assistant.objects.create(
            tenant=self.other_tenant, name="Confidential foreign assistant", slug="foreign-group-preview"
        )
        AssistantAccess.objects.create(assistant=foreign, group=self.auth_group)
        self.auth_group.user_set.add(self.foreign)
        preview = self.preview()
        row = preview["groups"][0]
        self.assertEqual(row["member_count"], 0)
        self.assertEqual(row["assistant_count"], 1)
        self.assertEqual(row["skip_reason"], "foreign_associations")
        self.assertNotIn(foreign.name, str(preview))
        self.assertNotIn(self.foreign.email, str(preview))
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["skipped"], 1)
        self.assertTrue(TenantGroup.objects.filter(pk=self.group.pk).exists())

    def test_mixed_tenant_selection_is_rejected_atomically(self):
        """A foreign UUID invalidates the entire batch before any deletion."""
        foreign = TenantGroup.objects.create(
            tenant=self.other_tenant, auth_group=Group.objects.create(name="Foreign batch"), name="Secret"
        )
        selected = self.selection(self.group) + [{"uuid": str(foreign.pk), "version": "invalid"}]
        response = self.admin_client.post(
            "/api/v1/tenant/groups/delete", {"groups": selected, "preview": True}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(TenantGroup.objects.filter(pk=self.group.pk).exists())

    def test_confirmation_is_actor_bound_and_expires(self):
        """Another admin or an expired signed proof cannot approve this actor's review."""
        from unittest.mock import patch

        from django.core import signing

        from accounts.services.group_management import SALT

        preview = self.preview()
        TenantMembership.objects.filter(user=self.member).update(role="tenant_admin")
        self.assertEqual(self.confirm(preview, self.member_client).status_code, 409)
        with patch("accounts.services.group_management.signing.loads", side_effect=signing.SignatureExpired):
            self.assertEqual(self.confirm(preview).status_code, 409)
        payload = signing.loads(preview["confirmation"], salt=SALT)
        payload["tenant"] = str(self.other_tenant.pk)
        preview["confirmation"] = signing.dumps(payload, salt=SALT)
        self.assertEqual(self.confirm(preview).status_code, 409)

    def test_platform_identity_and_stale_authority_are_denied(self):
        """Platform role and stale cached membership cannot grant group administration."""
        from accounts.models import Profile

        Profile.objects.filter(user=self.admin).update(platform_role="super_admin")
        self.assertEqual(self.admin_client.get("/api/v1/tenant/groups").status_code, 403)
        Profile.objects.filter(user=self.admin).update(platform_role="none")
        TenantMembership.objects.filter(user=self.admin).update(role="member")
        self.assertEqual(self.admin_client.get("/api/v1/tenant/groups").status_code, 403)

    def test_foreign_removal_and_inactive_addition_are_rejected(self):
        """Group mutations never edit outside users or add disabled accounts."""
        response = self.admin_client.delete(f"/api/v1/tenant/groups/{self.group.pk}/members/{self.foreign.pk}")
        self.assertEqual(response.status_code, 404)
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        response = self.admin_client.post(
            f"/api/v1/tenant/groups/{self.group.pk}/members", {"user_id": self.member.pk}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_group_mutations_validate_field_limits_and_member_ids(self):
        """Malformed requests return validation errors without creating or changing groups."""
        for payload in ({"name": "x" * 101}, {"name": "Valid", "description": "x" * 501}):
            response = self.admin_client.post("/api/v1/tenant/groups", payload, format="json")
            self.assertEqual(response.status_code, 400)
        response = self.admin_client.patch(f"/api/v1/tenant/groups/{self.group.pk}", {"name": "x" * 101}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.admin_client.post(
            f"/api/v1/tenant/groups/{self.group.pk}/members", {"user_id": "invalid"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "研发部")
        self.assertEqual(TenantGroup.objects.filter(tenant=self.tenant).count(), 1)

    def test_changed_assistant_grants_invalidate_group_review(self):
        """The user must review newly affected assistant access before deleting."""
        preview = self.preview()
        AssistantAccess.objects.filter(group=self.auth_group).delete()
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertTrue(TenantGroup.objects.filter(pk=self.group.pk).exists())

    def test_group_deletion_audit_failure_rolls_back_all_selected_groups(self):
        """The whole batch, backing groups and grants survive a failed commit."""
        from unittest.mock import patch

        other = TenantGroup.objects.create(
            tenant=self.tenant, auth_group=Group.objects.create(name="rollback-test"), name="Other group"
        )
        preview = self.preview(self.group, other)
        with patch("accounts.services.group_management.record_audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaises(RuntimeError):
                self.confirm(preview)
        self.assertEqual(TenantGroup.objects.filter(pk__in=[self.group.pk, other.pk]).count(), 2)
        self.assertTrue(AssistantAccess.objects.filter(group=self.auth_group).exists())
