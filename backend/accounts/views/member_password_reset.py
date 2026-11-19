"""Administrative delivery of an existing member's password recovery link."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from accounts.services.member_management import CanManageTenantMembers
from accounts.services.member_password_reset import reset_member_password


class MemberPasswordResetInput(serializers.Serializer):
    """Accept only the member version; the server owns recipient and recovery credentials."""

    version = serializers.CharField(max_length=64)

    def to_internal_value(self, data):
        """Reject recipient overrides instead of silently discarding unknown fields."""
        if isinstance(data, dict) and set(data) - {"version"}:
            raise serializers.ValidationError({"member": "UNEXPECTED_RESET_FIELD"})
        return super().to_internal_value(data)


class MemberPasswordResetOutput(serializers.Serializer):
    """Report backend acceptance without returning the recovery link."""

    status = serializers.ChoiceField(choices=("sent",))
    member_id = serializers.IntegerField()


class MemberPasswordResetThrottle(UserRateThrottle):
    """Bound administrative recovery mail per human actor."""

    scope = "member_password_reset"
    rate = "30/hour"


class TenantMemberPasswordResetView(APIView):
    """Require explicit current tenant administrator authority for recovery mail."""

    permission_classes = [CanManageTenantMembers]
    throttle_classes = [MemberPasswordResetThrottle]

    @extend_schema(request=MemberPasswordResetInput, responses={200: MemberPasswordResetOutput})
    def post(self, request, user_id):
        """Validate the reviewed member and deliver outside the database transaction."""
        serializer = MemberPasswordResetInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(reset_member_password(request=request, user_id=user_id, **serializer.validated_data))
