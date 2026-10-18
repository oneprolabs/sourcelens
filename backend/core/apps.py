import importlib
import logging
import os

from django.apps import AppConfig

logger = logging.getLogger(__name__)

LANGFUSE_CALLBACK = "langfuse_otel"


def configure_langfuse() -> bool:
    """Enable LiteLLM Langfuse tracing when both credentials are set."""

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not public_key and not secret_key:
        return False
    if not public_key or not secret_key:
        logger.warning(
            "Langfuse observability is disabled because its configuration "
            "is incomplete; set both LANGFUSE_PUBLIC_KEY and "
            "LANGFUSE_SECRET_KEY."
        )
        return False

    import litellm

    callbacks = list(getattr(litellm, "callbacks", None) or [])
    if LANGFUSE_CALLBACK not in callbacks:
        callbacks.append(LANGFUSE_CALLBACK)
        litellm.callbacks = callbacks
    logger.info("Langfuse observability is enabled.")
    return True


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self) -> None:
        """Finish cyclic provider imports before serving concurrent requests."""

        importlib.import_module("litellm")
        configure_langfuse()
