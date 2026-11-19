from django.apps import AppConfig


class LensConfig(AppConfig):
    """Lens application configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "lens"

    def ready(self):
        """Register model signal handlers."""

        from django.db.models.signals import post_save

        from . import signals  # noqa: F401
        from .notification_scope import own_notification_record
        from .model_scope import own_model_usage

        post_save.connect(
            own_model_usage,
            sender="agentcore_metering.LLMUsage",
            dispatch_uid="lens.model_usage_scope",
        )

        post_save.connect(
            own_notification_record,
            sender="agentcore_notifier.NotificationRecord",
            dispatch_uid="lens.notification_record_scope",
        )
