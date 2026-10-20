"""Revocable agent access tokens and legacy JWT confinement tests."""

from datetime import timedelta
from hashlib import sha256

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.authentication import agent_token_route_allowed
from accounts.models import AgentToken

TOKEN_URL = "/api/v1/auth/agent/token"
TOKENS_URL = "/api/v1/auth/agent/tokens"
AGENT_TOKEN_ALLOWED_ROUTES = tuple(settings.AGENT_TOKEN_ALLOWED_ROUTES) + (("GET", r"/api/v1/auth/probe"),)


@override_settings(ROOT_URLCONF="accounts.tests.urls", AGENT_TOKEN_ALLOWED_ROUTES=AGENT_TOKEN_ALLOWED_ROUTES)
class AgentTokenTests(TestCase):
    """Issue, authenticate, list and revoke per-user agent tokens."""

    def setUp(self):
        """Create an authenticated client for one user."""
        self.user = User.objects.create_user(username="agent-user", password="Original7Qx9")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def issue(self, months=None):
        """Issue a token through the public API."""
        payload = {} if months is None else {"lifetime_months": months}
        response = self.client.post(TOKEN_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def bearer_client(self, token):
        """Return a client authenticated by the supplied bearer value."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def test_anonymous_requests_are_rejected(self):
        """Issuance and token management require web authentication."""
        for method, url in (
            ("post", TOKEN_URL),
            ("get", TOKENS_URL),
            ("delete", f"{TOKENS_URL}/00000000-0000-0000-0000-000000000001"),
        ):
            with self.subTest(method=method):
                self.assertEqual(getattr(APIClient(), method)(url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_only_token_hash_is_stored(self):
        """The full opaque credential is returned once and never persisted."""
        payload = self.issue()
        token = AgentToken.objects.get(pk=payload["id"])
        self.assertTrue(payload["access"].startswith("sl_agent_"))
        self.assertEqual(payload["token_type"], "Bearer")
        self.assertEqual(payload["scope"], "agent")
        self.assertEqual(token.user, self.user)
        self.assertEqual(token.token_hash, sha256(payload["access"].encode()).hexdigest())
        self.assertNotIn(payload["access"], str(token.__dict__))
        listing = self.client.get(TOKENS_URL)
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertNotIn(payload["access"], str(listing.data))
        self.assertNotIn(token.token_hash, str(listing.data))
        self.assertEqual(str(listing.data["tokens"][0]["id"]), str(token.pk))

    def test_default_and_month_lifetimes_are_honored(self):
        """Finite validity retains the existing day and month settings."""
        for months in (None, 1, 3, 6):
            with self.subTest(months=months):
                payload = self.issue(months)
                lifetime = timedelta(days=(months or 1) * 30)
                token = AgentToken.objects.get(pk=payload["id"])
                self.assertEqual(payload["expires_in"], int(lifetime.total_seconds()))
                self.assertAlmostEqual(token.expires_at.timestamp(), (timezone.now() + lifetime).timestamp(), delta=10)
                self.assertEqual(payload["expires_at"], int(token.expires_at.timestamp()))

    @override_settings(AGENT_TOKEN_LIFETIME_DAYS=7)
    def test_configured_default_lifetime_is_honored(self):
        """The configured day default still applies when no option is sent."""
        self.assertEqual(self.issue()["expires_in"], 7 * 24 * 3600)

    def test_zero_months_returns_permanent_token(self):
        """Permanent tokens have no expiry and remain server managed."""
        payload = self.issue(0)
        self.assertIsNone(payload["expires_in"])
        self.assertIsNone(payload["expires_at"])
        token = AgentToken.objects.get(pk=payload["id"])
        self.assertIsNone(token.expires_at)
        token.created_at = timezone.now() - timedelta(days=365 * 100)
        token.save(update_fields=["created_at"])
        response = self.bearer_client(payload["access"]).get("/api/v1/auth/probe")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user_id"], self.user.pk)

    def test_invalid_lifetimes_are_rejected(self):
        """Unlisted, negative and null validity values are rejected."""
        for months in (2, -1, None, "invalid"):
            with self.subTest(months=months):
                response = self.client.post(TOKEN_URL, {"lifetime_months": months}, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(AGENT_TOKEN_LIFETIME_DAYS=0)
    def test_disabled_lifetime_rejects_all_issuance(self):
        """The global disable also prevents permanent issuance."""
        for payload in ({}, {"lifetime_months": 0}):
            with self.subTest(payload=payload):
                response = self.client.post(TOKEN_URL, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["error"], "AGENT_TOKEN_DISABLED")

    def test_minted_tokens_remain_scoped(self):
        """Finite and permanent tokens cannot manage tokens or the account."""
        for months in (1, 0):
            with self.subTest(months=months):
                payload = self.issue(months)
                client = self.bearer_client(payload["access"])
                self.assertEqual(client.get("/api/v1/auth/probe").status_code, status.HTTP_200_OK)
                for method, url in (
                    ("get", "/api/v1/auth/user"),
                    ("get", TOKENS_URL),
                    ("post", TOKEN_URL),
                    ("delete", f"{TOKENS_URL}/{payload['id']}"),
                ):
                    response = getattr(client, method)(url)
                    self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                    self.assertEqual(response.data["detail"], "AGENT_TOKEN_SCOPE_RESTRICTED")

    def test_revocation_immediately_invalidates_only_selected_token(self):
        """Revocation affects the next request and leaves other tokens valid."""
        first, second = self.issue(0), self.issue(1)
        client = self.bearer_client(first["access"])
        self.assertEqual(client.get("/api/v1/auth/probe").status_code, status.HTTP_200_OK)
        url = f"{TOKENS_URL}/{first['id']}"
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertIsNotNone(AgentToken.objects.get(pk=first["id"]).revoked_at)
        self.assertEqual(client.get("/api/v1/auth/probe").status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.bearer_client(second["access"]).get("/api/v1/auth/probe").status_code, status.HTTP_200_OK)
        ids = [str(item["id"]) for item in self.client.get(TOKENS_URL).data["tokens"]]
        self.assertNotIn(str(first["id"]), ids)
        self.assertIn(str(second["id"]), ids)

    def test_other_users_cannot_list_or_revoke_tokens(self):
        """Token management is isolated to its owning user."""
        payload = self.issue(0)
        other = User.objects.create_user(username="other-agent-user")
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(TOKENS_URL).data["tokens"], [])
        self.assertEqual(self.client.delete(f"{TOKENS_URL}/{payload['id']}").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.bearer_client(payload["access"]).get("/api/v1/auth/probe").status_code, status.HTTP_200_OK
        )

    def test_expired_unknown_and_inactive_user_tokens_are_rejected(self):
        """Lookup checks expiry and the current account status every time."""
        payload = self.issue(1)
        token = AgentToken.objects.get(pk=payload["id"])
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save(update_fields=["expires_at"])
        self.assertEqual(
            self.bearer_client(payload["access"]).get("/api/v1/auth/probe").status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.assertEqual(self.client.get(TOKENS_URL).data["tokens"], [])
        permanent = self.issue(0)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        for raw in (permanent["access"], "sl_agent_unknown", permanent["access"][:-1]):
            self.assertEqual(
                self.bearer_client(raw).get("/api/v1/auth/probe").status_code, status.HTTP_401_UNAUTHORIZED
            )

    def test_legacy_scoped_jwt_remains_confined_and_finite(self):
        """Existing finite agent JWTs retain their previous behavior."""
        token = AccessToken.for_user(self.user)
        token["scope"] = "agent"
        client = self.bearer_client(str(token))
        self.assertEqual(client.get("/api/v1/auth/probe").status_code, status.HTTP_200_OK)
        self.assertEqual(client.get("/api/v1/auth/user").status_code, status.HTTP_403_FORBIDDEN)
        del token["exp"]
        token["never_expires"] = True
        self.assertEqual(
            self.bearer_client(str(token)).get("/api/v1/auth/probe").status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_unscoped_jwt_keeps_full_user_access(self):
        """Normal web JWT authentication is unchanged."""
        client = self.bearer_client(str(AccessToken.for_user(self.user)))
        self.assertEqual(client.get("/api/v1/auth/user").status_code, status.HTTP_200_OK)


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
