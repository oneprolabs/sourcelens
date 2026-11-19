"""
This module defines the Profile model for extending the built-in User model
using a one-to-one relationship. The Profile model adds additional fields
to store personal details, preferences, and registration state.

For OAuth/Social authentication, this project uses django-allauth's
SocialAccount model instead of storing provider-specific IDs in Profile.
See: allauth.socialaccount.models.SocialAccount

Django's default User model includes the following fields:
    - username: A unique identifier for the user.
    - first_name: The user's first name.
    - last_name: The user's last name.
    - email: The user's email address.
    - password: The user's hashed password.
    - is_staff: Boolean indicating if the user can access the admin site.
    - is_active: Boolean indicating if the user account is active.
    - date_joined: The date when the user account was created.
    - last_login: The last time the user logged in.

Other methods to extend the User model:
    - Proxy model: Modify behavior without changing the schema.
    - Subclassing User: Create a custom user model with your own fields.
    - Using a ForeignKey: Establish a many-to-one relationship for extensions.
"""

import hashlib
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.db import models

from accounts.access import normalize_feature_keys, normalize_platform_key

DEFAULT_ANSWER_LANGUAGE = "en-US"


class AgentToken(models.Model):
    """Store revocable agent credentials without retaining the plaintext."""

    PREFIX = "sl_agent_"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_tokens")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_hint = models.CharField(max_length=32, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @staticmethod
    def digest(raw_token):
        """Hash a high-entropy credential for indexed server-side lookup."""

        value = raw_token.encode("utf-8") if isinstance(raw_token, str) else raw_token
        return hashlib.sha256(value).hexdigest()

    @classmethod
    def issue(cls, user, expires_at):
        """Persist authorization and return its plaintext credential once."""

        raw_token = cls.PREFIX + secrets.token_urlsafe(32)
        token = cls.objects.create(
            user=user,
            token_hash=cls.digest(raw_token),
            token_hint=f"{raw_token[:12]}...{raw_token[-4:]}",
            expires_at=expires_at,
        )
        return token, raw_token


class Tenant(models.Model):
    """Workspace boundary for tenant-scoped resources and members."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        PENDING = "pending", "Pending"

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")
    logo_url = models.CharField(max_length=500, blank=True, default="")
    default_language = models.CharField(
        max_length=10,
        choices=[
            ("zh-CN", "简体中文"),
            ("en-US", "English"),
        ],
        default="zh-CN",
    )
    timezone = models.CharField(
        max_length=50,
        choices=[
            ("Asia/Shanghai", "Asia/Shanghai (UTC+8)"),
            ("UTC", "UTC"),
        ],
        default="Asia/Shanghai",
    )
    contact_email = models.EmailField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "uuid"]

    def __str__(self):
        return self.name


class PasswordLoginState(models.Model):
    """Serialize consecutive password failures for registered and unknown email identifiers."""

    email = models.CharField(primary_key=True, max_length=254)
    failed_attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class TenantMembership(models.Model):
    """One user's membership, role, and revocation state in a tenant."""

    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        TENANT_ADMIN = "tenant_admin", "Tenant Admin"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INVITED = "invited", "Invited"
        DISABLED = "disabled", "Disabled"

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="membership",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    auth_version = models.PositiveBigIntegerField(default=1)
    joined_at = models.DateTimeField(null=True, blank=True)
    invited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant_id", "user_id"]
        indexes = [
            models.Index(
                fields=["tenant", "status"],
                name="acct_member_tenant_status_idx",
            ),
            models.Index(
                fields=["tenant", "role"],
                name="acct_member_tenant_role_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user} @ {self.tenant}"


class TenantGroup(models.Model):
    """Tenant-owned user group backed by a Django auth group."""

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="groups",
    )
    auth_group = models.OneToOneField(
        Group,
        on_delete=models.CASCADE,
        related_name="tenant_group",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_tenant_groups",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "uuid"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="accounts_tenant_group_name_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "name"],
                name="accounts_tenant_group_idx",
            ),
        ]

    def __str__(self):
        return f"{self.name} @ {self.tenant}"


class TenantApiKey(models.Model):
    """Tenant-scoped machine credential for external assistant callers."""

    PREFIX = "slk_"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"
        REVOKED = "revoked", "Revoked"

    class Scope(models.TextChoices):
        ALL = "all", "All assistants"
        SELECTED = "selected", "Selected assistants"

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=150)
    key_prefix = models.CharField(max_length=20)
    key_suffix = models.CharField(max_length=4, blank=True, default="", db_default="")
    key_suffix_version = models.PositiveIntegerField(default=0, db_default=0)
    description = models.TextField(max_length=1000, blank=True, default="", db_default="")
    key_hash = models.CharField(max_length=64, unique=True)
    scope = models.CharField(
        max_length=16,
        choices=Scope.choices,
        default=Scope.ALL,
    )
    assistant_uuids = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_api_keys",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    key_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-uuid"]
        indexes = [
            models.Index(
                fields=["tenant", "status"],
                name="acct_apikey_tenant_status_idx",
            ),
        ]

    @staticmethod
    def digest(raw_token):
        """Hash a high-entropy API key for indexed lookup."""
        value = raw_token.encode("utf-8") if isinstance(raw_token, str) else raw_token
        return hashlib.sha256(value).hexdigest()

    @classmethod
    def issue(
        cls,
        *,
        tenant,
        name,
        scope,
        assistant_uuids,
        created_by,
        expires_at,
        description="",
    ):
        """Persist a new key and return its plaintext once."""
        raw_token = cls.PREFIX + secrets.token_urlsafe(32)
        record = cls.objects.create(
            tenant=tenant,
            name=name,
            key_prefix=raw_token[:12],
            key_suffix=raw_token[-4:],
            key_suffix_version=1,
            description=description,
            key_hash=cls.digest(raw_token),
            scope=scope,
            assistant_uuids=assistant_uuids,
            created_by=created_by,
            expires_at=expires_at,
        )
        return record, raw_token

    def rotate(self):
        """Replace the secret and invalidate the previous one."""
        raw_token = self.PREFIX + secrets.token_urlsafe(32)
        self.key_prefix = raw_token[:12]
        self.key_hash = self.digest(raw_token)
        self.key_version += 1
        self.key_suffix = raw_token[-4:]
        self.key_suffix_version = self.key_version
        self.save(
            update_fields=[
                "key_prefix",
                "key_suffix",
                "key_suffix_version",
                "key_hash",
                "key_version",
                "expires_at",
                "updated_at",
            ]
        )
        return raw_token

    @property
    def current_key_suffix(self):
        """Hide a stale suffix when a previous application version rotated the key."""
        return self.key_suffix if self.key_suffix_version == self.key_version else ""

    @property
    def key_display(self):
        """Prefer the real suffix and retain known prefixes for hash-only legacy records."""
        if self.current_key_suffix:
            return f"{self.PREFIX}••••{self.current_key_suffix}"
        return f"{self.key_prefix}••••"

    @property
    def effective_status(self):
        """Return the status shown to administrators."""
        from django.utils import timezone

        if self.status == self.Status.REVOKED:
            return self.Status.REVOKED
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return "expired"
        return self.status

    def __str__(self):
        return f"{self.name} ({self.key_display})"


class TenantApiKeyCall(models.Model):
    """Safe HTTP invocation metadata without request or answer content."""

    key = models.ForeignKey(TenantApiKey, on_delete=models.CASCADE, related_name="calls")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="api_key_calls")
    key_version = models.PositiveIntegerField()
    assistant_uuid = models.UUIDField(null=True, blank=True)
    run_uuid = models.UUIDField(null=True, blank=True)
    status_code = models.PositiveSmallIntegerField()
    duration_ms = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["tenant", "key", "-created_at"], name="acct_key_call_recent_idx")]


class ApiKeyControlNotice(models.Model):
    """Durable tenant notification for committed platform credential controls."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="api_key_control_notices")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=16, choices=[("disable", "Disable"), ("revoke", "Revoke")])
    keys = models.JSONField(default=list)
    status = models.CharField(
        max_length=16, default="pending", choices=[("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")]
    )
    delivered_user_ids = models.JSONField(default=list)
    attempts = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=40, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-uuid"]
        indexes = [models.Index(fields=["status", "updated_at"], name="acct_key_notice_status_idx")]


class TenantDelegation(models.Model):
    """Time-bounded platform support session into one tenant."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    class Mode(models.TextChoices):
        READONLY = "readonly", "Read only"
        OPERATIONAL = "operational", "Operational"

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    platform_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant_delegations",
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="delegations",
    )
    reason = models.TextField(blank=True, default="")
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.OPERATIONAL,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ended_tenant_delegations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-uuid"]
        indexes = [
            models.Index(
                fields=["platform_user", "status"],
                name="acct_deleg_user_status_idx",
            ),
            models.Index(
                fields=["tenant", "status"],
                name="acct_deleg_tenant_status_idx",
            ),
        ]

    @property
    def is_active(self):
        """Return whether this delegation may still be used."""
        from django.utils import timezone

        return self.status == self.Status.ACTIVE and self.expires_at > timezone.now()

    def __str__(self):
        return f"{self.platform_user} -> {self.tenant}"


class AuditLog(models.Model):
    """Append-only platform/tenant operation audit record."""

    class ActorType(models.TextChoices):
        USER = "user", "User"
        API_KEY = "api_key", "API key"
        SYSTEM = "system", "System"

    class Result(models.TextChoices):
        SUCCESS = "success", "Success"
        DENIED = "denied", "Denied"
        FAILED = "failed", "Failed"

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    tenant = models.ForeignKey(
        Tenant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    actor_type = models.CharField(
        max_length=16,
        choices=ActorType.choices,
        default=ActorType.USER,
    )
    delegation = models.ForeignKey(
        "TenantDelegation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=150)
    resource_type = models.CharField(max_length=50, blank=True, default="")
    resource_id = models.CharField(max_length=64, blank=True, default="")
    result = models.CharField(
        max_length=16,
        choices=Result.choices,
        default=Result.SUCCESS,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=100, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-uuid"]
        indexes = [
            models.Index(
                fields=["tenant", "created_at"],
                name="acct_audit_tenant_time_idx",
            ),
            models.Index(
                fields=["action", "created_at"],
                name="acct_audit_action_time_idx",
            ),
        ]

    def __str__(self):
        return self.action


def normalize_answer_language(value):
    """Return a canonical AI answer language choice."""

    normalized = str(value or "").strip().replace("_", "-").lower()
    prefix = normalized.split("-", 1)[0]
    return {
        "en": "en-US",
        "zh": "zh-CN",
        "es": "es",
    }.get(prefix, DEFAULT_ANSWER_LANGUAGE)


def answer_language_name(value):
    """Return the English display name used in model instructions."""

    if normalize_answer_language(value) == "zh-CN":
        return "Simplified Chinese"
    if normalize_answer_language(value) == "es":
        return "Spanish"
    return "English"


class Role(models.Model):
    """Role-based visibility definition for consoles and route groups."""

    name = models.CharField(
        max_length=120,
        unique=True,
        help_text="Human-readable role name.",
    )
    visible_features = models.JSONField(
        default=list,
        blank=True,
        help_text="Feature keys visible to users who hold this role.",
    )
    preferred_platform = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Default platform to open after login.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this role participates in access calculation.",
    )
    users = models.ManyToManyField(
        User,
        blank=True,
        related_name="platform_roles",
        help_text="Users directly bound to this role.",
    )
    groups = models.ManyToManyField(
        "auth.Group",
        blank=True,
        related_name="platform_roles",
        help_text="Groups whose members inherit this role.",
    )

    class Meta:
        ordering = ["name", "id"]

    def save(self, *args, **kwargs):
        """Normalize stored feature and platform values."""
        self.visible_features = normalize_feature_keys(self.visible_features)
        self.preferred_platform = normalize_platform_key(self.preferred_platform)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Profile(models.Model):
    """
    The Profile model extends the built-in User model using a one-to-one
    relationship. It stores personal information, preferences, and
    registration state.

    Note: OAuth/Social authentication uses django-allauth's SocialAccount
    model. To query social accounts:
        from allauth.socialaccount.models import SocialAccount
        social_accounts = SocialAccount.objects.filter(user=user)
        google_account = SocialAccount.objects.get(
            user=user,
            provider='google'
        )
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    registration_completed = models.BooleanField(
        default=False,
        help_text=(
            "Indicates whether user has completed the registration process "
            "(set password, virtual email, scene selection)."
        ),
    )

    registration_token = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Temporary token for email registration verification. " "Expires after REGISTRATION_TOKEN_EXPIRY_HOURS."
        ),
    )

    registration_token_expires = models.DateTimeField(
        null=True, blank=True, help_text="Expiration datetime for registration_token."
    )

    nickname = models.CharField(max_length=30, blank=True, help_text="User nickname.")

    avatar_url = models.URLField(blank=True, help_text="URL link to the user's avatar.")

    bio = models.TextField(max_length=500, blank=True, help_text="Personal biography or description.")

    language = models.CharField(
        max_length=10,
        default="zh-CN",
        choices=[
            ("en-US", "English"),
            ("zh-CN", "简体中文"),
            ("es", "Español"),
            ("ja-JP", "日本語"),
            ("ko-KR", "한국어"),
        ],
        help_text=(
            "Specifies the language used by AI when generating summaries, "
            "titles, and metadata. This is a global setting shared across "
            "all applications."
        ),
    )

    timezone = models.CharField(
        max_length=50,
        default="Asia/Shanghai",
        help_text=(
            "User's timezone for displaying dates and times. "
            "Common values: 'UTC', 'Asia/Shanghai', 'America/New_York', etc."
        ),
    )

    preferred_platform = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text=("Optional user-level preferred platform. " "If empty, the effective role preference is used."),
    )

    class PlatformRole(models.TextChoices):
        NONE = "none", "None"
        SUPER_ADMIN = "super_admin", "Super Admin"

    platform_role = models.CharField(
        max_length=20,
        choices=PlatformRole.choices,
        default=PlatformRole.NONE,
        help_text=(
            "Platform-level role. Tenant roles live on TenantMembership; "
            "super_admin is not a tenant membership role."
        ),
    )

    def __str__(self):
        """
        Returns the username of the associated User model for a readable
        representation of the Profile instance.
        """
        return self.user.username
