"""Keep legacy global account operations outside tenant identities."""

from types import SimpleNamespace
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from accounts.delegation import clear_delegation_id, set_delegation_id
from accounts.models import Role, Tenant, TenantDelegation, TenantMembership
from accounts.principals import ApiKeyPrincipal
from accounts.views import management


class LegacyManagementBoundaryTests(TestCase):
    """Exercise all legacy read and write entry points before mutation."""

    def setUp(self):
        """Create independent legacy and tenant administrators."""
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Boundary", slug="legacy-boundary")
        self.legacy = get_user_model().objects.create_user(username="legacy-manager", is_staff=True)
        self.admin = get_user_model().objects.create_user(username="tenant-manager")
        self.membership = TenantMembership.objects.create(user=self.admin, tenant=self.tenant, role="tenant_admin")
        self.target = get_user_model().objects.create_user(username="protected-user")
        self.group = Group.objects.create(name="protected-group")
        self.role = Role.objects.create(name="protected-role")
        self.addCleanup(clear_delegation_id)

    def endpoints(self):
        """Cover each method exposed by the nine global views."""
        return [
            (management.ManagementUserListView, "get", {}, {}),
            (management.ManagementUserListView, "post", {}, {"username": "forbidden-created"}),
            (management.ManagementUserDetailView, "patch", {"user_id": self.target.pk}, {"is_active": False}),
            (management.ManagementUserBulkView, "post", {}, {"ids": [self.target.pk], "is_active": False}),
            (management.ManagementGroupListView, "get", {}, {}),
            (management.ManagementGroupListView, "post", {}, {"name": "forbidden-created"}),
            (management.ManagementGroupDetailView, "patch", {"group_id": self.group.pk}, {"name": "forbidden-edit"}),
            (management.ManagementGroupDetailView, "delete", {"group_id": self.group.pk}, {}),
            (management.ManagementGroupBulkDeleteView, "post", {}, {"ids": [self.group.pk]}),
            (management.ManagementRoleListView, "get", {}, {}),
            (management.ManagementRoleListView, "post", {}, {"name": "forbidden-created"}),
            (management.ManagementRoleDetailView, "patch", {"role_id": self.role.pk}, {"is_active": False}),
            (management.ManagementRoleBulkView, "post", {}, {"ids": [self.role.pk], "is_active": False}),
        ]

    def assert_denied(self, user):
        """Reject reads and writes before resolving or changing global objects."""
        for view, method, kwargs, body in self.endpoints():
            with self.subTest(view=view.__name__, method=method):
                request = getattr(self.factory, method)("/api/v1/management/", body, format="json")
                force_authenticate(request, user=user)
                response = view.as_view()(request, **kwargs)
                self.assertEqual(response.status_code, 403, response.data)
        self.target.refresh_from_db()
        self.group.refresh_from_db()
        self.role.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertTrue(self.role.is_active)
        self.assertEqual(self.group.name, "protected-group")
        self.assertFalse(get_user_model().objects.filter(username="forbidden-created").exists())
        self.assertFalse(Group.objects.filter(name="forbidden-created").exists())

    def test_tenant_administrator_cannot_access_global_accounts(self):
        """Tenant admin features do not authorize the legacy global API."""
        self.assert_denied(self.admin)

    def test_staff_membership_cannot_bypass_tenant_boundary(self):
        """A staff flag does not erase tenant membership."""
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.assert_denied(self.admin)

    def test_inactive_membership_cannot_fall_back_to_global_access(self):
        """Suspending membership does not grant legacy privileges."""
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.membership.status = "disabled"
        self.membership.save(update_fields=["status"])
        self.assert_denied(self.admin)

    def test_invalid_delegation_requests_fail_closed(self):
        """Any request carrying delegation must not reach the global API."""
        set_delegation_id(str(uuid4()))
        self.assert_denied(self.legacy)

    def test_active_delegation_cannot_reach_legacy_global_operations(self):
        """Neither support mode grants global account management in a tenant."""
        self.legacy.is_superuser = True
        self.legacy.save(update_fields=["is_superuser"])
        delegation = TenantDelegation.objects.create(
            platform_user=self.legacy,
            tenant=self.tenant,
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        for mode in ("readonly", "operational"):
            delegation.mode = mode
            delegation.save(update_fields=["mode"])
            set_delegation_id(str(delegation.uuid))
            self.assert_denied(self.legacy)

    def test_api_key_cannot_inherit_global_management_features(self):
        """Machine credentials are not global user administrators."""
        self.assert_denied(ApiKeyPrincipal(SimpleNamespace(pk=1, tenant=self.tenant)))

    def test_legacy_staff_retains_existing_global_list_access(self):
        """Preserve the non-tenant management contract during migration."""
        for view in (
            management.ManagementUserListView,
            management.ManagementGroupListView,
            management.ManagementRoleListView,
        ):
            request = self.factory.get("/api/v1/management/")
            force_authenticate(request, user=self.legacy)
            response = view.as_view()(request)
            self.assertEqual(response.status_code, 200, response.data)

    def test_legacy_staff_cannot_read_or_mutate_tenant_linked_records(self):
        """Legacy lists, details and bulk operations share the unowned scope."""
        self.admin.groups.add(self.group)
        self.role.users.add(self.admin)
        client = APIClient()
        client.force_authenticate(self.legacy)
        base = "/api/v1/management"
        for resource, target in (("users", self.admin), ("groups", self.group), ("roles", self.role)):
            with self.subTest(resource=resource):
                response = client.get(f"{base}/{resource}/")
                self.assertEqual(response.status_code, 200, response.data)
                self.assertNotIn(target.pk, {item["id"] for item in response.data["results"]})
        response = client.post(
            f"{base}/users/bulk-status/",
            {"user_ids": [self.target.pk, self.admin.pk], "is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.target.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertTrue(self.admin.is_active)
