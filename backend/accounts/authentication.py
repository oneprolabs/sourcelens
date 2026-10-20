"""Keep long-lived agent client tokens inside their read-only Q&A scope.

Agent credentials resolve to their owning user and are confined to the
routes in ``settings.AGENT_TOKEN_ALLOWED_ROUTES``; anything else is rejected
before permissions run.
"""

import re

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import AgentToken

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
    """Authenticate revocable credentials and retain legacy JWT support."""

    def authenticate(self, request):
        """Authenticate, then confine agent-scoped tokens to their allowlist."""

        header = self.get_header(request)
        raw_token = self.get_raw_token(header) if header is not None else None
        if raw_token is not None and raw_token.startswith(AgentToken.PREFIX.encode("ascii")):
            token = AgentToken.objects.select_related("user").filter(
                token_hash=AgentToken.digest(raw_token),
                revoked_at__isnull=True,
            ).first()
            if token is None or not token.user.is_active or (
                token.expires_at is not None and token.expires_at <= timezone.now()
            ):
                raise AuthenticationFailed("AGENT_TOKEN_INVALID", code="agent_token_invalid")
            result = (token.user, {AGENT_SCOPE_CLAIM: AGENT_TOKEN_SCOPE, "token_id": str(token.pk)})
        else:
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
