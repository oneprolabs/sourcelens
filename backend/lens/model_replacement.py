"""Validate and atomically replace assistant model references."""

from agentcore_metering.adapters.django.models import LLMConfig
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException

from .model_checks import MODEL_REF_FIELDS, build_model_check_settings
from .models import Assistant
from .vision_capabilities import validate_vision_model_ref


class AssistantModelSnapshotSerializer(serializers.Serializer):
    """Identify an assistant and the model observed before replacement."""

    uuid = serializers.UUIDField()
    model_ref = serializers.UUIDField(allow_null=True)


class ModelReplacementSerializer(serializers.Serializer):
    """Accept an explicit bounded batch and a model slot."""

    model_field = serializers.ChoiceField(choices=MODEL_REF_FIELDS)
    target_model_ref = serializers.UUIDField()
    assistant_models = AssistantModelSnapshotSerializer(many=True, allow_empty=False, max_length=1000)
    preview = serializers.BooleanField(default=True)

    def validate_assistant_models(self, value):
        """Reject duplicate identities rather than applying them twice."""

        if len({item["uuid"] for item in value}) != len(value):
            raise serializers.ValidationError("Assistant UUIDs must be unique.")
        return value


class ModelReplacementConflict(APIException):
    """Report that the observed assistant configuration is stale."""

    status_code = 409
    default_detail = "Assistant models have changed. Reload the list and preview again."
    default_code = "model_replacement_conflict"


@transaction.atomic
def replace_assistant_models(*, user, model_field, target_model_ref, assistant_models, preview):
    """Validate the complete batch before writing any assistant."""

    target = (
        LLMConfig.objects.select_for_update()
        .filter(
            uuid=target_model_ref,
            scope=LLMConfig.Scope.GLOBAL,
            model_type=LLMConfig.MODEL_TYPE_LLM,
            is_active=True,
        )
        .first()
    )
    if target is None:
        raise serializers.ValidationError({"target_model_ref": "Select an active global LLM configuration."})
    if model_field == "multimodal_model_ref":
        reason = validate_vision_model_ref(target.uuid)
        if reason:
            raise serializers.ValidationError({"target_model_ref": reason})

    expected = {item["uuid"]: item["model_ref"] for item in assistant_models}
    assistants = list(
        Assistant.objects.visible_to(user)
        .filter(uuid__in=expected, status=Assistant.Status.ACTIVE, is_system=False)
        .order_by("uuid")
        .select_for_update()
    )
    if len(assistants) != len(expected):
        raise serializers.ValidationError({"assistant_models": "Some assistants are unavailable. Reload the list."})
    for assistant in assistants:
        if getattr(assistant, model_field) != expected[assistant.uuid]:
            raise ModelReplacementConflict()
        if model_field == "multimodal_model_ref" and assistant.routing_mode == Assistant.RoutingMode.SMART:
            raise serializers.ValidationError({"assistant_models": "Smart assistants do not use a multimodal model."})

    changed = sorted(
        (assistant for assistant in assistants if getattr(assistant, model_field) != target.uuid),
        key=lambda assistant: (assistant.name, str(assistant.uuid)),
    )
    result = {
        "count": len(changed),
        "assistants": [
            {"uuid": str(assistant.uuid), "name": assistant.name, "model_ref": expected[assistant.uuid]}
            for assistant in changed
        ],
        "preview": preview,
    }
    if not preview:
        now = timezone.now()
        checks = {}
        for assistant in changed:
            setattr(assistant, model_field, target.uuid)
            model_refs = tuple(getattr(assistant, field_name) for field_name in MODEL_REF_FIELDS)
            if model_refs not in checks:
                checks[model_refs] = build_model_check_settings(assistant)
            assistant.settings = {**(assistant.settings or {}), "_model_check": checks[model_refs]}
            assistant.updated_at = now
        Assistant.objects.bulk_update(changed, [model_field, "settings", "updated_at"])
    return result
