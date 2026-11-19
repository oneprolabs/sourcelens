"""Tenant administrator invitation actions and public mailbox acceptance."""

from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from accounts.audit import record_audit
from accounts.models import TenantMembership
from accounts.serializers import normalize_password_fields
from accounts.services.bulk_invitations import current_invitation_tenant
from accounts.services.member_management import CanManageTenantMembers
from accounts.services.tenant_invitations import (
    accept_invitation,
    deliver_invitation,
    invitation_timeout,
    prepare_invitation,
    prepare_resend,
)


class CanInviteTenantMembers(CanManageTenantMembers):
    """Require the same current human authority as tenant member management."""


class InvitationSendThrottle(UserRateThrottle):
    """Bound authenticated invitation email volume."""

    scope = "tenant_invitation_send"
    rate = "30/hour"


class InvitationAcceptThrottle(AnonRateThrottle):
    """Bound public password-setting attempts per client address."""

    scope = "tenant_invitation_accept"
    rate = "30/minute"


class InvitationInput(serializers.Serializer):
    """Accept only recipient, tenant role, and tenant-backed auth groups."""

    email = serializers.EmailField(max_length=254)
    target_id = serializers.IntegerField(min_value=1, required=False)
    version = serializers.CharField(max_length=64, required=False)
    role = serializers.ChoiceField(choices=TenantMembership.Role.choices, default="member")
    group_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), default=list, max_length=100)

    def validate(self, attrs):
        """A reviewed reinstatement must identify both the target and its member version."""
        if ("target_id" in attrs) != ("version" in attrs):
            raise serializers.ValidationError("INCOMPLETE_INVITATION_TARGET")
        return attrs

    def validate_email(self, value):
        """Canonicalize recipient lookup without changing an existing account."""

        return value.strip().lower()


class InvitationAcceptInput(serializers.Serializer):
    """Validate bounded public input before signature and password policy checks."""

    token = serializers.CharField(max_length=2048, trim_whitespace=False)
    new_password1 = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)
    new_password2 = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)

    def to_internal_value(self, data):
        """Accept both API password spellings after camel-case parsing."""

        return super().to_internal_value(normalize_password_fields(data))


def _delivered(request, membership, token, created=False):
    """Return safe delivery metadata, preserving the member after SMTP failure."""

    sent = deliver_invitation(membership, token)
    record_audit(
        action="member.invite" if created else "member.invite_resend",
        request=request,
        tenant=membership.tenant,
        actor=request.user,
        resource_type="member",
        resource_id=str(membership.user_id),
        result="success" if sent else "failed",
        metadata={"role": membership.role, "delivered": sent},
    )
    if not sent:
        return Response({"detail": "INVITATION_DELIVERY_FAILED", "member_id": membership.user_id}, status=502)
    return Response(
        {
            "member_id": membership.user_id,
            "status": "invited",
            "email": membership.user.email,
            "expires_in": invitation_timeout(),
        },
        status=201 if created else 200,
    )


class TenantInvitationView(APIView):
    """Invite one new member or retry a pending delivery for the same email."""

    permission_classes = [CanInviteTenantMembers]
    throttle_classes = [InvitationSendThrottle]

    def post(self, request):
        """Validate all role/group fields before creating a pending member."""

        serializer = InvitationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        member, token = prepare_invitation(
            current_invitation_tenant(request), **serializer.validated_data, request=request
        )
        return _delivered(request, member, token, created=True)


class TenantInvitationResendView(APIView):
    """Resend only within the caller's current tenant."""

    permission_classes = [CanInviteTenantMembers]
    throttle_classes = [InvitationSendThrottle]

    def post(self, request, user_id):
        """Rotate and deliver the pending member's invitation."""

        member, token = prepare_resend(current_invitation_tenant(request), user_id, request=request)
        return _delivered(request, member, token)


class AcceptTenantInvitationView(APIView):
    """Accept an invitation independently of browser login state."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [InvitationAcceptThrottle]

    def post(self, request):
        """Set a password and activate membership without issuing login tokens."""

        serializer = InvitationAcceptInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        member = accept_invitation(data["token"], data["new_password1"], data["new_password2"])
        record_audit(
            action="member.join",
            tenant=member.tenant,
            actor=member.user,
            resource_type="member",
            resource_id=str(member.user_id),
        )
        return Response({"status": "active"})
