from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from agentcore_metering.adapters.django.models import LLMConfig
from lens.llm_resilience import (
    MODEL_UNAVAILABLE,
    fallback_model_refs,
    is_transient_provider_error,
    transient_error_code,
    call_and_track_with_fallback,
)


User = get_user_model()


class LLMResilienceTests(TestCase):
    def test_stream_fallback_happens_before_first_chunk(self):
        class ProviderBusyError(Exception):
            status_code = 503

        calls = []

        def tracked_call(**kwargs):
            calls.append(kwargs["model_uuid"])
            if len(calls) == 1:
                def failed_stream():
                    raise ProviderBusyError("busy")
                    yield None

                return failed_stream()

            def successful_stream():
                yield "content", "ok"
                return {"total_tokens": 1}

            return successful_stream()

        with (
            patch(
                "agentcore_metering.adapters.django.LLMTracker.call_and_track",
                side_effect=tracked_call,
            ),
            patch(
                "lens.llm_resilience.fallback_model_refs",
                return_value=["fallback"],
            ),
        ):
            generator = call_and_track_with_fallback(
                messages=[{"role": "user", "content": "hello"}],
                model_uuid="primary",
                state={},
                stream=True,
            )
            self.assertEqual(next(generator), ("content", "ok"))

        self.assertEqual(calls, ["primary", "fallback"])

    def test_stream_failure_after_output_is_not_replayed(self):
        class ProviderBusyError(Exception):
            status_code = 503

        def tracked_call(**_kwargs):
            def failed_stream():
                yield "content", "partial"
                raise ProviderBusyError("busy")

            return failed_stream()

        with (
            patch(
                "agentcore_metering.adapters.django.LLMTracker.call_and_track",
                side_effect=tracked_call,
            ),
            patch(
                "lens.llm_resilience.fallback_model_refs",
                return_value=["fallback"],
            ),
        ):
            generator = call_and_track_with_fallback(
                messages=[{"role": "user", "content": "hello"}],
                model_uuid="primary",
                state={},
                stream=True,
            )
            self.assertEqual(next(generator), ("content", "partial"))
            with self.assertRaises(ProviderBusyError):
                next(generator)

    def test_provider_5xx_and_rate_limit_are_transient(self):
        for status_code in (429, 502, 503):
            error = Mock(status_code=status_code)
            self.assertTrue(is_transient_provider_error(error))
            self.assertEqual(transient_error_code(error), MODEL_UNAVAILABLE)

    def test_authentication_error_is_not_transient(self):
        error = type("AuthenticationError", (Exception,), {})(
            "401 invalid api key"
        )

        self.assertFalse(is_transient_provider_error(error))
        self.assertEqual(transient_error_code(error), "")

    def test_fallback_configs_use_only_explicit_active_models(self):
        primary = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL,
            model_type=LLMConfig.MODEL_TYPE_LLM,
            provider="deepseek",
            config={"model": "deepseek-v4", "fallback_model_uuid": ""},
            is_active=True,
            is_default=True,
        )
        explicit = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL,
            model_type=LLMConfig.MODEL_TYPE_LLM,
            provider="qwen",
            config={"model": "qwen-plus"},
            is_active=True,
        )
        primary.config["fallback_model_uuid"] = str(explicit.uuid)
        primary.save(update_fields=["config"])
        safety_net = LLMConfig.objects.create(
            scope=LLMConfig.Scope.GLOBAL,
            model_type=LLMConfig.MODEL_TYPE_LLM,
            provider="openai",
            config={"model": "gpt-4o-mini"},
            is_active=True,
        )

        self.assertEqual(
            fallback_model_refs(primary.uuid), [str(explicit.uuid)]
        )
        safety_net.is_active = False
        safety_net.save(update_fields=["is_active"])
        self.assertEqual(
            fallback_model_refs(primary.uuid), [str(explicit.uuid)]
        )
