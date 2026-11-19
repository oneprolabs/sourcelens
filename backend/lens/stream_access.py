"""Keep long-lived HTTP streams bound to current web authority."""

from contextvars import copy_context
from types import SimpleNamespace

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user, get_user_model
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import Token

from accounts.access import get_user_tenant
from lens.models import Run


class StreamAuthority:
    """Recheck credentials, current identity and a fixed tenant before each frame."""

    def __init__(self, request):
        """Capture authority while the delegation middleware context is still active."""
        self.request = request
        self.user_id = request.user.pk
        tenant = get_user_tenant(request.user)
        self.tenant_id = tenant.pk if tenant is not None else None
        self.auth = getattr(request, "auth", None)
        self.context = copy_context()
        session = getattr(request, "session", None)
        self.session_store = session.__class__ if session is not None else None
        self.session_key = session.session_key if session is not None else None

    def allows(self, check):
        """Discard request caches and evaluate authorization in the original delegation."""

        def refresh():
            if isinstance(self.auth, Token):
                try:
                    self.auth.check_exp(current_time=timezone.now())
                except TokenError:
                    return False
                user = get_user_model().objects.select_related("membership__tenant").filter(pk=self.user_id).first()
            elif self.auth is None and self.session_key:
                session = self.session_store(session_key=self.session_key)
                user = get_user(SimpleNamespace(session=session))
            else:
                return False
            if user is None or not user.is_authenticated or not user.is_active or user.pk != self.user_id:
                return False
            self.request.user = user
            if hasattr(self.request, "_assistant_configuration_scope"):
                del self.request._assistant_configuration_scope
            tenant = get_user_tenant(user)
            return (tenant.pk if tenant is not None else None) == self.tenant_id and check()

        return self.context.run(refresh)


async def authorized_stream_events(events, authority, check):
    """Close the source on revocation, deletion, disconnect or normal completion."""
    try:
        if not await sync_to_async(authority.allows)(check):
            return
        async for event in events:
            if not await sync_to_async(authority.allows)(check):
                return
            yield event
    except Run.DoesNotExist:
        return
    finally:
        await events.aclose()
