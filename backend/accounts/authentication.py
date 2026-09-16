"""Keep long-lived MCP client tokens inside their read-only Q&A scope.

The MCP token is a normal access token for its owning user, so without a
guard it would inherit the full account privileges (including admin writes)
for its whole lifetime. Every mcp-scoped token is therefore confined to the
routes in ``settings.MCP_TOKEN_ALLOWED_ROUTES``; anything else is rejected
before permissions run.
"""

import re

from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

MCP_TOKEN_SCOPE = "mcp"
MCP_SCOPE_CLAIM = "scope"
_UNRESTRICTED_METHODS = frozenset({"HEAD", "OPTIONS"})
_ROUTE_CACHE = {}


def _allowed_routes():
    """Return the compiled (method, pattern) allowlist, cached per config."""

    configured = tuple(
        (str(method).upper(), str(pattern))
        for method, pattern in getattr(
            settings, "MCP_TOKEN_ALLOWED_ROUTES", ()
        )
    )
    cached = _ROUTE_CACHE.get("routes")
    if cached is None or cached[0] != configured:
        compiled = tuple(
            (method, re.compile(pattern))
            for method, pattern in configured
        )
        _ROUTE_CACHE["routes"] = (configured, compiled)
        cached = _ROUTE_CACHE["routes"]
    return cached[1]


def mcp_token_route_allowed(method, path):
    """Return whether an MCP-scoped token may reach this request."""

    method = str(method or "").upper()
    if method in _UNRESTRICTED_METHODS:
        return True
    path = str(path or "")
    return any(
        method == allowed_method and pattern.fullmatch(path)
        for allowed_method, pattern in _allowed_routes()
    )


class MCPRestrictedJWTAuthentication(JWTAuthentication):
    """Reject MCP-scoped tokens outside the read-only Q&A surface."""

    def authenticate(self, request):
        """Authenticate, then confine mcp-scoped tokens to their allowlist."""

        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        try:
            scope = token.get(MCP_SCOPE_CLAIM)
        except Exception:
            scope = None
        if scope != MCP_TOKEN_SCOPE:
            return result
        if mcp_token_route_allowed(request.method, request.path):
            return result
        raise PermissionDenied(
            detail="MCP_TOKEN_SCOPE_RESTRICTED",
            code="mcp_token_scope_restricted",
        )
