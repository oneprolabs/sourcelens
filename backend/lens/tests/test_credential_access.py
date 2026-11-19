"""Access-control regression tests for datasource credentials."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role
from lens.models import DataSource, DataSourceCredential


class DataSourceCredentialAccessTests(TestCase):
    """Credential metadata and plaintext are staff-admin only in S0."""

    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="credential-staff",
            email="credential-staff@example.com",
            password="x",
            is_staff=True,
        )
        self.member = user_model.objects.create_user(
            username="credential-member",
            email="credential-member@example.com",
            password="x",
        )
        self.credential = DataSourceCredential.objects.create(
            name="GitHub token",
            provider=DataSourceCredential.Provider.GITHUB,
            auth_type=DataSourceCredential.AuthType.HTTPS_TOKEN,
            endpoint_url="https://github.com",
        )
        self.credential.set_secret("ghp_secret_value")
        self.credential.save(update_fields=["encrypted_secret"])
        self.list_url = "/api/lens/admin/credentials/"
        self.detail_url = f"{self.list_url}{self.credential.uuid}/"
        self.reveal_url = f"{self.detail_url}reveal/"

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_anonymous_cannot_list_or_reveal(self):
        anonymous = APIClient()

        self.assertIn(
            anonymous.get(self.list_url).status_code,
            (401, 403),
        )
        self.assertIn(
            anonymous.post(self.reveal_url).status_code,
            (401, 403),
        )

    def test_member_cannot_list_or_reveal(self):
        client = self._client(self.member)

        self.assertEqual(client.get(self.list_url).status_code, 403)
        self.assertEqual(client.post(self.reveal_url).status_code, 403)

    def test_member_cannot_mutate_credentials(self):
        client = self._client(self.member)

        create = client.post(
            self.list_url,
            {
                "name": "Created by member",
                "provider": DataSourceCredential.Provider.GITHUB,
                "auth_type": DataSourceCredential.AuthType.HTTPS_TOKEN,
                "secret": "ghp_member_value",
            },
            format="json",
        )
        patch = client.patch(
            self.detail_url,
            {"name": "Renamed by member"},
            format="json",
        )
        delete = client.delete(self.detail_url)

        self.assertEqual(create.status_code, 403)
        self.assertEqual(patch.status_code, 403)
        self.assertEqual(delete.status_code, 403)

    def test_member_with_admin_console_role_is_still_denied(self):
        role = Role.objects.create(
            name="Credential admin role",
            visible_features=["admin_console"],
        )
        role.users.add(self.member)
        client = self._client(self.member)

        self.assertEqual(client.get(self.list_url).status_code, 403)
        self.assertEqual(client.post(self.reveal_url).status_code, 403)

    def test_staff_can_list_without_exposing_secret(self):
        client = self._client(self.staff)

        response = client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("secret", rows[0])
        self.assertNotIn("app_secret", rows[0])
        self.assertEqual(rows[0]["masked_secret"], "********")
        self.assertNotIn(
            "ghp_secret_value",
            json.dumps(response.data),
        )

    def test_staff_can_reveal_with_no_store_headers(self):
        client = self._client(self.staff)

        response = client.post(self.reveal_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["secret"], "ghp_secret_value")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Pragma"], "no-cache")

    def test_staff_can_create_update_and_delete(self):
        client = self._client(self.staff)

        created = client.post(
            self.list_url,
            {
                "name": "Created by staff",
                "provider": DataSourceCredential.Provider.GITLAB,
                "auth_type": DataSourceCredential.AuthType.HTTPS_TOKEN,
                "endpoint_url": "https://gitlab.com",
                "secret": "glpat_staff_value",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)

        credential_uuid = created.data["uuid"]
        updated = client.patch(
            f"{self.list_url}{credential_uuid}/",
            {"name": "Renamed by staff"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["name"], "Renamed by staff")

        deleted = client.delete(f"{self.list_url}{credential_uuid}/")
        self.assertEqual(deleted.status_code, 204)

    def test_validate_requires_staff(self):
        member = self._client(self.member)
        staff = self._client(self.staff)

        self.assertEqual(
            member.post(f"{self.detail_url}validate/").status_code,
            403,
        )
        with patch(
            "lens.views.credentials."
            "_validate_datasource_credential_connectivity",
            return_value={"status": "success", "message": "ok"},
        ):
            response = staff.post(f"{self.detail_url}validate/")

        self.assertEqual(response.status_code, 200)

    def test_delete_rejects_credential_in_use(self):
        DataSource.objects.create(
            name="Repository",
            source_type=DataSource.SourceType.GIT,
            credential=self.credential,
        )
        client = self._client(self.staff)

        response = client.delete(self.detail_url)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "CREDENTIAL_IN_USE")
