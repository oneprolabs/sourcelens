"""Preview and send bounded tenant invitation batches."""

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from accounts.services.bulk_invitations import BulkInvitationInput, invite_members, preview_invitations
from accounts.services.member_management import CanManageTenantMembers
from accounts.views.tenant_invitations import InvitationSendThrottle


class InvitationPreviewThrottle(UserRateThrottle):
    """Bound authenticated availability checks separately from actual email deliveries."""

    scope = "tenant_invitation_preview"
    rate = "60/minute"


class BulkInvitationPreviewView(APIView):
    """Classify a bounded recipient envelope without modifying member state."""

    permission_classes = [CanManageTenantMembers]
    throttle_classes = [InvitationPreviewThrottle]

    def post(self, request):
        """Return per-row validation and a short-lived confirmation."""
        serializer = BulkInvitationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(preview_invitations(request=request, entries=serializer.validated_data["entries"]))


class BulkInvitationView(APIView):
    """Charge the existing invitation budget once per eligible recipient."""

    permission_classes = [CanManageTenantMembers]

    def post(self, request):
        """Apply the reviewed envelope and return each delivery outcome."""
        serializer = BulkInvitationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "confirmation" not in data:
            raise serializers.ValidationError({"confirmation": "REQUIRED"})
        return Response(
            invite_members(
                request=request, **data, allow_send=lambda: InvitationSendThrottle().allow_request(request, self)
            )
        )
