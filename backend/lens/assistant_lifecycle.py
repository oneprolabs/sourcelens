"""Transactional guards for creating work against an assistant."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction

from accounts.access import get_user_tenant
from accounts.principals import ApiKeyPrincipal, session_owner_filter, session_owner_values

from .models import Assistant, GlobalSetting, Session


SMART_COLLABORATION_SLUG = "__system-smart-collaboration__"
SMART_COLLABORATION_MODEL_SETTING = "lens.smart_collaboration.model_ref"


class AssistantNotRunnableError(RuntimeError):
    """Raised when an assistant cannot accept new work."""


class SmartCollaborationModelNotConfiguredError(AssistantNotRunnableError):
    """Raised when Smart Collaboration has no coordinator model configured."""


def _has_document_attachments(session):
    """Return whether cached document uploads still belong to a session."""

    key = f"lens:session_document_attachments:{session.uuid}"
    return bool(cache.get(key) or [])


def _find_reusable_empty_session(assistant, user):
    """Return the oldest active empty session for an assistant and user."""

    candidates = Session.objects.filter(
        assistant=assistant,
        tenant=get_user_tenant(user),
        **session_owner_filter(user),
        status=Session.Status.ACTIVE,
        title="",
        title_manually_edited=False,
    ).select_for_update().order_by("created_at", "pk")
    for session in candidates:
        if session.message_set.exists():
            continue
        if session.run_set.exists():
            continue
        if session.attachments.exists():
            continue
        if _has_document_attachments(session):
            continue
        return session
    return None


def lock_assistant_for_new_work(assistant, user=None):
    """Lock and return an assistant after checking its current state."""

    lock_work_user(user)
    try:
        locked = Assistant.objects.select_for_update().get(pk=assistant.pk)
    except Assistant.DoesNotExist as exc:
        raise AssistantNotRunnableError from exc
    if (
        locked.slug == SMART_COLLABORATION_SLUG
        and locked.capability != Assistant.Capability.GENERAL_CHAT
    ):
        locked.capability = Assistant.Capability.GENERAL_CHAT
        locked.save(update_fields=["capability", "updated_at"])
    if isinstance(user, ApiKeyPrincipal) and locked.is_system:
        raise AssistantNotRunnableError
    if locked.is_system:
        is_runnable = locked.status == Assistant.Status.ACTIVE and locked.is_enabled
    elif user is None:
        is_runnable = locked.status == Assistant.Status.ACTIVE and locked.is_enabled
    else:
        is_runnable = locked.is_runnable_by(user)
    if not is_runnable:
        raise AssistantNotRunnableError
    return locked


def lock_work_user(user):
    """Serialize human work creation with permanent account removal before locking resources."""
    if user is not None and not isinstance(user, ApiKeyPrincipal):
        if not get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).exists():
            raise AssistantNotRunnableError


@transaction.atomic
def create_assistant_session(assistant_uuid, user, title=""):
    """Create a session while serializing against assistant archival."""

    try:
        assistant = Assistant.objects.get(uuid=assistant_uuid, is_system=False)
    except Assistant.DoesNotExist as exc:
        raise AssistantNotRunnableError from exc
    assistant = lock_assistant_for_new_work(assistant, user)
    normalized_title = " ".join(str(title or "").split())
    if assistant.mode_handler.supports_members and not assistant.is_system:
        members = fixed_collaboration_assistants(assistant, user)
        session = Session.objects.create(
            tenant=get_user_tenant(user),
            assistant=assistant,
            **session_owner_values(user),
            title=normalized_title,
            routing_mode=Session.RoutingMode.SMART,
            allowed_assistant_uuids=[str(item.uuid) for item in members],
            title_manually_edited=bool(normalized_title),
            title_generation_status=(
                Session.TitleGenerationStatus.SKIPPED
                if normalized_title
                else Session.TitleGenerationStatus.PENDING
            ),
        )
        return session
    if not normalized_title:
        existing = _find_reusable_empty_session(assistant, user)
        if existing is not None:
            return existing
    session = Session.objects.create(
        tenant=get_user_tenant(user),
        assistant=assistant,
        **session_owner_values(user),
        title=normalized_title,
        title_manually_edited=bool(normalized_title),
        title_generation_status=(
            Session.TitleGenerationStatus.SKIPPED
            if normalized_title
            else Session.TitleGenerationStatus.PENDING
        ),
    )
    return session


def fixed_collaboration_assistants(assistant, user):
    """Return active accessible members for a Smart Assistant."""

    if not assistant.mode_handler.supports_members or assistant.is_system:
        raise AssistantNotRunnableError
    members = list(
        assistant.collaboration_members.filter(
            status=Assistant.Status.ACTIVE,
            is_enabled=True,
            is_system=False,
            routing_mode=Assistant.RoutingMode.DIRECT,
        ).order_by("name", "uuid")
    )
    configured_ids = {
        str(value) for value in assistant.collaboration_members.values_list(
            "uuid", flat=True
        )
    }
    if not members or {str(item.uuid) for item in members} != configured_ids:
        raise AssistantNotRunnableError
    if not all(item.is_accessible_by(user) for item in members):
        raise AssistantNotRunnableError
    return members


def smart_collaboration_assistants(
    user,
    assistant_uuids=None,
    allow_empty=False,
):
    """Return active assistants allowed for Smart Collaboration."""

    assistants = Assistant.objects.visible_to(user).filter(
        status=Assistant.Status.ACTIVE,
            is_enabled=True,
        is_system=False,
        routing_mode=Assistant.RoutingMode.DIRECT,
        capability__in=[
            Assistant.Capability.GENERAL_CHAT,
            Assistant.Capability.CODE_ANALYSIS,
            Assistant.Capability.KNOWLEDGE_QA,
        ],
    )
    requested = {str(value) for value in assistant_uuids or []}
    if assistant_uuids is not None:
        assistants = assistants.filter(uuid__in=requested)
    values = list(assistants)
    if (
        assistant_uuids is not None
        and {str(item.uuid) for item in values} != requested
    ):
        raise AssistantNotRunnableError
    if not values and not allow_empty:
        raise AssistantNotRunnableError
    return values


def _smart_collaboration_model_ref(tenant=None):
    """Return the coordinator model, falling back to the default LLM."""

    setting = GlobalSetting.objects.filter(
        key=SMART_COLLABORATION_MODEL_SETTING
    ).first()
    model_ref = str(setting.value or "") if setting else ""
    from lens.model_scope import default_model_ref, model_queryset

    if model_ref and model_queryset(tenant).filter(uuid=model_ref, is_active=True).exists():
        return model_ref
    return str(default_model_ref(tenant) or "")


def _smart_collaboration_assistant(tenant=None):
    """Return the hidden coordinator backed by the configured global model."""

    model_ref = _smart_collaboration_model_ref(tenant)
    if not model_ref:
        raise SmartCollaborationModelNotConfiguredError
    assistant, _ = Assistant.objects.get_or_create(
        slug=f"{SMART_COLLABORATION_SLUG}-{tenant.pk}" if tenant else SMART_COLLABORATION_SLUG,
        defaults={
            "name": "Smart Collaboration",
            "description": "Internal Smart Collaboration coordinator.",
            "capability": Assistant.Capability.GENERAL_CHAT,
            "agent_model_ref": model_ref,
            "is_system": True,
            "tenant": tenant,
        },
    )
    if assistant.agent_model_ref != model_ref or not assistant.is_system:
        assistant.agent_model_ref = model_ref
        assistant.is_system = True
        assistant.save(update_fields=["agent_model_ref", "is_system", "updated_at"])
    return assistant


@transaction.atomic
def create_smart_collaboration_session(user, title="", assistant_uuids=None):
    """Create a Smart Collaboration session with a user-scoped range."""

    lock_work_user(user)
    allowed = smart_collaboration_assistants(
        user,
        assistant_uuids or [],
        allow_empty=True,
    )
    assistant = _smart_collaboration_assistant(get_user_tenant(user))
    normalized_title = " ".join(str(title or "").split())
    return Session.objects.create(
        tenant=get_user_tenant(user),
        assistant=assistant,
        **session_owner_values(user),
        title=normalized_title,
        routing_mode=Session.RoutingMode.SMART,
        allowed_assistant_uuids=[str(item.uuid) for item in allowed],
        title_manually_edited=bool(normalized_title),
        title_generation_status=(
            Session.TitleGenerationStatus.SKIPPED
            if normalized_title
            else Session.TitleGenerationStatus.PENDING
        ),
    )
