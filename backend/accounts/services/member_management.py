"""Versioned tenant member batches and explicit administrative authority."""

import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.audit import record_audit
from accounts.delegation import is_delegated_request
from accounts.models import PasswordLoginState, Tenant, TenantApiKey, TenantApiKeyCall, TenantGroup, TenantMembership
from accounts.principals import ApiKeyPrincipal
from accounts.services.api_key_lifecycle import disable_member_api_keys
from accounts.services.password_login import active_password_lock, password_locked_until, with_password_state


class CanManageTenantMembers(BasePermission):
    """Require a current human tenant administrator without staff or delegation fallback."""

    def has_permission(self, request, view):
        """Read current authority instead of trusting a cached membership or staff flag."""
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and not isinstance(user, ApiKeyPrincipal)
            and not is_delegated_request()
            and not (isinstance(request.auth, dict) and request.auth.get("principal_type") == "tenant_api_key")
            and TenantMembership.objects.filter(
                user_id=user.pk, user__is_active=True, role="tenant_admin", status="active", tenant__status="active"
            ).exists()
        )


def member_version(membership):
    """Bind a reviewed row to the membership, account status and effective group assignments."""
    values = [
        str(membership.pk),
        str(membership.tenant_id),
        membership.role,
        membership.status,
        membership.auth_version,
        membership.updated_at.isoformat(),
        membership.user.is_active,
        membership.user.email,
        str(password_locked_until(membership)),
        sorted(group.pk for group in membership.user.groups.all()),
    ]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


class MemberSelection(serializers.Serializer):
    """Identify exactly the row the administrator reviewed."""

    id = serializers.IntegerField(min_value=1)
    version = serializers.CharField(max_length=64)


class MemberBulkInput(serializers.Serializer):
    """Bound a tenant-local action and reject unrelated action parameters."""

    action = serializers.ChoiceField(choices=("role", "groups", "enable", "disable", "resend"))
    members = MemberSelection(many=True, allow_empty=False, max_length=100)
    role = serializers.ChoiceField(choices=TenantMembership.Role.choices, required=False)
    group_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=100, required=False
    )
    disable_api_keys = serializers.JSONField(required=False)
    preview = serializers.BooleanField(default=False)

    def validate(self, attrs):
        """Do not silently accept coercion, duplicate identities or mismatched action fields."""
        ids = [row["id"] for row in attrs["members"]]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({"members": "DUPLICATE_MEMBERS"})
        if attrs["action"] == "role" and "role" not in attrs:
            raise serializers.ValidationError({"role": "REQUIRED"})
        if attrs["action"] == "groups" and "group_ids" not in attrs:
            raise serializers.ValidationError({"group_ids": "REQUIRED"})
        for field, action in (("role", "role"), ("group_ids", "groups"), ("disable_api_keys", "disable")):
            if field in attrs and attrs["action"] != action:
                raise serializers.ValidationError({field: "UNEXPECTED_ACTION_FIELD"})
        if "disable_api_keys" in attrs and type(attrs["disable_api_keys"]) is not bool:
            raise serializers.ValidationError({"disable_api_keys": "INVALID_KEY_DISABLE_OPTION"})
        return attrs


class MemberSelectionChanged(APIException):
    """Require refreshed confirmation after another administrator changes the selection."""

    status_code = 409
    default_detail = "MEMBER_SELECTION_CHANGED"


def require_member_authority(request, tenant):
    """Recheck authority under the same tenant lock used by the mutation."""
    if (
        not CanManageTenantMembers().has_permission(request, None)
        or not TenantMembership.objects.filter(
            user_id=request.user.pk,
            tenant=tenant,
            role="tenant_admin",
            status="active",
            user__is_active=True,
            tenant__status="active",
        ).exists()
    ):
        raise PermissionDenied("MEMBER_ADMIN_AUTHORITY_CHANGED")


@transaction.atomic
def unlock_member(*, request, user_id, version):
    """Clear only the reviewed password lock without changing account or membership state."""
    current = TenantMembership.objects.filter(user_id=request.user.pk).values_list("tenant_id", flat=True).first()
    tenant = Tenant.objects.select_for_update().filter(pk=current).first()
    if tenant is None:
        raise PermissionDenied("MEMBER_ADMIN_AUTHORITY_CHANGED")
    require_member_authority(request, tenant)
    member = (
        TenantMembership.objects.select_for_update(of=("self",))
        .select_related("user", "tenant")
        .prefetch_related("user__groups")
        .filter(tenant=tenant, user_id=user_id)
        .first()
    )
    if member is None:
        raise NotFound("MEMBER_NOT_FOUND")
    state = PasswordLoginState.objects.select_for_update().filter(email=member.user.email.strip().lower()).first()
    member.password_locked_until = state.locked_until if state else None
    if member_version(member) != version:
        raise MemberSelectionChanged()
    if not active_password_lock(member):
        raise serializers.ValidationError({"status": "MEMBER_NOT_PASSWORD_LOCKED"})
    state.failed_attempts = 0
    state.locked_until = None
    state.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    member.password_locked_until = None
    record_audit(
        action="member.unlock",
        request=request,
        tenant=tenant,
        actor=request.user,
        resource_type="member",
        resource_id=str(user_id),
    )
    return member


def _locked_selection(request, data, expected_tenant=None):
    """Serialize with member lifecycle and key issuance before reading the complete batch."""
    current = TenantMembership.objects.filter(user_id=request.user.pk).values_list("tenant_id", flat=True).first()
    tenant = Tenant.objects.select_for_update(no_key=True).filter(pk=current).first()
    if tenant is None or (expected_tenant is not None and tenant.pk != expected_tenant):
        raise PermissionDenied("MEMBER_ADMIN_AUTHORITY_CHANGED")
    require_member_authority(request, tenant)
    versions = {row["id"]: row["version"] for row in data["members"]}
    members = list(
        with_password_state(TenantMembership.objects.select_for_update(of=("self",)))
        .select_related("user", "tenant")
        .prefetch_related("user__groups")
        .filter(tenant=tenant, user_id__in=versions)
        .order_by("user_id")
    )
    if len(members) != len(versions):
        raise NotFound("MEMBER_NOT_FOUND")
    if any(member_version(member) != versions[member.user_id] for member in members):
        raise MemberSelectionChanged()
    return tenant, members


def _skip_reason(member, data):
    """Explain inapplicable states without bypassing invitation acceptance."""
    action = data["action"]
    if action == "role":
        return "unchanged" if member.role == data["role"] else ""
    if action == "groups":
        if member.status != "active" or not member.user.is_active:
            return "inactive_member"
        return "unchanged" if set(data["group_ids"]).issubset({g.pk for g in member.user.groups.all()}) else ""
    if action == "enable":
        if member.status == "invited" or (member.invited_at and not member.joined_at):
            return "invitation_required"
        return "unchanged" if member.status != "disabled" else ""
    if action == "disable":
        return "unchanged" if member.status == "disabled" and not member.user.is_active else ""
    return "not_invited" if member.status != "invited" or member.user.is_active else ""


def _key_summary(tenant, members):
    """Count real creator keys even after the creator has been demoted."""
    keys = TenantApiKey.objects.filter(tenant=tenant, created_by_id__in=[m.user_id for m in members])
    now = timezone.now()
    summary = keys.aggregate(
        total=Count("pk"),
        active=Count("pk", filter=Q(status="active") & (Q(expires_at__isnull=True) | Q(expires_at__gt=now))),
    )
    summary["calls_7d"] = TenantApiKeyCall.objects.filter(
        tenant=tenant, key__in=keys, created_at__gte=now - timedelta(days=7)
    ).count()
    return summary


@transaction.atomic
def mutate_members(*, request, data):
    """Apply a whole reviewed selection with last-admin and self-disable invariants."""
    if data["action"] == "resend":
        raise serializers.ValidationError("RESEND_REQUIRES_DELIVERY")
    tenant, members = _locked_selection(request, data)
    action = data["action"]
    group_ids = data.get("group_ids", [])
    if action == "groups":
        groups = list(TenantGroup.objects.select_for_update().filter(tenant=tenant, auth_group_id__in=group_ids))
        if len(groups) != len(set(group_ids)):
            raise serializers.ValidationError({"group_ids": "INVALID_TENANT_GROUPS"})
    changing = [member for member in members if not _skip_reason(member, data)]
    if action == "disable" and any(member.user_id == request.user.pk for member in changing):
        raise serializers.ValidationError({"status": "CANNOT_DISABLE_SELF"})
    if action == "disable" or (action == "role" and data["role"] == "member"):
        removed_admins = [
            m.user_id for m in changing if m.role == "tenant_admin" and m.status == "active" and m.user.is_active
        ]
        if (
            removed_admins
            and not TenantMembership.objects.filter(
                tenant=tenant, role="tenant_admin", status="active", user__is_active=True
            )
            .exclude(user_id__in=removed_admins)
            .exists()
        ):
            raise serializers.ValidationError({"role": "LAST_TENANT_ADMIN_REQUIRED"})
    result = {"changed": 0, "skipped": 0, "failed": 0, "items": [], "disabled_keys": 0}
    if action == "disable":
        result["key_summary"] = _key_summary(tenant, changing)
    for member in members:
        reason = _skip_reason(member, data)
        item = {"id": member.user_id, "status": "skipped" if reason else "changed", "reason": reason}
        result["items"].append(item)
        if reason:
            result["skipped"] += 1
            continue
        if not data["preview"]:
            if action == "role":
                member.role = data["role"]
                member.save(update_fields=["role", "updated_at"])
            elif action == "groups":
                member.user.groups.add(*group_ids)
                member.save(update_fields=["updated_at"])
            elif action in {"enable", "disable"}:
                member.status = "active" if action == "enable" else "disabled"
                member.user.is_active = action == "enable"
                member.user.save(update_fields=["is_active"])
                member.save(update_fields=["status", "updated_at"])
                if data.get("disable_api_keys"):
                    result["disabled_keys"] += len(
                        disable_member_api_keys(request=request, tenant=tenant, user=member.user)
                    )
            record_audit(
                action=f"member.bulk_{action}",
                request=request,
                tenant=tenant,
                actor=request.user,
                resource_type="member",
                resource_id=str(member.user_id),
                metadata={"role": data.get("role"), "group_ids": group_ids},
            )
        result["changed"] += 1
    return result


def resend_members(*, request, data, allow_send):
    """Validate the whole batch, then rotate and send each eligible invitation independently."""
    from accounts.services.tenant_invitations import deliver_invitation, prepare_resend

    with transaction.atomic():
        tenant, members = _locked_selection(request, data)
        tenant_id = tenant.pk
        selected = {row["id"]: row for row in data["members"]}
        rows = [(member.user_id, _skip_reason(member, data)) for member in members]
    result = {"changed": 0, "skipped": 0, "failed": 0, "items": []}
    for user_id, reason in rows:
        status = "skipped" if reason else "changed"
        if not reason and not data["preview"]:
            try:
                with transaction.atomic():
                    tenant, current = _locked_selection(
                        request, {**data, "members": [selected[user_id]]}, expected_tenant=tenant_id
                    )
                    reason = _skip_reason(current[0], data)
                    if reason:
                        status = "skipped"
                    elif not allow_send():
                        status, reason = "failed", "rate_limited"
                    else:
                        member, token = prepare_resend(tenant, user_id)
                if not reason:
                    sent = deliver_invitation(member, token)
                    status, reason = ("changed", "") if sent else ("failed", "delivery_failed")
                    record_audit(
                        action="member.bulk_resend",
                        request=request,
                        tenant=tenant,
                        actor=request.user,
                        resource_type="member",
                        resource_id=str(user_id),
                        result="success" if sent else "failed",
                        metadata={"delivered": sent},
                    )
            except APIException:
                status, reason = "failed", "selection_changed"
        result[status] += 1
        result["items"].append({"id": user_id, "status": status, "reason": reason})
    return result
