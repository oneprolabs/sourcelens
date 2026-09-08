"""Tests for the interactive AI model setup command."""

import sys
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from agentcore_metering.adapters.django.models import LLMConfig
from core.management.commands.setup_ai_model import Command


class FakeTerminalInput:
    """Provide character-by-character terminal input for mask tests."""

    def __init__(self, value):
        self._characters = iter(value)

    @staticmethod
    def fileno():
        """Return a harmless file descriptor for mocked terminal calls."""

        return 0

    def read(self, size):
        """Return the next simulated terminal character."""

        del size
        return next(self._characters)


@pytest.mark.django_db
def test_check_fails_without_active_global_model():
    """The installer can detect when model setup is still required."""

    with pytest.raises(CommandError):
        call_command(Command(), "--check", stdout=StringIO())


@pytest.mark.django_db
def test_existing_active_global_model_skips_prompts(monkeypatch):
    """Rerunning the installer does not replace an existing model."""

    LLMConfig.objects.create(
        scope=LLMConfig.Scope.GLOBAL,
        model_type=LLMConfig.MODEL_TYPE_LLM,
        provider="openai",
        config={"api_key": "existing-key", "model": "gpt-4o-mini"},
        is_active=True,
        is_default=True,
    )
    monkeypatch.setattr(
        Command,
        "_input",
        lambda self, prompt: pytest.fail(f"Unexpected prompt: {prompt}"),
    )
    output = StringIO()

    call_command(Command(), stdout=output)
    call_command(Command(), "--check", stdout=StringIO())

    assert "already configured" in output.getvalue()


@pytest.mark.django_db
def test_interactive_setup_tests_and_saves_default_model(monkeypatch):
    """A successful connection creates one active system default model."""

    answers = iter(["", ""])
    secret = "test-api-key"
    captured = {}

    monkeypatch.setattr(Command, "_has_tty", lambda self: True)
    monkeypatch.setattr(
        Command,
        "_input",
        lambda self, prompt: next(answers),
    )
    monkeypatch.setattr(
        Command,
        "_select_option",
        lambda self, title, options, selected=0: selected,
    )
    monkeypatch.setattr(
        Command,
        "_secret_input",
        lambda self, prompt: secret,
    )

    def fake_validate(provider, config, user=None):
        captured.update(provider=provider, config=config, user=user)
        return True, ""

    monkeypatch.setattr(
        "core.management.commands.setup_ai_model.validate_llm_config",
        fake_validate,
    )
    output = StringIO()

    call_command(Command(), stdout=output)

    config = LLMConfig.objects.get()
    assert config.scope == LLMConfig.Scope.GLOBAL
    assert config.model_type == LLMConfig.MODEL_TYPE_LLM
    assert config.is_active is True
    assert config.is_default is True
    assert config.config["api_key"] == secret
    assert captured["provider"] == config.provider
    assert captured["config"] == config.config
    assert captured["user"] is None
    assert secret not in output.getvalue()
    assert "saved as the system default" in output.getvalue()


@pytest.mark.django_db
def test_interactive_setup_can_be_skipped(monkeypatch):
    """Skipping model setup leaves the working installation unchanged."""

    monkeypatch.setattr(Command, "_has_tty", lambda self: True)
    monkeypatch.setattr(Command, "_input", lambda self, prompt: "no")
    output = StringIO()

    call_command(Command(), stdout=output)

    assert not LLMConfig.objects.exists()
    assert "skipped" in output.getvalue().lower()


def test_option_menu_uses_arrow_keys(monkeypatch):
    """Arrow keys move the active option before Enter confirms it."""

    keys = iter(["down", "down", "up", "enter"])
    monkeypatch.setattr(Command, "_read_key", lambda self: next(keys))
    output = StringIO()
    command = Command(stdout=output)

    selected = command._select_option(
        "Providers",
        ["OpenAI", "Anthropic", "DeepSeek"],
    )

    assert selected == 1
    assert "use ↑/↓ and press Enter" in output.getvalue()
    assert "> Anthropic" in output.getvalue()


def test_secret_input_displays_masks_and_supports_backspace(monkeypatch):
    """Typed and pasted secret characters are represented only by masks."""

    terminal_input = FakeTerminalInput("abc\x7fd\r")
    terminal_output = StringIO()
    restored = []
    monkeypatch.setattr(sys, "stdin", terminal_input)
    monkeypatch.setattr(sys, "stdout", terminal_output)
    monkeypatch.setattr(
        "core.management.commands.setup_ai_model.termios.tcgetattr",
        lambda fd: [fd],
    )
    monkeypatch.setattr(
        "core.management.commands.setup_ai_model.termios.tcsetattr",
        lambda fd, when, value: restored.append((fd, when, value)),
    )
    monkeypatch.setattr(
        "core.management.commands.setup_ai_model.tty.setraw",
        lambda fd: None,
    )

    secret = Command._secret_input("API key: ")

    assert secret == "abd"
    assert "abc" not in terminal_output.getvalue()
    assert terminal_output.getvalue().count("*") == 4
    assert restored
