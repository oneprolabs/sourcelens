"""Request middleware for delegation context and safe API invocation accounting."""

import logging
import re
from time import monotonic
from uuid import UUID

from django.http import JsonResponse

from accounts.delegation import clear_delegation_id, get_active_delegation, set_delegation_id

logger = logging.getLogger(__name__)


class ApiKeyCallMiddleware:
    """Record actual execution requests after authentication and response handling."""

    def __init__(self, get_response):
        """Keep middleware construction independent of Django model initialization."""
        self.get_response = get_response

    def __call__(self, request):
        """Count invocations, excluding assistant discovery and run polling."""
        started = monotonic()
        response = self.get_response(request)
        identity = getattr(request, "tenant_api_key_call_identity", None)
        match = re.fullmatch(r"/api/lens/sessions/([0-9A-Fa-f-]{36})/runs/?", request.path)
        if identity and request.method == "POST" and match:
            from accounts.services.api_key_insights import record_invocation

            try:
                record_invocation(response, identity, match.group(1), monotonic() - started)
            except Exception:
                logger.warning("API key invocation accounting unavailable")
        return response


_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_PREFIXES = (
    "/api/v1/platform/",
    "/api/v1/tenant",
    "/api/v1/auth/",
    "/api/v1/admin/",
)


class DelegationContextMiddleware:
    """Bind the optional X-Delegation-ID header to one request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        delegation_id = request.META.get("HTTP_X_DELEGATION_ID")
        if delegation_id:
            try:
                delegation_id = UUID(delegation_id)
            except ValueError:
                return JsonResponse({"detail": "Invalid delegation ID."}, status=403)
        set_delegation_id(delegation_id)
        try:
            response = self.get_response(request)
            self._record_operation(request, response, delegation_id)
            return response
        finally:
            clear_delegation_id()

    def _record_operation(self, request, response, delegation_id):
        """Audit successful tenant-side writes performed during delegation."""
        if (
            not delegation_id
            or request.method not in _UNSAFE_METHODS
            or not 200 <= response.status_code < 400
            or request.path.startswith(_SKIP_PREFIXES)
        ):
            return
        delegation = get_active_delegation(getattr(request, "user", None))
        if delegation is None:
            return
        from accounts.audit import record_audit

        record_audit(
            action="delegation.operation",
            request=request,
            tenant=delegation.tenant,
            actor=delegation.platform_user,
            delegation=delegation,
            resource_type="request",
            metadata={
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
            },
        )
