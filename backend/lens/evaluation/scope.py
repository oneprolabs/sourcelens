"""Persistent tenant boundaries for evaluation content and asynchronous evidence."""

from django.db.models import F, Q
from rest_framework import permissions

from accounts.access import get_user_membership
from accounts.delegation import is_delegated_request
from accounts.permissions import HasPlatformSuperAdmin, HasRunEvaluationsAccess
from lens.assistant_management import assistant_configuration_scope
from lens.run_access import CanInspectTenantRuns


class HasEvaluationAccess(CanInspectTenantRuns):
    """Tenant administrators and valid delegates manage evaluations within their scope."""

    def has_permission(self, request, view):
        """Preserve explicit legacy evaluation permissions only for unowned history."""
        return super().has_permission(request, view) and (
            get_user_membership(request.user) is not None or HasRunEvaluationsAccess().has_permission(request, view)
        )


class HasEvaluationMetricAccess(permissions.BasePermission):
    """Global scoring templates are writable only by platform or legacy operators."""

    def has_permission(self, request, view):
        """Allow tenant reads without granting write authority over shared defaults."""
        auth = getattr(request, "auth", None)
        if isinstance(auth, dict) and auth.get("principal_type") == "tenant_api_key":
            return False
        if not is_delegated_request() and HasPlatformSuperAdmin().has_permission(request, view):
            return request.user.is_active
        if request.method in permissions.SAFE_METHODS:
            return HasEvaluationAccess().has_permission(request, view)
        if is_delegated_request():
            return False
        return get_user_membership(request.user) is None and HasEvaluationAccess().has_permission(request, view)


def evaluation_queryset(queryset, request, prefix=""):
    """Require both frozen ownership and current assistant ownership."""
    allowed, tenant_id = assistant_configuration_scope(request)
    if not allowed:
        return queryset.none()
    return queryset.filter(
        **{
            f"{prefix}tenant_id": tenant_id,
            f"{prefix}assistant__tenant_id": tenant_id,
        }
    )


def evaluation_run_queryset(queryset, request):
    """A batch cannot drift into another dataset's administrative scope."""
    allowed, tenant_id = assistant_configuration_scope(request)
    if not allowed:
        return queryset.none()
    return evaluation_queryset(queryset.filter(tenant_id=tenant_id), request, "dataset__")


def evaluation_results(batch):
    """Do not read result snapshots through malformed case or source-run relations."""
    return (
        batch.results.exclude(error="EVALUATION_TENANT_MISMATCH")
        .filter(
            tenant_id=batch.tenant_id,
            case__dataset_id=batch.dataset_id,
            evaluation_run__dataset__tenant_id=batch.tenant_id,
            evaluation_run__dataset__assistant__tenant_id=batch.tenant_id,
        )
        .filter(
            Q(source_run__isnull=True)
            | Q(
                source_run__tenant_id=batch.tenant_id,
                source_run__session__tenant_id=batch.tenant_id,
                source_run__session__assistant_id=batch.dataset.assistant_id,
                source_run__input_message__session_id=F("source_run__session_id"),
            )
            & (
                Q(source_run__output_message__isnull=True)
                | Q(source_run__output_message__session_id=F("source_run__session_id"))
            )
        )
    )


def validate_evaluation_scope(batch, case=None, run=None, *, require_active=False):
    """Fail before dispatch, scoring or copying evidence across a tenant boundary."""
    dataset = batch.dataset
    valid = batch.tenant_id == dataset.tenant_id == dataset.assistant.tenant_id
    if case is not None:
        valid = valid and case.dataset_id == batch.dataset_id
    if run is not None:
        valid = valid and run.tenant_id == run.session.tenant_id == batch.tenant_id
        valid = valid and run.session.assistant_id == dataset.assistant_id
        valid = valid and run.input_message.session_id == run.session_id
        if run.output_message_id:
            valid = valid and run.output_message.session_id == run.session_id
    if not valid:
        raise ValueError("EVALUATION_TENANT_MISMATCH")
    if require_active and batch.tenant_id is not None and batch.tenant.status != "active":
        raise ValueError("EVALUATION_TENANT_INACTIVE")
