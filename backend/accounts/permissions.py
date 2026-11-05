"""Custom DRF permissions for role-based platform access."""

from rest_framework.permissions import BasePermission

from accounts.access import get_effective_feature_keys


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


class HasRunDiagnosticsAccess(BasePermission):
    """Allow staff or explicitly authorized Admin Console operators."""

    def has_permission(self, request, view):
        del view
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        return "admin_console" in get_effective_feature_keys(user) and user.has_perm(
            "lens.run_diagnostics"
        )


class HasRunEvaluationsAccess(BasePermission):
    """Allow operators with the evaluation feature and model permission."""

    def has_permission(self, request, view):
        del view
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        features = get_effective_feature_keys(user)
        return "run_evaluations" in features and user.has_perm(
            "lens.run_evaluations"
        )
