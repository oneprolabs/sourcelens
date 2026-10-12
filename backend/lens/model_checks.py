from django.utils import timezone

MODEL_REF_FIELDS = (
    "multimodal_model_ref",
    "agent_model_ref",
    "decision_model_ref",
)


def build_model_check_settings(assistant):
    """Return model reference check results for an assistant."""

    result = {"checked_at": timezone.now().isoformat()}
    for field_name in MODEL_REF_FIELDS:
        model_ref = getattr(assistant, field_name)
        result[field_name] = _check_model_ref(model_ref, "decision" if field_name == "decision_model_ref" else "llm")
    return result


def check_assistant_model_refs(assistant):
    """Persist non-blocking model reference checks to assistant settings."""

    settings = dict(assistant.settings or {})
    settings["_model_check"] = build_model_check_settings(assistant)
    assistant.settings = settings
    assistant.save(update_fields=["settings", "updated_at"])
    return settings["_model_check"]


def _check_model_ref(model_ref, model_type="llm"):
    if not model_ref:
        return {
            "status": "skipped",
            "error": "",
            "model_ref": "",
        }

    model_ref_value = str(model_ref)
    from agentcore_metering.adapters.django.models import LLMConfig

    try:
        config = LLMConfig.objects.get(uuid=model_ref)
    except (LLMConfig.DoesNotExist, ValueError, TypeError):
        return {
            "status": "error",
            "error": "LLMConfig not found.",
            "model_ref": model_ref_value,
        }

    if config.model_type != model_type:
        return {
            "status": "error",
            "error": f"LLMConfig model_type is not {model_type}.",
            "model_ref": model_ref_value,
        }

    if not config.is_active:
        return {
            "status": "error",
            "error": "LLMConfig is inactive.",
            "model_ref": model_ref_value,
        }

    return {
        "status": "ok",
        "error": "",
        "model_ref": model_ref_value,
    }
