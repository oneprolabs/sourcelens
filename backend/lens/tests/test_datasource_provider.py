import httpx
from django.test import SimpleTestCase
from lens.plugins.providers.base import DatasourceProviderError
from lens.plugins.providers import get_datasource_provider


class GitHubDatasourceProviderTests(SimpleTestCase):
    """Verify GitHub implements the generic datasource contract."""

    def setUp(self):
        self.provider = get_datasource_provider("github")
        self.scope = {"repositories": ["HyperBDR/sourcelens"]}

    def test_accepts_a_repository_within_connection_scope(self):
        config = self.provider.validate_datasource_config(
            self.scope,
            {
                "repository": "HyperBDR/sourcelens",
                "branch": "main",
                "directory": "docs",
            },
        )

        self.assertEqual(config["repository"], "HyperBDR/sourcelens")
        self.assertEqual(config["directory"], "docs")

    def test_accepts_plural_repository_selection_within_connection_scope(self):
        config = self.provider.validate_datasource_config(
            self.scope,
            {"repositories": ["HyperBDR/sourcelens"]},
        )

        self.assertEqual(config["repositories"], ["HyperBDR/sourcelens"])

    def test_rejects_a_repository_outside_connection_scope(self):
        with self.assertRaisesMessage(DatasourceProviderError, "scope"):
            self.provider.validate_datasource_config(
                self.scope,
                {"repository": "other/repository"},
            )

    def test_rejects_credential_fields_in_datasource_config(self):
        with self.assertRaisesMessage(DatasourceProviderError, "credentials"):
            self.provider.validate_datasource_config(
                self.scope,
                {
                    "repository": "HyperBDR/sourcelens",
                    "access_token": "not-allowed",
                },
            )

    def test_rejects_overlong_branch_and_directory_values(self):
        invalid_configs = [
            {
                "repository": "HyperBDR/sourcelens",
                "branch": "b" * 256,
            },
            {
                "repository": "HyperBDR/sourcelens",
                "directory": "d" * 1001,
            },
        ]

        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(DatasourceProviderError):
                    self.provider.validate_datasource_config(
                        self.scope,
                        config,
                    )

    def test_rejects_non_github_connection_endpoint(self):
        with self.assertRaisesMessage(DatasourceProviderError, "endpoint"):
            self.provider.validate_connection(
                "https://evil.example",
                {},
            )

    def test_normalizes_public_github_connection_endpoint(self):
        self.assertEqual(
            self.provider.validate_connection("https://github.com/", {}),
            "https://github.com",
        )

    def test_defaults_an_omitted_github_connection_endpoint(self):
        self.assertEqual(
            self.provider.validate_connection("", {}),
            "https://github.com",
        )

    def test_rejects_non_git_datasource_source_type(self):
        with self.assertRaisesMessage(DatasourceProviderError, "source type"):
            self.provider.validate_datasource_source_type("feishu")

    def test_normalizes_connection_repository_scope(self):
        scope = self.provider.validate_connection_scope(
            {
                "repositories": [" HyperBDR/sourcelens/", "owner/repo"],
            }
        )

        self.assertEqual(
            scope,
            {"repositories": ["HyperBDR/sourcelens", "owner/repo"]},
        )

    def test_live_validation_rejects_redirect_without_leaking_body(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"Location": "https://evil.example"},
                text="provider-secret-body",
                request=request,
            )
        )

        with httpx.Client(transport=transport) as client:
            with self.assertRaisesMessage(
                DatasourceProviderError,
                "GITHUB_REDIRECT_REJECTED",
            ) as error:
                self.provider.validate_live_connection("secret", client=client)

        self.assertNotIn("provider-secret-body", str(error.exception))

    def test_discovers_allowed_repositories_without_remote_requests(self):
        def handler(request):
            raise AssertionError(request.url)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            resources = self.provider.discover_resources(
                {"repositories": ["owner/repo"]},
                "secret",
                client=client,
            )

        self.assertEqual(
            resources,
            {
                "resources": {
                    "repositories": {
                        "items": [
                            {"value": "owner/repo", "label": "owner/repo"}
                        ]
                    }
                }
            },
        )

    def test_discovers_branches_for_one_allowed_repository(self):
        def handler(request):
            if request.url.path == "/repos/owner/repo/branches":
                return httpx.Response(
                    200,
                    json=[{"name": "main"}, {"name": "release"}],
                    request=request,
                )
            raise AssertionError(request.url)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            resources = self.provider.discover_resource_options(
                {"repositories": ["owner/repo"]},
                "secret",
                "branches",
                {"repository": "owner/repo"},
                client=client,
            )

        self.assertEqual(
            resources,
            {
                "resources": {
                    "branches": {
                        "items": [
                            {"value": "main", "label": "main"},
                            {"value": "release", "label": "release"},
                        ]
                    }
                }
            },
        )

    def test_rejects_branches_for_repository_outside_connection_scope(self):
        with self.assertRaisesMessage(DatasourceProviderError, "scope"):
            self.provider.discover_resource_options(
                {"repositories": ["owner/repo"]},
                "secret",
                "branches",
                {"repository": "other/repo"},
            )
