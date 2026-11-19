"""Tenant core models, helpers, and bootstrap migration tests."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.access import (
    get_user_membership,
    get_user_tenant,
    get_user_tenant_role,
    is_platform_super_admin,
)
from accounts.models import Profile, Role, Tenant, TenantMembership
from accounts.serializers import UserDetailsSerializer


bootstrap_migration = importlib.import_module(
    "accounts.migrations.0009_multitenancy"
)


class TenantCoreModelTests(TestCase):
    """Verify the one-user-one-tenant invariant and built-in roles."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tenant-member",
            email="tenant-member@example.com",
            password="x",
        )
        self.tenant = Tenant.objects.create(
            name="Acme",
            slug="acme",
        )

    def test_one_membership_per_user(self):
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.MEMBER,
        )
        other = Tenant.objects.create(name="Other", slug="other")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TenantMembership.objects.create(
                    tenant=other,
                    user=self.user,
                    role=TenantMembership.Role.MEMBER,
                )

    def test_access_helpers_resolve_membership_and_role(self):
        membership = TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.TENANT_ADMIN,
        )

        self.assertEqual(get_user_membership(self.user), membership)
        self.assertEqual(get_user_tenant(self.user), self.tenant)
        self.assertEqual(
            get_user_tenant_role(self.user),
            TenantMembership.Role.TENANT_ADMIN,
        )

    def test_helpers_return_empty_without_membership(self):
        self.assertIsNone(get_user_membership(self.user))
        self.assertIsNone(get_user_tenant(self.user))
        self.assertEqual(get_user_tenant_role(self.user), "")

    def test_user_details_include_tenant_and_roles(self):
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        Profile.objects.filter(user=self.user).update(
            platform_role=Profile.PlatformRole.NONE
        )

        data = UserDetailsSerializer(self.user).data

        self.assertEqual(data["tenant"]["slug"], "acme")
        self.assertEqual(
            data["tenant_role"],
            TenantMembership.Role.TENANT_ADMIN,
        )
        self.assertEqual(
            data["platform_role"],
            Profile.PlatformRole.NONE,
        )

    def test_platform_super_admin_comes_from_profile(self):
        profile, _ = Profile.objects.get_or_create(user=self.user)

        self.assertFalse(is_platform_super_admin(self.user))

        profile.platform_role = Profile.PlatformRole.SUPER_ADMIN
        profile.save(update_fields=["platform_role"])
        self.user = get_user_model().objects.get(pk=self.user.pk)

        self.assertTrue(is_platform_super_admin(self.user))


class BootstrapTenantMigrationTests(TestCase):
    """Verify existing users map to the built-in roles."""

    def test_bootstrap_maps_staff_admin_console_and_member(self):
        user_model = get_user_model()
        staff = user_model.objects.create_user(
            username="bootstrap-staff",
            email="bootstrap-staff@example.com",
            password="x",
            is_staff=True,
        )
        role_admin = user_model.objects.create_user(
            username="bootstrap-role-admin",
            email="bootstrap-role-admin@example.com",
            password="x",
        )
        member = user_model.objects.create_user(
            username="bootstrap-member",
            email="bootstrap-member@example.com",
            password="x",
        )
        role = Role.objects.create(
            name="Bootstrap admin console",
            visible_features=["admin_console"],
        )
        role.users.add(role_admin)

        bootstrap_migration.bootstrap_default_tenant(apps, None)

        tenant = Tenant.objects.get(slug="default")
        staff_membership = TenantMembership.objects.get(user=staff)
        admin_membership = TenantMembership.objects.get(user=role_admin)
        member_membership = TenantMembership.objects.get(user=member)

        self.assertEqual(staff_membership.tenant, tenant)
        self.assertEqual(
            staff_membership.role,
            TenantMembership.Role.TENANT_ADMIN,
        )
        self.assertEqual(
            Profile.objects.get(user=staff).platform_role,
            Profile.PlatformRole.SUPER_ADMIN,
        )
        self.assertEqual(
            admin_membership.role,
            TenantMembership.Role.TENANT_ADMIN,
        )
        self.assertEqual(
            Profile.objects.get(user=role_admin).platform_role,
            Profile.PlatformRole.NONE,
        )
        self.assertEqual(
            member_membership.role,
            TenantMembership.Role.MEMBER,
        )
        self.assertEqual(
            Profile.objects.get(user=member).platform_role,
            Profile.PlatformRole.NONE,
        )
