"""Bounded tenant member batches."""

from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.member_management import CanManageTenantMembers, MemberBulkInput, mutate_members, resend_members


class TenantMemberBulkView(APIView):
    """Apply one explicit action to a versioned tenant-local selection."""

    permission_classes = [CanManageTenantMembers]

    def post(self, request):
        """Validate the complete selection before any account or role changes."""
        serializer = MemberBulkInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["action"] == "resend":
            from accounts.views.tenant_invitations import InvitationSendThrottle

            return Response(
                resend_members(
                    request=request,
                    data=data,
                    allow_send=lambda: InvitationSendThrottle().allow_request(request, self),
                )
            )
        return Response(mutate_members(request=request, data=data))
