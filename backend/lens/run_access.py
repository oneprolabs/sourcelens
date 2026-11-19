"""Shared boundaries for run usage and tenant operations."""

from django.db.models import Q
from rest_framework import permissions

from accounts.access import get_user_membership, get_user_tenant
from accounts.authentication import AGENT_SCOPE_CLAIM, AGENT_TOKEN_SCOPE, tenant_api_key_allows_assistant
from accounts.delegation import get_active_delegation, is_delegated_request
from accounts.permissions import HasPlatformSuperAdmin
from accounts.principals import ApiKeyPrincipal
from lens.assistant_management import can_read_assistant_configuration
from lens.models import Run, user_sees_all_assistants


class CanInspectTenantRuns(permissions.BasePermission):
    """Require explicit tenant authority and honor read-only delegation for mutations."""

    def has_permission(self, request, view):
        """A console feature or staff flag must not grant another tenant's history."""
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        auth = getattr(request, "auth", None)
        if isinstance(auth, dict) and auth.get("principal_type") == "tenant_api_key":
            return False
        if HasPlatformSuperAdmin().has_permission(request, view) and not is_delegated_request():
            return False
        membership = get_user_membership(user)
        if membership is None:
            return not is_delegated_request() and user_sees_all_assistants(user)
        if not (
            membership.status == "active" and membership.role == "tenant_admin" and membership.tenant.status == "active"
        ):
            return False
        delegation = get_active_delegation(user)
        return not (delegation and delegation.mode == "readonly" and request.method not in permissions.SAFE_METHODS)


def scope_run_queryset(queryset, tenant_id):
    """A missing tenant is the legacy unowned scope, never a global bypass."""
    return queryset.filter(tenant_id=tenant_id, session__tenant_id=tenant_id)


def can_inspect_run_configuration(request, run):
    """Inspect only runs and assistant configuration belonging to the same administrative scope."""
    if request is None:
        return False
    return bool(
        not run.configuration_evidence_restricted
        and run.tenant_id == run.session.tenant_id
        and can_read_assistant_configuration(request, run.session.assistant)
    )


def can_inspect_run_evidence(request, run):
    """Require configuration authority over the entire connected, tenant-scoped run family.

    Parent steps and frozen diagnoses can embed delegated runtime evidence.
    Checking the family also protects a child trajectory's parent progress view.
    """
    frontier = [run]
    seen = set()
    while frontier:
        if any(not can_inspect_run_configuration(request, item) for item in frontier):
            return False
        seen.update(item.pk for item in frontier)
        parent_ids = [item.parent_run_id for item in frontier if item.parent_run_id]
        frontier = list(
            scope_run_queryset(
                Run.objects.filter(Q(pk__in=parent_ids) | Q(parent_run_id__in=[item.pk for item in frontier])),
                run.tenant_id,
            )
            .exclude(pk__in=seen)
            .select_related("session__assistant")
        )
    return True


class CanUseConversations(permissions.BasePermission):
    """Require active owner authority and honor the delegated management boundary."""

    def has_permission(self, request, view):
        """Ordinary owner access remains separate from delegated management."""
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if is_delegated_request():
            return CanInspectTenantRuns().has_permission(request, view)
        membership = get_user_membership(user)
        return membership is None or (membership.status == "active" and membership.tenant.status == "active")


def can_audit_session_content(request, session=None):
    """Allow runtime inspection with web credentials and explicit tenant authority."""
    auth = getattr(request, "auth", None)
    if auth is not None and auth.get(AGENT_SCOPE_CLAIM) == AGENT_TOKEN_SCOPE:
        return False
    if not CanInspectTenantRuns().has_permission(request, None):
        return False
    tenant = get_user_tenant(request.user)
    return session is None or session.tenant_id == (tenant.pk if tenant is not None else None)


def can_access_session_content(request, session):
    """Share the owner and auditor boundary across images, documents, sources and files."""
    tenant = get_user_tenant(request.user)
    if session.tenant_id != (tenant.pk if tenant is not None else None):
        return False
    if not tenant_api_key_allows_assistant(request, session.assistant.uuid):
        return False
    if isinstance(request.user, ApiKeyPrincipal):
        return (
            session.user_id is None
            and session.api_key_id == request.user.key.pk
            and session.api_key.tenant_id == session.tenant_id
            and session.assistant.tenant_id == session.tenant_id
            and CanUseConversations().has_permission(request, None)
        )
    if session.api_key_id is None and session.user_id is not None and session.user_id == request.user.pk:
        return CanUseConversations().has_permission(request, None)
    return can_audit_session_content(request, session)
