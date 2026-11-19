"""Non-user identities for tenant API execution."""

from django.utils import timezone


class ApiKeyPrincipal:
    """Represent one credential without inheriting its creator's identity or grants."""

    is_authenticated = True
    is_anonymous = False
    is_staff = False
    is_superuser = False
    principal_type = "tenant_api_key"

    def __init__(self, key):
        """Bind this principal to its own credential and tenant."""
        self.key = key
        self.pk = key.pk
        self.tenant = key.tenant

    @property
    def is_active(self):
        """Allow new work only while this credential and tenant remain active."""
        return (
            self.key.status == "active"
            and self.tenant.status == "active"
            and (self.key.expires_at is None or self.key.expires_at > timezone.now())
        )


def session_owner_filter(principal, prefix=""):
    """Return mutually exclusive ORM ownership fields for a session or related run."""
    if isinstance(principal, ApiKeyPrincipal):
        return {
            f"{prefix}api_key_id": principal.key.pk,
            f"{prefix}api_key__tenant_id": principal.tenant.pk,
            f"{prefix}assistant__tenant_id": principal.tenant.pk,
            f"{prefix}user__isnull": True,
        }
    return {f"{prefix}user": principal, f"{prefix}api_key__isnull": True}


def session_owner_values(principal):
    """Return model fields when creating a session for the execution principal."""
    if isinstance(principal, ApiKeyPrincipal):
        return {"user": None, "api_key": principal.key}
    return {"user": principal, "api_key": None}
