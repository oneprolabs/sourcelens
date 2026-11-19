"""Tenant invitations with expiring, versioned, single-use email credentials."""

import hashlib
import logging
from urllib.parse import quote

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.html import format_html
from rest_framework.exceptions import ValidationError

from accounts.models import Profile, Tenant, TenantGroup, TenantMembership
from accounts.serializers import validate_password_policy
from accounts.services.email import get_email_delivery_options

logger = logging.getLogger(__name__)
INVITATION_SALT = "accounts.tenant-invitation.v1"


def invitation_timeout():
    """Return the bounded lifetime shared by issuance and acceptance."""

    return max(300, int(getattr(settings, "TENANT_INVITATION_TIMEOUT", 3 * 24 * 3600)))


def send_invitation_email(membership, token):
    """Send an escaped invitation using the configured frontend origin."""

    url = f"{settings.FRONTEND_URL.rstrip('/')}/accept-invitation#token={quote(token, safe='')}"
    hours = invitation_timeout() // 3600
    language = membership.tenant.default_language
    copy = {
        "zh-CN": ("邀请你加入", "设置密码并加入", f"邀请链接将在 {hours} 小时后失效。"),
        "es": ("Invitación a", "Establecer contraseña y unirse", f"El enlace caduca en {hours} horas."),
    }.get(language, ("Invitation to", "Set password and join", f"The link expires in {hours} hours."))
    subject = f"{copy[0]} {membership.tenant.name}".replace("\r", " ").replace("\n", " ")
    sender, connection = get_email_delivery_options()
    message = EmailMultiAlternatives(
        subject=subject,
        body=f"{subject}\n\n{copy[1]}\n{url}\n\n{copy[2]}",
        from_email=sender,
        to=[membership.user.email],
        connection=connection,
    )
    message.attach_alternative(
        format_html('<h1>{}</h1><p><a href="{}">{}</a></p><p>{}</p>', subject, url, copy[1], copy[2]), "text/html"
    )
    if message.send() != 1:
        raise OSError("Invitation delivery unavailable")


@transaction.atomic
def prepare_invitation(
    tenant, email, role, group_ids, *, request=None, allow_existing=True, target_id=None, version=None
):
    """Create or retry a pending member without claiming an existing account."""

    tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
    if tenant.status != Tenant.Status.ACTIVE:
        raise ValidationError({"detail": "TENANT_NOT_ACTIVE"})
    if request is not None:
        from accounts.services.member_management import require_member_authority

        require_member_authority(request, tenant)
    groups = list(TenantGroup.objects.filter(tenant=tenant, auth_group_id__in=group_ids))
    if len(groups) != len(set(group_ids)):
        raise ValidationError({"group_ids": "INVALID_TENANT_GROUPS"})
    user = get_user_model().objects.select_for_update().filter(email__iexact=email).first()
    if target_id is not None and (user is None or user.pk != target_id):
        from accounts.services.member_management import MemberSelectionChanged

        raise MemberSelectionChanged()
    if user is not None:
        membership = TenantMembership.objects.select_for_update().filter(user=user, tenant=tenant).first()
        if target_id is not None:
            from accounts.services.member_management import MemberSelectionChanged, member_version

            if membership is None or member_version(membership) != version:
                raise MemberSelectionChanged()
        can_reinvite = membership is not None and (
            membership.status == TenantMembership.Status.INVITED
            or (
                membership.status == TenantMembership.Status.DISABLED
                and membership.invited_at is not None
                and membership.joined_at is None
            )
        )
        if not allow_existing or not can_reinvite or user.is_active:
            raise ValidationError({"email": "INVITATION_EMAIL_UNAVAILABLE"})
    else:
        # The username's unique index serializes concurrent invitations for the same canonical email.
        username = email if len(email) <= 150 else "invited_" + hashlib.sha256(email.encode()).hexdigest()
        try:
            with transaction.atomic():
                user = get_user_model().objects.create_user(
                    username=username, email=email, password=None, is_active=False
                )
        except IntegrityError:
            raise ValidationError({"email": "INVITATION_EMAIL_UNAVAILABLE"}) from None
        Profile.objects.update_or_create(
            user=user,
            defaults={
                "registration_completed": False,
                "language": tenant.default_language,
                "timezone": tenant.timezone,
            },
        )
        membership = TenantMembership.objects.create(tenant=tenant, user=user, status="invited", role=role)
    membership.role = role
    membership.status = TenantMembership.Status.INVITED
    membership.invited_at = membership.invited_at or timezone.now()
    membership.auth_version += 1
    membership.save(update_fields=["role", "status", "invited_at", "auth_version", "updated_at"])
    user.groups.set([group.auth_group_id for group in groups])
    return membership, _credential(membership)


@transaction.atomic
def prepare_resend(tenant, user_id, *, request=None):
    """Rotate one pending member's credential before attempting delivery."""

    from rest_framework.exceptions import NotFound

    tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
    if tenant.status != Tenant.Status.ACTIVE:
        raise ValidationError({"detail": "TENANT_NOT_ACTIVE"})
    if request is not None:
        from accounts.services.member_management import require_member_authority

        require_member_authority(request, tenant)
    membership = (
        TenantMembership.objects.select_for_update(of=("self",))
        .select_related("tenant", "user__profile")
        .filter(tenant=tenant, user_id=user_id)
        .first()
    )
    if membership is None:
        raise NotFound("MEMBER_NOT_FOUND")
    if membership.status != TenantMembership.Status.INVITED or membership.user.is_active:
        raise ValidationError({"detail": "MEMBER_NOT_INVITED"})
    membership.auth_version += 1
    membership.save(update_fields=["auth_version", "updated_at"])
    return membership, _credential(membership)


def _credential(membership):
    """Sign membership identity and revocation state without storing a raw token."""

    return signing.dumps(
        {
            "membership": str(membership.pk),
            "version": membership.auth_version,
            "email": membership.user.email,
        },
        salt=INVITATION_SALT,
    )


@transaction.atomic
def accept_invitation(token, password, confirmation):
    """Activate a still-pending member exactly once after mailbox proof."""

    try:
        data = signing.loads(token, salt=INVITATION_SALT, max_age=invitation_timeout())
        membership_id = data["membership"]
        # Serialize with tenant suspension and invitation issuance.
        tenant_id = TenantMembership.objects.values_list("tenant_id", flat=True).get(pk=membership_id)
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        membership = TenantMembership.objects.select_for_update().select_related("user").get(pk=membership_id)
    except (signing.BadSignature, KeyError, TypeError, ValueError, TenantMembership.DoesNotExist, Tenant.DoesNotExist):
        raise ValidationError({"token": "INVITATION_INVALID_OR_EXPIRED"}) from None
    user = membership.user
    if (
        membership.status != TenantMembership.Status.INVITED
        or tenant.status != Tenant.Status.ACTIVE
        or user.is_active
        or membership.auth_version != data.get("version")
        or user.email != data.get("email")
    ):
        raise ValidationError({"token": "INVITATION_INVALID_OR_EXPIRED"})
    if password != confirmation:
        raise ValidationError({"new_password2": "PASSWORDS_DO_NOT_MATCH"})
    validate_password_policy(password, user=user)
    user.set_password(password)
    user.is_active = True
    user.save(update_fields=["password", "is_active"])
    membership.status = TenantMembership.Status.ACTIVE
    membership.joined_at = timezone.now()
    membership.auth_version += 1
    membership.save(update_fields=["status", "joined_at", "auth_version", "updated_at"])
    Profile.objects.update_or_create(
        user=user,
        defaults={
            "registration_completed": True,
            "registration_token": None,
            "registration_token_expires": None,
        },
    )
    EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={"verified": True, "primary": True})
    return membership


def deliver_invitation(membership, token):
    """Report delivery failure without logging the invitation credential."""

    try:
        send_invitation_email(membership, token)
    except Exception:
        logger.warning("Tenant invitation delivery failed", extra={"member_id": membership.user_id})
        return False
    return True
