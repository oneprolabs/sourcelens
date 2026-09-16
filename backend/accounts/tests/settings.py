"""Minimal Django settings for accounts API tests."""

from core.settings.rest import (  # noqa: F401
    MCP_TOKEN_ALLOWED_ROUTES as PRODUCTION_MCP_TOKEN_ALLOWED_ROUTES,
)
from core.settings.rest import MCP_TOKEN_LIFETIME_DAYS  # noqa: F401

# Exercise the production allowlist plus one route that exists in the
# accounts test URL conf, so the confinement is testable here.
MCP_TOKEN_ALLOWED_ROUTES = PRODUCTION_MCP_TOKEN_ALLOWED_ROUTES + (
    ("GET", r"/api/v1/auth/probe"),
)

SECRET_KEY = "test-secret-key-at-least-32-bytes-long"
DEBUG = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "rest_framework",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "accounts",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

ROOT_URLCONF = "accounts.tests.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
    },
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

USE_TZ = True
SITE_ID = 1

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "accounts.authentication.MCPRestrictedJWTAuthentication",
    ),
    "DEFAULT_PARSER_CLASSES": (
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

REST_AUTH = {
    "USE_JWT": True,
    "TOKEN_MODEL": None,
    "OLD_PASSWORD_FIELD_ENABLED": True,
    "PASSWORD_CHANGE_SERIALIZER": (
        "accounts.serializers.CustomPasswordChangeSerializer"
    ),
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        )
    },
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_LOGIN_METHODS = ["email"]
ACCOUNT_SIGNUP_FIELDS = [
    "email*",
    "username*",
    "password1*",
    "password2*",
]

PASSWORD_RESET_TIMEOUT = 86400
