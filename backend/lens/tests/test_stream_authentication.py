"""Scope enforcement at native SSE HTTP boundaries."""

from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase
from rest_framework_simplejwt.tokens import AccessToken

from lens.views.base import _authenticate_stream_request


class StreamAuthenticationTests(SimpleTestCase):
    """Native streams must enforce MCP scope before user permissions."""

    paths = (
        "/api/lens/runs/00000000-0000-0000-0000-000000000001/stream/",
        "/api/lens/admin/runs/00000000-0000-0000-0000-000000000001/"
        "trajectory/stream/",
    )

    def setUp(self):
        """Mock account lookup while exercising real JWT verification."""

        self.user = SimpleNamespace(is_authenticated=True, is_staff=True)
        lookup = patch(
            "rest_framework_simplejwt.authentication.JWTAuthentication."
            "get_user",
            return_value=self.user,
        )
        lookup.start()
        self.addCleanup(lookup.stop)

    def token(self, scoped=True):
        """Return a signed token without querying the database."""

        token = AccessToken()
        token["user_id"] = 1
        if scoped:
            token["scope"] = "mcp"
        return str(token)

    def test_mcp_tokens_receive_http_403_on_both_streams(self):
        """A scope rejection is a forbidden response, not a server error."""

        for path in self.paths:
            with self.subTest(path=path):
                response = self.client.get(
                    path, HTTP_AUTHORIZATION=f"Bearer {self.token()}"
                )
                self.assertEqual(response.status_code, 403)

    def test_session_user_cannot_bypass_bearer_scope(self):
        """A session cookie cannot override an explicitly scoped bearer."""

        request = RequestFactory().get(
            self.paths[0], HTTP_AUTHORIZATION=f"Bearer {self.token()}"
        )
        request.user = self.user
        with self.assertRaises(PermissionDenied):
            _authenticate_stream_request(request)

    def test_normal_tokens_and_session_only_requests_keep_access(self):
        """Ordinary JWT and session authentication remain supported."""

        request = RequestFactory().get(
            self.paths[0], HTTP_AUTHORIZATION=f"Bearer {self.token(False)}"
        )
        self.assertIs(_authenticate_stream_request(request), self.user)
        session_request = RequestFactory().get(self.paths[0])
        session_request.user = self.user
        self.assertIs(
            _authenticate_stream_request(session_request), self.user
        )

    def test_anonymous_stream_requests_return_401(self):
        """Anonymous callers remain unauthorized."""

        for path in self.paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
