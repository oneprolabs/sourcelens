"""Validated, atomic tenant API key lifecycle operations."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.audit import record_audit
from accounts.delegation import is_delegated_request
from accounts.models import Tenant, TenantApiKey, TenantMembership
from accounts.permissions import HasPlatformSuperAdmin
from accounts.services.api_key_notices import create_control_notice, notice_payload


class CanManageTenantApiKeys(BasePermission):
    """Require a live tenant administrator, never staff or delegation inheritance."""

    def has_permission(self, request, view):
        """Check the stored membership without a legacy staff fallback."""
        auth = request.auth
        return bool(
            request.user.is_authenticated
            and request.user.is_active
            and not is_delegated_request()
            and not (isinstance(auth, dict) and auth.get("principal_type") == "tenant_api_key")
            and TenantMembership.objects.filter(
                user=request.user, role="tenant_admin", status="active", tenant__status="active"
            ).exists()
        )


class ApiKeyConflict(APIException):
    """Reject an outdated or terminal credential mutation."""

    status_code = 409
    default_detail = "API_KEY_CONFLICT"


class CanControlPlatformApiKeys(HasPlatformSuperAdmin):
    """Platform credentials must not inherit authority through a tenant API key."""

    def has_permission(self, request, view):
        """Require an active platform user and a human management credential."""
        auth = request.auth
        return bool(
            request.user.is_authenticated
            and request.user.is_active
            and not (isinstance(auth, dict) and auth.get("principal_type") == "tenant_api_key")
            and super().has_permission(request, view)
        )


class KeySnapshotInput(serializers.Serializer):
    """Identify the exact version reviewed by the administrator."""

    uuid = serializers.UUIDField()
    updated_at = serializers.DateTimeField()


class ApiKeyBulkInput(serializers.Serializer):
    """Bound a batch and restrict extensions to the prototype choices."""

    action = serializers.ChoiceField(choices=("enable", "disable", "extend", "revoke"))
    keys = KeySnapshotInput(many=True, allow_empty=False, max_length=100)
    days = serializers.JSONField(required=False)

    def validate(self, attrs):
        """Reject duplicate identities and coercions of the extension choice."""
        ids = [item["uuid"] for item in attrs["keys"]]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({"keys": "DUPLICATE_KEYS"})
        days = attrs.get("days")
        if attrs["action"] == "extend" and (type(days) is not int or days not in (30, 90, 365)):
            raise serializers.ValidationError({"days": "INVALID_EXTENSION"})
        return attrs


class PlatformApiKeyBulkInput(ApiKeyBulkInput):
    """Restrict platform batches to forced disable and revoke operations."""

    action = serializers.ChoiceField(choices=("disable", "revoke"))


def apply_platform_control(record, request, action):
    """Apply one already-locked state transition and leave its platform audit."""
    if action == "disable" and record.status == "disabled":
        return False
    record.status = "disabled" if action == "disable" else "revoked"
    if action == "revoke":
        record.revoked_at = timezone.now()
    record.save(update_fields=["status", "revoked_at", "updated_at"])
    record_audit(
        action=f"api_key.force_{action}",
        request=request,
        tenant=record.tenant,
        actor=request.user,
        resource_type="api_key",
        resource_id=str(record.pk),
        metadata={"name": record.name},
    )
    return True


@transaction.atomic
def control_platform_api_keys(*, request, data):
    """Validate the complete cross-tenant selection and group notices by actual ownership."""
    versions = {item["uuid"]: item["updated_at"] for item in data["keys"]}
    records = list(
        TenantApiKey.objects.select_for_update(of=("self",))
        .select_related("tenant")
        .filter(pk__in=versions)
        .order_by("pk")
    )
    if len(records) != len(versions):
        raise NotFound("API_KEY_NOT_FOUND")
    if any(record.updated_at != versions[record.pk] for record in records):
        raise ApiKeyConflict("API_KEY_SELECTION_CHANGED")
    result = {"changed": 0, "skipped": 0, "skipped_items": [], "notices": []}
    grouped = {}
    for record in records:
        reason = "revoked" if record.status == "revoked" else ""
        if not reason and not apply_platform_control(record, request, data["action"]):
            reason = "unchanged"
        if reason:
            result["skipped_items"].append({"uuid": str(record.pk), "reason": reason})
        else:
            grouped.setdefault(record.tenant_id, []).append(record)
            result["changed"] += 1
    for keys in grouped.values():
        notice = create_control_notice(tenant=keys[0].tenant, actor=request.user, action=data["action"], keys=keys)
        result["notices"].append(notice_payload(notice))
    result["skipped"] = len(result["skipped_items"])
    return result


@transaction.atomic
def mutate_api_keys(*, request, tenant, data):
    """Lock the complete tenant selection before changing any applicable key."""
    versions = {item["uuid"]: item["updated_at"] for item in data["keys"]}
    records = list(TenantApiKey.objects.select_for_update().filter(tenant=tenant, pk__in=versions).order_by("pk"))
    if len(records) != len(versions):
        raise NotFound("API_KEY_NOT_FOUND")
    if any(record.updated_at != versions[record.pk] for record in records):
        raise ApiKeyConflict("API_KEY_SELECTION_CHANGED")
    result = {"changed": 0, "skipped": 0, "skipped_items": []}
    action = data["action"]
    now = timezone.now()
    for record in records:
        reason = ""
        if record.status == "revoked":
            reason = "revoked"
        elif action != "revoke" and record.expires_at is not None and record.expires_at <= now:
            reason = "expired"
        elif action == "extend" and record.expires_at is None:
            reason = "never_expires"
        elif action in ("enable", "disable") and record.status == ("active" if action == "enable" else "disabled"):
            reason = "unchanged"
        if reason:
            result["skipped_items"].append({"uuid": str(record.pk), "reason": reason})
            continue
        if action == "extend":
            try:
                record.expires_at += timedelta(days=data["days"])
            except OverflowError as exc:
                raise serializers.ValidationError({"days": "INVALID_EXTENSION"}) from exc
        else:
            record.status = {"enable": "active", "disable": "disabled", "revoke": "revoked"}[action]
            if action == "revoke":
                record.revoked_at = now
        record.save(update_fields=["status", "revoked_at", "expires_at", "updated_at"])
        record_audit(
            action=f"api_key.bulk_{action}",
            request=request,
            tenant=tenant,
            actor=request.user,
            resource_type="api_key",
            resource_id=str(record.pk),
            metadata={"status": record.status, "days": data.get("days") if action == "extend" else None},
        )
        result["changed"] += 1
    result["skipped"] = len(result["skipped_items"])
    return result


@transaction.atomic
def disable_member_api_keys(*, request, tenant, user):
    """Disable the member's currently active tenant keys without reviving terminal credentials."""
    disabled = []
    records = (
        TenantApiKey.objects.select_for_update()
        .filter(tenant=tenant, created_by=user, status=TenantApiKey.Status.ACTIVE)
        .order_by("pk")
    )
    for record in records:
        record.status = TenantApiKey.Status.DISABLED
        record.save(update_fields=["status", "updated_at"])
        disabled.append(str(record.pk))
        record_audit(
            action="api_key.disable_with_member",
            request=request,
            tenant=tenant,
            actor=request.user,
            resource_type="api_key",
            resource_id=str(record.pk),
            metadata={"member_id": user.pk},
        )
    return disabled


@transaction.atomic
def create_tenant_api_key(*, request, tenant, **values):
    """Serialize issuance with member disable and recheck the creator's live authority."""
    tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
    if (
        tenant.status != Tenant.Status.ACTIVE
        or not TenantMembership.objects.filter(
            tenant=tenant, user_id=request.user.pk, user__is_active=True, status="active", role="tenant_admin"
        ).exists()
    ):
        raise PermissionDenied("API_KEY_CREATOR_AUTHORITY_CHANGED")
    return TenantApiKey.issue(tenant=tenant, created_by=request.user, **values)
