from django.contrib.auth.models import Group, Permission, User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.access import (
    get_access_profile,
)
from accounts.models import Role
from accounts.serializers import UserDetailsSerializer


class AccessProfileTests(TestCase):
    def test_effective_roles_include_group_union(self):
        user = User.objects.create_user(
            username='alice',
            email='alice@example.com',
            password='password123',
        )
        group = Group.objects.create(name='operators')
        role = Role.objects.create(
            name='Admins',
            visible_features=['admin_console'],
            preferred_platform='admin_console',
        )
        role.groups.add(group)
        user.groups.add(group)

        access_profile = get_access_profile(user)

        self.assertEqual(
            access_profile['visible_features'],
            ['admin_console'],
        )
        self.assertEqual(
            access_profile['preferred_platform'],
            'admin_console',
        )
        self.assertEqual(
            access_profile['landing_path'],
            '/management/users',
        )

    def test_default_features_without_roles_exclude_admin_console(self):
        user = User.objects.create_user(
            username='bob',
            email='bob@example.com',
            password='password123',
        )

        access_profile = get_access_profile(user)

        self.assertEqual(
            access_profile['visible_features'],
            [
                'workspace',
            ],
        )

    def test_user_details_serializer_includes_effective_permissions(self):
        user = User.objects.create_user(
            username='dave',
            email='dave@example.com',
            password='password123',
        )
        permission = Permission.objects.get(
            content_type__app_label='auth',
            codename='change_user',
        )
        user.user_permissions.add(permission)

        data = UserDetailsSerializer(user).data

        self.assertIn('auth.change_user', data['permissions'])


class ManagementUserListQueryTests(TestCase):
    """Keep the management user list bounded as role-less users grow."""

    def _query_count(self, client, page_size):
        """Return the query count for one management user page request."""

        with CaptureQueriesContext(connection) as captured:
            response = client.get(
                '/api/v1/management/users/',
                {'page': 1, 'page_size': page_size},
            )
        self.assertEqual(response.status_code, 200)
        return len(captured.captured_queries)

    def test_users_without_roles_do_not_add_queries_per_user(self):
        """An empty effective-role list must not trigger a roles re-query."""

        admin = User.objects.create_user(
            username='list-admin',
            password='password123',
            is_staff=True,
        )
        client = APIClient()
        client.force_authenticate(admin)
        for index in range(10):
            User.objects.create_user(
                username=f'operator-{index}',
                password='password123',
            )

        baseline = self._query_count(client, page_size=1)
        expanded = self._query_count(client, page_size=1000)

        self.assertLessEqual(expanded, baseline + 1)
        self.assertLessEqual(expanded, 6)
