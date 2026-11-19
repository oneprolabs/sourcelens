"""
Views for user authentication and management.
"""

from .oauth import (
    CompleteGoogleSetupView,
    OAuthCallbackRedirectView,
)
from .login_otp import (
    SendLoginCodeView,
    VerifyLoginCodeView,
)
from .registration import (
    SendRegistrationEmailView,
    VerifyRegistrationTokenView,
    CompleteRegistrationView,
    CheckVirtualEmailUsernameView,
)
from .password import (
    ConfirmPasswordResetView,
    SendPasswordResetEmailView,
)
from .user import CustomUserDetailsView
from .scenes import GetAvailableScenesView
from .agent_token import AgentTokenListView, AgentTokenRevokeView, AgentTokenView
from .tenant import (
    CurrentTenantView,
    TenantLogoView,
    TenantMemberDetailView,
    TenantMemberListView,
)
from .platform_audit import (
    PlatformAuditListView,
    PlatformOverviewView,
)
from .platform_api_keys import (
    PlatformApiKeyActionView, PlatformApiKeyListView, PlatformApiKeyBulkView, PlatformApiKeyNoticeView,
)
from .platform_members import PlatformMemberListView
from .platform_stats import PlatformTenantStatsView
from .platform import (
    PlatformDelegationEndView,
    PlatformTenantDelegationView,
    PlatformTenantListView,
)
from .tenant_api_keys import (
    TenantApiKeyBulkView,
    TenantApiKeyDetailView,
    TenantApiKeyListView,
)
from .tenant_groups import (
    TenantGroupDetailView,
    TenantGroupListView,
    TenantGroupMemberView,
)

__all__ = [
    'CompleteGoogleSetupView',
    'OAuthCallbackRedirectView',
    'SendLoginCodeView',
    'VerifyLoginCodeView',
    'SendRegistrationEmailView',
    'VerifyRegistrationTokenView',
    'CompleteRegistrationView',
    'CheckVirtualEmailUsernameView',
    'SendPasswordResetEmailView',
    'ConfirmPasswordResetView',
    'CustomUserDetailsView',
    'GetAvailableScenesView',
    'AgentTokenView',
    'AgentTokenListView',
    'AgentTokenRevokeView',
    'CurrentTenantView',
    'TenantLogoView',
    'TenantMemberListView',
    'TenantMemberDetailView',
    'TenantGroupListView',
    'TenantGroupDetailView',
    'TenantGroupMemberView',
    'TenantApiKeyListView',
    'TenantApiKeyBulkView',
    'TenantApiKeyDetailView',
    'PlatformTenantListView',
    'PlatformTenantDelegationView',
    'PlatformDelegationEndView',
    'PlatformOverviewView',
    'PlatformAuditListView',
    'PlatformTenantStatsView',
    'PlatformMemberListView',
    'PlatformApiKeyListView',
    'PlatformApiKeyActionView',
    'PlatformApiKeyBulkView',
    'PlatformApiKeyNoticeView',
]
