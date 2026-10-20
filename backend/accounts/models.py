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
from django.contrib.auth.models import User
from django.db import models

from accounts.access import normalize_feature_keys, normalize_platform_key


DEFAULT_ANSWER_LANGUAGE = 'en-US'


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


def normalize_answer_language(value):
    """Return a canonical AI answer language choice."""

    normalized = str(value or '').strip().replace('_', '-').lower()
    prefix = normalized.split('-', 1)[0]
    return {
        'en': 'en-US',
        'zh': 'zh-CN',
        'es': 'es',
    }.get(prefix, DEFAULT_ANSWER_LANGUAGE)


def answer_language_name(value):
    """Return the English display name used in model instructions."""

    if normalize_answer_language(value) == 'zh-CN':
        return 'Simplified Chinese'
    if normalize_answer_language(value) == 'es':
        return 'Spanish'
    return 'English'


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
        default='',
        help_text="Default platform to open after login.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this role participates in access calculation.",
    )
    users = models.ManyToManyField(
        User,
        blank=True,
        related_name='platform_roles',
        help_text="Users directly bound to this role.",
    )
    groups = models.ManyToManyField(
        'auth.Group',
        blank=True,
        related_name='platform_roles',
        help_text="Groups whose members inherit this role.",
    )

    class Meta:
        ordering = ['name', 'id']

    def save(self, *args, **kwargs):
        """Normalize stored feature and platform values."""
        self.visible_features = normalize_feature_keys(self.visible_features)
        self.preferred_platform = normalize_platform_key(
            self.preferred_platform
        )
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

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE
    )

    registration_completed = models.BooleanField(
        default=False,
        help_text=(
            "Indicates whether user has completed the registration process "
            "(set password, virtual email, scene selection)."
        )
    )

    registration_token = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Temporary token for email registration verification. "
            "Expires after REGISTRATION_TOKEN_EXPIRY_HOURS."
        )
    )

    registration_token_expires = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Expiration datetime for registration_token."
    )

    nickname = models.CharField(
        max_length=30,
        blank=True,
        help_text="User nickname."
    )

    avatar_url = models.URLField(
        blank=True,
        help_text="URL link to the user's avatar."
    )

    bio = models.TextField(
        max_length=500,
        blank=True,
        help_text="Personal biography or description."
    )

    language = models.CharField(
        max_length=10,
        default='zh-CN',
        choices=[
            ('en-US', 'English'),
            ('zh-CN', '简体中文'),
            ('es', 'Español'),
            ('ja-JP', '日本語'),
            ('ko-KR', '한국어'),
        ],
        help_text=(
            "Specifies the language used by AI when generating summaries, "
            "titles, and metadata. This is a global setting shared across "
            "all applications."
        )
    )

    timezone = models.CharField(
        max_length=50,
        default='Asia/Shanghai',
        help_text=(
            "User's timezone for displaying dates and times. "
            "Common values: 'UTC', 'Asia/Shanghai', 'America/New_York', etc."
        )
    )

    preferred_platform = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text=(
            "Optional user-level preferred platform. "
            "If empty, the effective role preference is used."
        )
    )

    def __str__(self):
        """
        Returns the username of the associated User model for a readable
        representation of the Profile instance.
        """
        return self.user.username
