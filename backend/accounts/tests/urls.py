"""URLs for accounts tests."""
from django.urls import path

from dj_rest_auth.views import PasswordChangeView
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.urls import CustomLoginView
from accounts.views import (
    CustomUserDetailsView,
    McpTokenView,
    VerifyLoginCodeView,
)
from accounts.views.management import (
    ManagementGroupBulkDeleteView,
    ManagementGroupListView,
    ManagementRoleBulkView,
    ManagementUserBulkView,
    ManagementUserListView,
)
from accounts.views.password import (
    ConfirmPasswordResetView,
    SendPasswordResetEmailView,
)


class AuthenticatedProbeView(APIView):
    """Return the authenticated user's ID for JWT regression tests."""

    def get(self, request):
        """Return a minimal authenticated response."""
        return Response({"user_id": request.user.pk})


urlpatterns = [
    path("api/v1/auth/login", CustomLoginView.as_view()),
    path(
        "api/v1/auth/login/verify-code",
        VerifyLoginCodeView.as_view(),
    ),
    path("api/v1/auth/user", CustomUserDetailsView.as_view()),
    path(
        "api/v1/auth/password/reset",
        SendPasswordResetEmailView.as_view(),
    ),
    path(
        "api/v1/auth/password/reset/confirm",
        ConfirmPasswordResetView.as_view(),
    ),
    path(
        "api/v1/auth/password/change",
        PasswordChangeView.as_view(),
    ),
    path("api/v1/auth/probe", AuthenticatedProbeView.as_view()),
    path("api/v1/auth/mcp/token", McpTokenView.as_view()),
    path(
        "api/v1/management/users/",
        ManagementUserListView.as_view(),
    ),
    path(
        "api/v1/management/groups/",
        ManagementGroupListView.as_view(),
    ),
    path(
        "api/v1/management/users/bulk-status/",
        ManagementUserBulkView.as_view(),
    ),
    path(
        "api/v1/management/groups/bulk-delete/",
        ManagementGroupBulkDeleteView.as_view(),
    ),
    path(
        "api/v1/management/roles/bulk-status/",
        ManagementRoleBulkView.as_view(),
    ),
]
