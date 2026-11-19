"""Tenant administrators preview and confirm permanent member removal."""

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.member_deletion import MemberDeletionInput, delete_members
from accounts.services.member_management import CanManageTenantMembers


class TenantMemberDeletionView(APIView):
    """Do not expose a direct, unreviewed member deletion route."""

    permission_classes = [CanManageTenantMembers]

    @extend_schema(request=MemberDeletionInput)
    def post(self, request):
        """Validate the fixed selection and return either impact or actual results."""
        serializer = MemberDeletionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(delete_members(request=request, data=serializer.validated_data))
