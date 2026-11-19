from django.urls import path
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView
from dj_rest_auth.views import (
    LoginView, LogoutView,
    PasswordChangeView,
)

from accounts.views.member_bulk import TenantMemberBulkView
from accounts.views.member_deletion import TenantMemberDeletionView
from accounts.views.tenant_groups import TenantGroupDeletionView
from accounts.views.member_unlock import TenantMemberUnlockView
from accounts.views.member_password_reset import TenantMemberPasswordResetView
from accounts.views.bulk_invitations import BulkInvitationPreviewView, BulkInvitationView
from accounts.services import turnstile
from accounts.serializers_login import PasswordLoginSerializer
from accounts.views.tenant_overview import TenantOverviewView
from accounts.views.tenant_invitations import (
    AcceptTenantInvitationView,
    TenantInvitationResendView,
    TenantInvitationView,
)
from accounts.views import (
    CheckVirtualEmailUsernameView,
    CompleteGoogleSetupView,
    CompleteRegistrationView,
    ConfirmPasswordResetView,
    CustomUserDetailsView,
    CurrentTenantView,
    GetAvailableScenesView,
    TenantLogoView,
    TenantMemberDetailView,
    TenantMemberListView,
    TenantGroupDetailView,
    TenantGroupListView,
    TenantGroupMemberView,
    TenantApiKeyDetailView,
    TenantApiKeyListView,
    TenantApiKeyBulkView,
    PlatformDelegationEndView,
    PlatformTenantDelegationView,
    PlatformTenantListView,
    PlatformOverviewView,
    PlatformAuditListView,
    PlatformTenantStatsView,
    PlatformMemberListView,
    PlatformApiKeyListView,
    PlatformApiKeyActionView,
    PlatformApiKeyBulkView,
    PlatformApiKeyNoticeView,
    AgentTokenView,
    AgentTokenListView,
    AgentTokenRevokeView,
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
    serializer_class = PasswordLoginSerializer

    def post(self, request, *args, **kwargs):
        """Verify Cloudflare Turnstile before password authentication."""

        token = request.data.get('turnstile_token', '')
        passed, _errors = turnstile.verify_token(
            token,
            turnstile.get_client_ip(request.META),
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


class TurnstileStatusView(APIView):
    """Expose the same client policy used by authentication enforcement."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        """Return a non-cacheable policy without exposing the client IP."""
        client_ip = turnstile.get_client_ip(request.META)
        return Response(
            {
                'required': turnstile.is_verification_required(client_ip),
                'bypassed': turnstile.is_bypassed_ip(client_ip),
            },
            headers={'Cache-Control': 'no-store'},
        )


urlpatterns = [
    path("api/v1/tenant/members/delete", TenantMemberDeletionView.as_view(), name="tenant_member_deletion"),
    path("api/v1/tenant/members/<int:user_id>/unlock", TenantMemberUnlockView.as_view(), name="tenant_member_unlock"),
    path(
        "api/v1/tenant/members/<int:user_id>/password-reset",
        TenantMemberPasswordResetView.as_view(),
        name="tenant_member_password_reset",
    ),
    path("api/v1/tenant/invitations/bulk/preview", BulkInvitationPreviewView.as_view(), name="tenant_invitations_preview"),
    path("api/v1/tenant/invitations/bulk", BulkInvitationView.as_view(), name="tenant_invitations_bulk"),
    path("api/v1/tenant/members/bulk", TenantMemberBulkView.as_view(), name="tenant_members_bulk"),
    path('api/v1/tenant/invitations', TenantInvitationView.as_view(), name='tenant_invitations'),
    path('api/v1/tenant/invitations/<int:user_id>/resend', TenantInvitationResendView.as_view(),
         name='tenant_invitation_resend'),
    path('api/v1/auth/invitations/accept', AcceptTenantInvitationView.as_view(), name='tenant_invitation_accept'),
    path('api/v1/tenant/overview', TenantOverviewView.as_view(), name='current_tenant_overview'),
    path(
        'api/v1/tenant',
        CurrentTenantView.as_view(),
        name='current_tenant',
    ),
    path(
        'api/v1/tenant/logo',
        TenantLogoView.as_view(),
        name='current_tenant_logo',
    ),
    path(
        'api/v1/tenant/members',
        TenantMemberListView.as_view(),
        name='current_tenant_members',
    ),
    path(
        'api/v1/tenant/members/<int:user_id>',
        TenantMemberDetailView.as_view(),
        name='current_tenant_member_detail',
    ),
    path(
        'api/v1/tenant/groups',
        TenantGroupListView.as_view(),
        name='current_tenant_groups',
    ),
    path('api/v1/tenant/groups/delete', TenantGroupDeletionView.as_view(), name='tenant_group_deletion'),
    path(
        'api/v1/tenant/groups/<uuid:group_uuid>',
        TenantGroupDetailView.as_view(),
        name='current_tenant_group_detail',
    ),
    path(
        'api/v1/tenant/groups/<uuid:group_uuid>/members',
        TenantGroupMemberView.as_view(),
        name='current_tenant_group_member',
    ),
    path(
        'api/v1/tenant/groups/<uuid:group_uuid>/members/<int:user_id>',
        TenantGroupMemberView.as_view(),
        name='current_tenant_group_member_detail',
    ),
    path(
        'api/v1/tenant/api-keys',
        TenantApiKeyListView.as_view(),
        name='current_tenant_api_keys',
    ),
    path(
        'api/v1/tenant/api-keys/bulk',
        TenantApiKeyBulkView.as_view(),
        name='tenant-api-key-bulk',
    ),
    path(
        'api/v1/tenant/api-keys/<uuid:key_uuid>',
        TenantApiKeyDetailView.as_view(),
        name='current_tenant_api_key_detail',
    ),
    path(
        'api/v1/platform/tenants',
        PlatformTenantListView.as_view(),
        name='platform_tenants',
    ),
    path(
        'api/v1/platform/tenants/<uuid:tenant_uuid>/delegate',
        PlatformTenantDelegationView.as_view(),
        name='platform_tenant_delegate',
    ),
    path(
        'api/v1/platform/delegations/<uuid:delegation_uuid>/end',
        PlatformDelegationEndView.as_view(),
        name='platform_delegation_end',
    ),
    path(
        'api/v1/platform/overview',
        PlatformOverviewView.as_view(),
        name='platform_overview',
    ),
    path(
        'api/v1/platform/audit',
        PlatformAuditListView.as_view(),
        name='platform_audit',
    ),
    path(
        'api/v1/platform/tenant-stats',
        PlatformTenantStatsView.as_view(),
        name='platform_tenant_stats',
    ),
    path(
        'api/v1/platform/members',
        PlatformMemberListView.as_view(),
        name='platform_members',
    ),
    path(
        'api/v1/platform/api-keys',
        PlatformApiKeyListView.as_view(),
        name='platform_api_keys',
    ),
    path(
        'api/v1/platform/api-keys/bulk',
        PlatformApiKeyBulkView.as_view(),
        name='platform-api-key-bulk',
    ),
    path(
        'api/v1/platform/api-key-notices',
        PlatformApiKeyNoticeView.as_view(),
        name='platform-api-key-notices',
    ),
    path(
        'api/v1/platform/api-key-notices/<uuid:notice_uuid>',
        PlatformApiKeyNoticeView.as_view(),
        name='platform-api-key-notice',
    ),
    path(
        'api/v1/platform/api-key-notices/<uuid:notice_uuid>/retry',
        PlatformApiKeyNoticeView.as_view(),
        name='platform-api-key-notice-retry',
    ),
    path(
        'api/v1/platform/api-keys/<uuid:key_uuid>/<str:action>',
        PlatformApiKeyActionView.as_view(),
        name='platform_api_key_action',
    ),
    path(
        'api/v1/auth/turnstile/status',
        TurnstileStatusView.as_view(),
        name='turnstile_status',
    ),
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
    # Revocable credentials for external agent clients (Codex / Claude)
    path(
        'api/v1/auth/agent/token',
        AgentTokenView.as_view(),
        name='agent_token'
    ),
    path('api/v1/auth/agent/tokens', AgentTokenListView.as_view(), name='agent_token_list'),
    path('api/v1/auth/agent/tokens/<uuid:token_id>', AgentTokenRevokeView.as_view(), name='agent_token_revoke'),
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
