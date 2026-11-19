"""
Registration service for handling user registration operations.
"""

import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from accounts.models import (
    DEFAULT_ANSWER_LANGUAGE,
    Profile,
    normalize_answer_language,
)

logger = logging.getLogger(__name__)


def check_username_uniqueness(username):
    """
    Check if username is unique.

    Note: This is a placeholder. If you have EmailAlias or similar
    functionality, implement the uniqueness check here.
    For now, we just check if User.username is unique.
    """
    return not User.objects.filter(username=username).exists()


class RegistrationService:
    """
    Service class for handling user registration operations.
    Provides atomic operations for creating users with complete
    configuration.
    """

    @staticmethod
    def generate_registration_token() -> str:
        """
        Generate a secure random token for registration verification.

        Returns:
            str: A secure random token (64 characters)
        """
        return secrets.token_urlsafe(48)

    @staticmethod
    def calculate_token_expiry():
        """
        Calculate token expiration datetime.

        Returns:
            datetime: Token expiration datetime
        """
        return (
            timezone.now() +
            timedelta(hours=settings.REGISTRATION_TOKEN_EXPIRY_HOURS)
        )

    @staticmethod
    def is_token_valid(token: str, expires_at) -> bool:
        """
        Check if registration token is still valid.

        Args:
            token: Registration token
            expires_at: Token expiration datetime

        Returns:
            bool: True if token is valid, False otherwise
        """
        if not token or not expires_at:
            return False

        return timezone.now() < expires_at

    @staticmethod
    def validate_virtual_email_alias(alias: str) -> tuple[bool, str]:
        """
        Validate virtual email alias format and uniqueness.

        Format requirements:
        - Length: 3-64 characters
        - Characters: letters, numbers, dots, underscore, hyphen
        - Must start with letter or number
        - Must end with letter or number
        - Cannot start or end with dot

        Args:
            alias: Virtual email alias to validate

        Returns:
            tuple: (is_valid, error_message)
                - (True, '') if valid
                - (False, error_message) if invalid
        """
        if not alias:
            return False, 'Alias cannot be empty'

        if len(alias) < 3 or len(alias) > 64:
            return (
                False,
                'Alias must be between 3 and 64 characters'
            )

        if alias.startswith('.') or alias.endswith('.'):
            return (
                False,
                'Username cannot start or end with a dot'
            )

        pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]$'
        if len(alias) == 3:
            pattern = r'^[a-zA-Z0-9.]{3}$'

        if not re.match(pattern, alias):
            return (
                False,
                'Alias must start and end with letter or number, '
                'and can only contain letters, numbers, dots, '
                'underscores, and hyphens'
            )

        if not check_username_uniqueness(alias):
            return False, 'This virtual email is already taken'

        return True, ''

    @staticmethod
    @transaction.atomic
    def create_user_with_config(
        email: str,
        password: str,
        username: str,
        scene: str,
        language: str,
        timezone_str: str
    ) -> User:
        """
        Create user with complete configuration in atomic transaction.

        This method performs the following operations atomically:
        1. Create User and Profile
        2. Create EmailAlias for virtual email (if available)
        3. Initialize prompt_config based on scene and language (if available)
        4. Initialize email_config for auto_assign mode (if available)

        Args:
            email: User's real email address (for login)
            password: User's password
            username: Custom username for virtual email
            scene: User's selected scene (chat, product_issue, etc.)
            language: User language for the interface and generated content
            timezone_str: User's timezone

        Returns:
            User: Created user instance

        Raises:
            ValueError: If validation fails or configuration error
            Exception: If any step in the creation process fails
        """
        language = normalize_answer_language(language)
        is_valid, error_msg = (
            RegistrationService.validate_virtual_email_alias(
                username
            )
        )
        if not is_valid:
            raise ValueError(f'Invalid virtual email username: {error_msg}')

        if User.objects.filter(email=email).exists():
            raise ValueError('Email already exists')

        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password
            )
            logger.info(f"Created user: {username}")

            profile, profile_created = Profile.objects.get_or_create(
                user=user,
                defaults={
                    'registration_completed': True,
                    'language': language,
                    'timezone': timezone_str
                }
            )

            if not profile_created:
                profile.registration_completed = True
                profile.language = language
                profile.timezone = timezone_str
                profile.save()
                logger.info(
                    f"Updated profile for user: {username} "
                    f"(profile was created by signal)"
                )
            else:
                logger.info(f"Created profile for user: {username}")

            return user

        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)
            logger.error(
                f"Failed to create user with config - "
                f"Email: {email}, "
                f"Username: {username}, "
                f"Scene: {scene}, "
                f"Language: {language}, "
                f"Error: {error_type}: {error_message}",
                exc_info=True,
                extra={
                    'email': email,
                    'username': username,
                    'scene': scene,
                    'language': language,
                    'timezone': timezone_str,
                    'exception_type': error_type,
                    'exception_message': error_message,
                    'service': 'RegistrationService',
                    'method': 'create_user_with_config',
                }
            )
            raise

    @staticmethod
    def create_registration_token(
        email: str,
        language: str
    ) -> tuple[str, Profile]:
        """
        Create or update a registration token for email registration.

        Creates a temporary user and profile with registration token
        if user doesn't exist. Updates token if user exists but
        registration is not completed.

        Args:
            email: User's email address
            language: User's preferred language

        Returns:
            tuple: (token, profile)

        Raises:
            ValueError: If user exists and registration is completed
        """
        language = normalize_answer_language(language)
        token = RegistrationService.generate_registration_token()
        expires_at = (
            timezone.now() +
            timedelta(
                hours=settings.REGISTRATION_TOKEN_EXPIRY_HOURS
            )
        )

        user = User.objects.filter(email__iexact=email).first()
        if user is not None and (not user.is_active or hasattr(user, 'membership')):
            raise ValueError('ACCOUNT_REGISTRATION_UNAVAILABLE')

        if not user:
            username = email.split('@')[0]
            base_username = username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1

            user = User.objects.create_user(
                username=username,
                email=email,
                password=None
            )
            user.set_unusable_password()
            user.save()
            logger.info(f"Created new user: {username} for email: {email}")

        profile, profile_created = Profile.objects.get_or_create(
            user=user,
            defaults={
                'registration_completed': False,
                'registration_token': token,
                'registration_token_expires': expires_at,
                'language': language
            }
        )

        if not profile_created:
            profile.registration_token = token
            profile.registration_token_expires = expires_at
            profile.language = language
            profile.save()

        if profile.registration_completed:
            raise ValueError(
                'User already exists and registration is completed'
            )

        if profile_created:
            logger.info(
                f"Created profile for user: {user.username} "
                f"(email: {email})"
            )
        else:
            logger.info(
                f"Updated registration token for user: {user.username} "
                f"(email: {email})"
            )

        return token, profile

    @staticmethod
    @transaction.atomic
    def get_or_create_otp_user(
        email: str,
        language: str = DEFAULT_ANSWER_LANGUAGE,
        timezone_str: str = 'Asia/Shanghai',
    ) -> User:
        """
        Return an existing user by email or auto-provision a new one.

        Used by the email verification-code login flow. Newly created
        users have no usable password, no staff flag and no roles. The
        profile is marked as registration_completed so the frontend does
        not push them into the password onboarding wizard.

        Args:
            email: User's email address
            language: Preferred user language
            timezone_str: User's timezone

        Returns:
            User: The existing or newly created user
        """
        email = email.lower().strip()
        language = normalize_answer_language(language)
        user = User.objects.filter(email__iexact=email).first()
        user_created = user is None
        if user is not None and not user.is_active:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("ACCOUNT_INACTIVE")

        if user_created:
            base_username = re.sub(
                r'[^a-zA-Z0-9._-]', '', email.split('@')[0]
            )[:140]
            if not base_username:
                base_username = f"user_{secrets.token_hex(4)}"
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1

            user = User.objects.create_user(username=username, email=email)
            user.set_unusable_password()
            user.save()
            logger.info(f"Auto-provisioned OTP user: {username} ({email})")

        profile, created = Profile.objects.get_or_create(
            user=user,
            defaults={
                'registration_completed': True,
                'language': language,
                'timezone': timezone_str,
            },
        )
        if not created:
            update_fields = []
            if user_created:
                profile.language = language
                profile.timezone = timezone_str
                update_fields.extend(['language', 'timezone'])
                user.profile = profile
            if not profile.registration_completed:
                profile.registration_completed = True
                profile.registration_token = None
                profile.registration_token_expires = None
                update_fields.extend(
                    [
                        'registration_completed',
                        'registration_token',
                        'registration_token_expires',
                    ]
                )
            if update_fields:
                profile.save(update_fields=update_fields)

        return user

    @staticmethod
    def verify_registration_token(token: str) -> tuple[bool, Profile]:
        """
        Verify registration token validity and expiration.

        Args:
            token: Registration token to verify

        Returns:
            tuple: (is_valid, profile)
                - (True, profile) if valid
                - (False, None) if invalid or expired
        """
        try:
            profile = Profile.objects.get(
                registration_token=token,
                registration_completed=False
            )

            if (
                profile.registration_token_expires and
                profile.registration_token_expires < timezone.now()
            ):
                logger.warning(
                    f"Registration token expired for user: "
                    f"{profile.user.username}"
                )
                return False, None

            return True, profile

        except Profile.DoesNotExist:
            logger.warning(
                f"Registration token not found or already used: {token}"
            )
            return False, None
