"""Exercise assistant visibility and shared-Q&A tenant boundaries."""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantDelegation, TenantGroup, TenantMembership
from lens.models import Assistant, AssistantAccess, SharedQA, SharedQAFile
from lens.serializers import AssistantSerializer


class ShareTenancyTests(TestCase):
    """Shares must never widen the owning assistant's audience."""

    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="A", slug="share-a")
        self.other_tenant = Tenant.objects.create(name="B", slug="share-b")
        self.admin = self.member("admin", self.tenant, "tenant_admin")
        self.peer = self.member("peer", self.tenant)
        self.other = self.member("other", self.other_tenant, "tenant_admin")
        self.staff = get_user_model().objects.create_user(username="share-staff", is_staff=True)
        self.platform = get_user_model().objects.create_user(username="share-platform")
        Profile.objects.filter(user=self.platform).update(platform_role="super_admin")
        self.assistant = Assistant.objects.create(
            name="A", slug="tenant-share-a", tenant=self.tenant, visibility="tenant"
        )
        self.other_assistant = Assistant.objects.create(
            name="B", slug="tenant-share-b", tenant=self.other_tenant, visibility="public"
        )
        self.share = self.make_share(self.assistant, "tenant-link", self.peer)
        self.other_share = self.make_share(self.other_assistant, "foreign-link", self.other)

    def member(self, name, tenant, role="member"):
        """Create one member without using Django staff as authorization."""
        user = get_user_model().objects.create_user(username=f"share-{name}")
        TenantMembership.objects.create(user=user, tenant=tenant, role=role)
        return user

    def make_share(self, assistant, token, publisher):
        """Create a link-only published snapshot."""
        return SharedQA.objects.create(
            assistant=assistant,
            token=token,
            question="Question",
            answer="Tenant content",
            assistant_name=assistant.name,
            assistant_slug=assistant.slug,
            title=token,
            published_by=publisher,
            published_at=timezone.now(),
            content_language="en-US",
        )

    def test_tenant_visibility_uses_active_membership_and_matches_collection(self):
        for viewer, allowed in [
            (self.peer, True),
            (self.admin, True),
            (self.other, False),
            (self.staff, False),
            (self.platform, False),
            (AnonymousUser(), False),
        ]:
            with self.subTest(viewer=str(viewer)):
                self.assertEqual(self.assistant.is_accessible_by(viewer), allowed)
                self.assertEqual(Assistant.objects.visible_to(viewer).filter(pk=self.assistant.pk).exists(), allowed)

    def test_restricted_grants_cannot_cross_a_tenant_boundary(self):
        self.assistant.visibility = "private"
        self.assistant.save(update_fields=["visibility"])
        AssistantAccess.objects.create(assistant=self.assistant, user=self.other)
        self.assertFalse(self.assistant.is_accessible_by(self.other))
        self.assertFalse(Assistant.objects.visible_to(self.other).filter(pk=self.assistant.pk).exists())

    def test_suspended_member_and_tenant_lose_access_even_with_grants(self):
        AssistantAccess.objects.create(assistant=self.assistant, user=self.peer)
        TenantMembership.objects.filter(user=self.peer).update(status="disabled")
        self.peer = get_user_model().objects.get(pk=self.peer.pk)
        self.assertFalse(self.assistant.is_accessible_by(self.peer))
        self.assertFalse(Assistant.objects.visible_to(self.peer).filter(pk=self.assistant.pk).exists())
        self.tenant.status = "suspended"
        self.tenant.save(update_fields=["status"])
        self.assistant.visibility = "public"
        self.assistant.save(update_fields=["visibility"])
        self.assertFalse(self.assistant.is_accessible_by(AnonymousUser()))
        self.assertFalse(Assistant.objects.visible_to(AnonymousUser()).filter(pk=self.assistant.pk).exists())

    def test_public_share_defaults_to_assistant_permissions_and_requires_same_tenant(self):
        for viewer, expected in [(self.peer, 200), (self.other, 403), (None, 403)]:
            self.client.force_authenticate(viewer)
            response = self.client.get(f"/api/lens/public/qa/{self.share.token}/")
            self.assertEqual(response.status_code, expected)
        self.client.force_authenticate(self.peer)
        self.assistant.visibility = "public"
        self.assistant.save(update_fields=["visibility"])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/lens/public/qa/{self.share.token}/").status_code, 403)

    def test_explicit_public_share_and_assistant_are_both_required_for_anonymous_access(self):
        self.share.require_login = False
        self.share.save(update_fields=["require_login"])
        self.assertEqual(self.client.get(f"/api/lens/public/qa/{self.share.token}/").status_code, 403)
        self.assistant.visibility = "public"
        self.assistant.save(update_fields=["visibility"])
        self.assertEqual(self.client.get(f"/api/lens/public/qa/{self.share.token}/").status_code, 200)

    def test_tenant_admin_reviews_only_own_tenant_shares(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/lens/admin/shares/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["token"] for row in response.data["results"]], [self.share.token])
        self.assertEqual(self.client.get(f"/api/lens/admin/shares/{self.other_share.uuid}/").status_code, 404)
        self.assertEqual(
            self.client.patch(
                f"/api/lens/admin/shares/{self.other_share.uuid}/", {"is_listed": True}, format="json"
            ).status_code,
            404,
        )
        accepted = self.client.patch(f"/api/lens/admin/shares/{self.share.uuid}/", {"is_listed": True}, format="json")
        self.assertEqual(accepted.status_code, 200)
        self.share.refresh_from_db()
        self.assertTrue(self.share.is_listed)

    def test_public_assistant_does_not_move_foreign_conversations_into_moderation(self):
        """A public assistant's tenant cannot moderate another tenant's Q&A."""
        self.assistant.visibility = "public"
        self.assistant.save(update_fields=["visibility"])
        foreign_share = self.make_share(self.assistant, "foreign-publisher", self.other)
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/lens/admin/shares/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["token"] for row in response.data["results"]], [self.share.token])
        self.assertEqual(self.client.get(f"/api/lens/admin/shares/{foreign_share.uuid}/").status_code, 404)
        overview = self.client.get("/api/v1/tenant/overview")
        self.assertEqual(overview.data["attention"]["pending_share_count"], 1)

    def test_staff_member_and_platform_admin_cannot_review_without_tenant_authority(self):
        for user in (self.staff, self.peer, self.platform):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get("/api/lens/admin/shares/").status_code, 403)

    def test_readonly_delegation_reads_target_tenant_and_rejects_review_writes(self):
        delegation = TenantDelegation.objects.create(
            platform_user=self.platform,
            tenant=self.tenant,
            mode="readonly",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        self.client.force_authenticate(self.platform)
        headers = {"HTTP_X_DELEGATION_ID": str(delegation.uuid)}
        response = self.client.get("/api/lens/admin/shares/", **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["token"] for row in response.data["results"]], [self.share.token])
        self.assertEqual(
            self.client.patch(
                f"/api/lens/admin/shares/{self.share.uuid}/", {"is_listed": True}, format="json", **headers
            ).status_code,
            403,
        )

    def test_policy_lookup_rejects_inaccessible_assistants(self):
        self.client.force_authenticate(self.other)
        response = self.client.get("/api/lens/shares/policy/", {"assistant_uuid": str(self.assistant.uuid)})
        self.assertEqual(response.status_code, 404)

    def test_restricted_user_grants_reject_foreign_members_atomically(self):
        serializer = AssistantSerializer(context={"request": SimpleNamespace(user=self.admin)})
        AssistantAccess.objects.create(assistant=self.assistant, user=self.peer)
        with self.assertRaises(ValidationError):
            serializer._sync_access_grants(self.assistant, [{"type": "user", "id": self.other.pk}])
        self.assertTrue(self.assistant.access_grants.filter(user=self.peer).exists())

    def test_public_gallery_exposes_only_links_the_visitor_can_open(self):
        self.assistant.visibility = "public"
        self.assistant.save(update_fields=["visibility"])
        self.share.is_listed = True
        self.share.save(update_fields=["is_listed"])
        open_share = self.make_share(self.assistant, "explicit-open", self.peer)
        open_share.is_listed = True
        open_share.require_login = False
        open_share.save(update_fields=["is_listed", "require_login"])
        url = f"/api/lens/public/assistants/{self.assistant.slug}/qa/"
        for viewer, expected in [
            (None, [open_share.token]),
            (self.other, [open_share.token]),
            (self.peer, [open_share.token, self.share.token]),
        ]:
            self.client.force_authenticate(viewer)
            response = self.client.get(url, HTTP_ACCEPT_LANGUAGE="en")
            self.assertEqual(response.status_code, 200)
            self.assertCountEqual([row["token"] for row in response.data["results"]], expected)

    def test_cross_tenant_and_anonymous_cannot_export_or_read_share_files(self):
        file = SharedQAFile.objects.create(
            share=self.share, kind="output", filename="private.txt", file="missing-private.txt"
        )
        for viewer in (None, self.other):
            self.client.force_authenticate(viewer)
            for suffix in ("pdf/", f"files/{file.uuid}/"):
                response = self.client.get(f"/api/lens/public/qa/{self.share.token}/{suffix}")
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("Tenant content", str(response.data))

    def test_review_records_metadata_without_snapshot_content(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f"/api/lens/admin/shares/{self.share.uuid}/", {"is_listed": True}, format="json")
        self.assertEqual(response.status_code, 200)
        audit = AuditLog.objects.get(action="share.review")
        self.assertEqual(audit.tenant_id, self.tenant.pk)
        self.assertNotIn("Tenant content", str(audit.metadata))

    def test_overview_counts_only_pending_published_shares_of_this_tenant(self):
        self.other_share.is_listed = False
        self.other_share.save(update_fields=["is_listed"])
        hidden = self.make_share(self.assistant, "hidden-share", self.peer)
        hidden.status = "hidden"
        hidden.save(update_fields=["status"])
        self.client.force_authenticate(self.admin)
        data = self.client.get("/api/v1/tenant/overview").data
        self.assertEqual(data["attention"]["pending_share_count"], 1)
        self.assertNotIn("Tenant content", str(data))

    def test_policy_reports_effective_assistant_restriction_and_tenant_default(self):
        self.client.force_authenticate(self.peer)
        url = "/api/lens/shares/policy/"
        data = self.client.get(url, {"assistant_uuid": str(self.assistant.uuid)}).data
        self.assertEqual(data["effective_policy"], "login")
        self.assertEqual(data["source"], "visibility")
        self.assistant.visibility = "public"
        self.assistant.save(update_fields=["visibility"])
        data = self.client.get(url, {"assistant_uuid": str(self.assistant.uuid)}).data
        self.assertEqual(data["effective_policy"], "login")
        self.assertTrue(data["requires_choice"])

    def test_private_groups_must_belong_to_the_assistants_tenant(self):
        self.assistant.visibility = "private"
        self.assistant.save(update_fields=["visibility"])
        foreign_group = Group.objects.create(name="foreign-tenant-share")
        TenantGroup.objects.create(tenant=self.other_tenant, auth_group=foreign_group, name="Foreign")
        self.peer.groups.add(foreign_group)
        AssistantAccess.objects.create(assistant=self.assistant, group=foreign_group)
        self.assertFalse(self.assistant.is_accessible_by(self.peer))
        own_group = Group.objects.create(name="own-tenant-share")
        TenantGroup.objects.create(tenant=self.tenant, auth_group=own_group, name="Own")
        self.peer.groups.add(own_group)
        AssistantAccess.objects.create(assistant=self.assistant, group=own_group)
        self.assertTrue(self.assistant.is_accessible_by(self.peer))

    def test_operational_delegation_can_review_and_audit_identifies_delegation(self):
        delegation = TenantDelegation.objects.create(
            platform_user=self.platform,
            tenant=self.tenant,
            mode="operational",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        self.client.force_authenticate(self.platform)
        response = self.client.patch(
            f"/api/lens/admin/shares/{self.share.uuid}/",
            {"is_listed": True},
            format="json",
            HTTP_X_DELEGATION_ID=str(delegation.uuid),
        )
        self.assertEqual(response.status_code, 200)
        audit = AuditLog.objects.get(action="share.review")
        self.assertEqual(audit.delegation_id, delegation.pk)
        self.assertEqual(audit.tenant_id, self.tenant.pk)

    def test_restricted_share_ignores_public_setting_and_respects_grant_revocation(self):
        from lens.models import GlobalSetting

        self.assistant.visibility = "private"
        self.assistant.save(update_fields=["visibility"])
        GlobalSetting.objects.create(key="lens.public_share.policy", value="public")
        grant = AssistantAccess.objects.create(assistant=self.assistant, user=self.peer)
        self.client.force_authenticate(self.peer)
        url = f"/api/lens/public/qa/{self.share.token}/"
        self.assertEqual(self.client.get(url).status_code, 200)
        grant.delete()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_moderation_rejects_suspended_membership_tenant_and_expired_delegation(self):
        TenantMembership.objects.filter(user=self.admin).update(status="disabled")
        self.client.force_authenticate(get_user_model().objects.get(pk=self.admin.pk))
        self.assertEqual(self.client.get("/api/lens/admin/shares/").status_code, 403)
        TenantMembership.objects.filter(user=self.admin).update(status="active")
        Tenant.objects.filter(pk=self.tenant.pk).update(status="suspended")
        self.client.force_authenticate(get_user_model().objects.get(pk=self.admin.pk))
        self.assertEqual(self.client.get("/api/lens/admin/shares/").status_code, 403)
        delegation = TenantDelegation.objects.create(
            platform_user=self.platform,
            tenant=self.other_tenant,
            mode="operational",
            expires_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        self.client.force_authenticate(self.platform)
        self.assertEqual(
            self.client.get("/api/lens/admin/shares/", HTTP_X_DELEGATION_ID=str(delegation.uuid)).status_code, 403
        )
