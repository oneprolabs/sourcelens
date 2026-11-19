"""Resolve notification ownership identically for HTTP and background delivery."""

from contextlib import contextmanager
from contextvars import ContextVar

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.access import get_user_membership
from accounts.delegation import get_active_delegation, is_delegated_request
from accounts.permissions import HasPlatformSuperAdmin

_current_scope = ContextVar("notification_scope", default=())


def notification_tenant(request):
    """Resolve an authorized scope; client headers never grant platform access."""

    platform = HasPlatformSuperAdmin().has_permission(request, None)
    if request.headers.get("X-Notification-Scope") == "platform":
        if not platform:
            raise PermissionDenied("Platform notification access is required.")
        return None
    if platform and not is_delegated_request():
        return None
    membership = get_user_membership(request.user)
    if not membership or membership.status != "active" or membership.tenant.status != "active":
        raise PermissionDenied("An active tenant is required.")
    if membership.role != "tenant_admin":
        raise PermissionDenied("Tenant administrator access is required.")
    return membership.tenant


class HasNotificationAccess(BasePermission):
    """Require an active tenant administrator or platform administrator."""

    def has_permission(self, request, view):
        """Resolve scope before any endpoint can inspect global data."""

        if not request.user or not request.user.is_authenticated:
            return False
        notification_tenant(request)
        delegation = get_active_delegation(request.user)
        writes = request.method not in {"GET", "HEAD", "OPTIONS"} or "/register/poll/" in request.path
        if delegation and delegation.mode == "readonly" and writes:
            raise PermissionDenied("This delegation is read only.")
        return True


@contextmanager
def notification_record_scope(tenant):
    """Pin records from synchronous vendor senders to the selected scope."""

    token = _current_scope.set((tenant,))
    try:
        yield
    finally:
        _current_scope.reset(token)


def channel_queryset(tenant):
    """Unowned channels stay invisible, including to platform collection queries."""

    from agentcore_notifier.adapters.django.models import NotificationChannel

    return NotificationChannel.objects.filter(ownership__tenant=tenant, ownership__isnull=False)


def notification_audit(request, action, resource_type, resource_id="", tenant=None):
    """Record ownership and action without message bodies or channel credentials."""

    from accounts.audit import record_audit

    record_audit(
        action=action,
        request=request,
        actor=request.user,
        tenant=tenant,
        delegation=get_active_delegation(request.user),
        resource_type=resource_type,
        resource_id=str(resource_id),
    )


def own_notification_record(sender, instance, created=False, raw=False, **kwargs):
    """Persist the send scope once, before channel deletion can erase its provenance."""

    if not created or raw:
        return
    from accounts.models import TenantMembership
    from lens.models import NotificationChannelScope, NotificationRecordScope

    if instance.channel_link_id:
        owner = NotificationChannelScope.objects.filter(channel_id=instance.channel_link_id).first()
        if owner is None:
            return
        tenant_id = owner.tenant_id
    elif _current_scope.get():
        tenant = _current_scope.get()[0]
        tenant_id = tenant.pk if tenant else None
    elif instance.user_id:
        tenant_id = (
            TenantMembership.objects.filter(user_id=instance.user_id).values_list("tenant_id", flat=True).first()
        )
        if tenant_id is None:
            return
    else:
        return
    NotificationRecordScope.objects.create(record=instance, tenant_id=tenant_id)
