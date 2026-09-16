"""Base viewsets, renderers, and shared authentication helpers."""

from django.core.exceptions import PermissionDenied
from rest_framework import exceptions, permissions, viewsets
from rest_framework.renderers import BaseRenderer

from accounts.authentication import MCPRestrictedJWTAuthentication
from lens.lensnode_auth import token_matches
from lens.models import LensNode, Run


class BaseAuthenticatedViewSet(viewsets.ModelViewSet):
    """Base viewset requiring authentication."""

    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "uuid"


class BaseAdminViewSet(BaseAuthenticatedViewSet):
    """Base viewset requiring staff access."""

    permission_classes = [permissions.IsAdminUser]


class EventStreamRenderer(BaseRenderer):
    """Renderer used only for SSE content negotiation."""

    media_type = "text/event-stream"
    format = "event-stream"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        """Return bytes for DRF negotiation fallback paths."""

        del accepted_media_type, renderer_context
        return data or b""


def _authenticate_stream_request(request):
    """Authenticate a native Django SSE request with JWT."""

    try:
        authenticated = MCPRestrictedJWTAuthentication().authenticate(request)
    except exceptions.PermissionDenied as exc:
        # Native Django views do not run DRF's exception handler.
        raise PermissionDenied(str(exc.detail)) from exc
    except exceptions.AuthenticationFailed:
        return None
    if authenticated is not None:
        user, _ = authenticated
        return user
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def _get_user_run(run_uuid, user):
    """Return a run visible to the authenticated user."""

    return (
        Run.objects.filter(uuid=run_uuid, session__user=user)
        .select_related("output_message")
        .first()
    )


class LensNodeAuthMixin:
    """Shared LensNode bearer-token authentication for gateway views."""

    def _authenticate_lensnode(self, request):
        """Authenticate bearer token against approved LensNodes."""

        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        token = header.removeprefix("Bearer ").strip()
        for lensnode in LensNode.objects.filter(
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            token_revoked=False,
        ).exclude(auth_token_hash=""):
            if token_matches(lensnode, token):
                return lensnode
        return None
