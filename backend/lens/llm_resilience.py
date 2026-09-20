"""Metered model failover before any response output is emitted."""

import logging
from uuid import UUID, uuid4

from django.db.models import Q

from agentcore_metering.adapters.django.services.litellm_retry import (
    is_retryable_exception,
)

LOGGER = logging.getLogger(__name__)
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
MAX_FALLBACKS = 3


def is_transient_provider_error(error):
    """Use the metering package's structured transient error policy."""

    return is_retryable_exception(error)


def transient_error_code(error):
    """Return the public code when an exception is provider-transient."""

    return MODEL_UNAVAILABLE if is_transient_provider_error(error) else ""


def fallback_model_refs(model_ref, user_id=None, messages=()):
    """Resolve explicitly configured, authorized and compatible fallbacks.

    The primary config owns an ordered ``fallback_model_uuids`` list.
    No other configs are selected implicitly and fallback lists do not recurse.
    """

    from agentcore_metering.adapters.django.models import LLMConfig

    if not model_ref:
        return []
    try:
        primary_uuid = UUID(str(model_ref))
    except (TypeError, ValueError):
        return []
    allowed_scope = Q(scope=LLMConfig.Scope.GLOBAL, user__isnull=True)
    if user_id is not None:
        allowed_scope |= Q(scope=LLMConfig.Scope.USER, user_id=user_id)
    eligible = LLMConfig.objects.filter(
        allowed_scope, model_type=LLMConfig.MODEL_TYPE_LLM, is_active=True
    )
    primary = eligible.filter(uuid=primary_uuid).first()
    if primary is None or not isinstance(primary.config, dict):
        return []
    configured = primary.config.get("fallback_model_uuids")
    if configured is None:
        configured = primary.config.get("fallback_model_uuid", [])
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, list):
        return []
    candidates = []
    for value in configured[:MAX_FALLBACKS]:
        try:
            candidate = UUID(str(value))
        except (TypeError, ValueError):
            continue
        if candidate != primary_uuid and candidate not in candidates:
            candidates.append(candidate)
    available = set(
        eligible.filter(uuid__in=candidates).values_list("uuid", flat=True)
    )
    requires_vision = any(
        isinstance(message.get("content"), list)
        and any(
            isinstance(block, dict) and block.get("type") == "image_url"
            for block in message["content"]
        )
        for message in messages
        if isinstance(message, dict)
    )
    if requires_vision:
        from .vision_capabilities import resolve_model_capability

        available = {
            candidate for candidate in available
            if resolve_model_capability(candidate)["supports_vision"]
        }
    return [str(value) for value in candidates if value in available]


def call_and_track_with_fallback(*, on_retry=None, **kwargs):
    """Keep per-provider retries and all metering in LLMTracker.

    Fallback starts only after the selected provider exhausts its own retry
    budget. A yielded content, reasoning or tool-call delta forbids replay.
    """

    attempts = _tracked_attempts(on_retry=on_retry, **kwargs)
    if kwargs.get("stream"):
        return attempts
    try:
        next(attempts)
    except StopIteration as done:
        return done.value
    raise RuntimeError("Non-streaming model call unexpectedly yielded output")


def _tracked_attempts(*, on_retry=None, **kwargs):
    """Try the primary and its bounded fallback list once each."""

    from agentcore_metering.adapters.django import LLMTracker

    primary = kwargs.get("model_uuid")
    state = kwargs.get("state") or {}
    call_id = uuid4().hex
    candidates = None
    selected = primary
    attempt = 0
    while True:
        emitted = False
        attempt_state = {
            **state,
            "metadata": {
                **(state.get("metadata") or {}),
                "model_call_id": call_id,
                "primary_model_ref": str(primary or ""),
                "selected_model_ref": str(selected or ""),
                "fallback_attempt": attempt,
            },
        }
        try:
            result = LLMTracker.call_and_track(
                **{**kwargs, "model_uuid": selected, "state": attempt_state}
            )
            if not kwargs.get("stream"):
                return result
            try:
                while True:
                    chunk = next(result)
                    emitted = True
                    yield chunk
            except StopIteration as done:
                return done.value
            finally:
                result.close()
        except Exception as error:
            if emitted or not is_transient_provider_error(error):
                raise
            if candidates is None:
                candidates = iter(fallback_model_refs(
                    primary, state.get("user_id"), kwargs.get("messages", ())
                ))
            selected = next(candidates, None)
            if selected is None:
                raise
            attempt += 1
            LOGGER.warning(
                "Model failover call=%s primary=%s selected=%s attempt=%d "
                "error_type=%s",
                call_id, primary, selected, attempt, type(error).__name__,
            )
            if on_retry is not None:
                on_retry({
                    "code": MODEL_UNAVAILABLE,
                    "attempt": attempt,
                })
