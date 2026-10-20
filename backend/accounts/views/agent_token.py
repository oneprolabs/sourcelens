"""Revocable access credentials for external SourceLens agent clients.

Coding agents (Codex, Claude) cannot run the interactive refresh flow used
by the web app, so they receive a single long-lived access token minted
from the user settings page. The token has a server-managed scope and
still resolves to the owning user, which keeps the Q&A surface's existing
per-user isolation intact.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from drf_spectacular.utils import extend_schema

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..authentication import AGENT_TOKEN_SCOPE
from ..models import AgentToken
from ..serializers import (
    AgentTokenListSerializer,
    AgentTokenListResponseSerializer,
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
        """Return a revocable, agent-scoped credential for the current user."""

        default_lifetime_days = settings.AGENT_TOKEN_LIFETIME_DAYS
        if default_lifetime_days <= 0:
            return Response(
                {"error": "AGENT_TOKEN_DISABLED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AgentTokenRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        lifetime_months = serializer.validated_data.get("lifetime_months")

        if lifetime_months == 0:
            lifetime_days = None
        elif lifetime_months is None:
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

        if lifetime_days is None:
            expires_in = None
            expires_at = None
        else:
            lifetime = timedelta(days=lifetime_days)
            expires_at = timezone.now() + lifetime
            expires_in = int(lifetime.total_seconds())

        token, raw_token = AgentToken.issue(request.user, expires_at)

        return Response(
            {
                "id": str(token.pk),
                "access": raw_token,
                "token_type": "Bearer",
                "scope": AGENT_TOKEN_SCOPE,
                "expires_in": expires_in,
                "expires_at": int(expires_at.timestamp()) if expires_at is not None else None,
            },
            status=status.HTTP_200_OK,
        )


class AgentTokenListView(APIView):
    """List active agent authorizations belonging to the current user."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={status.HTTP_200_OK: AgentTokenListResponseSerializer})
    def get(self, request):
        """Return metadata only for unexpired, unrevoked credentials."""

        tokens = AgentToken.objects.filter(user=request.user, revoked_at__isnull=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        )
        return Response({"tokens": AgentTokenListSerializer(tokens, many=True).data})


class AgentTokenRevokeView(APIView):
    """Revoke one authorization without affecting other agent clients."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={status.HTTP_204_NO_CONTENT: None})
    def delete(self, request, token_id):
        """Invalidate the selected credential for all subsequent requests."""

        token = get_object_or_404(AgentToken, pk=token_id, user=request.user)
        AgentToken.objects.filter(pk=token.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)
