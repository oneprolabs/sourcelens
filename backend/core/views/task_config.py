"""Atomic task configuration and database-backed Beat schedules."""

from agentcore_task.adapters.django import conf
from agentcore_task.adapters.django.serializers import TaskConfigUpdateSerializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.audit import record_audit
from accounts.permissions import HasPlatformSuperAdmin
from core.services.task_config import SCHEDULES, effective_task_config, update_task_config


class PlatformTaskConfigSerializer(TaskConfigUpdateSerializer):
    """Validate the entire supported configuration before performing writes."""

    cleanup_enabled = serializers.BooleanField(required=False)
    mark_timeout_enabled = serializers.BooleanField(required=False)

    def validate(self, attrs):
        """Reject unsupported fields, invalid schedules and unavailable workers."""
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "Unsupported setting." for key in sorted(unknown)})
        for prefix in SCHEDULES:
            key = f"{prefix}_crontab"
            if key in attrs and not conf.is_valid_crontab_expression(attrs[key]):
                raise serializers.ValidationError({key: "Invalid 5-field cron expression."})
            available = getattr(conf, f"get_{prefix}_enabled")()
            if attrs.get(f"{prefix}_enabled") and not available:
                raise serializers.ValidationError({f"{prefix}_enabled": "Disabled by worker configuration."})
        return attrs


class PlatformTaskConfigView(APIView):
    """Manage global task parameters and real scheduler switches."""

    permission_classes = [HasPlatformSuperAdmin]

    def get(self, request):
        """Return persisted effective task settings."""
        return Response(effective_task_config())

    def patch(self, request):
        """Save all parameters and schedules together, or save nothing."""
        serializer = PlatformTaskConfigSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config = update_task_config(data)
        if data:
            record_audit(
                action="task_config.update",
                actor=request.user,
                request=request,
                resource_type="task_config",
                resource_id="global",
                metadata={"changed_keys": sorted(data)},
            )
        return Response(config)
