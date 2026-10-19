"""Long review requests retain their evidence, timeout, and usage accounting."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from lens.runtime_decisions import call_runtime_decision


class RuntimeDecisionTests(SimpleTestCase):
    """Exercise the TypeSafe SDK seam without real provider calls or credentials."""

    def setUp(self):
        self.kwargs = {
            "provider": "typesafe",
            "config": {"model": "jev-latest", "api_key": "test", "request_timeout_seconds": 10},
            "input": "证据" * 6000,
            "questions": [{"type": "predicate", "name": "supported", "instructions": "Is it supported?"}],
            "state": {"metadata": {"run_uuid": "run", "call_id": "call"}},
            "config_uuid": "config",
        }
        self.payload = {
            "model": "typesafe/jev-latest",
            "answers": [{"type": "predicate", "name": "supported", "probability": 0.9}],
            "usage": {
                "input_tokens": 18000,
                "output_tokens": 10,
                "total_tokens": 18010,
                "input_tokens_details": {"cached_tokens": 1000},
                "output_tokens_details": {"reasoning_tokens": 5},
            },
        }

    def test_small_typesafe_and_other_providers_keep_shared_tracker(self):
        for override in ({"input": "Small evidence"}, {"provider": "openai_compatible"}):
            with (
                self.subTest(override=override),
                patch(
                    "lens.runtime_decisions.DecisionTracker.call_with_config",
                    return_value=([], {}),
                ) as tracker,
            ):
                self.assertEqual(call_runtime_decision(**{**self.kwargs, **override}), ([], {}))
                tracker.assert_called_once_with(**{**self.kwargs, **override})

    def test_large_review_uses_header_without_truncation_and_tracks_usage(self):
        response = SimpleNamespace(model_dump=lambda **_: self.payload)
        with (
            patch("litellm.decisions", return_value=response) as call,
            patch("lens.runtime_decisions.LLMTracker._save_usage_to_db") as save,
            patch("lens.runtime_decisions.get_cost_from_response", return_value=None),
        ):
            answers, usage = call_runtime_decision(**self.kwargs)
        self.assertEqual(answers, self.payload["answers"])
        self.assertEqual(usage["total_tokens"], 18010)
        self.assertEqual(usage["prompt_tokens"], 18000)
        self.assertEqual(usage["completion_tokens"], 10)
        self.assertEqual(usage["cached_tokens"], 1000)
        self.assertEqual(usage["reasoning_tokens"], 5)
        self.assertEqual(call.call_args.kwargs["input"], self.kwargs["input"])
        self.assertEqual(call.call_args.kwargs["timeout"], 10)
        self.assertEqual(call.call_args.kwargs["num_retries"], 0)
        self.assertEqual(call.call_args.kwargs["extra_headers"], {"TypeSafe-Danger-Zone-Long-Context": "true"})
        save.assert_called_once()
        metadata = save.call_args.kwargs["state"]["metadata"]
        self.assertEqual(metadata["run_uuid"], "run")
        self.assertTrue(metadata["long_context"])
        self.assertNotIn("input", metadata)

    def test_invalid_and_failed_responses_are_metered_and_propagated(self):
        for payload, error in (({**self.payload, "answers": []}, ValueError), (None, RuntimeError)):
            with (
                self.subTest(payload=payload),
                patch(
                    "litellm.decisions",
                    side_effect=RuntimeError("provider rejected") if payload is None else None,
                    return_value=SimpleNamespace(model_dump=lambda **_: payload),
                ) as call,
                patch("lens.runtime_decisions.LLMTracker._save_usage_to_db") as save,
                patch(
                    "lens.runtime_decisions.get_cost_from_response",
                    return_value=None,
                ),
            ):
                with self.assertRaises(error):
                    call_runtime_decision(**self.kwargs)
                call.assert_called_once()
                save.assert_called_once()
                self.assertFalse(save.call_args.kwargs["success"])
                self.assertNotIn("provider rejected", save.call_args.kwargs["error"])
