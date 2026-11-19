"""Request-scoped platform delegation into one tenant."""

from contextvars import ContextVar

from django.utils import timezone

_delegation_id = ContextVar("tenant_delegation_id", default=None)


class DelegatedMembership:
    """Lightweight tenant-admin membership used only during delegation."""

    role = "tenant_admin"
    status = "active"
    auth_version = 0
    pk = None
    joined_at = None

    def __init__(self, delegation):
        self.tenant = delegation.tenant
        self.delegation = delegation

    def __str__(self):
        return f"Delegated {self.tenant}"


def set_delegation_id(value):
    """Set the delegation id for the current request."""
    _delegation_id.set(value or None)


def clear_delegation_id():
    """Clear the request-scoped delegation id."""
    _delegation_id.set(None)


def is_delegated_request():
    """Return whether the current request carries a delegation id."""
    return bool(_delegation_id.get())


def get_active_delegation(user):
    """Return the active delegation for this user, or None."""
    delegation_id = _delegation_id.get()
    if not delegation_id or not (user and user.is_authenticated and user.is_active):
        return None
    from accounts.principals import ApiKeyPrincipal

    if isinstance(user, ApiKeyPrincipal):
        return None
    from accounts.models import Profile, TenantDelegation

    is_platform_admin = bool(
        user.is_superuser
        or Profile.objects.filter(
            user=user,
            platform_role=Profile.PlatformRole.SUPER_ADMIN,
        ).exists()
    )
    if not is_platform_admin:
        return None
    delegation = (
        TenantDelegation.objects.select_related("tenant")
        .filter(
            uuid=delegation_id,
            platform_user_id=user.pk,
            status=TenantDelegation.Status.ACTIVE,
            tenant__status="active",
        )
        .first()
    )
    if delegation is None or delegation.expires_at <= timezone.now():
        return None
    return delegation
