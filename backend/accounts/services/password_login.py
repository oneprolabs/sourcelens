"""Password-only lockouts remain separate from membership disablement and verified email login."""

from datetime import timedelta

from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.db.models.functions import Lower, Trim
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from accounts.models import PasswordLoginState

MAX_FAILURES = 5
LOCK_DURATION = timedelta(minutes=15)


class PasswordLoginRejected(APIException):
    """Return one stable public outcome without serializer field-error list coercion."""

    status_code = 400
    default_code = "password_login_rejected"


def with_password_state(queryset):
    """Project the current lock in the same query, including filters before pagination."""
    state = PasswordLoginState.objects.filter(email=Lower(Trim(OuterRef("user__email"))))
    return queryset.annotate(password_locked_until=Subquery(state.values("locked_until")[:1]))


def password_locked_until(membership):
    """Use a projected value when available and query once for individual mutations."""
    if hasattr(membership, "password_locked_until"):
        return membership.password_locked_until
    return (
        PasswordLoginState.objects.filter(email=membership.user.email.strip().lower())
        .values_list("locked_until", flat=True)
        .first()
    )


def active_password_lock(membership):
    """Only an enabled, accepted member has the effective temporary locked status."""
    until = password_locked_until(membership)
    return (
        until
        if membership.status == "active" and membership.user.is_active and until and until > timezone.now()
        else None
    )


def authenticate_password(*, email, authenticate):
    """Commit a failure before returning its public error; serialize concurrent attempts."""
    email = email.strip().lower()
    with transaction.atomic():
        PasswordLoginState.objects.get_or_create(email=email)
        state = PasswordLoginState.objects.select_for_update().get(email=email)
        now = timezone.now()
        if state.locked_until and state.locked_until > now:
            failure = {
                "error_code": "PASSWORD_LOGIN_LOCKED",
                "remaining_attempts": 0,
                "locked_until": state.locked_until.isoformat(),
            }
        else:
            if state.locked_until:
                state.failed_attempts = 0
                state.locked_until = None
            try:
                result = authenticate()
                if result is None:
                    raise ValidationError("PASSWORD_LOGIN_INVALID")
            except ValidationError:
                state.failed_attempts = min(state.failed_attempts + 1, MAX_FAILURES)
                if state.failed_attempts == MAX_FAILURES:
                    state.locked_until = now + LOCK_DURATION
                failure = {
                    "error_code": "PASSWORD_LOGIN_LOCKED" if state.locked_until else "PASSWORD_LOGIN_INVALID",
                    "remaining_attempts": MAX_FAILURES - state.failed_attempts,
                }
                if state.locked_until:
                    failure["locked_until"] = state.locked_until.isoformat()
            else:
                state.failed_attempts = 0
                state.locked_until = None
                state.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
                return result
            state.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    raise PasswordLoginRejected(failure)
