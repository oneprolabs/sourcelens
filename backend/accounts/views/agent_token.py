"""Long-lived JWT issuance for external SourceLens agent clients.

Coding agents (Codex, Claude) cannot run the interactive refresh flow used
by the web app, so they receive a single long-lived access token minted
from the user settings page. The token is scoped by a ``scope`` claim and
still resolves to the owning user, which keeps the Q&A surface's existing
per-user isolation intact.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from drf_spectacular.utils import extend_schema

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from ..authentication import AGENT_TOKEN_SCOPE
from ..serializers import (
    AgentTokenRequestSerializer,
    AgentTokenResponseSerializer,
)


class AgentTokenView(APIView):
    """Mint one long-lived access token for the authenticated user."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=AgentTokenRequestSerializer,
        responses={status.HTTP_200_OK: AgentTokenResponseSerializer},
    )
    def post(self, request):
        """Return a long-lived, agent-scoped JWT for the current user."""

        default_lifetime_days = settings.AGENT_TOKEN_LIFETIME_DAYS
        if default_lifetime_days <= 0:
            return Response(
                {"error": "AGENT_TOKEN_DISABLED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AgentTokenRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        lifetime_months = serializer.validated_data.get("lifetime_months")

        if lifetime_months is None:
            lifetime_days = default_lifetime_days
        elif lifetime_months in settings.AGENT_TOKEN_LIFETIME_MONTHS_OPTIONS:
            lifetime_days = (
                lifetime_months * settings.AGENT_TOKEN_DAYS_PER_MONTH
            )
        else:
            return Response(
                {"error": "AGENT_TOKEN_INVALID_LIFETIME"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lifetime = timedelta(days=lifetime_days)
        expires_at = timezone.now() + lifetime
        token = AccessToken.for_user(request.user)
        token["scope"] = AGENT_TOKEN_SCOPE
        token.set_exp(lifetime=lifetime)

        return Response(
            {
                "access": str(token),
                "token_type": "Bearer",
                "scope": AGENT_TOKEN_SCOPE,
                "expires_in": int(lifetime.total_seconds()),
                "expires_at": int(expires_at.timestamp()),
            },
            status=status.HTTP_200_OK,
        )
