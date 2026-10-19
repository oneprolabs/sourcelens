"""Native robot channel APIs alongside the existing agentcore email/app endpoints."""

from agentcore_notifier.adapters.django.models import NotificationChannel
from agentcore_notifier.adapters.django.views.channels import (
    ChannelValidateView,
    NotificationChannelDetailView,
    NotificationChannelListView,
    NotificationChannelTestView,
    _channel_to_dict,
)
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response

from core.services.notification_targets import NATIVE_TARGET_TYPES, TEST_MESSAGES, send_target, validate_target_config


def target_dict(channel):
    """Extend the shared response contract with write-only robot credentials."""

    data = _channel_to_dict(channel)
    if channel.channel_type in NATIVE_TARGET_TYPES:
        data["config"] = {key: value for key, value in channel.config.items() if key not in {"url", "sign_secret"}}
        data["config"].update(
            url_configured=bool(channel.config.get("url")),
            sign_secret_configured=bool(channel.config.get("sign_secret")),
        )
    return data


class TargetSerializer(serializers.ModelSerializer):
    """Own the native type contract without modifying the external dependency."""

    channel_type = serializers.ChoiceField(choices=sorted(NATIVE_TARGET_TYPES))

    class Meta:
        model = NotificationChannel
        fields = ["channel_type", "name", "is_active", "config"]

    def validate(self, attrs):
        """Validate partial updates against the existing immutable target type."""

        provider = attrs.get("channel_type", self.instance.channel_type if self.instance else None)
        if self.instance and provider != self.instance.channel_type:
            raise serializers.ValidationError({"channel_type": "Target type cannot be changed."})
        previous = self.instance.config if self.instance else None
        attrs["config"] = validate_target_config(provider, attrs.get("config", {}), previous)
        return attrs


class TargetListView(NotificationChannelListView):
    """Create first-class native targets and retain existing channel APIs."""

    def get(self, request):
        """List channels without disclosing native robot credentials."""

        channels = NotificationChannel.objects.select_related("user").order_by("created_at")
        if request.query_params.get("channel_type"):
            channels = channels.filter(channel_type=request.query_params["channel_type"])
        results = [target_dict(channel) for channel in channels]
        return Response({"total": len(results), "results": results})

    def post(self, request):
        """Persist independent platform types rather than provider aliases."""

        provider = request.data.get("channel_type")
        if not isinstance(provider, str):
            raise serializers.ValidationError({"channel_type": "Expected a target type."})
        if provider not in NATIVE_TARGET_TYPES:
            return super().post(request)
        serializer = TargetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(target_dict(serializer.save()), status=201)


class TargetDetailView(NotificationChannelDetailView):
    """Retrieve and edit native targets while preserving write-only secrets."""

    def get(self, request, uuid):
        """Return the shared representation with native secret masking."""

        return Response(target_dict(get_object_or_404(NotificationChannel, uuid=uuid)))

    def put(self, request, uuid):
        """Update native targets or delegate existing channel types."""

        channel = get_object_or_404(NotificationChannel, uuid=uuid)
        if channel.channel_type not in NATIVE_TARGET_TYPES:
            return super().put(request, uuid)
        serializer = TargetSerializer(channel, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(target_dict(serializer.save()))


class TargetValidateView(ChannelValidateView):
    """Send the same native format used in production, with a persisted attempt."""

    def post(self, request):
        """Validate supplied configuration or retained saved credentials."""

        provider = request.data.get("channel_type")
        if not isinstance(provider, str):
            raise serializers.ValidationError({"channel_type": "Expected a target type."})
        if provider not in NATIVE_TARGET_TYPES:
            return super().post(request)
        channel = None
        if request.data.get("channel_uuid"):
            channel = get_object_or_404(
                NotificationChannel,
                uuid=request.data["channel_uuid"],
                channel_type=provider,
            )
        config = validate_target_config(
            provider,
            request.data.get("config", {}),
            channel.config if channel else None,
        )
        target = NotificationChannel(channel_type=provider, config=config)
        if channel:
            target.pk = channel.pk
            target.uuid = channel.uuid
            target.name = channel.name
        title, body = TEST_MESSAGES[config.get("language", "en")]
        result = send_target(
            target,
            title,
            body,
            source_type="channel_validate",
            user=request.user,
            warning=False,
        )
        return Response(result, status=200 if result["success"] else 400)


class TargetTestView(NotificationChannelTestView):
    """Support explicit test sends by saved native target UUID."""

    def post(self, request, uuid):
        """Use saved credentials and honor disabled targets."""

        channel = get_object_or_404(NotificationChannel, uuid=uuid)
        if channel.channel_type not in NATIVE_TARGET_TYPES:
            return super().post(request, uuid)
        if not channel.is_active:
            return Response(
                {"success": False, "error": "Notification target is disabled."},
                status=400,
            )
        title, body = TEST_MESSAGES[channel.config.get("language", "en")]
        result = send_target(
            channel,
            title,
            body,
            source_type="channel_validate",
            user=request.user,
            warning=False,
        )
        return Response(result, status=200 if result["success"] else 400)
