"""Custom DRF permissions for role-based platform access."""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.access import get_effective_feature_keys, get_user_membership


class HasRequiredFeature(BasePermission):
    """Allow access when the authenticated user can see the required feature."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        required_feature = getattr(view, "required_feature", "")
        if not required_feature:
            return False

        return required_feature in get_effective_feature_keys(user)


class HasLegacyAccountManagementAccess(HasRequiredFeature):
    """Keep global account management outside tenant and delegated sessions."""

    def has_permission(self, request, view):
        from accounts.delegation import is_delegated_request
        from accounts.principals import ApiKeyPrincipal

        user = request.user
        if not user or not user.is_authenticated:
            return False
        if isinstance(user, ApiKeyPrincipal) or is_delegated_request():
            return False
        if get_user_membership(user) is not None:
            return False
        return super().has_permission(request, view)


class HasPlatformSuperAdmin(BasePermission):
    """Allow only platform super administrators."""

    def has_permission(self, request, view):
        del view
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        from accounts.models import Profile
        from accounts.principals import ApiKeyPrincipal

        if isinstance(user, ApiKeyPrincipal):
            return False
        return Profile.objects.filter(
            user=user,
            platform_role=Profile.PlatformRole.SUPER_ADMIN,
        ).exists()


class HasTenantAdminAccess(BasePermission):
    """Require an active tenant administrator or a valid delegation."""

    allow_legacy = False

    def has_permission(self, request, view):
        from accounts.delegation import get_active_delegation, is_delegated_request
        from accounts.principals import ApiKeyPrincipal

        user = request.user
        if not user or not user.is_authenticated or not user.is_active or isinstance(user, ApiKeyPrincipal):
            return False
        delegated = is_delegated_request()
        membership = get_user_membership(user)
        if membership is None:
            return self.allow_legacy and not delegated and user.is_staff
        if not delegated and HasPlatformSuperAdmin().has_permission(request, view):
            return False
        if not (
            membership.status == "active" and membership.role == "tenant_admin" and membership.tenant.status == "active"
        ):
            return False
        delegation = get_active_delegation(user) if delegated else None
        return not (delegation and delegation.mode == "readonly" and request.method not in SAFE_METHODS)


class HasTenantOrLegacyAdminAccess(HasTenantAdminAccess):
    """Permit legacy staff on endpoints that limit them to unowned records."""

    allow_legacy = True


class HasTenantAdminAccessWithoutDelegation(HasTenantAdminAccess):
    """Tenant-admin access that platform delegation must not inherit."""

    def has_permission(self, request, view):
        from accounts.delegation import is_delegated_request

        if is_delegated_request():
            return False
        return super().has_permission(request, view)


class HasRunDiagnosticsAccess(BasePermission):
    """Allow staff or explicitly authorized Admin Console operators."""

    def has_permission(self, request, view):
        del view
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        return "admin_console" in get_effective_feature_keys(user) and user.has_perm("lens.run_diagnostics")


class HasRunEvaluationsAccess(BasePermission):
    """Allow operators with the evaluation feature and model permission."""

    def has_permission(self, request, view):
        del view
        user = request.user
        if not user or not user.is_authenticated:
            return False
        from accounts.delegation import is_delegated_request
        from lens.run_access import CanInspectTenantRuns

        if (
            get_user_membership(user) is not None
            or is_delegated_request()
            or HasPlatformSuperAdmin().has_permission(request, None)
        ):
            return CanInspectTenantRuns().has_permission(request, None)
        if user.is_staff:
            return True
        features = get_effective_feature_keys(user)
        return "run_evaluations" in features and user.has_perm("lens.run_evaluations")
