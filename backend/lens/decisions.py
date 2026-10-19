"""Host-owned decision definitions and Assistant model configuration."""

from types import SimpleNamespace

from agentcore_metering.adapters.django.models import LLMConfig
from rest_framework import serializers

from .plugins.decisions import (
    GATE_ACTIVE_PHASE,
    GATE_PHASES,
    default_control_gates,
    gate_is_active,
    resolve_decision_analyses,
    resolve_decision_gates,
    validate_decision_analyses,
    validate_decision_gates,
)
from .plugins.registry import PluginRegistryError

ALL_CAPABILITIES = ["general_chat", "knowledge_qa", "code_analysis"]
DEFAULT_REVIEW_STATE_CHARS = 128000
DECISIONS = [
    {
        "key": key,
        "mode": "control",
        "kind": kind,
        "applies_to": capabilities,
        "tool_keys": ["builtin_decision"],
        "defaults": defaults,
    }
    for key, kind, capabilities, defaults in [
        (
            "search_needed",
            "noul",
            ["knowledge_qa", "code_analysis"],
            {"threshold": 0.5, "margin": 0.1, "max_state_chars": 4000},
        ),
        ("evidence_requirement", "noul", ["general_chat"], {"threshold": 0.5, "margin": 0.1, "max_state_chars": 4000}),
        (
            "evidence_sufficient",
            "noul",
            ALL_CAPABILITIES,
            {"threshold": 0.5, "margin": 0.1, "max_state_chars": DEFAULT_REVIEW_STATE_CHARS},
        ),
        ("answer_supported", "choice", ALL_CAPABILITIES, {"max_state_chars": DEFAULT_REVIEW_STATE_CHARS}),
        ("evidence_strength", "choice", ALL_CAPABILITIES, {"max_state_chars": DEFAULT_REVIEW_STATE_CHARS}),
    ]
] + [
    {
        "key": "plan_quality",
        "mode": "analysis",
        "kind": "score",
        "tool_keys": ["builtin_decision"],
        "summary": "Score one candidate plan against a shared ordered rubric.",
        "rubric": ["weak", "acceptable", "strong"],
    },
    {
        "key": "evidence_relevance",
        "mode": "analysis",
        "kind": "score",
        "tool_keys": ["builtin_decision"],
        "summary": "Score one retrieved excerpt by how much it helps answer the question.",
        "rubric": ["irrelevant", "related", "helpful", "direct_answer"],
    },
]
DEFINITION = SimpleNamespace(plugin_type="decision", decisions=DECISIONS)


def decision_catalog():
    """Return public host metadata for Assistant configuration."""

    return {
        "decisions": DECISIONS,
        "gate_phases": GATE_PHASES,
        "gate_active": {key: gate_is_active(phase) for key, phase in GATE_PHASES.items()},
        "active_phase": GATE_ACTIVE_PHASE,
    }


def validate_assistant_decisions(attrs, instance, capability):
    """Validate active shared models and the effective host decision settings."""

    model_ref = attrs.get("decision_model_ref", getattr(instance, "decision_model_ref", None))
    if (
        model_ref
        and "decision_model_ref" in attrs
        and not LLMConfig.objects.filter(
            uuid=model_ref,
            model_type="decision",
            scope="global",
            is_active=True,
        ).exists()
    ):
        raise serializers.ValidationError({"decision_model_ref": "Select an active global decision model."})
    if not model_ref:
        return
    for field, validator in (
        ("decision_gates", validate_decision_gates),
        ("decision_analyses", validate_decision_analyses),
    ):
        value = attrs.get(field, getattr(instance, field, {}))
        try:
            normalized = (
                validator(DEFINITION, value, capability) if field == "decision_gates" else validator(DEFINITION, value)
            )
        except PluginRegistryError as exc:
            raise serializers.ValidationError({field: str(exc)}) from exc
        if field in attrs or "decision_model_ref" in attrs:
            attrs[field] = normalized


def build_assistant_decision_model(assistant):
    """Freeze a model UUID and host policy without provider credentials."""

    if not assistant.decision_model_ref:
        return {}
    gates = (
        default_control_gates(DEFINITION, assistant.capability) if assistant.decision_auto else assistant.decision_gates
    )
    config = LLMConfig.objects.filter(uuid=assistant.decision_model_ref).values_list("config", flat=True).first() or {}
    timeout = min(max(int(config.get("request_timeout_seconds") or 3), 1), 30)
    return {
        "model_ref": str(assistant.decision_model_ref),
        "gates": {key: {**value, "timeout_seconds": timeout}
                  for key, value in resolve_decision_gates(DEFINITION, gates).items()},
        "analyses": {key: {**value, "timeout_seconds": timeout}
                     for key, value in resolve_decision_analyses(DEFINITION, assistant.decision_analyses).items()},
        "score": {"kind": "score", "timeout_seconds": timeout},
    }
