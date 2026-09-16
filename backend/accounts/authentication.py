"""Keep long-lived agent client tokens inside their read-only Q&A scope.

The agent token is a normal access token for its owning user, so without a
guard it would inherit the full account privileges (including admin writes)
for its whole lifetime. Every agent-scoped token is therefore confined to the
routes in ``settings.AGENT_TOKEN_ALLOWED_ROUTES``; anything else is rejected
before permissions run.
"""

import re

from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

AGENT_TOKEN_SCOPE = "agent"
AGENT_SCOPE_CLAIM = "scope"
_UNRESTRICTED_METHODS = frozenset({"HEAD", "OPTIONS"})
_ROUTE_CACHE = {}


def _allowed_routes():
    """Return the compiled (method, pattern) allowlist, cached per config."""

    configured = tuple(
        (str(method).upper(), str(pattern))
        for method, pattern in getattr(
            settings, "AGENT_TOKEN_ALLOWED_ROUTES", ()
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


def agent_token_route_allowed(method, path):
    """Return whether an agent-scoped token may reach this request."""

    method = str(method or "").upper()
    if method in _UNRESTRICTED_METHODS:
        return True
    path = str(path or "")
    return any(
        method == allowed_method and pattern.fullmatch(path)
        for allowed_method, pattern in _allowed_routes()
    )


class AgentRestrictedJWTAuthentication(JWTAuthentication):
    """Reject agent-scoped tokens outside the read-only Q&A surface."""

    def authenticate(self, request):
        """Authenticate, then confine agent-scoped tokens to their allowlist."""

        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        try:
            scope = token.get(AGENT_SCOPE_CLAIM)
        except Exception:
            scope = None
        if scope != AGENT_TOKEN_SCOPE:
            return result
        if agent_token_route_allowed(request.method, request.path):
            return result
        raise PermissionDenied(
            detail="AGENT_TOKEN_SCOPE_RESTRICTED",
            code="agent_token_scope_restricted",
        )
