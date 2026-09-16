"""
REST Framework Settings

This module configures Django REST Framework (DRF) and related JWT settings.

JWT Authentication:
- Uses access tokens in the "Authorization" header ("Bearer <token>").
- Suitable for stateless API communication without cookies.
- Requires clients to store and include tokens in the header per request.

JWTCookieAuthentication:
- Uses cookies to store JWT tokens (access and refresh).
- Tokens are automatically sent by browsers via cookies.
- Ideal for web apps; simplifies frontend as browsers handle cookies.

Below, JWTAuthentication is used, requiring tokens in the request header.

DRF Parameters:
- DEFAULT_RENDERER_CLASSES: Renders API responses in standardized format.
  - CustomJSONRenderer: Ensures consistent response structure
    with code, message and data.
  - BrowsableAPIRenderer: Provides a browsable API interface.
- DEFAULT_PARSER_CLASSES: Parses incoming camelCase request data to snake_case.
  - CamelCaseJSONParser: Handles JSON with camelCase keys for internal
    processing.
"""

from datetime import timedelta
import os

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'accounts.authentication.AgentRestrictedJWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_RENDERER_CLASSES': (
        'core.settings.renders.CustomJSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ),
    'DEFAULT_PARSER_CLASSES': (
        'djangorestframework_camel_case.parser.CamelCaseJSONParser',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    # Schema generation for API documentation
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # Pagination Settings
    # -----------------
    # DEFAULT_PAGINATION_CLASS options:
    # - PageNumberPagination: Classic page-based style (?page=2)
    # - LimitOffsetPagination: Limit/skip style (?limit=10&offset=20)
    # - CursorPagination: Cursor-based for large datasets, prevents skipping
    'DEFAULT_PAGINATION_CLASS': (
        'core.paginations.APIPagination'
    ),

    # PAGE_SIZE: Number of items per page
    # Can be overridden per view using:
    # - pagination_class attribute
    # - page_size attribute
    'PAGE_SIZE': 10,

    # Additional pagination settings (optional):
    # ---------------------------------------
    # Allow client to override page size
    # 'PAGINATE_BY_PARAM': 'page_size',
    # Maximum limit for page size
    # 'MAX_PAGE_SIZE': 100,
    # Custom page parameter (default: page)
    # 'PAGE_QUERY_PARAM': 'p',
    # Allow client to set page size
    # 'PAGE_SIZE_QUERY_PARAM': 'size',

    # --- Sorting Configuration ---
    # DEFAULT_FILTER_BACKENDS: Enable ordering filter backend
    # Using OrderingFilter allows clients to sort results via
    # ?ordering=field_name
    # Example: ?ordering=-created_date for descending sort
    'DEFAULT_FILTER_BACKENDS': (
        'rest_framework.filters.OrderingFilter',
    ),

    # Optional sorting parameters:
    # -----------------------------------------------------------------
    # ORDERING_PARAM: Custom query parameter name (default: 'ordering')
    # 'ORDERING_PARAM': 'sort_by',

    # ORDERING_FIELDS: Explicitly declare allowed sorting fields in views
    # Note: Must be defined per view using ordering_fields attribute
    # 'ORDERING_FIELDS': ['created_date', 'updated_date'],

    # DEFAULT_ORDERING: Default sorting when no parameter is provided
    # Example: '-created_date' for default descending sort by creation date
    # 'DEFAULT_ORDERING': '-created_date',
}

# SimpleJWT Settings:
#
# - ACCESS_TOKEN_LIFETIME: Duration for which the access token is valid.
# - REFRESH_TOKEN_LIFETIME: Duration for which the refresh token is valid.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
}

# Long-lived JWT for external agent clients (Codex / Claude CLI).
#
# Those clients cannot run an interactive refresh flow, so the token is
# minted once from the user settings page and reused until it expires.
# Override the lifetime with AGENT_TOKEN_LIFETIME_DAYS; a non-positive value
# disables the minting endpoint. SimpleJWT blacklisting is not enabled, so
# an issued token cannot be revoked individually before it expires.
AGENT_TOKEN_LIFETIME_DAYS = int(os.getenv("AGENT_TOKEN_LIFETIME_DAYS", "30"))

# Validity choices (in months) the user may pick when minting a token.
# The requested value, when present, overrides AGENT_TOKEN_LIFETIME_DAYS;
# each month is treated as AGENT_TOKEN_DAYS_PER_MONTH days.
AGENT_TOKEN_LIFETIME_MONTHS_OPTIONS = (1, 3, 6)
AGENT_TOKEN_DAYS_PER_MONTH = 30

# Routes an agent-scoped token may reach. The token exists for read-only Q&A,
# so it is confined to the session/run endpoints the CLI drives and the
# assistant catalog it needs to choose an assistant. Everything else is
# rejected with AGENT_TOKEN_SCOPE_RESTRICTED, including admin endpoint reads.
AGENT_TOKEN_ALLOWED_ROUTES = (
    ("POST", r"/api/lens/sessions/?"),
    ("POST", r"/api/lens/sessions/[0-9A-Fa-f-]{36}/runs/?"),
    ("GET", r"/api/lens/runs/[0-9A-Fa-f-]{36}/?"),
    ("GET", r"/api/lens/assistants/?"),
    ("GET", r"/api/lens/assistants/[0-9A-Fa-f-]{36}/?"),
)

# REST Authentication Settings:
#
# - USE_JWT: Enables JWT for authentication.
# - JWT_AUTH_HTTPONLY: Determines if refresh tokens should be HTTP-only.
# - SESSION_LOGIN: Disable session login to avoid CSRF issues with JWT.
REST_AUTH = {
    "USE_JWT": True,
    "JWT_AUTH_HTTPONLY": False,
    "SESSION_LOGIN": False,
    "OLD_PASSWORD_FIELD_ENABLED": True,
    "PASSWORD_CHANGE_SERIALIZER": (
        "accounts.serializers.CustomPasswordChangeSerializer"
    ),
    "USER_DETAILS_SERIALIZER": (
        "accounts.serializers.UserDetailsSerializer"
    ),
}
