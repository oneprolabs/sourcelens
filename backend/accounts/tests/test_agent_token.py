"""Long-lived agent client token issuance tests."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.authentication import agent_token_route_allowed

TOKEN_URL = "/api/v1/auth/agent/token"


@override_settings(ROOT_URLCONF="accounts.tests.urls")
class AgentTokenTests(TestCase):
    """Mint a long-lived, agent-scoped access token for authenticated users."""

    def setUp(self):
        """Create an authenticated client for one user."""
        self.user = User.objects.create_user(
            username="agent-user",
            email="agent-user@example.com",
            password="Original7Qx9",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_anonymous_request_is_rejected(self):
        """The endpoint requires an authenticated user."""
        response = APIClient().post(TOKEN_URL)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_long_lived_agent_scoped_token(self):
        """The minted token carries the agent scope and the long lifetime."""
        response = self.client.post(TOKEN_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["token_type"], "Bearer")
        self.assertEqual(response.data["scope"], "agent")
        self.assertEqual(
            response.data["expires_in"],
            int(timedelta(days=30).total_seconds()),
        )

        token = AccessToken(response.data["access"])
        self.assertEqual(token["user_id"], self.user.pk)
        self.assertEqual(token["scope"], "agent")
        self.assertEqual(token["token_type"], "access")

    def test_custom_lifetime_is_honored(self):
        """AGENT_TOKEN_LIFETIME_DAYS overrides the default lifetime."""
        with override_settings(AGENT_TOKEN_LIFETIME_DAYS=7):
            response = self.client.post(TOKEN_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["expires_in"],
            int(timedelta(days=7).total_seconds()),
        )
        token = AccessToken(response.data["access"])
        self.assertEqual(token["exp"] - token["iat"], 7 * 24 * 3600)

    def test_requested_lifetime_months_is_honored(self):
        """A requested month option is treated as 30 days per month."""
        for months in (1, 3, 6):
            with self.subTest(months=months):
                response = self.client.post(
                    TOKEN_URL, {"lifetime_months": months}, format="json"
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                token = AccessToken(response.data["access"])
                self.assertEqual(
                    token["exp"] - token["iat"], months * 30 * 24 * 3600
                )

    def test_unlisted_lifetime_months_is_rejected(self):
        """Only the configured month options are accepted."""
        response = self.client.post(
            TOKEN_URL, {"lifetime_months": 2}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["error"], "AGENT_TOKEN_INVALID_LIFETIME"
        )

    def test_expires_at_matches_lifetime(self):
        """The reported expiry is about one lifetime away."""
        response = self.client.post(TOKEN_URL)

        expected = timezone.now().timestamp() + timedelta(
            days=30
        ).total_seconds()
        self.assertAlmostEqual(response.data["expires_at"], expected, delta=10)

    @override_settings(AGENT_TOKEN_LIFETIME_DAYS=0)
    def test_disabled_lifetime_rejects_issuance(self):
        """A non-positive lifetime turns the endpoint off."""
        response = self.client.post(TOKEN_URL)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"], "AGENT_TOKEN_DISABLED")

    def test_minted_token_authenticates_allowlisted_routes(self):
        """The token authenticates the read-only routes it is scoped to."""
        token = self.client.post(TOKEN_URL).data["access"]

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.get("/api/v1/auth/probe")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user_id"], self.user.pk)

    def test_minted_token_is_confined_to_allowlisted_routes(self):
        """Every other route is rejected even though the user is authorized."""
        token = self.client.post(TOKEN_URL).data["access"]

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.get("/api/v1/auth/user")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["detail"], "AGENT_TOKEN_SCOPE_RESTRICTED")
        self.assertEqual(
            response.data["detail"].code, "agent_token_scope_restricted"
        )

    def test_unscoped_token_keeps_full_user_access(self):
        """A normal access token is unaffected by the agent confinement."""
        token = str(AccessToken.for_user(self.user))

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.get("/api/v1/auth/user")

        self.assertNotEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AgentTokenRouteAllowlistTests(SimpleTestCase):
    """The allowlist admits only the read-only Q&A surface."""

    RUN = "2f6c1a3e-1f5a-4d4e-9a3a-0e6b7c8d9e0f"
    ALLOWED = (
        ("POST", "/api/lens/sessions/"),
        ("POST", "/api/lens/sessions"),
        ("POST", f"/api/lens/sessions/{RUN}/runs/"),
        ("GET", f"/api/lens/runs/{RUN}/"),
        ("GET", "/api/lens/assistants/"),
        ("GET", f"/api/lens/assistants/{RUN}/"),
        ("OPTIONS", "/api/lens/admin/global-settings/"),
        ("HEAD", "/api/lens/admin/global-settings/"),
    )
    DENIED = (
        ("GET", "/api/lens/admin/global-settings/"),
        ("GET", "/api/lens/admin/mcp-servers/"),
        ("GET", "/api/lens/admin/environment-variable-sets/"),
        ("POST", "/api/lens/admin/skills/"),
        ("POST", "/api/lens/assistants/"),
        ("GET", "/api/lens/runs/"),
        ("POST", f"/api/lens/runs/{RUN}/cancel/"),
        ("DELETE", "/api/lens/sessions/"),
        ("PUT", f"/api/lens/assistants/{RUN}/"),
        ("GET", f"/api/lens/assistants/{RUN}/qa/"),
        ("GET", f"/api/lens/runs/{RUN}/extra/"),
    )

    def test_allowlisted_routes_are_reachable(self):
        for method, path in self.ALLOWED:
            with self.subTest(method=method, path=path):
                self.assertTrue(agent_token_route_allowed(method, path))

    def test_everything_else_is_rejected(self):
        for method, path in self.DENIED:
            with self.subTest(method=method, path=path):
                self.assertFalse(agent_token_route_allowed(method, path))
