"""Scoped notification channel management without vendor URL fallbacks."""

from copy import copy

from agentcore_notifier.adapters.django.models import NotificationChannel
from agentcore_notifier.adapters.django.services import notification_test
from agentcore_notifier.adapters.django.views.channels import (
    CREATABLE_CHANNEL_TYPES,
    _channel_to_dict,
    _validate_email_config,
    _validate_webhook_config,
    _validate_wecom_app_config,
)
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from core.services.notification_targets import NATIVE_TARGET_TYPES, TEST_MESSAGES, send_target, validate_target_config
from lens.models import NotificationChannelScope
from lens.notification_scope import (
    HasNotificationAccess,
    channel_queryset,
    notification_audit,
    notification_record_scope,
    notification_tenant,
)

SECRET_FIELDS = {
    "url",
    "sign_secret",
    "smtp_password",
    "app_secret",
    "encrypt_key",
    "verification_token",
    "secret",
    "corp_secret",
}


def target_dict(channel):
    """Expose configuration without returning robot URLs or authentication values."""

    data = _channel_to_dict(channel)
    config = dict(channel.config or {})
    data["config"] = {key: value for key, value in config.items() if key not in SECRET_FIELDS}
    for key in SECRET_FIELDS & config.keys():
        data["config"][f"{key}_configured"] = bool(config[key])
    owner = channel.ownership
    data["scope"] = "tenant" if owner.tenant_id else "platform"
    return data


class TargetSerializer(serializers.ModelSerializer):
    """Validate all supported transports and preserve write-only configuration."""

    channel_type = serializers.ChoiceField(choices=sorted(CREATABLE_CHANNEL_TYPES | NATIVE_TARGET_TYPES))
    config = serializers.DictField(required=False)
    user_id = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = NotificationChannel
        fields = ["channel_type", "name", "is_active", "is_default", "config", "user_id"]

    def validate(self, attrs):
        """Channel type and scope cannot change during an update."""

        provider = attrs.get("channel_type", self.instance.channel_type if self.instance else None)
        if self.instance and provider != self.instance.channel_type:
            raise serializers.ValidationError({"channel_type": "Target type cannot be changed."})
        previous = self.instance.config if self.instance else {}
        config = attrs.get("config", {})
        if provider in NATIVE_TARGET_TYPES:
            attrs["config"] = validate_target_config(provider, config, previous)
        else:
            merged = {**previous, **config}
            for key in SECRET_FIELDS:
                if not merged.get(key) and previous.get(key):
                    merged[key] = previous[key]
                merged.pop(f"{key}_configured", None)
            attrs["config"] = merged
        if attrs.get("user_id") is not None:
            tenant = self.context["tenant"]
            users = get_user_model().objects.filter(pk=attrs["user_id"], is_active=True)
            if tenant is None:
                users = users.filter(profile__platform_role="super_admin")
            else:
                users = users.filter(membership__tenant=tenant, membership__status="active")
            if not users.exists():
                raise serializers.ValidationError({"user_id": "Select a user in the current scope."})
        return attrs


class TargetListView(APIView):
    """List and create channels in the authenticated notification scope."""

    permission_classes = [HasNotificationAccess]

    def get(self, request):
        """List only owned channels, with credentials omitted."""

        channels = channel_queryset(notification_tenant(request)).select_related("user", "ownership")
        if request.query_params.get("channel_type"):
            channels = channels.filter(channel_type=request.query_params["channel_type"])
        results = [target_dict(channel) for channel in channels.order_by("created_at")]
        return Response({"total": len(results), "results": results})

    @transaction.atomic
    def post(self, request):
        """Save ownership and channel together; defaults are local to this scope."""

        tenant = notification_tenant(request)
        serializer = TargetSerializer(data=request.data, context={"tenant": tenant})
        serializer.is_valid(raise_exception=True)
        channel = serializer.save()
        NotificationChannelScope.objects.create(channel=channel, tenant=tenant)
        if channel.is_default:
            channel_queryset(tenant).filter(channel_type=channel.channel_type).exclude(pk=channel.pk).update(
                is_default=False
            )
        notification_audit(request, "notification_channel.create", "notification_channel", channel.uuid, tenant)
        return Response(target_dict(channel), status=201)


class TargetDetailView(APIView):
    """Retrieve, update and remove a channel with the same ownership check."""

    permission_classes = [HasNotificationAccess]

    def get(self, request, uuid):
        """Return a channel belonging to the selected scope."""

        return Response(target_dict(get_object_or_404(channel_queryset(notification_tenant(request)), uuid=uuid)))

    @transaction.atomic
    def put(self, request, uuid):
        """Update a channel without clearing another scope's default."""

        tenant = notification_tenant(request)
        channel = get_object_or_404(channel_queryset(tenant).select_for_update(), uuid=uuid)
        serializer = TargetSerializer(channel, data=request.data, partial=True, context={"tenant": tenant})
        serializer.is_valid(raise_exception=True)
        channel = serializer.save()
        if channel.is_default:
            channel_queryset(tenant).filter(channel_type=channel.channel_type).exclude(pk=channel.pk).update(
                is_default=False
            )
        notification_audit(request, "notification_channel.update", "notification_channel", channel.uuid, tenant)
        return Response(target_dict(channel))

    def delete(self, request, uuid):
        """Preserve historical record ownership when deleting a transport."""

        tenant = notification_tenant(request)
        channel = get_object_or_404(channel_queryset(tenant), uuid=uuid)
        channel.delete()
        notification_audit(request, "notification_channel.delete", "notification_channel", uuid, tenant)
        return Response(status=204)


class TargetValidateView(APIView):
    """Reuse saved credentials only after verifying channel ownership."""

    permission_classes = [HasNotificationAccess]

    def post(self, request):
        """Validate draft configuration and pin attempts to its authorized scope."""

        tenant = notification_tenant(request)
        provider = request.data.get("channel_type")
        channel = None
        if request.data.get("channel_uuid"):
            channel = get_object_or_404(
                channel_queryset(tenant),
                uuid=serializers.UUIDField().run_validation(request.data["channel_uuid"]),
                channel_type=provider,
            )
        serializer = TargetSerializer(
            channel,
            data={"name": "Validation", **request.data},
            partial=channel is not None,
            context={"tenant": tenant},
        )
        serializer.is_valid(raise_exception=True)
        config = serializer.validated_data.get("config", {})
        with notification_record_scope(tenant):
            if provider in NATIVE_TARGET_TYPES:
                target = NotificationChannel(channel_type=provider, config=config)
                if channel:
                    target.pk, target.uuid, target.name = channel.pk, channel.uuid, channel.name
                title, body = TEST_MESSAGES[config.get("language", "en")]
                result = send_target(
                    target, title, body, source_type="channel_validate", user=request.user, warning=False
                )
            elif provider == "email":
                recipient = request.data.get("test_recipient") or None
                if recipient:
                    serializers.EmailField().run_validation(recipient)
                result = _validate_email_config(config, test_recipient=recipient, user_id=request.user.pk)
            elif provider == "webhook":
                result = _validate_webhook_config(config, user_id=request.user.pk)
            elif provider == "wecom_app":
                result = _validate_wecom_app_config(config)
            else:
                raise serializers.ValidationError({"channel_type": "This type requires a saved channel test."})
        notification_audit(
            request, "notification_channel.validate", "notification_channel", channel.uuid if channel else "", tenant
        )
        return Response(result, status=200 if result.get("success") else 400)


class TargetTestView(APIView):
    """Test only active channels from the authenticated scope."""

    permission_classes = [HasNotificationAccess]

    def post(self, request, uuid):
        """Keep native and vendor test senders behind the same permission boundary."""

        tenant = notification_tenant(request)
        channel = get_object_or_404(channel_queryset(tenant), uuid=uuid)
        if not channel.is_active:
            return Response({"success": False, "error": "Notification target is disabled."}, status=400)
        with notification_record_scope(tenant):
            if channel.channel_type in NATIVE_TARGET_TYPES:
                title, body = TEST_MESSAGES[channel.config.get("language", "en")]
                result = send_target(
                    channel, title, body, source_type="channel_validate", user=request.user, warning=False
                )
            elif channel.channel_type == "email":
                recipient = request.data.get("test_recipient") or request.user.email
                recipient = serializers.EmailField().run_validation(recipient)
                result = _validate_email_config(channel.config, test_recipient=recipient, user_id=request.user.pk)
            elif channel.channel_type == "webhook":
                result = _validate_webhook_config(channel.config, user_id=request.user.pk)
            else:
                if channel.channel_type == "feishu_app" and not all(
                    channel.config.get(key) for key in ("app_id", "app_secret")
                ):
                    shared = channel_queryset(tenant).filter(
                        channel_type="feishu_app", is_active=True, user__isnull=True
                    )
                    credentials = next(
                        (row.config for row in shared if row.config.get("app_id") and row.config.get("app_secret")),
                        None,
                    )
                    if credentials is None:
                        return Response(
                            {"success": False, "error": "No Feishu application configured in this scope."}, status=400
                        )
                    channel = copy(channel)
                    channel.config = {
                        **channel.config,
                        "app_id": credentials["app_id"],
                        "app_secret": credentials["app_secret"],
                    }
                result = notification_test.send_test_message(channel)
        notification_audit(request, "notification_channel.test", "notification_channel", uuid, tenant)
        return Response(result, status=200 if result.get("success") else 400)


class TargetBulkDeleteView(APIView):
    """Delete a bounded owned selection atomically."""

    permission_classes = [HasNotificationAccess]

    @transaction.atomic
    def post(self, request):
        """Reject the entire selection when any channel is unavailable."""

        ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=100).run_validation(
            request.data.get("channel_ids")
        )
        tenant = notification_tenant(request)
        channels = channel_queryset(tenant).select_for_update().filter(uuid__in=set(ids))
        selected = list(channels)
        if len(selected) != len(set(ids)):
            return Response({"detail": "One or more channels are unavailable."}, status=404)
        channels.delete()
        for channel in selected:
            notification_audit(request, "notification_channel.delete", "notification_channel", channel.uuid, tenant)
        return Response({"count": len(selected)})
