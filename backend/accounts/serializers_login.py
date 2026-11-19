"""Preserve the existing password authentication contract behind bounded retry state."""

from dj_rest_auth.serializers import LoginSerializer
from rest_framework.exceptions import ValidationError


class PasswordLoginSerializer(LoginSerializer):
    """Count only validated email/password requests after the Turnstile check."""

    def validate(self, attrs):
        """Expose the backend's committed retry outcome only on the public REST login."""
        try:
            return super().validate(attrs)
        except ValidationError:
            failure = getattr(self.context["request"], "password_login_failure", None)
            if failure is not None:
                raise failure
            raise
