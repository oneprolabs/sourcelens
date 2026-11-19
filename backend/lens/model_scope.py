"""Model configuration authorization and immutable metering ownership."""

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from django.core.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.access import get_user_membership
from accounts.delegation import get_active_delegation, is_delegated_request
from accounts.permissions import HasPlatformSuperAdmin

_call_scope = ContextVar("model_call_scope", default=())


def model_tenant(request):
    """Resolve platform or delegated/ordinary tenant administration."""

    platform = HasPlatformSuperAdmin().has_permission(request, None)
    if request.headers.get("X-Model-Scope") == "platform":
        if not platform:
            raise PermissionDenied("Platform model access is required.")
        return None
    if platform and not is_delegated_request():
        return None
    membership = get_user_membership(request.user)
    if not membership or membership.status != "active" or membership.tenant.status != "active":
        raise PermissionDenied("An active tenant is required.")
    if membership.role != "tenant_admin":
        raise PermissionDenied("Tenant administrator access is required.")
    return membership.tenant


class HasModelAccess(BasePermission):
    """Do not grant platform privileges to legacy staff accounts."""

    def has_permission(self, request, view):
        """Check scope and readonly delegation before inspecting any model."""

        if not request.user or not request.user.is_authenticated:
            return False
        model_tenant(request)
        delegation = get_active_delegation(request.user)
        if delegation and delegation.mode == "readonly" and request.method not in {"GET", "HEAD", "OPTIONS"}:
            raise PermissionDenied("This delegation is read only.")
        return True


def model_queryset(tenant):
    """Require explicit ownership, including for platform configurations."""

    from agentcore_metering.adapters.django.models import LLMConfig

    return LLMConfig.objects.filter(ownership__isnull=False, ownership__tenant=tenant)


def default_model_ref(tenant, model_type="llm"):
    """Resolve defaults within one scope, without a global fallback."""

    return (
        model_queryset(tenant)
        .filter(model_type=model_type, is_active=True)
        .order_by("-is_default", "created_at", "pk")
        .values_list("uuid", flat=True)
        .first()
    )


def is_platform_model(model_ref):
    """Check a server-selected platform feature model, without granting tenant access."""

    try:
        return model_queryset(None).filter(uuid=UUID(str(model_ref)), is_active=True).exists()
    except (ValueError, TypeError):
        return False


@contextmanager
def model_call_scope(tenant):
    """Pin metering to the business tenant throughout a call or stream."""

    token = _call_scope.set((tenant,))
    try:
        yield
    finally:
        _call_scope.reset(token)


def runtime_model_tenant(state):
    """Prefer server-owned run provenance over mutable user membership."""

    from accounts.models import TenantMembership
    from lens.models import Run

    if "_model_scope" in state:
        return state["_model_scope"][0]
    metadata = state.get("metadata") or {}
    run_uuid = metadata.get("run_uuid")
    if run_uuid:
        try:
            run = Run.objects.select_related("tenant").get(uuid=run_uuid)
        except (Run.DoesNotExist, ValidationError, ValueError, TypeError):
            raise ValueError("Model call Run is unavailable.") from None
        if run.tenant and run.tenant.status != "active":
            raise ValueError("Model tenant is inactive.")
        return run.tenant
    if _call_scope.get():
        return _call_scope.get()[0]
    user_id = state.get("user_id")
    if user_id:
        member = (
            TenantMembership.objects.select_related("tenant")
            .filter(user_id=user_id, status="active", tenant__status="active")
            .first()
        )
        if member is None:
            from django.contrib.auth import get_user_model

            from accounts.models import Profile

            platform = Profile.objects.filter(user_id=user_id, platform_role="super_admin").exists()
            if platform or get_user_model().objects.filter(pk=user_id, is_superuser=True, is_active=True).exists():
                return None
            raise ValueError("An active model tenant is required.")
        return member.tenant
    return None


def authorized_model_ref(model_ref, tenant, model_type="llm"):
    """Reject foreign, inactive and unknown configs before provider I/O."""

    query = model_queryset(tenant).filter(model_type=model_type, is_active=True)
    if model_ref:
        try:
            query = query.filter(uuid=UUID(str(model_ref)))
        except (ValidationError, ValueError, TypeError):
            raise ValueError("Model configuration is unavailable.") from None
        value = query.values_list("uuid", flat=True).first()
    else:
        value = default_model_ref(tenant, model_type)
    if value is None:
        raise ValueError("Model configuration is unavailable in this scope.")
    if tenant is not None and tenant.status != "active":
        raise ValueError("Model tenant is inactive.")
    return str(value)


def own_model_usage(sender, instance, created=False, raw=False, **kwargs):
    """Persist provenance once; unknown legacy writers remain invisible."""

    if not created or raw:
        return
    from lens.models import LLMConfigScope, LLMUsageScope, Run

    metadata = instance.metadata or {}
    run_uuid = metadata.get("run_uuid")
    if run_uuid:
        try:
            run = Run.objects.filter(uuid=run_uuid).first()
        except (ValidationError, ValueError, TypeError):
            return
        if run is None:
            return
        tenant_id = run.tenant_id
    elif _call_scope.get():
        tenant = _call_scope.get()[0]
        tenant_id = tenant.pk if tenant else None
    else:
        ref = metadata.get("selected_model_ref") or metadata.get("config_uuid")
        try:
            owner = LLMConfigScope.objects.filter(config__uuid=UUID(str(ref))).first()
        except (ValueError, TypeError):
            return
        if owner is None:
            return
        tenant_id = owner.tenant_id
    LLMUsageScope.objects.create(usage=instance, tenant_id=tenant_id)
