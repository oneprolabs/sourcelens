"""Send tenant-scoped recovery mail for a reviewed, accepted member."""

from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.db import transaction
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, Throttled

from accounts.audit import record_audit
from accounts.models import Profile, Tenant, TenantMembership
from accounts.services.email import PasswordResetEmailService
from accounts.services.member_management import MemberSelectionChanged, member_version, require_member_authority
from accounts.services.password_login import with_password_state


class MemberResetDeliveryUnavailable(APIException):
    """Report administrative delivery failure without exposing transport internals."""

    status_code = 503
    default_detail = "MEMBER_RESET_DELIVERY_UNAVAILABLE"


def can_reset_member_password(member):
    """Keep invitation acceptance separate from recovery of an established account."""
    profile = getattr(member.user, "profile", None)
    return bool(
        member.status != "invited"
        and not (member.invited_at and not member.joined_at)
        and profile
        and profile.registration_completed
        and member.user.email
    )


@transaction.atomic
def _prepare_reset(*, request, user_id, version):
    """Freeze the authorized recipient and reserve a cooldown before leaving database locks."""
    current = TenantMembership.objects.filter(user_id=request.user.pk).values_list("tenant_id", flat=True).first()
    tenant = Tenant.objects.select_for_update(no_key=True).filter(pk=current).first()
    if tenant is None:
        raise PermissionDenied("MEMBER_ADMIN_AUTHORITY_CHANGED")
    require_member_authority(request, tenant)
    member = (
        with_password_state(TenantMembership.objects.select_for_update(of=("self", "user")))
        .select_related("user", "user__profile")
        .prefetch_related("user__groups")
        .filter(tenant=tenant, user_id=user_id)
        .first()
    )
    if member is None:
        raise NotFound("MEMBER_NOT_FOUND")
    profile = Profile.objects.select_for_update().filter(user_id=user_id).first()
    if member_version(member) != version:
        raise MemberSelectionChanged()
    if not can_reset_member_password(member) or not profile or not profile.registration_completed:
        raise serializers.ValidationError({"member": "MEMBER_RESET_NOT_AVAILABLE"})
    try:
        reserved = cache.add(f"member-password-reset:{user_id}", True, timeout=60)
    except Exception:
        raise MemberResetDeliveryUnavailable() from None
    if not reserved:
        raise Throttled(wait=60, detail="MEMBER_RESET_COOLDOWN")
    return (
        tenant,
        {
            "email": member.user.email,
            "language": profile.language,
            "uid": urlsafe_base64_encode(force_bytes(user_id)),
            "token": default_token_generator.make_token(member.user),
        },
    )


def reset_member_password(*, request, user_id, version):
    """Request one real recovery email without changing password, activation or password lock."""
    tenant, delivery = _prepare_reset(request=request, user_id=user_id, version=version)
    try:
        sent = PasswordResetEmailService.send_password_reset_email(**delivery)
    except Exception:
        sent = False
    record_audit(
        action="member.password_reset",
        request=request,
        tenant=tenant,
        actor=request.user,
        resource_type="member",
        resource_id=str(user_id),
        result="success" if sent else "failed",
    )
    if not sent:
        raise MemberResetDeliveryUnavailable()
    return {"status": "sent", "member_id": user_id}
