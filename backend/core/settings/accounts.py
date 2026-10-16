"""
Accounts and Authentication Configuration

This module contains all settings related to:
- User registration and authentication
- Email configuration for registration emails
- Google OAuth configuration
- Social account authentication
"""
import os

from django.core.exceptions import ImproperlyConfigured


# ============================
# Email Configuration
# ============================

EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.smtp.EmailBackend'
)

EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')

EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))

EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'false').lower() == "true"

EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'false').lower() == "true"

EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')

EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')

DEFAULT_FROM_EMAIL = os.getenv(
    'DEFAULT_FROM_EMAIL',
    'noreply@backend.local'
)


# ============================
# Social Account Providers Configuration
# ============================

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        },
    },
}

# Custom adapter for business logic (user creation, profile setup)
SOCIALACCOUNT_ADAPTER = 'accounts.adapters.CustomSocialAccountAdapter'

# Basic account settings
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_REQUIRED = False
SOCIALACCOUNT_STORE_TOKENS = True
SOCIALACCOUNT_LOGIN_ON_GET = True

# Account configuration
# Unique email addresses (prevent duplicate registrations)
ACCOUNT_UNIQUE_EMAIL = True

# Keep Django's username backend for the admin while allauth handles email
# authentication for the public password login API.
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Login methods (replaces deprecated AUTHENTICATION_METHOD)
ACCOUNT_LOGIN_METHODS = ['email']

# Accounts created through allauth must have the identifier required by the
# password login flow. Usernames remain required profile identifiers.
ACCOUNT_SIGNUP_FIELDS = [
    'email*',
    'username*',
    'password1*',
    'password2*',
]

# Force HTTPS for OAuth callback URLs in production
# This ensures django-allauth generates https:// URLs for OAuth providers
# Environment variable allows flexibility for local development (http)
ACCOUNT_DEFAULT_HTTP_PROTOCOL = os.getenv(
    'ACCOUNT_DEFAULT_HTTP_PROTOCOL',
    'http'
)

# Email-based authentication for social accounts
# If OAuth email matches existing user, auto-connect instead of creating
# new user
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

# ============================
# OAuth Redirect Configuration
# ============================

# Frontend URL
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000')

# OAuth flows require traditional /accounts/ endpoints for provider callbacks
HEADLESS_ONLY = False

# OAuth callback redirect URL
# Points to custom view that generates JWT tokens before redirecting to frontend
LOGIN_REDIRECT_URL = "/accounts/oauth/callback/"

# Frontend URLs for email verification and password reset (optional)
# Note: socialaccount_signup is NOT used as we have custom redirect logic
HEADLESS_FRONTEND_URLS = {
    "socialaccount_login_error": f"{FRONTEND_URL}/auth/oauth/error",
    "account_confirm_email": f"{FRONTEND_URL}/account/verify-email/{{key}}",
    "account_reset_password": f"{FRONTEND_URL}/account/password/reset",
    "account_reset_password_from_key": (
        f"{FRONTEND_URL}/account/password/reset/key/{{key}}"
    ),
}


# ============================
# Registration Configuration
# ============================

REGISTRATION_TOKEN_EXPIRY_HOURS = int(
    os.getenv('REGISTRATION_TOKEN_EXPIRY_HOURS', '24')
)

# ============================
# Password Reset Configuration
# ============================

# Password reset token validity (in seconds)
# Django default is 3 days (259200 seconds)
# We set it to 1 day (86400 seconds) for better security
PASSWORD_RESET_TIMEOUT = int(
    os.getenv('PASSWORD_RESET_TIMEOUT', '86400')
)


# ============================
# Email Verification Code (OTP) Login
# ============================

# Lifetime of a login verification code (seconds).
OTP_CODE_TTL_SECONDS = int(os.getenv('OTP_CODE_TTL_SECONDS', '300'))

# Maximum verify attempts before a code is invalidated.
OTP_MAX_ATTEMPTS = int(os.getenv('OTP_MAX_ATTEMPTS', '5'))

# Minimum interval between two code sends for the same email (seconds).
OTP_SEND_COOLDOWN_SECONDS = int(os.getenv('OTP_SEND_COOLDOWN_SECONDS', '60'))

# Maximum code sends per email within a rolling 24h window.
OTP_SEND_MAX_PER_DAY = int(os.getenv('OTP_SEND_MAX_PER_DAY', '10'))

# Maximum code sends per client IP within a rolling 1h window.
OTP_SEND_MAX_PER_IP_HOUR = int(os.getenv('OTP_SEND_MAX_PER_IP_HOUR', '20'))


# ============================
# Cloudflare Turnstile
# ============================

# Server-side secret used to verify Turnstile tokens. Required when the
# check is enabled outside debug mode.
TURNSTILE_SECRET_KEY = os.getenv('TURNSTILE_SECRET_KEY', '')

# Explicit switch for local development. Only "false" disables the check.
TURNSTILE_ENABLED = os.getenv(
    'TURNSTILE_ENABLED',
    'true',
).strip().lower() != 'false'

# Comma-separated IP addresses or CIDR networks allowed to skip Turnstile.
TURNSTILE_BYPASS_IPS = tuple(
    ip.strip()
    for ip in os.getenv('TURNSTILE_BYPASS_IPS', '').split(',')
    if ip.strip()
)

TURNSTILE_VERIFY_URL = (
    'https://challenges.cloudflare.com/turnstile/v0/siteverify'
)

# Fail fast in production: an empty secret silently bypasses human
# verification on every login entry, so refuse to start without it unless
# explicitly in debug mode.
if TURNSTILE_ENABLED and not TURNSTILE_SECRET_KEY and (
    os.getenv('DJANGO_DEBUG', 'false').lower() != 'true'
):
    raise ImproperlyConfigured(
        'TURNSTILE_SECRET_KEY is required when DJANGO_DEBUG is not true. '
        'Set it to enable Cloudflare Turnstile, or run with '
        'DJANGO_DEBUG=true to bypass it in development.'
    )
