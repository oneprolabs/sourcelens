"""Keep long-lived agent client tokens inside their read-only Q&A scope.

Agent credentials resolve to their owning user and are confined to the
routes in ``settings.AGENT_TOKEN_ALLOWED_ROUTES``; anything else is rejected
before permissions run.
"""

import re

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.authentication import BaseAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import AgentToken, TenantApiKey

AGENT_TOKEN_SCOPE = "agent"
AGENT_SCOPE_CLAIM = "scope"
TENANT_API_KEY_PRINCIPAL = "tenant_api_key"
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


_API_KEY_ROUTE_CACHE = {}


def _api_key_allowed_routes():
    """Return the compiled tenant API key route allowlist."""
    configured = tuple(
        (str(method).upper(), str(pattern))
        for method, pattern in getattr(
            settings, "TENANT_API_KEY_ALLOWED_ROUTES", ()
        )
    )
    cached = _API_KEY_ROUTE_CACHE.get("routes")
    if cached is None or cached[0] != configured:
        compiled = tuple(
            (method, re.compile(pattern))
            for method, pattern in configured
        )
        _API_KEY_ROUTE_CACHE["routes"] = (configured, compiled)
        cached = _API_KEY_ROUTE_CACHE["routes"]
    return cached[1]


def tenant_api_key_route_allowed(method, path):
    """Return whether a tenant API key may reach this request."""
    method = str(method or "").upper()
    path = str(path or "")
    if method == "HEAD":
        method = "GET"
    return any(
        (method == allowed_method or method == "OPTIONS") and pattern.fullmatch(path)
        for allowed_method, pattern in _api_key_allowed_routes()
    )


def tenant_api_key_allows_assistant(request, assistant_uuid):
    """Return whether the current API key may use one assistant."""
    auth = getattr(request, "auth", None)
    if not isinstance(auth, dict) or (
        auth.get("principal_type") != TENANT_API_KEY_PRINCIPAL
    ):
        return True
    if auth.get("scope") == TenantApiKey.Scope.ALL:
        return True
    return str(assistant_uuid) in {
        str(value) for value in (auth.get("assistant_uuids") or [])
    }


class TenantApiKeyAuthentication(BaseAuthentication):
    """Authenticate tenant API keys scoped to assistant QA routes."""

    def authenticate(self, request):
        """Resolve a slk_ bearer key and attach its tenant scope."""
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        raw_token = header.removeprefix("Bearer ").strip()
        if not raw_token.startswith(TenantApiKey.PREFIX):
            return None
        record = (
            TenantApiKey.objects.select_related("tenant")
            .filter(key_hash=TenantApiKey.digest(raw_token))
            .first()
        )
        if record is None:
            raise AuthenticationFailed("API_KEY_INVALID")
        if record.status != TenantApiKey.Status.ACTIVE:
            raise AuthenticationFailed("API_KEY_INACTIVE")
        if record.expires_at is not None and record.expires_at <= timezone.now():
            raise AuthenticationFailed("API_KEY_EXPIRED")
        if record.tenant.status != "active":
            raise AuthenticationFailed("TENANT_INACTIVE")
        if request.headers.get("X-Delegation-ID"):
            raise PermissionDenied("API_KEY_DELEGATION_FORBIDDEN")
        if not tenant_api_key_route_allowed(request.method, request.path):
            raise PermissionDenied("API_KEY_ROUTE_FORBIDDEN")
        request._request.tenant_api_key_call_identity = {
            "key_id": record.pk,
            "tenant_id": record.tenant_id,
            "key_version": record.key_version,
            "scope": record.scope,
            "assistant_uuids": record.assistant_uuids,
        }
        TenantApiKey.objects.filter(pk=record.pk).update(
            last_used_at=timezone.now()
        )
        from accounts.principals import ApiKeyPrincipal

        return ApiKeyPrincipal(record), {
            "principal_type": TENANT_API_KEY_PRINCIPAL,
            "api_key_id": str(record.uuid),
            "tenant_id": str(record.tenant_id),
            "scope": record.scope,
            "assistant_uuids": list(record.assistant_uuids or []),
        }

    def authenticate_header(self, request):
        """Return the challenge scheme for unauthenticated responses."""
        del request
        return "Bearer"


def tenant_api_key_selected_assistants(request):
    """Return selected assistant UUIDs for a restricted key, else None."""
    auth = getattr(request, "auth", None)
    if not isinstance(auth, dict) or (
        auth.get("principal_type") != TENANT_API_KEY_PRINCIPAL
    ):
        return None
    if auth.get("scope") == TenantApiKey.Scope.ALL:
        return None
    return {str(value) for value in (auth.get("assistant_uuids") or [])}
