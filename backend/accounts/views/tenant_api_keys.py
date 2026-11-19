"""Tenant-scoped API key management."""

from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import get_user_tenant
from accounts.audit import record_audit
from accounts.models import TenantApiKey
from accounts.services.api_key_insights import annotate_call_counts, detail_insights, key_risks, risk_summary
from accounts.services.api_key_lifecycle import (
    ApiKeyBulkInput,
    CanManageTenantApiKeys,
    create_tenant_api_key,
    mutate_api_keys,
)
from lens.models import Assistant


def payload_expiry(record):
    """Return an ISO expiry string or None for audit metadata."""
    return record.expires_at.isoformat() if record.expires_at else None


def _assistant_count(record):
    """Return the number of assistants this key can call."""
    assistants = Assistant.objects.filter(
        tenant_id=record.tenant_id, status=Assistant.Status.ACTIVE, is_enabled=True, is_system=False
    )
    if record.scope != TenantApiKey.Scope.ALL:
        assistants = assistants.filter(uuid__in=record.assistant_uuids or [])
    return assistants.count()


def _api_key_payload(record, assistant_count=None):
    """Serialize one key without ever returning the secret."""
    return {
        "uuid": str(record.uuid),
        "name": record.name,
        "key_prefix": record.key_prefix,
        "key_suffix": record.current_key_suffix,
        "key_display": record.key_display,
        "description": record.description,
        "scope": record.scope,
        "assistant_uuids": list(record.assistant_uuids or []),
        "assistant_count": _assistant_count(record) if assistant_count is None else assistant_count,
        "status": record.effective_status,
        "raw_status": record.status,
        "created_by": (record.created_by.username if record.created_by_id else ""),
        "expires_at": (record.expires_at.isoformat() if record.expires_at else None),
        "last_used_at": (record.last_used_at.isoformat() if record.last_used_at else None),
        "key_version": record.key_version,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "calls_7d": getattr(record, "calls_7d", None),
        **key_risks(record),
    }


def _page_payload(records, tenant):
    """Count callable assistants for the page with at most two shared queries."""
    assistants = Assistant.objects.filter(
        tenant=tenant, status=Assistant.Status.ACTIVE, is_enabled=True, is_system=False
    )
    all_count = assistants.count() if any(record.scope == TenantApiKey.Scope.ALL for record in records) else 0
    selected = {
        str(value)
        for record in records
        if record.scope == TenantApiKey.Scope.SELECTED
        for value in (record.assistant_uuids or [])
    }
    allowed = (
        {str(value) for value in assistants.filter(uuid__in=selected).values_list("uuid", flat=True)}
        if selected
        else set()
    )
    return [
        _api_key_payload(
            record,
            (
                all_count
                if record.scope == TenantApiKey.Scope.ALL
                else len(allowed.intersection(str(value) for value in (record.assistant_uuids or [])))
            ),
        )
        for record in records
    ]


def _tenant_assistant_uuids(tenant):
    """Return the tenant's assistant UUID strings."""
    return {str(value) for value in Assistant.objects.filter(tenant=tenant).values_list("uuid", flat=True)}


def _parse_description(data, default=""):
    """Validate optional purpose text without coercing structured or null input."""
    value = data.get("description", default)
    if not isinstance(value, str):
        return None, "INVALID_DESCRIPTION"
    value = value.strip()
    return (None, "INVALID_DESCRIPTION") if len(value) > 1000 else (value, None)


def _validate_scope(tenant, scope, assistant_uuids):
    """Validate a requested assistant scope for one tenant."""
    if scope not in TenantApiKey.Scope.values:
        return None, "INVALID_SCOPE"
    if scope == TenantApiKey.Scope.ALL:
        return [], None
    if not isinstance(assistant_uuids, list):
        return None, "INVALID_ASSISTANT_SCOPE"
    normalized = [str(value) for value in assistant_uuids]
    valid = _tenant_assistant_uuids(tenant)
    if not normalized or any(value not in valid for value in normalized):
        return None, "INVALID_ASSISTANT_SCOPE"
    return normalized, None


def _parse_expiry(record, expires_in_days, expires_at):
    """Update expiry from either a day count or an ISO timestamp."""
    if expires_at is not None:
        if expires_at == "":
            return None, None
        try:
            parsed = parse_datetime(str(expires_at))
        except (ValueError, OverflowError):
            return None, "INVALID_EXPIRY"
        if parsed is None:
            return None, "INVALID_EXPIRY"
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed, None
    if expires_in_days is None:
        return getattr(record, "expires_at", None), None
    if isinstance(expires_in_days, (bool, float)):
        return None, "INVALID_EXPIRY"
    try:
        days = int(expires_in_days)
    except (TypeError, ValueError):
        return None, "INVALID_EXPIRY"
    if days < 0:
        return None, "INVALID_EXPIRY"
    if days == 0:
        return None, None
    try:
        return timezone.now() + timedelta(days=days), None
    except OverflowError:
        return None, "INVALID_EXPIRY"


class TenantApiKeyListView(APIView):
    """List and create tenant API keys."""

    permission_classes = [CanManageTenantApiKeys]

    def get(self, request):
        """Return keys with effective status and assistant counts."""
        tenant = get_user_tenant(request.user)
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        queryset = TenantApiKey.objects.filter(tenant=tenant).select_related("created_by")
        summary = risk_summary(queryset)
        search = str(request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(created_by__username__icontains=search)
                | Q(key_prefix__icontains=search)
                | Q(key_suffix__icontains=search, key_suffix_version=F("key_version"))
            )
        status_value = str(request.query_params.get("status") or "").strip()
        now = timezone.now()
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
            page_size = min(max(int(request.query_params.get("page_size") or 20), 1), 100)
        except (TypeError, ValueError):
            page, page_size = 1, 20
        start = (page - 1) * page_size
        end = start + page_size
        return Response(
            {
                "count": queryset.count(),
                "page": page,
                "page_size": page_size,
                "summary": summary,
                "results": _page_payload(list(annotate_call_counts(queryset)[start:end]), tenant),
            }
        )

    @transaction.atomic
    def post(self, request):
        """Create a key and return the plaintext secret exactly once."""
        tenant = get_user_tenant(request.user)
        if tenant is None:
            return Response(
                {"detail": "TENANT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        name = " ".join(str(request.data.get("name") or "").split())
        if not name:
            return Response(
                {"name": "API_KEY_NAME_REQUIRED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        scope = str(request.data.get("scope") or TenantApiKey.Scope.ALL)
        if len(name) > 150:
            return Response({"name": "API_KEY_NAME_TOO_LONG"}, status=400)
        description, error = _parse_description(request.data)
        if error:
            return Response({"description": error}, status=400)
        assistant_uuids, error = _validate_scope(
            tenant,
            scope,
            request.data.get("assistant_uuids"),
        )
        if error:
            return Response(
                {"assistant_uuids": error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        expires_at, error = _parse_expiry(
            None,
            request.data.get("expires_in_days"),
            request.data.get("expires_at"),
        )
        if error:
            return Response(
                {"expires_at": error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record, raw_token = create_tenant_api_key(
            request=request,
            tenant=tenant,
            name=name,
            description=description,
            scope=scope,
            assistant_uuids=assistant_uuids,
            expires_at=expires_at,
        )
        record_audit(
            action="api_key.create",
            request=request,
            tenant=tenant,
            actor=request.user,
            resource_type="api_key",
            resource_id=str(record.uuid),
            metadata={
                "name": record.name,
                "scope": record.scope,
                "expires_at": payload_expiry(record),
            },
        )
        payload = _api_key_payload(record)
        payload["key"] = raw_token
        return Response(payload, status=status.HTTP_201_CREATED)


class TenantApiKeyBulkView(APIView):
    """Apply bounded tenant-scoped lifecycle changes to a reviewed selection."""

    permission_classes = [CanManageTenantApiKeys]

    def post(self, request):
        """Validate the complete request before locking and changing any key."""
        serializer = ApiKeyBulkInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            mutate_api_keys(request=request, tenant=get_user_tenant(request.user), data=serializer.validated_data)
        )


class TenantApiKeyDetailView(APIView):
    """Update, rotate, or revoke one tenant API key."""

    permission_classes = [CanManageTenantApiKeys]

    def get(self, request, key_uuid):
        """Read safe metadata and recent actual invocations in the current tenant."""
        record = TenantApiKey.objects.select_related("created_by").filter(
            tenant=get_user_tenant(request.user), uuid=key_uuid
        ).first()
        if record is None:
            return Response({"detail": "API_KEY_NOT_FOUND"}, status=404)
        return Response({**_api_key_payload(record), **detail_insights(record)})

    def _get_key(self, request, key_uuid):
        tenant = get_user_tenant(request.user)
        if tenant is None:
            return None
        return (
            TenantApiKey.objects.select_for_update(of=("self",))
            .filter(
                tenant=tenant,
                uuid=key_uuid,
            )
            .select_related("created_by")
            .first()
        )

    @transaction.atomic
    def patch(self, request, key_uuid):
        """Update metadata, status, scope, or expiry."""
        record = self._get_key(request, key_uuid)
        if record is None:
            return Response(
                {"detail": "API_KEY_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if record.status == TenantApiKey.Status.REVOKED:
            return Response({"detail": "API_KEY_REVOKED"}, status=409)
        if "updated_at" in request.data:
            value = request.data["updated_at"]
            try:
                expected = parse_datetime(value) if isinstance(value, str) else None
            except ValueError:
                expected = None
            if expected is None or timezone.is_naive(expected):
                return Response({"updated_at": "INVALID_VERSION"}, status=400)
            if record.updated_at != expected:
                return Response({"detail": "API_KEY_SELECTION_CHANGED"}, status=409)
        description, error = _parse_description(request.data, record.description)
        if error:
            return Response({"description": error}, status=400)
        record.description = description
        name = request.data.get("name")
        status_value = request.data.get("status")
        if name is not None:
            normalized = " ".join(str(name).split())
            if not normalized:
                return Response(
                    {"name": "API_KEY_NAME_REQUIRED"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if len(normalized) > 150:
                return Response({"name": "API_KEY_NAME_TOO_LONG"}, status=400)
            record.name = normalized
        if status_value is not None:
            if status_value not in TenantApiKey.Status.values:
                return Response(
                    {"status": "INVALID_STATUS"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            record.status = status_value
            record.revoked_at = timezone.now() if status_value == TenantApiKey.Status.REVOKED else None
        scope = request.data.get("scope")
        if scope is not None:
            assistant_uuids, error = _validate_scope(
                record.tenant,
                scope,
                request.data.get("assistant_uuids"),
            )
            if error:
                return Response(
                    {"assistant_uuids": error},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            record.scope = scope
            record.assistant_uuids = assistant_uuids
        expires_at, error = _parse_expiry(
            record,
            request.data.get("expires_in_days"),
            request.data.get("expires_at"),
        )
        if error:
            return Response(
                {"expires_at": error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record.expires_at = expires_at
        record.save()
        record_audit(
            action="api_key.update",
            request=request,
            tenant=record.tenant,
            actor=request.user,
            resource_type="api_key",
            resource_id=str(record.uuid),
            metadata={"status": record.status, "scope": record.scope, "expires_at": payload_expiry(record)},
        )
        return Response(_api_key_payload(record))

    @transaction.atomic
    def post(self, request, key_uuid):
        """Rotate the secret and return the new plaintext once."""
        record = self._get_key(request, key_uuid)
        if record is None:
            return Response(
                {"detail": "API_KEY_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if record.status == TenantApiKey.Status.REVOKED:
            return Response({"detail": "API_KEY_REVOKED"}, status=409)
        expires_at, error = _parse_expiry(record, request.data.get("expires_in_days"), request.data.get("expires_at"))
        if error or (expires_at is not None and expires_at <= timezone.now()):
            return Response({"expires_at": "INVALID_EXPIRY"}, status=400)
        record.expires_at = expires_at
        raw_token = record.rotate()
        record_audit(
            action="api_key.rotate",
            request=request,
            tenant=record.tenant,
            actor=request.user,
            resource_type="api_key",
            resource_id=str(record.uuid),
            metadata={"version": record.key_version, "status": record.status, "expires_at": payload_expiry(record)},
        )
        payload = _api_key_payload(record)
        payload["key"] = raw_token
        return Response(payload)

    @transaction.atomic
    def delete(self, request, key_uuid):
        """Revoke a key without deleting its audit history."""
        record = self._get_key(request, key_uuid)
        if record is None:
            return Response(
                {"detail": "API_KEY_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if record.status == TenantApiKey.Status.REVOKED:
            return Response(status=status.HTTP_204_NO_CONTENT)
        record.status = TenantApiKey.Status.REVOKED
        record.revoked_at = timezone.now()
        record.save(update_fields=["status", "revoked_at", "updated_at"])
        record_audit(
            action="api_key.revoke",
            request=request,
            tenant=record.tenant,
            actor=request.user,
            resource_type="api_key",
            resource_id=str(record.uuid),
            metadata={"name": record.name},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
