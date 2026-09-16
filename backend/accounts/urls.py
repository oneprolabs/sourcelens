from django.urls import path
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenRefreshView
from dj_rest_auth.views import (
    LoginView, LogoutView,
    PasswordChangeView,
)

from accounts.services import turnstile
from accounts.views import (
    CheckVirtualEmailUsernameView,
    CompleteGoogleSetupView,
    CompleteRegistrationView,
    ConfirmPasswordResetView,
    CustomUserDetailsView,
    GetAvailableScenesView,
    McpTokenView,
    SendLoginCodeView,
    SendPasswordResetEmailView,
    SendRegistrationEmailView,
    VerifyLoginCodeView,
    VerifyRegistrationTokenView,
)
from accounts.views.management import (
    ManagementGroupBulkDeleteView,
    ManagementGroupListView,
    ManagementGroupDetailView,
    ManagementRoleBulkView,
    ManagementRoleDetailView,
    ManagementRoleListView,
    ManagementUserBulkView,
    ManagementUserDetailView,
    ManagementUserListView,
)


class CustomLoginView(LoginView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        """Verify Cloudflare Turnstile before password authentication."""

        token = request.data.get('turnstile_token', '')
        passed, _errors = turnstile.verify_token(
            token,
            request.META.get('REMOTE_ADDR'),
        )
        if not passed:
            return Response(
                {
                    'success': False,
                    'error_code': 'TURNSTILE_FAILED',
                    'message': _('Human verification failed.'),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().post(request, *args, **kwargs)


urlpatterns = [
    # Login endpoint
    path(
        'api/v1/auth/login',
        CustomLoginView.as_view(),
        name='rest_login'
    ),
    # Logout endpoint
    path(
        'api/v1/auth/logout',
        LogoutView.as_view(),
        name='rest_logout'
    ),
    # JWT token refresh (no auth required; uses refresh token in body)
    path(
        'api/v1/auth/token/refresh',
        TokenRefreshView.as_view(),
        name='token_refresh'
    ),
    # Long-lived JWT for external MCP clients (Codex / Claude)
    path(
        'api/v1/auth/mcp/token',
        McpTokenView.as_view(),
        name='mcp_token'
    ),
    # Passwordless email verification-code login
    path(
        'api/v1/auth/login/send-code',
        SendLoginCodeView.as_view(),
        name='login_send_code'
    ),
    path(
        'api/v1/auth/login/verify-code',
        VerifyLoginCodeView.as_view(),
        name='login_verify_code'
    ),
    # Get or update user details
    path(
        'api/v1/auth/user',
        CustomUserDetailsView.as_view(),
        name='rest_user_details'
    ),
    # Request password reset (custom implementation)
    path(
        'api/v1/auth/password/reset',
        SendPasswordResetEmailView.as_view(),
        name='rest_password_reset'
    ),
    # Confirm password reset (custom implementation)
    path(
        'api/v1/auth/password/reset/confirm',
        ConfirmPasswordResetView.as_view(),
        name='rest_password_reset_confirm'
    ),
    # Change password
    path(
        'api/v1/auth/password/change',
        PasswordChangeView.as_view(),
        name='rest_password_change'
    ),
    # Custom registration endpoints
    path(
        'api/v1/auth/register/send-email',
        SendRegistrationEmailView.as_view(),
        name='register_send_email'
    ),
    path(
        'api/v1/auth/register/verify-token/<str:token>',
        VerifyRegistrationTokenView.as_view(),
        name='register_verify_token'
    ),
    path(
        'api/v1/auth/register/complete',
        CompleteRegistrationView.as_view(),
        name='register_complete'
    ),
    path(
        'api/v1/auth/check-username/<str:username>',
        CheckVirtualEmailUsernameView.as_view(),
        name='check_username'
    ),

    # OAuth complete setup (generic for all OAuth providers)
    path(
        'api/v1/auth/oauth/complete-setup',
        CompleteGoogleSetupView.as_view(),
        name='oauth_complete_setup'
    ),

    # Backward compatibility: Google-specific endpoint
    path(
        'api/v1/auth/google/complete-setup',
        CompleteGoogleSetupView.as_view(),
        name='google_complete_setup'
    ),

    # Utility endpoints
    path(
        'api/v1/auth/scenes',
        GetAvailableScenesView.as_view(),
        name='available_scenes'
    ),

    # Management portal (admin-only)
    path(
        'api/v1/management/users/',
        ManagementUserListView.as_view(),
        name='management_users'
    ),
    path(
        'api/v1/management/users/bulk-status/',
        ManagementUserBulkView.as_view(),
        name='management_users_bulk_status'
    ),
    path(
        'api/v1/management/users/<int:user_id>/',
        ManagementUserDetailView.as_view(),
        name='management_user_detail'
    ),
    path(
        'api/v1/management/groups/',
        ManagementGroupListView.as_view(),
        name='management_groups'
    ),
    path(
        'api/v1/management/groups/bulk-delete/',
        ManagementGroupBulkDeleteView.as_view(),
        name='management_groups_bulk_delete'
    ),
    path(
        'api/v1/management/groups/<int:group_id>/',
        ManagementGroupDetailView.as_view(),
        name='management_group_detail'
    ),
    path(
        'api/v1/management/roles/',
        ManagementRoleListView.as_view(),
        name='management_roles'
    ),
    path(
        'api/v1/management/roles/bulk-status/',
        ManagementRoleBulkView.as_view(),
        name='management_roles_bulk_status'
    ),
    path(
        'api/v1/management/roles/<int:role_id>/',
        ManagementRoleDetailView.as_view(),
        name='management_role_detail'
    ),
]
