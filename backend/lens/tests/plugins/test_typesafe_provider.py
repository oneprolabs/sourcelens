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

        self.assertEqual(plugin.version, "1.4.0")
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

    def test_bundled_plugin_declares_control_decisions(self):
        plugin = installed_plugin("typesafe")

        self.assertEqual(plugin.plugin_type, "decision")
        decisions = {
            item["key"]: item
            for item in plugin.decisions
            if item["mode"] == "control"
        }
        self.assertEqual(
            set(decisions),
            {
                "search_needed",
                "evidence_requirement",
                "evidence_sufficient",
                "answer_supported",
            },
        )
        self.assertEqual(
            decisions["search_needed"]["applies_to"],
            ["knowledge_qa", "code_analysis"],
        )
        self.assertEqual(
            decisions["evidence_requirement"]["applies_to"],
            ["general_chat"],
        )
        self.assertEqual(
            decisions["evidence_sufficient"]["kind"],
            "noul",
        )
        self.assertEqual(
            decisions["evidence_sufficient"]["applies_to"],
            ["knowledge_qa", "code_analysis", "general_chat"],
        )
        self.assertEqual(
            decisions["answer_supported"]["kind"],
            "choice",
        )
        self.assertEqual(
            decisions["answer_supported"]["tool_keys"],
            ["typesafe_choice"],
        )

    def test_bundled_plugin_declares_a_rankable_analysis(self):
        plugin = installed_plugin("typesafe")

        analyses = {
            item["key"]: item
            for item in plugin.decisions
            if item["mode"] == "analysis"
        }
        self.assertIn("plan_quality", analyses)
        self.assertEqual(analyses["plan_quality"]["kind"], "score")
        self.assertEqual(
            analyses["plan_quality"]["rubric"],
            ["weak", "acceptable", "strong"],
        )
        self.assertEqual(
            analyses["plan_quality"]["tool_keys"],
            ["typesafe_score"],
        )

    def test_bundled_plugin_is_a_pure_decision_backend(self):
        plugin = installed_plugin("typesafe")

        self.assertEqual(plugin.assistant_guidance["topics"], [])
        self.assertTrue(
            all(tool.exposure == "internal" for tool in plugin.tools)
        )
        self.assertEqual(
            {tool.key for tool in plugin.tools},
            {"typesafe_noul", "typesafe_choice", "typesafe_score"},
        )


class TypesafeConnectionProviderTests(TestCase):
    """Verify TypeSafe endpoint and Connection scope validation."""

    def setUp(self):
        self.provider = get_datasource_provider("typesafe", "1.4.0")

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
            "https://api.typesafe.ai/v1?x=1",
            "https://api.typesafe.ai/../v1",
            "https://user@api.typesafe.ai",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    self.provider.validate_connection(endpoint, {})

    def test_accepts_a_base_path_for_gateways(self):
        for endpoint, expected in (
            (
                "https://ai-gateway.vercel.sh/typesafe",
                "https://ai-gateway.vercel.sh/typesafe",
            ),
            (
                "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
                "https://ai-gateway.vercel.sh/typesafe",
            ),
        ):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.provider.validate_connection(
                        endpoint,
                        {"model": "typesafe-ai/jev"},
                    ),
                    expected,
                )

    def test_http_origins_strip_the_gateway_base_path(self):
        self.assertEqual(
            self.provider.http_origins(
                "https://ai-gateway.vercel.sh/typesafe",
                {"model": "typesafe-ai/jev"},
            ),
            ("https://ai-gateway.vercel.sh",),
        )
        self.assertEqual(
            self.provider.http_origins(
                "https://api.typesafe.ai/v1/systemone",
                {"model": "jev-1.13.0"},
            ),
            ("https://api.typesafe.ai",),
        )

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

    def test_default_model_matches_the_manifest(self):
        plugin = installed_plugin("typesafe")
        manifest = json.loads(
            (plugin.path / "plugin.json").read_text(encoding="utf-8")
        )
        default = manifest["connection_schema"]["properties"]["model"]["default"]

        result = self.provider.validate_live_connection(
            "secret-key",
            endpoint="https://api.typesafe.ai",
            connection_config={},
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={"models": []},
                        request=request,
                    )
                )
            ),
        )

        self.assertEqual(result["model"], default)

    def test_live_validation_does_not_send_a_billable_evaluation(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "jev-latest",
                            "description": "General-purpose model",
                            "release_date": "2026-09-15",
                        }
                    ]
                },
                request=request,
            )

        result = self.provider.validate_live_connection(
            "secret-key",
            endpoint="https://api.typesafe.ai",
            connection_config={"model": "jev-1.13.0"},
            client=httpx.Client(
                transport=httpx.MockTransport(
                    handler
                )
            ),
        )

        self.assertEqual(
            result,
            {
                "status": "configured",
                "model": "jev-1.13.0",
                "available_models": ["jev-latest"],
            },
        )
        self.assertEqual(str(seen[0].url), "https://api.typesafe.ai/v1/models")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer secret-key")

    def test_live_validation_uses_gateway_base_path(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"models": []}, request=request)

        result = self.provider.validate_live_connection(
            "gateway-key",
            endpoint="https://ai-gateway.vercel.sh/typesafe",
            connection_config={"model": "typesafe-ai/jev"},
            client=httpx.Client(
                transport=httpx.MockTransport(handler)
            ),
        )

        self.assertEqual(result["status"], "configured")
        self.assertEqual(
            str(seen[0].url),
            "https://ai-gateway.vercel.sh/typesafe/v1/models",
        )

    def test_live_validation_rejects_invalid_models_response(self):
        with self.assertRaisesRegex(ValueError, "TYPESAFE_RESPONSE_INVALID"):
            self.provider.validate_live_connection(
                "secret-key",
                endpoint="https://api.typesafe.ai",
                connection_config={"model": "jev-1.13.0"},
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            json={"models": [{"name": ""}]},
                            request=request,
                        )
                    )
                ),
            )

    def test_live_validation_maps_authentication_failure(self):
        with self.assertRaisesRegex(ValueError, "TYPESAFE_ACCESS_DENIED"):
            self.provider.validate_live_connection(
                "secret-key",
                endpoint="https://api.typesafe.ai",
                connection_config={"model": "jev-1.13.0"},
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            401,
                            json={"error_type": "authentication_error"},
                            request=request,
                        )
                    )
                ),
            )


class TypesafeToolProviderTests(TestCase):
    """Verify typed question validation before LensNode execution."""

    def setUp(self):
        self.provider = get_tool_provider("typesafe", "1.4.0")

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
