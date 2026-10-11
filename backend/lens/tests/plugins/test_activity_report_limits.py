"""Control-plane snapshots accept the runtime's bounded pagination budget."""

from django.test import TestCase

from lens.plugins.tool_providers import ToolProviderError, get_tool_provider


class ActivityReportLimitTests(TestCase):
    """Keep provider authorization and runtime result budgets in agreement."""

    def test_activity_defaults_and_explicit_limits_match_runtime_contracts(self):
        for key, endpoint, project in [
            ("jira", "https://company.atlassian.net", "SL"),
            ("gitlab", "https://gitlab.example.com", "team/repo"),
        ]:
            provider = get_tool_provider(key, "1.0.0")
            base = {
                "projects": [project],
                "since": "2026-10-07T16:00:00Z",
                "until": "2026-10-08T16:00:00Z",
            }
            with self.subTest(provider=key):
                _, arguments = provider.validate_request(
                    endpoint, {"projects": [project]}, f"{key}_activity_summary", base
                )
                self.assertEqual(arguments["max_results"], 500)
                _, arguments = provider.validate_request(
                    endpoint,
                    {"projects": [project]},
                    f"{key}_activity_summary",
                    {**base, "max_results": 1000},
                )
                self.assertEqual(arguments["max_results"], 1000)
                with self.assertRaises(ToolProviderError):
                    provider.validate_request(
                        endpoint,
                        {"projects": [project]},
                        f"{key}_activity_summary",
                        {**base, "max_results": 1001},
                    )
