"""Tenant-local recovery for one reviewed password lock."""

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.member_management import CanManageTenantMembers, unlock_member
from accounts.views.tenant import _member_payload


class UnlockMemberInput(serializers.Serializer):
    """Bind an explicit unlock to the member row the administrator saw."""

    version = serializers.CharField(max_length=64)


class TenantMemberUnlockView(APIView):
    """Do not allow staff, delegation or API keys to bypass tenant administrator authority."""

    permission_classes = [CanManageTenantMembers]

    def post(self, request, user_id):
        """Validate the reviewed version before recovering password login."""
        serializer = UnlockMemberInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = unlock_member(request=request, user_id=user_id, version=serializer.validated_data["version"])
        return Response(_member_payload(member, request.user))
