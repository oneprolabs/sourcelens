from django.test import TestCase
from lens.plugins.providers import get_datasource_provider
from lens.plugins.registry import installed_plugin
from lens.plugins.tool_providers import ToolProviderError, get_tool_provider

GITHUB_READ_ONLY_TOOLS = [
    "github_read_file",
    "github_search_code",
    "github_repository_get",
    "github_activity_summary",
    "github_branch_list",
    "github_commit_list",
    "github_commit_get",
    "github_issue_list",
    "github_issue_get",
    "github_issue_comments",
    "github_pull_request_list",
    "github_pull_request_get",
    "github_pull_request_files",
    "github_pull_request_reviews",
    "github_release_list",
    "github_workflow_run_list",
    "github_workflow_run_get",
]


class GitHubPluginManifestTests(TestCase):
    """Verify the bundled GitHub Plugin exposes bounded read-only tools."""

    def test_bundled_github_plugin_declares_read_only_business_tools(self):
        plugin = installed_plugin("github")

        self.assertEqual(plugin.version, "1.0.0")
        self.assertEqual(
            [tool.key for tool in plugin.tools],
            GITHUB_READ_ONLY_TOOLS,
        )
        self.assertTrue(
            all(tool.side_effect == "none" for tool in plugin.tools)
        )
        self.assertTrue(
            all(tool.capability == "repository.read" for tool in plugin.tools)
        )


class GitHubToolProviderTests(TestCase):
    """Verify every GitHub Tool remains within the Connection allowlist."""

    def setUp(self):
        self.provider = get_tool_provider("github", "1.0.0")
        self.scope = {"repositories": ["HyperBDR/sourcelens"]}

    def test_normalizes_paginated_list_request(self):
        endpoint, arguments = self.provider.validate_request(
            "https://github.com",
            self.scope,
            "github_issue_list",
            {
                "repository": "hyperbdr/sourcelens",
                "state": "open",
                "labels": "bug,security",
                "page": 2,
                "per_page": 25,
            },
        )

        self.assertEqual(endpoint, "https://github.com")
        self.assertEqual(
            arguments,
            {
                "repository": "hyperbdr/sourcelens",
                "state": "open",
                "labels": "bug,security",
                "page": 2,
                "per_page": 25,
            },
        )

    def test_every_manifest_tool_is_supported_by_control_plane(self):
        plugin = installed_plugin("github")
        arguments = {
            "github_read_file": {"path": "README.md"},
            "github_search_code": {"query": "PluginRuntime"},
            "github_activity_summary": {
                "repositories": ["HyperBDR/sourcelens"],
                "since": "2026-09-01T16:00:00Z",
                "until": "2026-09-02T15:59:59Z",
            },
            "github_commit_get": {"ref": "main"},
            "github_issue_get": {"number": 1},
            "github_issue_comments": {"number": 1},
            "github_pull_request_get": {"number": 1},
            "github_pull_request_files": {"number": 1},
            "github_pull_request_reviews": {"number": 1},
            "github_workflow_run_get": {"run_id": 1},
        }

        for tool in plugin.tools:
            with self.subTest(tool=tool.key):
                _endpoint, normalized = self.provider.validate_request(
                    "https://github.com",
                    self.scope,
                    tool.key,
                    {
                        "repository": "HyperBDR/sourcelens",
                        **arguments.get(tool.key, {}),
                    },
                )

                repositories = normalized.get("repositories") or [
                    normalized["repository"]
                ]
                self.assertEqual(repositories, ["HyperBDR/sourcelens"])

    def test_normalizes_numbered_resource_request(self):
        _endpoint, arguments = self.provider.validate_request(
            "https://github.com",
            self.scope,
            "github_pull_request_reviews",
            {
                "repository": "HyperBDR/sourcelens",
                "number": 488,
                "page": 1,
                "per_page": 50,
            },
        )

        self.assertEqual(arguments["number"], 488)
        self.assertEqual(arguments["per_page"], 50)

    def test_normalizes_commit_filters(self):
        _endpoint, arguments = self.provider.validate_request(
            "https://github.com",
            self.scope,
            "github_commit_list",
            {
                "repository": "HyperBDR/sourcelens",
                "ref": "main",
                "path": "backend/lens",
                "page": 1,
                "per_page": 20,
            },
        )

        self.assertEqual(arguments["ref"], "main")
        self.assertEqual(arguments["path"], "backend/lens")

    def test_normalizes_activity_summary_request(self):
        _endpoint, arguments = self.provider.validate_request(
            "https://github.com",
            self.scope,
            "github_activity_summary",
            {
                "repositories": ["hyperbdr/sourcelens"],
                "since": "2026-09-01T16:00:00Z",
                "until": "2026-09-02T15:59:59Z",
                "per_page": 10,
            },
        )

        self.assertEqual(arguments["repositories"], ["hyperbdr/sourcelens"])
        self.assertEqual(arguments["per_page"], 10)

    def test_rejects_activity_repository_outside_connection_scope(self):
        with self.assertRaisesMessage(ToolProviderError, "scope"):
            self.provider.validate_request(
                "https://github.com",
                self.scope,
                "github_activity_summary",
                {
                    "repositories": [
                        "HyperBDR/sourcelens",
                        "other/private",
                    ],
                    "since": "2026-09-01T16:00:00Z",
                    "until": "2026-09-02T15:59:59Z",
                },
            )

    def test_activity_summary_accepts_repository_selectors(self):
        """Activity selectors retain their meaning until runtime expansion."""

        for selectors in (["HyperBDR/*"], ["*"], ["HyperBDR/*", "HyperBDR/sourcelens"]):
            with self.subTest(selectors=selectors):
                _, arguments = self.provider.validate_request(
                    "https://github.com",
                    self.scope,
                    "github_activity_summary",
                    {
                        "repositories": selectors,
                        "since": "2026-09-01T16:00:00Z",
                        "until": "2026-09-02T15:59:59Z",
                    },
                )
                self.assertEqual(arguments["repositories"], selectors)

    def test_activity_summary_rejects_invalid_selectors(self):
        """Only exact repositories, owner wildcards and the all selector are accepted."""

        for selector in ("HyperBDR", "*/sourcelens", "Hyper*/*", "HyperBDR/**", "../*", "a/b/c"):
            with self.subTest(selector=selector), self.assertRaises(ToolProviderError):
                self.provider.validate_request(
                    "https://github.com",
                    {"repositories": ["*"]},
                    "github_activity_summary",
                    {"repositories": [selector], "since": "2026-09-01T16:00:00Z", "until": "2026-09-02T15:59:59Z"},
                )

    def test_rejects_repository_outside_connection_scope(self):
        with self.assertRaisesMessage(ToolProviderError, "scope"):
            self.provider.validate_request(
                "https://github.com",
                self.scope,
                "github_issue_get",
                {"repository": "other/private", "number": 1},
            )

    def test_rejects_invalid_state_and_pagination(self):
        invalid_requests = [
            {"state": "deleted", "page": 1, "per_page": 20},
            {"state": "open", "page": 0, "per_page": 20},
            {"state": "open", "page": 1, "per_page": 101},
        ]

        for arguments in invalid_requests:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolProviderError):
                    self.provider.validate_request(
                        "https://github.com",
                        self.scope,
                        "github_issue_list",
                        {
                            "repository": "HyperBDR/sourcelens",
                            **arguments,
                        },
                    )

    def test_rejects_unknown_tool_instead_of_forwarding_an_api_request(self):
        with self.assertRaisesMessage(ToolProviderError, "unsupported"):
            self.provider.validate_request(
                "https://github.com",
                self.scope,
                "github_api",
                {
                    "repository": "HyperBDR/sourcelens",
                    "path": "/user",
                },
            )


class GitHubDatasourceProviderTests(TestCase):
    """Verify GitHub datasource resource lists stay within scope."""

    def setUp(self):
        self.provider = get_datasource_provider("github", "1.0.0")

    def test_accepts_multiple_repositories(self):
        config = self.provider.validate_datasource_config(
            {"repositories": ["oneprolabs/a", "oneprolabs/b"]},
            {
                "repositories": ["oneprolabs/a", "oneprolabs/b"],
                "branch": "main",
            },
        )

        self.assertEqual(
            config,
            {
                "repositories": ["oneprolabs/a", "oneprolabs/b"],
                "branch": "main",
            },
        )
