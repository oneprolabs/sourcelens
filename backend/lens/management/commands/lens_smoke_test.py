import json

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

EXPECTED_MODELS = {
    "Assistant",
    "AssistantAccess",
    "AssistantSkill",
    "AssistantMCP",
    "DataSource",
    "DataSourceCredential",
    "EnvironmentVariableSet",
    "GlobalSetting",
    "MCPServer",
    "Message",
    "MessageAttachment",
    "LensNode",
    "Run",
    "RunDiagnostic",
    "RunDiagnosticEvidence",
    "RunDiagnosticTurn",
    "RunExecution",
    "RunOutputFile",
    "RunStep",
    "RunTraceEvent",
    "RunTraceExport",
    "ScheduledTask",
    "Session",
    "SharedQA",
    "SharedQAFile",
    "Skill",
}


class Command(BaseCommand):
    help = "Run read-only Lens smoke checks for app wiring and dependencies."

    def handle(self, *args, **options):
        checks = {
            "installed_app": "lens" in settings.INSTALLED_APPS,
            "model_set": self._check_model_set(),
            "api_routes": self._check_api_routes(),
            "asgi": self._check_asgi(),
            "channels": self._check_channels(),
            "langgraph": self._check_langgraph(),
        }
        failed = [name for name, ok in checks.items() if not ok]
        self.stdout.write(json.dumps(checks, ensure_ascii=False, indent=2))
        if failed:
            raise CommandError(f'Lens smoke check failed: {", ".join(failed)}')
        self.stdout.write(self.style.SUCCESS("Lens smoke check passed."))

    def _check_model_set(self):
        model_names = {
            model.__name__
            for model in apps.get_app_config("lens").get_models()
        }
        return model_names == EXPECTED_MODELS

    def _check_api_routes(self):
        try:
            reverse("lens-assistants-list")
            reverse("lens-runs-list")
            reverse("lens-admin-lensnodes-list")
            reverse("lens-lensnode-ai-gateway")
        except Exception:
            return False
        return True

    def _check_asgi(self):
        return (
            getattr(settings, "ASGI_APPLICATION", "")
            == "core.asgi.application"
        )

    def _check_channels(self):
        try:
            from channels.layers import get_channel_layer
            from channels.routing import ProtocolTypeRouter
        except Exception:
            return False
        return (
            ProtocolTypeRouter is not None and get_channel_layer() is not None
        )

    def _check_langgraph(self):
        try:
            from langchain_core.language_models.chat_models import (
                BaseChatModel,
            )
            from langgraph.graph import StateGraph
        except Exception:
            return False
        return BaseChatModel is not None and StateGraph is not None
