"""Transactional guards for creating work against an assistant."""

from django.core.cache import cache
from django.db import transaction

from .models import Assistant, GlobalSetting, Session


SMART_COLLABORATION_SLUG = "__system-smart-collaboration__"
SMART_COLLABORATION_MODEL_SETTING = "lens.smart_collaboration.model_ref"


class AssistantNotRunnableError(RuntimeError):
    """Raised when an assistant cannot accept new work."""


def _has_document_attachments(session):
    """Return whether cached document uploads still belong to a session."""

    key = f"lens:session_document_attachments:{session.uuid}"
    return bool(cache.get(key) or [])


def _find_reusable_empty_session(assistant, user):
    """Return the oldest active empty session for an assistant and user."""

    candidates = Session.objects.filter(
        assistant=assistant,
        user=user,
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

    locked = Assistant.objects.select_for_update().get(pk=assistant.pk)
    if (
        locked.slug == SMART_COLLABORATION_SLUG
        and locked.capability != Assistant.Capability.GENERAL_CHAT
    ):
        locked.capability = Assistant.Capability.GENERAL_CHAT
        locked.save(update_fields=["capability", "updated_at"])
    if locked.is_system:
        is_runnable = locked.status == Assistant.Status.ACTIVE
    elif user is None:
        is_runnable = locked.status == Assistant.Status.ACTIVE
    else:
        is_runnable = locked.is_runnable_by(user)
    if not is_runnable:
        raise AssistantNotRunnableError
    return locked


@transaction.atomic
def create_assistant_session(assistant_uuid, user, title=""):
    """Create a session while serializing against assistant archival."""

    assistant = Assistant.objects.get(uuid=assistant_uuid)
    assistant = lock_assistant_for_new_work(assistant, user)
    normalized_title = " ".join(str(title or "").split())
    if assistant.mode_handler.supports_members and not assistant.is_system:
        members = fixed_collaboration_assistants(assistant, user)
        session = Session.objects.create(
            assistant=assistant,
            user=user,
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
        assistant=assistant,
        user=user,
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


def _smart_collaboration_assistant():
    """Return the hidden coordinator backed by the configured global model."""

    setting = GlobalSetting.objects.filter(
        key=SMART_COLLABORATION_MODEL_SETTING
    ).first()
    model_ref = str(setting.value or "") if setting else ""
    if not model_ref:
        raise AssistantNotRunnableError
    assistant, _ = Assistant.objects.get_or_create(
        slug=SMART_COLLABORATION_SLUG,
        defaults={
            "name": "Smart Collaboration",
            "description": "Internal Smart Collaboration coordinator.",
            "capability": Assistant.Capability.GENERAL_CHAT,
            "agent_model_ref": model_ref,
            "is_system": True,
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

    allowed = smart_collaboration_assistants(
        user,
        assistant_uuids or [],
        allow_empty=True,
    )
    assistant = _smart_collaboration_assistant()
    normalized_title = " ".join(str(title or "").split())
    return Session.objects.create(
        assistant=assistant,
        user=user,
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
