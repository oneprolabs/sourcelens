"""Keep large TypeSafe reviews intact while preserving decision usage metering."""

import json

from agentcore_metering.adapters.django.services.decisions import (
    DecisionTracker,
    _validate_answers,
    decision_params,
    validate_questions,
)
from agentcore_metering.adapters.django.trackers.llm import LLMTracker
from agentcore_metering.adapters.django.trackers.llm_usage import get_cost_from_response, usage_dict_from_usage_obj
from django.utils import timezone

TYPESAFE_SHORT_CONTEXT_BYTES = 30000


def call_runtime_decision(*, provider, config, input, questions, state, config_uuid):
    """Use extended context only when the UTF-8 upper bound approaches 32k tokens.

    The SDK's provider tokenizer is not exposed here. UTF-8 byte length is a
    conservative bound, with room reserved for its request template. Small
    decisions keep agentcore's existing path; large reviews keep all evidence.
    """

    size = len(input.encode("utf-8")) + len(json.dumps(questions, ensure_ascii=False).encode("utf-8"))
    if provider != "typesafe" or size <= TYPESAFE_SHORT_CONTEXT_BYTES:
        return DecisionTracker.call_with_config(
            provider=provider,
            config=config,
            input=input,
            questions=questions,
            state=state,
            config_uuid=config_uuid,
        )
    validate_questions(questions)
    params = decision_params(provider, config)
    params.pop("decision_provider")
    params["num_retries"] = 0
    metadata = {
        **(state.get("metadata") or {}),
        "model_type": "decision",
        "endpoint": "decisions",
        "question_count": len(questions),
        "config_uuid": config_uuid,
        "long_context": True,
    }
    state = {**state, "metadata": metadata}
    started_at = timezone.now()
    usage = None
    response_model = None
    try:
        import litellm

        response = litellm.decisions(
            **params,
            input=input,
            questions=questions,
            extra_headers={"TypeSafe-Danger-Zone-Long-Context": "true"},
        )
        payload = response.model_dump(mode="json")
        response_model = payload.get("model")
        raw_usage = payload.get("usage") or {}
        # Normalize native Decisions fields for older agentcore 0.4 installations.
        normalized_usage = {
            **raw_usage,
            "prompt_tokens": raw_usage.get("prompt_tokens", raw_usage.get("input_tokens", 0)),
            "completion_tokens": raw_usage.get("completion_tokens", raw_usage.get("output_tokens", 0)),
            "prompt_tokens_details": raw_usage.get("prompt_tokens_details", raw_usage.get("input_tokens_details")),
            "completion_tokens_details": raw_usage.get(
                "completion_tokens_details", raw_usage.get("output_tokens_details")
            ),
        }
        usage = usage_dict_from_usage_obj(normalized_usage, params["model"])
        cost = get_cost_from_response(response)
        usage["cost"] = float(cost) if cost is not None else None
        _validate_answers(payload, questions)
    except Exception as exc:
        LLMTracker._save_usage_to_db(
            state=state,
            **(usage or {"model": params["model"]}),
            success=False,
            error=f"Decision request failed ({type(exc).__name__}).",
            started_at=started_at,
            response_model=response_model,
        )
        raise
    LLMTracker._save_usage_to_db(state=state, **usage, started_at=started_at, response_model=response_model)
    return payload["answers"], usage
