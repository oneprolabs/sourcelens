"""Apply one password retry policy to REST, allauth and Django admin authentication."""

from allauth.account.auth_backends import AuthenticationBackend
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from accounts.services.password_login import PasswordLoginRejected, authenticate_password


class PasswordAuthenticationBackend(AuthenticationBackend):
    """Preserve email login and Django admin usernames without a password-lock bypass."""

    def authenticate(self, request, username=None, password=None, **credentials):
        """Use the same row lock before checking any password authentication entry point."""
        email = credentials.get("email")
        if not password or not (email or username):
            return None
        user = get_user_model().objects.filter(username=username).first() if username and not email else None
        identifier = email or (user.email if user and user.email else username)
        if user is None:
            matches = list(
                get_user_model()
                .objects.filter(Q(email__iexact=identifier) | Q(emailaddress__email__iexact=identifier))
                .distinct()
                .only("email")[:2]
            )
            if len(matches) == 1 and matches[0].email:
                identifier = matches[0].email

        def verify():
            """Retain the original username-admin and email-allauth behavior."""
            if user is not None:
                return ModelBackend().authenticate(request, username=username, password=password)
            return super(PasswordAuthenticationBackend, self).authenticate(
                request, username=username, password=password, **credentials
            )

        try:
            return authenticate_password(email=identifier, authenticate=verify)
        except PasswordLoginRejected as error:
            if request is not None:
                request.password_login_failure = error
            raise PermissionDenied from error
