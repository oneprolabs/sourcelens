"""Owned notification history, statistics and platform configuration endpoints."""

import hashlib
from datetime import timedelta

from agentcore_notifier.adapters.django.models import NotificationRecord
from agentcore_notifier.adapters.django.services import notification_config
from agentcore_notifier.adapters.django.services.feishu_app import device_registration
from agentcore_notifier.adapters.django.services.notification_stats import (
    _build_series_fixed_buckets,
    _parse_date,
    _parse_end_date,
    _safe_int,
)
from agentcore_notifier.adapters.django.views.channels import _qr_png_base64
from agentcore_notifier.constants import PROVIDER_DISPLAY_NAMES
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.delegation import is_delegated_request
from accounts.permissions import HasPlatformSuperAdmin
from core.services.notification_config import NotificationConfigSerializer, effective_config, update_config
from core.views.notification_targets import target_dict
from lens.models import NotificationChannelScope
from lens.notification_scope import HasNotificationAccess, channel_queryset, notification_audit, notification_tenant


def records(request):
    """Apply ownership before every user, date or status filter."""

    return NotificationRecord.objects.filter(
        ownership__tenant=notification_tenant(request), ownership__isnull=False
    ).select_related("user")


def parse_record_date(params, key, parser):
    """Reject malformed calendar dates before querying notification history."""

    if not params.get(key):
        return None
    try:
        value = parser(params[key])
    except (ValueError, TypeError):
        value = None
    if value is None:
        raise serializers.ValidationError({key: "Invalid date."})
    return value


def filtered_records(queryset, params):
    """Bound the date window and optional record filters."""

    for field in ("source_app", "source_type", "status", "user_id", "provider_type"):
        if params.get(field):
            value = params[field]
            if field == "user_id":
                value = serializers.IntegerField().run_validation(value)
            queryset = queryset.filter(**{field: value})
    for key, parser, lookup in (("start_date", _parse_date, "gte"), ("end_date", _parse_end_date, "lte")):
        if params.get(key):
            value = parse_record_date(params, key, parser)
            queryset = queryset.filter(**{f"created_at__{lookup}": value})
    return queryset


def record_dict(record, detail=False):
    """Return a stable record contract with only business content in details."""

    data = {
        key: getattr(record, key)
        for key in (
            "source_app",
            "source_type",
            "source_id",
            "provider_type",
            "status",
            "created_at",
            "sent_at",
            "user_id",
        )
    }
    data.update(
        uuid=str(record.uuid),
        provider_display_name=PROVIDER_DISPLAY_NAMES.get(record.provider_type, record.provider_type),
        user_display=record.user.username if record.user else None,
    )
    if detail:
        data.update(
            {
                key: getattr(record, key)
                for key in ("channel", "payload", "template_key", "locale", "sent_at", "provider_message_id")
            }
        )
        # Provider responses and transport targets can contain credentials.
        data.update(target={}, source_metadata={}, content_metadata={}, metadata={}, response=None)
        data["error_message"] = "Delivery failed" if record.error_message else None
    return data


class NotificationRecordListView(APIView):
    """Page only records belonging to the selected notification scope."""

    permission_classes = [HasNotificationAccess]

    def get(self, request):
        """Apply filters before counting and pagination."""

        queryset = filtered_records(records(request), request.query_params).order_by("-created_at", "-pk")
        page = _safe_int(request.query_params.get("page"), default=1, min_value=1)
        size = _safe_int(request.query_params.get("page_size"), default=20, min_value=1, max_value=100)
        start = (page - 1) * size
        end = start + size
        return Response(
            {
                "total": queryset.count(),
                "page": page,
                "page_size": size,
                "results": [record_dict(item) for item in queryset[start:end]],
            }
        )


class NotificationRecordDetailView(APIView):
    """Resolve detail UUIDs through the same owned queryset as the list."""

    permission_classes = [HasNotificationAccess]

    def get(self, request, uuid):
        """Do not expose any other tenant's record by guessed UUID."""

        return Response(record_dict(get_object_or_404(records(request), uuid=uuid), detail=True))


class NotificationUsersView(APIView):
    """Offer users represented in this scope's history only."""

    permission_classes = [HasNotificationAccess]

    def get(self, request):
        """Do not use the notifier dependency's global user list."""

        users = get_user_model().objects.filter(pk__in=records(request).values("user_id")).order_by("pk")
        return Response([{"user_id": user.pk, "display": user.username} for user in users])


def summary(queryset):
    """Calculate all displayed status counts in one aggregate."""

    return queryset.aggregate(
        total=Count("id"),
        **{
            name: Count("id", filter=Q(status=status))
            for name, status in (
                ("total_sent", "success"),
                ("total_failed", "failed"),
                ("total_merged", "merged"),
                ("total_silenced", "silenced"),
                ("total_pending", "pending"),
            )
        },
    )


class NotificationStatsView(APIView):
    """Aggregate owned records, including the previous comparison period."""

    permission_classes = [HasNotificationAccess]

    def get(self, request):
        """Build chart buckets from an already scoped queryset."""

        base = records(request)
        params = request.query_params
        queryset = filtered_records(base, params)
        providers = list(queryset.values("provider_type").annotate(count=Count("id")).order_by("-count"))
        for row in providers:
            row["provider_display_name"] = PROVIDER_DISPLAY_NAMES.get(row["provider_type"], row["provider_type"])
        data = {
            "summary": summary(queryset),
            "by_provider": providers,
            "by_source": list(
                queryset.values("source_app", "source_type").annotate(count=Count("id")).order_by("-count")
            ),
        }
        start = parse_record_date(params, "start_date", _parse_date)
        end = parse_record_date(params, "end_date", _parse_end_date)
        if start and end and end > start:
            previous = params.copy()
            previous["start_date"] = (start - (end - start) - timedelta(microseconds=1)).isoformat()
            previous["end_date"] = (start - timedelta(microseconds=1)).isoformat()
            data["summary_prev"] = summary(filtered_records(base, previous))
        granularity = params.get("granularity")
        if granularity in {"day", "month", "year"}:
            if start and end and (end - start).days > 366:
                raise serializers.ValidationError({"end_date": "Select a window of at most 366 days."})
            data["series"] = _build_series_fixed_buckets(queryset, granularity, start, end)
        return Response(data)


class NotificationConfigView(APIView):
    """Global notifier configuration is platform administration only."""

    permission_classes = [HasPlatformSuperAdmin]
    key = "global"

    def get(self, request):
        """Return platform configuration."""

        return Response(
            {
                "key": self.key,
                "value": effective_config() if self.key == "global" else notification_config.get_config(self.key) or [],
            }
        )

    def put(self, request):
        """Validate the container type and audit without retaining values."""

        value = request.data.get("value", request.data)
        expected = list if self.key == "silence_rules" else dict
        if not isinstance(value, expected):
            raise serializers.ValidationError({"value": "Invalid configuration type."})
        if self.key == "global":
            serializer = NotificationConfigSerializer(data=value)
            serializer.is_valid(raise_exception=True)
            value = update_config(serializer.validated_data)
        else:
            notification_config.set_config(self.key, value)
        notification_audit(request, "notification_config.update", "notification_config", self.key)
        return Response({"key": self.key, "value": value})


class NotificationSilenceRulesView(NotificationConfigView):
    """Keep legacy silence settings behind the platform boundary."""

    key = "silence_rules"


def registration_key(code):
    """Avoid including device credentials in cache key names."""

    return "notification-registration:" + hashlib.sha256(code.encode()).hexdigest()


class NotificationRegistrationStartView(APIView):
    """Bind app registration to its initiating operator and notification scope."""

    permission_classes = [HasNotificationAccess]

    def post(self, request):
        """Store the scope server-side before returning the registration challenge."""

        tenant = notification_tenant(request)
        state = device_registration.begin_app_registration()
        if not state:
            return Response({"detail": "Failed to start registration."}, status=502)
        cache.set(
            registration_key(state["device_code"]),
            {
                "user_id": request.user.pk,
                "tenant_id": str(tenant.pk) if tenant else None,
                "delegated": is_delegated_request(),
            },
            timeout=state["expires_in"],
        )
        return Response(
            {
                **{key: state[key] for key in ("device_code", "verification_uri_complete", "interval", "expires_in")},
                "qr_code_base64": _qr_png_base64(state["verification_uri_complete"]),
            }
        )


class NotificationRegistrationPollView(APIView):
    """Complete app registration without a global channel upsert."""

    permission_classes = [HasNotificationAccess]

    @transaction.atomic
    def get(self, request):
        """Reject foreign/expired challenges before contacting the provider."""

        tenant = notification_tenant(request)
        code = serializers.CharField(max_length=1024).run_validation(request.query_params.get("device_code"))
        state = cache.get(registration_key(code))
        expected = {
            "user_id": request.user.pk,
            "tenant_id": str(tenant.pk) if tenant else None,
            "delegated": is_delegated_request(),
        }
        if not state or any(state.get(key) != value for key, value in expected.items()):
            return Response({"detail": "Registration not found."}, status=404)
        if state.get("channel_uuid"):
            channel = get_object_or_404(channel_queryset(tenant), uuid=state["channel_uuid"])
            return Response({"status": "success", "channel": target_dict(channel)})
        result = device_registration.poll_app_registration(code)
        if result.get("status") != "success":
            return Response({key: value for key, value in result.items() if key in {"status", "error", "message"}})
        channel = (
            channel_queryset(tenant).select_for_update().filter(channel_type="feishu_app", user__isnull=True).first()
        )
        if channel is None:
            from agentcore_notifier.adapters.django.models import NotificationChannel

            channel = NotificationChannel.objects.create(channel_type="feishu_app", name="Feishu")
            NotificationChannelScope.objects.create(channel=channel, tenant=tenant)
        channel.config = {**channel.config, "app_id": result["client_id"], "app_secret": result["client_secret"]}
        channel.is_active = True
        channel.save(update_fields=["config", "is_active", "updated_at"])
        cache.set(registration_key(code), {**state, "channel_uuid": str(channel.uuid)}, timeout=300)
        notification_audit(request, "notification_channel.register", "notification_channel", channel.uuid, tenant)
        return Response({"status": "success", "channel": target_dict(channel)})
