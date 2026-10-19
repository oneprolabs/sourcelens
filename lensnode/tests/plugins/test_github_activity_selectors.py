from urllib.parse import parse_qs

import httpx
import pytest

from lensnode.plugin_package_loader import load_runtime_contract
from lensnode.plugin_runtime import PluginRuntimeError

GITHUB_RUNTIME = load_runtime_contract("github", "1.0.0")


def _execute(selectors, allowed_repositories, handler, token="github-secret"):
    """Execute the public activity tool against a mocked GitHub boundary."""
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return GITHUB_RUNTIME.execute_tool(
            "github_activity_summary",
            client,
            {
                "repositories": selectors,
                "since": "2026-09-01T00:00:00Z",
                "until": "2026-09-02T23:59:59Z",
            },
            token,
            "https://github.com",
            {"__allowed_scope": {"repositories": allowed_repositories}},
        )


def _empty_activity(request, permitted_repositories):
    """Reject requests outside the expected repositories and return no activity."""
    if request.url.path == "/search/issues":
        query = parse_qs(request.url.query.decode())["q"][0]
        repository = next(part.removeprefix("repo:") for part in query.split() if part.startswith("repo:"))
        assert repository in permitted_repositories
        return httpx.Response(200, json={"total_count": 0, "items": [], "incomplete_results": False})
    segments = request.url.path.strip("/").split("/")
    assert len(segments) == 4 and segments[0] == "repos"
    assert segments[-1] in {"commits", "pulls"}
    assert "/".join(segments[1:3]) in permitted_repositories
    return httpx.Response(200, json=[])


def test_owner_wildcard_discovers_organization_repositories_before_querying_activity():
    """An owner selector resolves concrete authorized repositories, never a literal star."""
    permitted = ["oneprolabs/a", "oneprolabs/b"]

    def handler(request):
        if request.url.path == "/orgs/oneprolabs/repos":
            return httpx.Response(
                200,
                json=[{"full_name": repository} for repository in [*permitted, "another/team"]],
            )
        return _empty_activity(request, permitted)

    result = _execute(["oneprolabs/*"], ["*"], handler)

    assert [item["repository"] for item in result["repositories"]] == permitted
    assert result["coverage"]["requested_selectors"] == ["oneprolabs/*"]
    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["coverage"]["discovery_complete"] is True
    assert result["coverage"]["repositories_truncated"] is False
    assert result["coverage"]["discovery_errors"] == []


def test_concrete_repository_selection_needs_no_discovery():
    """A concrete selector queries the repository directly even under an unrestricted scope."""
    permitted = ["oneprolabs/a"]

    result = _execute(permitted, ["*"], lambda request: _empty_activity(request, permitted))

    assert [item["repository"] for item in result["repositories"]] == permitted
    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["coverage"]["discovery_complete"] is True
    assert result["coverage"]["reasons"] == []


@pytest.mark.parametrize(
    ("selectors", "expected"),
    [
        (["oneprolabs/*"], ["oneprolabs/a", "oneprolabs/b"]),
        (["*"], ["oneprolabs/a", "oneprolabs/b", "other/c"]),
        (["oneprolabs/a", "oneprolabs/*"], ["oneprolabs/a", "oneprolabs/b"]),
    ],
)
def test_concrete_scope_expands_wildcards_locally_and_deduplicates(selectors, expected):
    """Finite authorization lists resolve locally without any discovery request."""
    allowed = ["oneprolabs/a", "oneprolabs/b", "other/c", "oneprolabs/a"]

    result = _execute(selectors, allowed, lambda request: _empty_activity(request, expected))

    assert [item["repository"] for item in result["repositories"]] == expected
    assert result["coverage"]["resolved_repositories"] == expected
    assert result["coverage"]["discovery_complete"] is True
    assert result["coverage"]["repositories_truncated"] is False
    assert result["coverage"]["discovery_errors"] == []


def test_all_selector_discovers_all_token_visible_repositories():
    """An unrestricted selector covers every owner visible to the connection's token."""
    permitted = ["oneprolabs/a", "other/b"]

    def handler(request):
        if request.url.path == "/user/repos":
            assert "github-secret" in request.headers["authorization"]
            return httpx.Response(200, json=[{"full_name": name} for name in [*permitted, permitted[0]]])
        return _empty_activity(request, permitted)

    result = _execute(["*"], ["*"], handler)

    assert [item["repository"] for item in result["repositories"]] == permitted
    assert result["coverage"]["requested_selectors"] == ["*"]
    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["coverage"]["discovery_complete"] is True


def test_all_selector_also_preserves_an_explicit_external_owner_selector():
    """Token-visible repositories and a public external org form a union."""
    permitted = ["oneprolabs/a", "external/b"]
    discovery_paths = []

    def handler(request):
        if request.url.path in {"/user/repos", "/orgs/external/repos"}:
            discovery_paths.append(request.url.path)
            repository = permitted[0] if request.url.path == "/user/repos" else permitted[1]
            return httpx.Response(200, json=[{"full_name": repository}])
        return _empty_activity(request, permitted)

    result = _execute(["*", "external/*"], ["*"], handler)

    assert discovery_paths == ["/user/repos", "/orgs/external/repos"]
    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["source_coverage_complete"] is True


def test_authenticated_personal_owner_unions_public_and_token_visible_private_repositories():
    """Authentication preserves external public repos and supplements private access."""
    permitted = ["developer/public", "developer/private"]

    def handler(request):
        if request.url.path == "/orgs/developer/repos":
            return httpx.Response(404, json={})
        if request.url.path == "/users/developer/repos":
            return httpx.Response(200, json=[{"full_name": permitted[0]}])
        if request.url.path == "/user/repos":
            return httpx.Response(200, json=[{"full_name": name} for name in [*permitted, "other/a"]])
        return _empty_activity(request, permitted)

    result = _execute(["developer/*"], ["*"], handler)

    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["source_coverage_complete"] is True


def test_owner_discovery_follows_pages_and_queries_more_than_fifty_repositories():
    """Discovery reaches later pages and queries every selected repository above the old limit."""
    permitted = [f"oneprolabs/repo-{number:03d}" for number in range(61)]
    first_page = [*permitted[:60], *(f"other/repo-{number:03d}" for number in range(40))]
    pages_seen = []

    def handler(request):
        if request.url.path == "/orgs/oneprolabs/repos":
            query = parse_qs(request.url.query.decode())
            assert query["per_page"] == ["100"]
            page = int(query.get("page", ["1"])[0])
            pages_seen.append(page)
            assert page in {1, 2}
            repositories = first_page if page == 1 else permitted[60:]
            headers = (
                {"Link": '<https://api.github.com/orgs/oneprolabs/repos?per_page=100&page=2>; rel="next"'}
                if page == 1
                else {}
            )
            return httpx.Response(200, json=[{"full_name": name} for name in repositories], headers=headers)
        return _empty_activity(request, permitted)

    result = _execute(["oneprolabs/*"], ["*"], handler)

    assert pages_seen == [1, 2]
    assert [item["repository"] for item in result["repositories"]] == permitted
    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["coverage"]["discovery_complete"] is True
    assert result["coverage"]["repositories_truncated"] is False
    assert result["coverage"]["reasons"] == []


def test_owner_wildcard_in_connection_scope_is_rejected_before_network_access():
    """Connection authorization supports finite repositories or a global star only."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    with pytest.raises(PluginRuntimeError):
        _execute(["*"], ["oneprolabs/*"], handler)

    assert requests == []


def test_concrete_unauthorized_repository_is_rejected_before_network_access():
    """Explicitly requesting an unauthorized repository must not trigger an HTTP request."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    with pytest.raises(PluginRuntimeError):
        _execute(["other/private"], ["oneprolabs/a"], handler)

    assert requests == []


def test_anonymous_owner_selector_discovers_public_organization_repositories():
    """Anonymous owner discovery uses the public org endpoint and no Authorization header."""
    permitted = ["oneprolabs/a", "oneprolabs/b"]

    def handler(request):
        assert "authorization" not in request.headers
        if request.url.path == "/orgs/oneprolabs/repos":
            return httpx.Response(200, json=[{"full_name": name} for name in permitted])
        return _empty_activity(request, permitted)

    result = _execute(["oneprolabs/*"], ["*"], handler, token="")

    assert [item["repository"] for item in result["repositories"]] == permitted
    assert result["coverage"]["discovery_complete"] is True


@pytest.mark.parametrize("token", ["github-secret", ""])
def test_personal_owner_selector_uses_credential_visible_or_public_repositories(token):
    """Personal owner discovery preserves private token access or public anonymous access."""
    permitted = ["developer/a"]
    discovery_paths = []

    def handler(request):
        if request.url.path == "/orgs/developer/repos":
            discovery_paths.append(request.url.path)
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path == "/users/developer/repos":
            discovery_paths.append(request.url.path)
            return httpx.Response(200, json=[{"full_name": name} for name in permitted])
        if token and request.url.path == "/user/repos":
            discovery_paths.append(request.url.path)
            return httpx.Response(200, json=[])
        return _empty_activity(request, permitted)

    result = _execute(["developer/*"], ["*"], handler, token=token)

    expected_paths = ["/orgs/developer/repos", "/users/developer/repos"]
    if token:
        expected_paths.append("/user/repos")
    assert discovery_paths == expected_paths
    assert [item["repository"] for item in result["repositories"]] == permitted
    assert result["coverage"]["discovery_complete"] is True
    assert result["coverage"]["discovery_errors"] == []


def test_anonymous_all_selector_requires_authentication_without_network_access():
    """Anonymous access has no enumerable universe of all public GitHub repositories."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    with pytest.raises(PluginRuntimeError, match="GITHUB_AUTHENTICATION_REQUIRED"):
        _execute(["*"], ["*"], handler, token="")

    assert requests == []


def test_discovery_failure_preserves_explicit_repository_and_reports_incomplete_coverage():
    """A failed wildcard discovery remains visible while explicit activity still returns."""
    permitted = ["oneprolabs/a"]

    def handler(request):
        if request.url.path == "/orgs/oneprolabs/repos":
            raise httpx.ReadTimeout("slow", request=request)
        return _empty_activity(request, permitted)

    result = _execute(["oneprolabs/a", "oneprolabs/*"], ["*"], handler)

    assert [item["repository"] for item in result["repositories"]] == permitted
    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["coverage"]["discovery_complete"] is False
    assert result["coverage"]["repositories_truncated"] is True
    assert result["coverage"]["discovery_errors"] == [
        {"selector": "oneprolabs/*", "error": "GITHUB_REQUEST_FAILED"},
    ]
    assert "discovery_failed" in result["coverage"]["reasons"]


def test_owner_selector_with_no_authorized_match_reports_complete_empty_coverage():
    """A finite scope with no matching owner produces an empty result without network access."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    result = _execute(["oneprolabs/*"], ["other/a"], handler)

    assert result["ok"] is True
    assert result["repositories"] == []
    assert result["coverage"]["resolved_repositories"] == []
    assert result["coverage"]["discovery_complete"] is True
    assert result["coverage"]["repositories_truncated"] is False
    assert requests == []


def test_anonymous_all_selector_with_finite_scope_needs_no_authentication():
    """A finite authorized list remains enumerable without a token."""
    permitted = ["oneprolabs/a", "other/b"]

    def handler(request):
        assert "authorization" not in request.headers
        return _empty_activity(request, permitted)

    result = _execute(["*"], permitted, handler, token="")

    assert [item["repository"] for item in result["repositories"]] == permitted
    assert result["coverage"]["discovery_complete"] is True


def test_repository_limit_is_exposed_in_coverage_instead_of_silently_dropping_repositories():
    """A bounded activity result identifies the repositories queried and the truncation reason."""
    allowed = [f"oneprolabs/repo-{number:03d}" for number in range(501)]
    selected = allowed[:500]

    result = _execute(["oneprolabs/*"], allowed, lambda request: _empty_activity(request, selected))

    assert [item["repository"] for item in result["repositories"]] == selected
    assert result["coverage"]["resolved_repositories"] == selected
    assert result["coverage"]["discovery_complete"] is True
    assert result["coverage"]["repositories_truncated"] is True
    assert "repository_limit" in result["coverage"]["reasons"]


def test_dynamic_repository_limit_marks_unfinished_discovery():
    """A dynamic selection above the bound discloses the unknown remaining tail."""

    repositories = [f"oneprolabs/repo-{number:03d}" for number in range(501)]
    selected = repositories[:500]

    def handler(request):
        if request.url.path == "/orgs/oneprolabs/repos":
            page = int(parse_qs(request.url.query.decode())["page"][0])
            return httpx.Response(
                200, json=[{"full_name": name} for name in repositories[(page - 1) * 100 : page * 100]]
            )
        return _empty_activity(request, selected)

    result = _execute(["oneprolabs/*"], ["*"], handler)

    assert len(result["repositories"]) == 500
    assert result["coverage"]["discovery_complete"] is False
    assert result["repositories_truncated"] is True
    assert result["source_coverage_complete"] is False
    assert result["coverage"]["reasons"] == ["repository_limit"]


def test_discovery_page_limit_marks_an_unresolved_owner_as_partial():
    """Exhausting discovery pages must not turn an unknown owner selection into empty success."""

    pages = []

    def handler(request):
        assert request.url.path == "/users/oneprolabs/repos"
        page = int(parse_qs(request.url.query.decode())["page"][0])
        pages.append(page)
        return httpx.Response(200, json=[{"full_name": f"other/repo-{page}-{i}"} for i in range(100)])

    result = _execute(
        ["oneprolabs/*"],
        ["*"],
        lambda request: (
            httpx.Response(404, json={}) if request.url.path == "/orgs/oneprolabs/repos" else handler(request)
        ),
    )

    assert pages == list(range(1, 21))
    assert result["repositories"] == []
    assert result["coverage"]["discovery_complete"] is False
    assert result["coverage"]["reasons"] == ["discovery_page_limit"]
    assert result["source_coverage_complete"] is False


def test_later_discovery_failure_retains_earlier_repositories():
    """A denied later page preserves verified repositories and the discovery error."""

    permitted = [f"oneprolabs/repo-{number:03d}" for number in range(100)]

    def handler(request):
        if request.url.path == "/orgs/oneprolabs/repos":
            page = int(parse_qs(request.url.query.decode())["page"][0])
            if page == 2:
                return httpx.Response(403, json={"message": "denied"})
            return httpx.Response(200, json=[{"full_name": name} for name in permitted])
        return _empty_activity(request, permitted)

    result = _execute(["oneprolabs/*"], ["*"], handler)

    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["coverage"]["discovery_errors"] == [{"selector": "oneprolabs/*", "error": "GITHUB_ACCESS_DENIED"}]
    assert result["source_coverage_complete"] is False


def test_repeated_discovery_page_does_not_claim_complete_coverage():
    """A provider ignoring page numbers cannot trap discovery or claim full coverage."""

    permitted = [f"oneprolabs/repo-{number:03d}" for number in range(100)]

    def handler(request):
        if request.url.path == "/orgs/oneprolabs/repos":
            return httpx.Response(200, json=[{"full_name": name} for name in permitted])
        return _empty_activity(request, permitted)

    result = _execute(["oneprolabs/*"], ["*"], handler)

    assert result["coverage"]["resolved_repositories"] == permitted
    assert result["coverage"]["reasons"] == ["discovery_stalled"]
    assert result["source_coverage_complete"] is False


def test_all_activity_failures_retain_repository_error_details():
    """Unavailable repositories are explicit gaps rather than an empty complete report."""

    result = _execute(["oneprolabs/a"], ["*"], lambda request: httpx.Response(403, json={"message": "denied"}))

    assert result["source_coverage_complete"] is False
    assert result["repositories"][0]["errors"] == {
        "commits": "GITHUB_ACCESS_DENIED",
        "pull_requests": "GITHUB_ACCESS_DENIED",
    }


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("denied_field", ["updated", "created", "closed"])
def test_issue_access_denied_is_ignored_without_discarding_collected_activity(status, denied_field):
    """Inaccessible issue searches do not mark otherwise readable activity as incomplete."""

    permitted = ["oneprolabs/devify-home"]

    def handler(request):
        if request.url.path != "/search/issues":
            return _empty_activity(request, permitted)
        query = parse_qs(request.url.query.decode())["q"][0]
        if f" {denied_field}:" in query:
            return httpx.Response(status, json={"message": "denied"})
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "incomplete_results": False,
                "items": [{"number": 10, "title": "Visible issue", "updated_at": "2026-09-02T08:00:00Z"}],
            },
        )

    result = _execute(permitted, permitted, handler)

    repository = result["repositories"][0]
    assert "errors" not in repository
    assert [item["number"] for item in repository["issues"]] == ([] if denied_field == "updated" else [10])
    assert repository["possibly_truncated"]["issues"] is False
    assert result["source_coverage_complete"] is True


@pytest.mark.parametrize(
    "status,headers,error",
    [
        (429, {}, "GITHUB_RATE_LIMITED"),
        (403, {"X-RateLimit-Remaining": "0"}, "GITHUB_RATE_LIMITED"),
        (403, {"Retry-After": "60"}, "GITHUB_RATE_LIMITED"),
        (500, {}, "GITHUB_REQUEST_FAILED"),
    ],
)
def test_issue_errors_other_than_access_denied_remain_coverage_gaps(monkeypatch, status, headers, error):
    """Skipping issue permissions must not conceal rate limits or provider failures."""

    permitted = ["oneprolabs/devify-deploy"]
    monkeypatch.setattr("time.sleep", lambda delay: None)

    def handler(request):
        if request.url.path == "/search/issues":
            return httpx.Response(status, headers=headers, json={"message": "unavailable"})
        return _empty_activity(request, permitted)

    result = _execute(permitted, permitted, handler)

    assert result["repositories"][0]["errors"] == {"issues": error}
    assert result["source_coverage_complete"] is False


@pytest.mark.parametrize("field", ["updated", "created", "closed"])
@pytest.mark.parametrize("recovers", [True, False])
def test_incomplete_issue_search_is_retried_before_declaring_a_coverage_gap(monkeypatch, field, recovers):
    """HTTP 200 search timeouts may recover, while persistent incomplete results stay explicit."""

    permitted = ["oneprolabs/a"]
    attempts = []
    delays = []
    monkeypatch.setattr("time.sleep", delays.append)

    def handler(request):
        if request.url.path != "/search/issues":
            return _empty_activity(request, permitted)
        query = parse_qs(request.url.query.decode())["q"][0]
        if f" {field}:" not in query:
            return _empty_activity(request, permitted)
        attempts.append(query)
        incomplete = not recovers or len(attempts) == 1
        return httpx.Response(200, json={
            "total_count": 1,
            "incomplete_results": incomplete,
            "items": [{"number": 10, "title": "Visible issue", "updated_at": "2026-09-02T08:00:00Z"}],
        })

    result = _execute(permitted, permitted, handler)

    assert len(attempts) == (2 if recovers else 3)
    assert delays == ([0.25] if recovers else [0.25, 0.5])
    assert result["source_coverage_complete"] is recovers
    repository = result["repositories"][0]
    assert [item["number"] for item in repository["issues"]] == [10]
    assert repository["possibly_truncated"]["issues"] is not recovers


@pytest.mark.parametrize("field,status", [
    ("updated", 403), ("created", 403), ("closed", 403),
    ("updated", 502), ("created", 502), ("closed", 502),
    ("updated", "invalid_json"), ("updated", "invalid_schema"), ("updated", "timeout"),
])
def test_failed_search_retry_preserves_observed_partial_items_and_the_gap(monkeypatch, field, status):
    """A failed retry cannot erase an earlier partial page or turn it into complete coverage."""

    permitted = ["oneprolabs/a"]
    attempts = []
    monkeypatch.setattr("time.sleep", lambda delay: None)

    def handler(request):
        if request.url.path != "/search/issues":
            return _empty_activity(request, permitted)
        query = parse_qs(request.url.query.decode())["q"][0]
        if f" {field}:" not in query:
            return _empty_activity(request, permitted)
        attempts.append(query)
        if len(attempts) > 1:
            if status == "invalid_json":
                return httpx.Response(200, content=b"invalid JSON")
            if status == "invalid_schema":
                return httpx.Response(200, json={"total_count": 0, "incomplete_results": False, "items": None})
            if status == "timeout":
                raise httpx.ReadTimeout("Temporary timeout", request=request)
            return httpx.Response(status, json={"message": "unavailable"})
        return httpx.Response(200, json={
            "total_count": 1,
            "incomplete_results": True,
            "items": [{"number": 10, "title": "Visible issue", "updated_at": "2026-09-02T08:00:00Z"}],
        })

    result = _execute(permitted, permitted, handler)

    assert len(attempts) > 1
    assert result["source_coverage_complete"] is False
    repository = result["repositories"][0]
    assert [item["number"] for item in repository["issues"]] == [10]
    assert repository["possibly_truncated"]["issues"] is True
