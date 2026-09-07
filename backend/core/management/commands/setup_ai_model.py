"""Configure and validate the first system AI model interactively."""

import sys
import termios
import tty

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from agentcore_metering.adapters.django.models import LLMConfig
from agentcore_metering.adapters.django.services.config_source import (
    get_default_llm_config_uuid,
    set_default_llm_config,
)
from agentcore_metering.adapters.django.services.litellm_params import (
    get_provider_params_schema,
)
from agentcore_metering.adapters.django.services.model_catalog import (
    get_model_type_for_model_id,
    get_providers_with_models,
)
from agentcore_metering.adapters.django.services.runtime_config import (
    VALIDATION_MESSAGE_IDS,
    get_validation_message,
    validate_llm_config,
)

FIELD_LABELS = {
    "api_base": "API endpoint",
    "api_version": "API version",
    "deployment": "Deployment name",
}


class Command(BaseCommand):
    """Run the first-install AI model setup wizard."""

    help = "Interactively configure and validate the system default AI model"
    requires_system_checks = []

    def add_arguments(self, parser):
        """Add a status check used by the one-command installer."""

        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit successfully only when an active system model exists.",
        )

    def handle(self, *args, **options):
        """Run model setup or report whether setup is complete."""

        if get_default_llm_config_uuid():
            if not options["check"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        "An active system AI model is already configured."
                    )
                )
            return

        if options["check"]:
            raise CommandError("No active system AI model is configured.")

        if not self._has_tty():
            raise CommandError(
                "Interactive model setup requires a terminal. Configure the "
                "model later in the SourceLens management console."
            )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("AI model setup"))
        self.stdout.write(
            "SourceLens needs an AI model before assistants can run."
        )
        if not self._confirm("Configure a model now?", default=True):
            self.stdout.write(
                self.style.WARNING(
                    "AI model setup skipped. You can configure it later in "
                    "the SourceLens management console."
                )
            )
            return

        try:
            self._configure_until_complete()
        except (EOFError, KeyboardInterrupt):
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "AI model setup skipped. The SourceLens installation is "
                    "still available."
                )
            )

    def _configure_until_complete(self):
        """Retry the wizard until setup succeeds or the user stops."""

        while True:
            provider = self._select_provider()
            config = self._collect_config(provider)
            provider_id = provider["id"]
            self.stdout.write("Testing the model connection...")
            ok, message = validate_llm_config(
                provider_id,
                config,
                user=None,
            )
            if ok:
                self._save_default(provider_id, config)
                self.stdout.write(
                    self.style.SUCCESS(
                        "Connection successful. The model was saved as the "
                        "system default."
                    )
                )
                return

            self.stdout.write(self.style.ERROR(self._friendly_error(message)))
            if not self._confirm("Try a different configuration?", True):
                self.stdout.write(
                    self.style.WARNING(
                        "AI model setup skipped. You can configure it later "
                        "in the SourceLens management console."
                    )
                )
                return

    def _select_provider(self):
        """Return a provider id selected from the shared model catalog."""

        schema = get_provider_params_schema().get("providers", {})
        providers = [
            provider
            for provider in get_providers_with_models().get("providers", [])
            if provider.get("id") in schema
        ]
        if not providers:
            raise CommandError("No AI model providers are available.")
        labels = [provider["label"] for provider in providers]
        selected = self._select_option("Model providers", labels)
        return providers[selected]

    def _collect_config(self, provider):
        """Collect only the model fields needed for first-time setup."""

        provider_id = provider["id"]
        schema = get_provider_params_schema()["providers"][provider_id]
        config = {"model": self._select_model(provider, schema)}
        required = set(schema.get("required") or [])
        default_endpoint = schema.get("default_api_base") or ""

        if "api_base" in required or default_endpoint:
            endpoint = self._required_or_default(
                FIELD_LABELS["api_base"],
                default_endpoint,
                required="api_base" in required,
            )
            if endpoint:
                config["api_base"] = endpoint

        if "deployment" in required:
            config["deployment"] = self._required_or_default(
                FIELD_LABELS["deployment"], "", required=True
            )
        if "api_version" in schema.get("editable_params", []):
            api_version = self._required_or_default(
                FIELD_LABELS["api_version"],
                "2024-02-15-preview",
                required=False,
            )
            if api_version:
                config["api_version"] = api_version

        while True:
            api_key = self._secret_input("API key: ").strip()
            if api_key:
                config["api_key"] = api_key
                break
            self.stdout.write(self.style.WARNING("API key is required."))
        return config

    def _select_model(self, provider, schema):
        """Select a chat model while still allowing custom model ids."""

        models = [
            model
            for model in provider.get("models", [])
            if model.get("mode", "chat") == "chat"
        ]
        default_model = schema.get("default_model") or ""
        if not models:
            return self._required_or_default(
                "Model", default_model, required=not bool(default_model)
            )

        default_index = None
        labels = []
        for index, model in enumerate(models):
            marker = " (default)" if model["id"] == default_model else ""
            labels.append(f"{model['label']} [{model['id']}]{marker}")
            if marker:
                default_index = index
        labels.append("Custom model")
        selected = self._select_option(
            f"Models from {provider['label']}",
            labels,
            selected=(
                default_index if default_index is not None else len(models)
            ),
        )
        if selected == len(models):
            return self._required_or_default("Model", "", required=True)
        return models[selected]["id"]

    def _select_option(self, title, options, selected=0):
        """Select one option using arrow keys and Enter."""

        self.stdout.write("")
        self.stdout.write(f"{title} (use ↑/↓ and press Enter):")
        self._render_options(options, selected)
        while True:
            key = self._read_key()
            if key == "up":
                selected = (selected - 1) % len(options)
            elif key == "down":
                selected = (selected + 1) % len(options)
            elif key == "enter":
                return selected
            else:
                continue
            self.stdout.write(
                f"\033[{len(options)}A",
                ending="",
            )
            self._render_options(options, selected)

    def _render_options(self, options, selected):
        """Render a terminal selection menu at the current cursor."""

        for index, option in enumerate(options):
            marker = ">" if index == selected else " "
            self.stdout.write(
                f"\r\033[2K  {marker} {option}",
            )
        self.stdout.flush()

    @staticmethod
    def _read_key():
        """Read one navigation key without requiring Enter."""

        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            char = sys.stdin.read(1)
            if char == "\x03":
                raise KeyboardInterrupt
            if char in {"\r", "\n"}:
                return "enter"
            if char == "\x1b":
                sequence = sys.stdin.read(2)
                if sequence == "[A":
                    return "up"
                if sequence == "[B":
                    return "down"
            return ""
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    def _required_or_default(self, label, default, required):
        """Prompt for a visible value with optional default handling."""

        prompt = f"{label}"
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        while True:
            value = self._input(prompt).strip()
            if value:
                return value
            if default:
                return default
            if not required:
                return ""
            self.stdout.write(self.style.WARNING(f"{label} is required."))

    def _save_default(self, provider, config):
        """Persist the validated model as the active global default."""

        model_type = get_model_type_for_model_id(
            provider,
            config.get("model", ""),
        )
        if model_type != LLMConfig.MODEL_TYPE_LLM:
            raise CommandError("The selected model is not a chat model.")
        with transaction.atomic():
            model_config = LLMConfig.objects.create(
                scope=LLMConfig.Scope.GLOBAL,
                user=None,
                model_type=LLMConfig.MODEL_TYPE_LLM,
                provider=provider,
                config=config,
                is_active=True,
            )
            set_default_llm_config(model_config)

    @staticmethod
    def _friendly_error(message):
        """Return only sanitized, user-actionable validation text."""

        if message in VALIDATION_MESSAGE_IDS:
            detail = get_validation_message(message, language="en")
        else:
            detail = get_validation_message("unknown", language="en")
        return f"Connection failed: {detail}"

    def _confirm(self, prompt, default):
        """Read a yes/no answer with an explicit default."""

        suffix = " [Y/n]: " if default else " [y/N]: "
        while True:
            answer = self._input(prompt + suffix).strip().lower()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.stdout.write(self.style.WARNING("Enter yes or no."))

    @staticmethod
    def _input(prompt):
        """Read a non-secret answer from the attached terminal."""

        return input(prompt)

    @staticmethod
    def _secret_input(prompt):
        """Read a secret while displaying one mask per character."""

        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
        secret = []
        sys.stdout.write(prompt)
        sys.stdout.flush()
        try:
            tty.setraw(fd)
            while True:
                char = sys.stdin.read(1)
                if char in {"\r", "\n"}:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return "".join(secret)
                if char == "\x03":
                    raise KeyboardInterrupt
                if char == "\x04":
                    raise EOFError
                if char in {"\x7f", "\b"}:
                    if secret:
                        secret.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue
                if char.isprintable():
                    secret.append(char)
                    sys.stdout.write("*")
                    sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    @staticmethod
    def _has_tty():
        """Refuse secret entry when input cannot be hidden."""

        return sys.stdin.isatty() and sys.stdout.isatty()
