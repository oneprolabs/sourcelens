from dataclasses import dataclass
from functools import lru_cache

from .llm_resilience import call_and_track_with_fallback


VISION_SUPPORTED = "supported"
VISION_UNSUPPORTED = "unsupported"
VISION_UNKNOWN = "unknown"


@dataclass(frozen=True)
class LensLLMResult:
    """LLM completion result plus metering usage."""

    content: str
    usage: dict
    metered: bool


@lru_cache(maxsize=1)
def _metered_chat_model_class():
    """Build the LangChain adapter only when an LLM call is needed."""

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class LensMeteredChatModel(BaseChatModel):
        """LangChain adapter that delegates calls to agentcore metering."""

        model_ref: str | None = None
        node_name: str
        user_id: int | None = None

        @property
        def _llm_type(self):
            """Return LangChain model type identifier."""

            return "lens_metered_chat_model"

        @property
        def _identifying_params(self):
            """Return identifying params for LangChain tracing."""

            return {
                "model_ref": self.model_ref,
                "node_name": self.node_name,
            }

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            """Generate one chat response through LLMTracker."""

            del stop, run_manager, kwargs

            content, usage = call_and_track_with_fallback(
                messages=[_message_to_dict(message) for message in messages],
                model_uuid=self.model_ref,
                node_name=self.node_name,
                state={
                    "user_id": self.user_id,
                    "source_type": "lens",
                    "node_name": self.node_name,
                },
            )
            message = AIMessage(
                content=content.strip(),
                response_metadata={"usage": usage},
            )
            return ChatResult(
                generations=[ChatGeneration(message=message)],
                llm_output={"usage": usage},
            )

    return LensMeteredChatModel


def _messages(system, user):
    """Build LangChain chat messages."""

    from langchain_core.messages import HumanMessage, SystemMessage

    return [
        SystemMessage(content=system),
        HumanMessage(content=user),
    ]


def _multimodal_messages(system, user_text, image_data_urls):
    """Build LangChain messages with text plus image content blocks."""

    from langchain_core.messages import HumanMessage, SystemMessage

    content = [{"type": "text", "text": user_text}]
    for url in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return [
        SystemMessage(content=system),
        HumanMessage(content=content),
    ]


def model_supports_vision(model_ref):
    """Return the configured model's known image capability."""

    from agentcore_metering.adapters.django.models import LLMConfig
    from agentcore_metering.adapters.django.services.runtime_config import (
        get_litellm_params,
    )
    from litellm.utils import supports_vision

    params = get_litellm_params(model_uuid=str(model_ref))
    config = LLMConfig.objects.filter(uuid=model_ref).values(
        "provider", "config"
    ).first() or {}
    config_data = config.get("config") or {}
    model = str(params.get("model") or "")
    if not model:
        return VISION_UNSUPPORTED

    explicit = params.get("supports_vision")
    if explicit is None:
        explicit = params.get("vision")
    if explicit is None:
        explicit = config_data.get("supports_vision")
    if explicit is None:
        explicit = config_data.get("vision")
    if isinstance(explicit, bool):
        return VISION_SUPPORTED if explicit else VISION_UNSUPPORTED

    if supports_vision(model=model):
        return VISION_SUPPORTED

    provider = str(
        params.get("custom_llm_provider")
        or params.get("provider")
        or config.get("provider")
        or config_data.get("provider")
        or ""
    ).lower()
    if provider in {"openai_compatible", "openai-compatible"}:
        return VISION_UNKNOWN
    return VISION_UNSUPPORTED


def _message_to_dict(message):
    """Convert a LangChain message to the metering tracker shape."""

    role_by_type = {
        "system": "system",
        "human": "user",
        "ai": "assistant",
    }
    return {
        "role": role_by_type.get(message.type, message.type),
        "content": message.content,
    }


def _call_metered_model(*, model_ref, messages, node_name, user_id):
    """Call agentcore metering through the LangChain adapter."""

    chat_model_class = _metered_chat_model_class()
    model = chat_model_class(
        model_ref=str(model_ref) if model_ref else None,
        node_name=node_name,
        user_id=user_id,
    )
    message = model.invoke(messages)
    usage = message.response_metadata.get("usage", {})
    return LensLLMResult(
        content=str(message.content).strip(),
        usage=usage,
        metered=True,
    )


def run_completion(*, model_ref, system, user, node_name, user_id=None):
    """Run one system+user completion through metering.

    Public entry point for backend LLM calls (e.g. query rewrite).
    """

    return _call_metered_model(
        model_ref=model_ref,
        messages=_messages(system, user),
        node_name=node_name,
        user_id=user_id,
    )


def run_completion_multimodal(
    *,
    model_ref,
    system,
    user_text,
    image_data_urls,
    node_name,
    user_id=None,
):
    """Run one multimodal (text + images) completion through metering.

    The user message carries OpenAI-style content blocks; the metering
    tracker forwards them unchanged to litellm, which passes the images
    to a vision-capable model.
    """

    return _call_metered_model(
        model_ref=model_ref,
        messages=_multimodal_messages(system, user_text, image_data_urls),
        node_name=node_name,
        user_id=user_id,
    )
