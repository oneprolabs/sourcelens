"""Platform-wide API key overview and forced lifecycle actions."""

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.audit import record_audit
from accounts.models import ApiKeyControlNotice, TenantApiKey
from accounts.services.api_key_insights import annotate_call_counts, key_risks, risk_summary
from accounts.services.api_key_lifecycle import (
    CanControlPlatformApiKeys,
    PlatformApiKeyBulkInput,
    apply_platform_control,
    control_platform_api_keys,
)
from accounts.services.api_key_notices import create_control_notice, enqueue_notice, notice_payload


def _key_payload(record):
    """Serialize one key for the platform view without secrets."""
    return {
        "uuid": str(record.uuid),
        "name": record.name,
        "key_prefix": record.key_prefix,
        "key_suffix": record.current_key_suffix,
        "key_display": record.key_display,
        "scope": record.scope,
        "assistant_uuids": list(record.assistant_uuids or []),
        "status": record.effective_status,
        "raw_status": record.status,
        "tenant": {
            "uuid": str(record.tenant.uuid),
            "name": record.tenant.name,
            "slug": record.tenant.slug,
        },
        "created_by": record.created_by.username if record.created_by_id else "",
        "expires_at": (record.expires_at.isoformat() if record.expires_at else None),
        "last_used_at": (record.last_used_at.isoformat() if record.last_used_at else None),
        "created_at": record.created_at.isoformat(),
        "revoked_at": (record.revoked_at.isoformat() if record.revoked_at else None),
        "updated_at": record.updated_at.isoformat(),
        "calls_7d": getattr(record, "calls_7d", None),
        **key_risks(record),
    }


class PlatformApiKeyListView(APIView):
    """List API keys across tenants with forced lifecycle control."""

    permission_classes = [CanControlPlatformApiKeys]

    def get(self, request):
        """Return a filtered page of tenant API keys."""
        queryset = TenantApiKey.objects.select_related("tenant", "created_by").order_by("-created_at", "-uuid")
        tenant_uuid = str(request.query_params.get("tenant") or "").strip()
        if tenant_uuid:
            queryset = queryset.filter(tenant__uuid=tenant_uuid)
        summary = risk_summary(queryset)
        search = str(request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(key_prefix__icontains=search)
                | Q(key_suffix__icontains=search, key_suffix_version=F("key_version"))
                | Q(created_by__username__icontains=search)
                | Q(tenant__name__icontains=search)
            )
        now = timezone.now()
        status_value = str(request.query_params.get("status") or "").strip()
        if status_value == "active":
            queryset = queryset.filter(
                status=TenantApiKey.Status.ACTIVE,
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        elif status_value == "expired":
            queryset = queryset.filter(
                status__in=[TenantApiKey.Status.ACTIVE, TenantApiKey.Status.DISABLED],
                expires_at__lte=now,
            )
        elif status_value in {
            TenantApiKey.Status.DISABLED,
            TenantApiKey.Status.REVOKED,
        }:
            queryset = queryset.filter(status=status_value)
            if status_value == TenantApiKey.Status.DISABLED:
                queryset = queryset.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        try:
            page = max(int(request.query_params.get("page") or 1), 1)
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get("page_size") or 20)
        except (TypeError, ValueError):
            page_size = 20
        page_size = min(max(page_size, 1), 100)
        start = (page - 1) * page_size
        end = start + page_size
        return Response(
            {
                "count": queryset.count(),
                "page": page,
                "page_size": page_size,
                "summary": summary,
                "results": [_key_payload(record) for record in annotate_call_counts(queryset)[start:end]],
            }
        )


class PlatformApiKeyBulkView(APIView):
    """Force-control a bounded snapshot across tenant ownership boundaries."""

    permission_classes = [CanControlPlatformApiKeys]

    def post(self, request):
        """Validate allowed actions before any key is locked or changed."""
        serializer = PlatformApiKeyBulkInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(control_platform_api_keys(request=request, data=serializer.validated_data))


class PlatformApiKeyNoticeView(APIView):
    """Read safe notification delivery state and explicitly retry failed attempts."""

    permission_classes = [CanControlPlatformApiKeys]

    def get(self, request, notice_uuid=None):
        """Expose status without recipients, email bodies or server errors."""
        if notice_uuid is None:
            try:
                page = max(int(request.query_params.get("page") or 1), 1)
                page_size = min(max(int(request.query_params.get("page_size") or 20), 1), 100)
            except (TypeError, ValueError):
                page, page_size = 1, 20
            notices = ApiKeyControlNotice.objects.select_related("tenant")
            start = (page - 1) * page_size
            end = start + page_size
            return Response(
                {
                    "count": notices.count(),
                    "page": page,
                    "page_size": page_size,
                    "results": [notice_payload(notice) for notice in notices[start:end]],
                }
            )
        notice = ApiKeyControlNotice.objects.select_related("tenant").filter(pk=notice_uuid).first()
        if notice is None:
            return Response({"detail": "NOTICE_NOT_FOUND"}, status=404)
        return Response(notice_payload(notice))

    @transaction.atomic
    def post(self, request, notice_uuid=None):
        """Retry only failed notices without repeating the key control operation."""
        if notice_uuid is None:
            return Response({"detail": "METHOD_NOT_ALLOWED"}, status=405)
        notice = ApiKeyControlNotice.objects.select_for_update().filter(pk=notice_uuid).first()
        if notice is None:
            return Response({"detail": "NOTICE_NOT_FOUND"}, status=404)
        if notice.status != "failed":
            return Response({"detail": "NOTICE_NOT_FAILED"}, status=409)
        notice.status, notice.error_code = "pending", ""
        notice.save(update_fields=["status", "error_code", "updated_at"])
        record_audit(
            action="api_key.notice_retry",
            request=request,
            actor=request.user,
            tenant=notice.tenant,
            resource_type="api_key_notice",
            resource_id=str(notice.pk),
        )
        transaction.on_commit(lambda: enqueue_notice(str(notice.pk)))
        return Response(notice_payload(notice))


class PlatformApiKeyActionView(APIView):
    """Force-disable or revoke one tenant API key."""

    permission_classes = [CanControlPlatformApiKeys]

    @transaction.atomic
    def post(self, request, key_uuid, action):
        """Apply a forced lifecycle action and record an audit entry."""
        if action not in {"disable", "revoke"}:
            return Response(
                {"detail": "INVALID_ACTION"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record = (
            TenantApiKey.objects.select_for_update(of=("self",)).select_related("tenant").filter(uuid=key_uuid).first()
        )
        if record is None:
            return Response(
                {"detail": "API_KEY_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if record.status == TenantApiKey.Status.REVOKED:
            return Response(
                {"detail": "API_KEY_REVOKED"},
                status=status.HTTP_409_CONFLICT,
            )
        changed = apply_platform_control(record, request, action)
        notice = (
            create_control_notice(tenant=record.tenant, actor=request.user, action=action, keys=[record])
            if changed
            else None
        )
        payload = _key_payload(record)
        payload["notification"] = notice_payload(notice) if notice else None
        return Response(payload)
