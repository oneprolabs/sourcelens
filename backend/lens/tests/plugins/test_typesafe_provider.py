import json

import httpx
from django.test import TestCase

from lens.plugins.providers import get_datasource_provider
from lens.plugins.registry import installed_plugin
from lens.plugins.tool_providers import get_tool_provider


class TypesafePluginManifestTests(TestCase):
    """Verify the bundled TypeSafe Plugin manifest and tool contract."""

    def test_bundled_plugin_exposes_decision_tools(self):
        plugin = installed_plugin("typesafe")

        self.assertEqual(plugin.version, "1.0.0")
        self.assertEqual(plugin.display_name, "TypeSafe AI")
        self.assertIsNone(plugin.datasource_source_type)
        self.assertEqual(
            [tool.key for tool in plugin.tools],
            [
                "typesafe_noul",
                "typesafe_choice",
                "typesafe_score",
            ],
        )
        self.assertTrue(
            all(
                tool.capability == "decision.evaluate"
                for tool in plugin.tools
            )
        )


class TypesafeConnectionProviderTests(TestCase):
    """Verify TypeSafe endpoint and Connection scope validation."""

    def setUp(self):
        self.provider = get_datasource_provider("typesafe", "1.0.0")

    def test_accepts_a_manual_https_endpoint(self):
        self.assertEqual(
            self.provider.validate_connection(
                "https://api.typesafe.ai/",
                {"model": "jev-1.13.0"},
            ),
            "https://api.typesafe.ai",
        )
        self.assertEqual(
            self.provider.validate_connection(
                "https://gateway.example",
                {"model": "custom-model"},
            ),
            "https://gateway.example",
        )
        for endpoint in (
            "http://api.typesafe.ai",
            "https://api.typesafe.ai/v1",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    self.provider.validate_connection(endpoint, {})

    def test_accepts_manual_model_with_default_preset(self):
        self.assertEqual(
            self.provider.validate_connection_scope({}),
            {},
        )
        self.assertEqual(
            self.provider.validate_connection(
                "https://api.typesafe.ai",
                {"model": "jev-1.13.0"},
            ),
            "https://api.typesafe.ai",
        )
        self.assertEqual(
            self.provider.validate_connection(
                "https://api.typesafe.ai",
                {"model": "jev-latest"},
            ),
            "https://api.typesafe.ai",
        )

    def test_live_validation_does_not_send_a_billable_evaluation(self):
        result = self.provider.validate_live_connection(
            "secret-key",
            endpoint="https://api.typesafe.ai",
            connection_config={"model": "jev-1.13.0"},
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(500, request=request)
                )
            ),
        )

        self.assertEqual(result, {"status": "configured"})


class TypesafeToolProviderTests(TestCase):
    """Verify typed question validation before LensNode execution."""

    def setUp(self):
        self.provider = get_tool_provider("typesafe", "1.0.0")

    def test_normalizes_noul_request(self):
        endpoint, arguments = self.provider.validate_request(
            "https://api.typesafe.ai",
            {},
            "typesafe_noul",
            {
                "state": "A support request",
                "instructions": "Is this urgent?",
                "criteria_true": "Time-sensitive",
                "criteria_false": "No urgency",
            },
        )

        self.assertEqual(endpoint, "https://api.typesafe.ai")
        self.assertEqual(arguments["state"], "A support request")
        self.assertEqual(arguments["instructions"], "Is this urgent?")

    def test_rejects_invalid_choice_criteria_json(self):
        with self.assertRaises(ValueError):
            self.provider.validate_request(
                "https://api.typesafe.ai",
                {},
                "typesafe_choice",
                {
                    "state": "text",
                    "instructions": "Choose one",
                    "criteria": "[]",
                },
            )

    def test_normalizes_score_criteria_as_json(self):
        _endpoint, arguments = self.provider.validate_request(
            "https://api.typesafe.ai",
            {},
            "typesafe_score",
            {
                "state": "text",
                "instructions": "Rate urgency",
                "criteria": json.dumps(["low", "high"]),
            },
        )

        self.assertEqual(arguments["criteria"], '["low", "high"]')
