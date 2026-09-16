import logging
import re

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from allauth.socialaccount import providers
from allauth.socialaccount.models import SocialAccount
from dj_rest_auth.serializers import PasswordChangeSerializer

from accounts.access import (
    get_access_profile,
    get_effective_roles,
    normalize_feature_keys,
    normalize_platform_key,
    serialize_feature_options,
    serialize_platform_options,
)
from accounts.models import Profile, normalize_answer_language


def normalize_language_code(value):
    """Normalize browser or API language variants for AI answers."""

    return normalize_answer_language(value)


def check_username_uniqueness(username):
    """
    Check if username is unique.

    Currently checks User.username uniqueness.
    Can be extended to check EmailAlias if needed.
    """
    return not User.objects.filter(username=username).exists()


def validate_password_policy(value, user=None):
    """Validate the shared product and Django password policies."""
    errors = []

    if len(value) < 8:
        errors.append(_("Password must be at least 8 characters long"))
    if len(value) > 32:
        errors.append(_("Password cannot exceed 32 characters"))
    if not re.search(r'[a-zA-Z]', value) or not re.search(r'[0-9]', value):
        errors.append(_("Password must contain both letters and numbers"))

    if not errors:
        try:
            validate_password(value, user=user)
        except DjangoValidationError as error:
            errors.extend(error.messages)

    if errors:
        raise serializers.ValidationError(errors)

    return value


def normalize_password_fields(data):
    """Normalize number-suffixed fields produced by the camel-case parser."""
    normalized = data.copy()
    aliases = {
        'new_password_1': 'new_password1',
        'new_password_2': 'new_password2',
    }

    for alias, field_name in aliases.items():
        if field_name not in normalized and alias in normalized:
            normalized[field_name] = normalized[alias]

    return normalized


class SuccessResponseSerializer(serializers.Serializer):
    """
    Standard success response for Swagger documentation.
    """
    success = serializers.BooleanField(default=True)
    message = serializers.CharField()


class TokenVerificationResponseSerializer(serializers.Serializer):
    """
    Token verification response with email.
    """
    success = serializers.BooleanField(default=True)
    email = serializers.EmailField()


class AuthTokenResponseSerializer(serializers.Serializer):
    """
    Authentication token response (JWT).
    """
    access = serializers.CharField(help_text=_("JWT access token"))
    refresh = serializers.CharField(help_text=_("JWT refresh token"))
    user = serializers.DictField(help_text=_("User basic info"))


class McpTokenRequestSerializer(serializers.Serializer):
    """
    Optional request for long-lived MCP client token issuance.
    """
    lifetime_months = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text=_("Requested token validity in months"),
    )


class McpTokenResponseSerializer(serializers.Serializer):
    """
    Long-lived token response for external MCP clients.
    """
    access = serializers.CharField(
        help_text=_("Long-lived JWT access token")
    )
    token_type = serializers.CharField(
        default="Bearer",
        help_text=_("Authorization scheme for the token"),
    )
    scope = serializers.CharField(
        help_text=_("Marker claim identifying the MCP scope")
    )
    expires_in = serializers.IntegerField(
        help_text=_("Remaining validity in seconds")
    )
    expires_at = serializers.IntegerField(
        help_text=_("Expiry as a Unix timestamp")
    )


class UsernameAvailabilityResponseSerializer(serializers.Serializer):
    """
    Username availability check response.
    """
    available = serializers.BooleanField()
    username = serializers.CharField()
    message = serializers.CharField()


class SceneSerializer(serializers.Serializer):
    """
    Scene information serializer.
    """
    key = serializers.CharField(
        help_text=_("Scene key (e.g., 'chat', 'product_issue')")
    )
    name = serializers.CharField(
        help_text=_("Scene display name in requested language")
    )
    description = serializers.CharField(
        help_text=_("Scene description in requested language")
    )


class SendRegistrationEmailSerializer(serializers.Serializer):
    """
    Serializer for sending registration email.
    """
    email = serializers.EmailField(
        required=True,
        help_text=_("User's email address")
    )

    def validate_email(self, value):
        """
        Validate email is not already registered and completed.

        Check if user registered via OAuth and provide friendly hint.
        Allow re-sending email if user exists but registration not completed.
        """
        value = value.lower().strip()

        try:
            user = User.objects.get(email=value)

            try:
                profile = user.profile
                if profile.registration_completed:
                    social_accounts = SocialAccount.objects.filter(
                        user=user
                    )

                    if social_accounts.exists():
                        provider_names = []
                        for acc in social_accounts:
                            try:
                                provider_class = (
                                    providers.registry.by_id(
                                        acc.provider
                                    )
                                )
                                provider_names.append(
                                    provider_class.name
                                )
                            except Exception:
                                provider_names.append(
                                    acc.provider.title()
                                )

                        providers_str = ' or '.join(provider_names)

                        raise serializers.ValidationError(
                            _(
                                "This email is already registered "
                                "via %(providers)s. "
                                "Please use %(providers)s to "
                                "login instead."
                            ) % {'providers': providers_str}
                        )
                    else:
                        raise serializers.ValidationError(
                            _(
                                "This email address is already "
                                "registered. Please login instead."
                            )
                        )
            except Profile.DoesNotExist:
                pass

        except User.DoesNotExist:
            pass

        return value


class SendLoginCodeSerializer(serializers.Serializer):
    """
    Serializer for requesting an email login verification code.
    """
    email = serializers.EmailField(
        required=True,
        help_text=_("User's email address")
    )
    turnstile_token = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Cloudflare Turnstile token")
    )

    def validate_email(self, value):
        """Normalize the email address."""
        return value.lower().strip()


class VerifyLoginCodeSerializer(serializers.Serializer):
    """
    Serializer for verifying an email login code.
    """
    email = serializers.EmailField(
        required=True,
        help_text=_("User's email address")
    )
    code = serializers.CharField(
        required=True,
        min_length=6,
        max_length=6,
        help_text=_("Verification code from email")
    )

    def validate_email(self, value):
        """Normalize the email address."""
        return value.lower().strip()

    def validate_code(self, value):
        """Require the six-digit numeric code issued by the service."""
        if not re.fullmatch(r'[0-9]{6}', value):
            raise serializers.ValidationError(
                _("The verification code is incorrect.")
            )
        return value


class VirtualEmailUsernameSerializer(serializers.Serializer):
    """
    Serializer for validating virtual email username.
    """
    username = serializers.CharField(
        min_length=3,
        max_length=64,
        required=True,
        help_text=_(
            "Virtual email username "
            "(will become username@domain)"
        )
    )

    def validate_username(self, value):
        """
        Validate virtual email username format and uniqueness.
        """
        value = value.lower().strip()

        if not re.match(r'^[a-zA-Z0-9._-]+$', value):
            raise serializers.ValidationError(
                _(
                    "Username can only contain letters, numbers, "
                    "dots, hyphens, and underscores"
                )
            )

        if value.startswith('.') or value.endswith('.'):
            raise serializers.ValidationError(
                _("Username cannot start or end with a dot")
            )

        reserved_words = [
            'admin', 'administrator', 'root', 'postmaster',
            'webmaster', 'hostmaster', 'noreply', 'no-reply',
            'support', 'help', 'info', 'contact'
        ]

        if value in reserved_words:
            raise serializers.ValidationError(
                _("This username is reserved and cannot be used")
            )

        if not check_username_uniqueness(value):
            raise serializers.ValidationError(
                _("This username is already taken")
            )

        return value


class CompleteRegistrationSerializer(serializers.Serializer):
    """
    Serializer for completing user registration.
    """
    token = serializers.CharField(
        required=True,
        help_text=_("Registration verification token")
    )

    password = serializers.CharField(
        min_length=8,
        max_length=32,
        write_only=True,
        style={'input_type': 'password'},
        help_text=_(
            "User password (8-32 characters, "
            "must contain letters and numbers)"
        )
    )

    virtual_email_username = serializers.CharField(
        min_length=3,
        max_length=64,
        required=True,
        help_text=_("Virtual email username")
    )

    scene = serializers.CharField(
        required=False,
        help_text=_(
            "User's selected scene "
            "(e.g., 'chat', 'product_issue')"
        )
    )

    language = serializers.CharField(
        required=True,
        help_text=_(
            "Specifies the language used by AI when generating "
            "summaries, titles, and metadata."
        )
    )

    timezone = serializers.CharField(
        required=True,
        help_text=_(
            "User's timezone "
            "(e.g., 'UTC', 'Asia/Shanghai')"
        )
    )

    def validate_password(self, value):
        """
        Validate password strength: 8-32 characters,
        must contain letters and numbers.
        """
        if len(value) < 8:
            raise serializers.ValidationError(
                _("Password must be at least 8 characters long")
            )

        if len(value) > 32:
            raise serializers.ValidationError(
                _("Password cannot exceed 32 characters")
            )

        has_letter = re.search(r'[a-zA-Z]', value)
        has_number = re.search(r'[0-9]', value)

        if not (has_letter and has_number):
            raise serializers.ValidationError(
                _("Password must contain both letters and numbers")
            )

        return value

    def validate_virtual_email_username(self, value):
        """
        Validate virtual email username.

        Reuse validation logic from VirtualEmailUsernameSerializer.
        """
        username_serializer = VirtualEmailUsernameSerializer(
            data={'username': value}
        )

        if not username_serializer.is_valid():
            raise serializers.ValidationError(
                username_serializer.errors['username']
            )

        return username_serializer.validated_data['username']

    def validate_language(self, value):
        """
        Normalize language to supported value.
        """
        return normalize_language_code(value)


class CompleteGoogleSetupSerializer(serializers.Serializer):
    """
    Serializer for completing Google user setup.

    Google users are already authenticated via OAuth,
    so they don't need to provide a password.
    They only need to complete virtual email and preferences setup.
    """

    virtual_email_username = serializers.CharField(
        min_length=3,
        max_length=64,
        required=True,
        help_text=_("Virtual email username")
    )

    scene = serializers.CharField(
        required=False,
        help_text=_("User's selected scene")
    )

    language = serializers.CharField(
        required=True,
        help_text=_(
            "Specifies the language used by AI when generating "
            "summaries, titles, and metadata."
        )
    )

    timezone = serializers.CharField(
        required=True,
        help_text=_("User's timezone")
    )

    def validate_scene(self, value):
        """
        Validate scene is valid.
        """
        if not value:
            return None
        return value

    def validate_virtual_email_username(self, value):
        """
        Validate virtual email username.

        Reuse validation logic from VirtualEmailUsernameSerializer.
        """
        username_serializer = VirtualEmailUsernameSerializer(
            data={'username': value}
        )

        if not username_serializer.is_valid():
            raise serializers.ValidationError(
                username_serializer.errors['username']
            )

        return username_serializer.validated_data['username']

    def validate_language(self, value):
        """
        Normalize language to supported value.
        """
        return normalize_language_code(value)


class UserDetailsSerializer(serializers.ModelSerializer):
    """
    Custom user details serializer for dj-rest-auth.
    Includes virtual email address from EmailAlias and profile information.
    Also includes authentication method information and password change
    capability.
    """
    display_name = serializers.SerializerMethodField(
        read_only=True,
        help_text=_(
            "User-friendly display name, prioritizing "
            "first_name + last_name from OAuth providers, "
            "falling back to username"
        )
    )

    virtual_email = serializers.SerializerMethodField(
        read_only=True,
        help_text=_(
            "Primary virtual email address for receiving emails"
        )
    )

    profile = serializers.SerializerMethodField(
        help_text=_("User profile information")
    )
    profile_language = serializers.CharField(
        write_only=True,
        required=False,
        help_text=_(
            "User's preferred language for the interface, "
            "AI generation, and backend logic"
        )
    )
    profile_timezone = serializers.CharField(
        write_only=True,
        required=False,
        help_text=_(
            "User's timezone for backend logic "
            "and date/time display"
        )
    )

    auth_info = serializers.SerializerMethodField(
        read_only=True,
        help_text=_("Authentication method and related information")
    )
    access_profile = serializers.SerializerMethodField(
        read_only=True,
        help_text=_("Resolved platform visibility and landing route"),
    )
    roles = serializers.SerializerMethodField(
        read_only=True,
        help_text=_("Effective roles resolved from direct and group bindings."),
    )
    permissions = serializers.SerializerMethodField(
        read_only=True,
        help_text=_("Effective Django permission codenames."),
    )

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'display_name',
            'virtual_email',
            'profile',
            'profile_language',
            'profile_timezone',
            'auth_info',
            'is_staff',
            'is_superuser',
            'roles',
            'permissions',
            'access_profile',
        ]
        read_only_fields = [
            'id',
            'username',
            'email',
            'virtual_email',
            'profile',
            'auth_info',
            'display_name',
            'is_staff',
            'is_superuser',
            'roles',
            'permissions',
            'access_profile',
        ]

    def get_display_name(self, obj):
        """
        Get user-friendly display name.

        Priority order:
        1. first_name + last_name (from OAuth providers like Google)
        2. first_name only (if last_name is empty)
        3. username (fallback)

        This provides a more friendly name display for OAuth users
        who typically have proper first_name and last_name from their provider.
        """
        if obj.first_name and obj.last_name:
            return f"{obj.first_name} {obj.last_name}".strip()
        elif obj.first_name:
            return obj.first_name.strip()
        else:
            return obj.username

    def get_virtual_email(self, obj):
        """
        Get the primary virtual email address for the user.

        Returns None as EmailAlias is not implemented in this project.
        Can be extended to return email alias if needed.
        """
        return None

    def get_profile(self, obj):
        """
        Get user profile information.

        Returns registration status and preferences.
        """
        try:
            profile = obj.profile
            return {
                'registration_completed': (
                    profile.registration_completed
                ),
                'language': profile.language,
                'timezone': profile.timezone,
                'nickname': profile.nickname,
                'avatar_url': profile.avatar_url
            }
        except Profile.DoesNotExist:
            return None

    def get_auth_info(self, obj):
        """
        Get authentication method information.

        Returns authentication type, provider info, and capabilities.
        """
        auth_info = {
            'method': 'email',
            'provider': None,
            'provider_account_id': None,
            'provider_email': None,
            'can_change_password': obj.has_usable_password(),
            'login_identifier': None
        }

        try:
            social_accounts = SocialAccount.objects.filter(user=obj)

            if social_accounts.exists():
                social_account = social_accounts.first()
                provider_id = social_account.provider

                provider_name_map = {
                    'google': 'Google',
                    'github': 'GitHub',
                    'facebook': 'Facebook',
                    'twitter': 'Twitter',
                }

                auth_info['method'] = 'oauth'
                auth_info['provider'] = provider_name_map.get(
                    provider_id,
                    provider_id.title()
                )
                auth_info['provider_account_id'] = social_account.uid
                auth_info['provider_email'] = (
                    social_account.extra_data.get('email')
                )
                auth_info['login_identifier'] = (
                    f"{auth_info['provider']} "
                    f"({auth_info['provider_email'] or social_account.uid})"
                )
            else:
                auth_info['login_identifier'] = obj.email

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(
                "Error getting auth info for user %s: %s",
                obj.id,
                e,
                exc_info=True
            )

        return auth_info

    def get_roles(self, obj):
        """Serialize effective roles for the current user."""
        effective_roles = get_effective_roles(obj)
        return [
            {
                'id': role.pk,
                'name': role.name,
                'visible_features': normalize_feature_keys(
                    role.visible_features
                ),
                'preferred_platform': normalize_platform_key(
                    role.preferred_platform
                ),
                'is_active': role.is_active,
            }
            for role in effective_roles
        ]

    def get_permissions(self, obj):
        """Serialize effective Django permissions for client-side routing."""
        return sorted(obj.get_all_permissions())

    def get_access_profile(self, obj):
        """Serialize resolved access information for the current user."""
        return get_access_profile(obj)

    def update(self, instance, validated_data):
        """
        Update user instance and profile language/timezone if provided.
        """
        profile_language = validated_data.pop('profile_language', None)
        profile_timezone = validated_data.pop('profile_timezone', None)

        # Update user fields
        instance = super().update(instance, validated_data)

        # Update profile language and/or timezone if provided
        if profile_language is not None or profile_timezone is not None:
            try:
                profile = instance.profile
                update_fields = []
                if profile_language is not None:
                    normalized_lang = normalize_language_code(
                        profile_language
                    )
                    profile.language = normalized_lang
                    update_fields.append('language')
                if profile_timezone is not None:
                    tz_value = (
                        profile_timezone.strip() or 'Asia/Shanghai'
                    )
                    profile.timezone = tz_value
                    update_fields.append('timezone')
                if update_fields:
                    profile.save(update_fields=update_fields)
            except Profile.DoesNotExist:
                default_lang = (
                    normalize_language_code(profile_language)
                    if profile_language
                    else Profile._meta.get_field('language').default
                )
                default_tz = (
                    profile_timezone.strip()
                    if profile_timezone
                    else 'Asia/Shanghai'
                )
                Profile.objects.create(
                    user=instance,
                    language=default_lang,
                    timezone=default_tz
                )
        return instance


class CustomPasswordResetSerializer(serializers.Serializer):
    """Validate reset request input without revealing account state."""
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Validate a reset token and replacement password."""
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password1 = serializers.CharField(write_only=True)
    new_password2 = serializers.CharField(write_only=True)

    def to_internal_value(self, data):
        """Accept the documented camelCase API fields."""
        return super().to_internal_value(normalize_password_fields(data))

    def validate(self, attrs):
        """Resolve the user and enforce reset requirements."""
        if attrs['new_password1'] != attrs['new_password2']:
            raise serializers.ValidationError(
                {'new_password2': _("Passwords do not match")}
            )

        try:
            user_id = urlsafe_base64_decode(attrs['uid']).decode()
            user = User.objects.get(pk=user_id)
        except (
            TypeError,
            ValueError,
            OverflowError,
            UnicodeDecodeError,
            User.DoesNotExist,
        ):
            raise serializers.ValidationError(
                {'token': _("Invalid or expired reset link")}
            )

        if not default_token_generator.check_token(user, attrs['token']):
            raise serializers.ValidationError(
                {'token': _("Invalid or expired reset link")}
            )

        validate_password_policy(attrs['new_password1'], user=user)
        attrs['user'] = user
        return attrs


class CustomPasswordChangeSerializer(PasswordChangeSerializer):
    """Require the current password and enforce the product password policy."""

    def to_internal_value(self, data):
        """Accept the documented camelCase API fields."""
        return super().to_internal_value(normalize_password_fields(data))

    def custom_validation(self, attrs):
        """Validate the new password before the form saves it."""
        validate_password_policy(attrs['new_password1'], user=self.user)
