import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from lensnode.plugin_package_loader import load_runtime_contract
from lensnode.plugin_runtime import PluginRuntimeError


GITHUB_RUNTIME = load_runtime_contract("github", "1.0.0")


def test_builds_multi_repository_datasource_command():
    command = GITHUB_RUNTIME.build_datasource_command(
        {
            "datasource_uuid": "ds-1",
            "resolved_config": {
                "endpoint": "https://github.com",
                "connection_scope": {
                    "repositories": ["oneprolabs/a", "oneprolabs/b"]
                },
                "datasource_config": {
                    "repositories": ["oneprolabs/a", "oneprolabs/b"],
                    "branch": "main",
                },
                "target_path": "/workspace/repos",
                "sync_policy": {},
            },
        },
        {
            "plugin_key": "github",
            "endpoint": "https://github.com",
            "value": "secret",
        },
        "manual",
    )

    assert [item["repo_url"] for item in command["config"]["repositories"]] == [
        "https://github.com/oneprolabs/a.git",
        "https://github.com/oneprolabs/b.git",
    ]
    assert [
        item["target_subdir"] for item in command["config"]["repositories"]
    ] == ["oneprolabs/a", "oneprolabs/b"]


def test_builds_single_repository_as_repository_collection():
    command = GITHUB_RUNTIME.build_datasource_command(
        {
            "datasource_uuid": "ds-1",
            "resolved_config": {
                "endpoint": "https://github.com",
                "connection_scope": {
                    "repositories": ["oneprolabs/a"]
                },
                "datasource_config": {
                    "repository": "oneprolabs/a",
                    "branch": "main",
                },
                "target_path": "/workspace/repos",
                "sync_policy": {},
            },
        },
        {
            "plugin_key": "github",
            "endpoint": "https://github.com",
            "value": "secret",
        },
        "manual",
    )

    assert "repo_url" not in command["config"]
    assert command["config"]["repositories"] == [
        {
            "repo_url": "https://github.com/oneprolabs/a.git",
            "branch": "main",
            "directory": "",
            "target_subdir": "oneprolabs/a",
            "enabled": True,
        }
    ]


def test_multi_repository_target_subdirs_remain_unique_for_same_names():
    command = GITHUB_RUNTIME.build_datasource_command(
        {
            "datasource_uuid": "ds-1",
            "resolved_config": {
                "endpoint": "https://github.com",
                "connection_scope": {
                    "repositories": ["team-a/common", "team_b/common"]
                },
                "datasource_config": {
                    "repositories": ["team-a/common", "team_b/common"]
                },
                "target_path": "/workspace/repos",
                "sync_policy": {},
            },
        },
        {
            "plugin_key": "github",
            "endpoint": "https://github.com",
            "value": "secret",
        },
        "manual",
    )

    target_subdirs = [
        item["target_subdir"] for item in command["config"]["repositories"]
    ]
    assert target_subdirs == ["team-a/common", "team_b/common"]
    assert len(set(target_subdirs)) == 2


def _execute(tool_key, arguments, handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return GITHUB_RUNTIME.execute_tool(
            tool_key,
            client,
            arguments,
            "github-secret",
            "https://github.com",
            {
                "__allowed_scope": {
                    "repositories": ["HyperBDR/sourcelens"],
                }
            },
        )


def test_builds_structured_tools_from_manifest_schemas():
    definition = {
        "key": "github_issue_get",
        "description": "Get one issue from an approved repository.",
        "capability": "repository.read",
        "side_effect": "none",
        "input_schema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string"},
                "number": {"type": "integer"},
            },
            "required": ["repository", "number"],
            "additionalProperties": False,
        },
    }

    tool = GITHUB_RUNTIME.build_tool(
        definition,
        lambda key, arguments, runtime: (key, arguments, runtime),
    )

    assert set(tool.args) == {"repository", "number"}
    assert "runtime" not in tool.args


def test_every_bundled_manifest_tool_builds_with_its_declared_schema():
    manifest_path = (
            Path(__file__).resolve().parents[3]
            / "plugins"
            / "github"
            / "plugin.json"
    )
    definitions = json.loads(manifest_path.read_text())["tools"]

    tools = [
        GITHUB_RUNTIME.build_tool(
            definition,
            lambda key, arguments, runtime: (key, arguments, runtime),
        )
        for definition in definitions
    ]

    assert [tool.name for tool in tools] == [
        definition["key"] for definition in definitions
    ]
    assert all("runtime" not in tool.args for tool in tools)


def test_repository_and_branch_results_are_bounded_and_sanitized():
    def repository_handler(request):
        assert request.url.path == "/repos/HyperBDR/sourcelens"
        return httpx.Response(
            200,
            json={
                "full_name": "HyperBDR/sourcelens",
                "description": "Source analysis",
                "private": True,
                "default_branch": "main",
                "archived": False,
                "fork": False,
                "language": "Python",
                "stargazers_count": 12,
                "forks_count": 3,
                "open_issues_count": 4,
                "updated_at": "2026-09-02T00:00:00Z",
                "html_url": "https://github.com/HyperBDR/sourcelens",
                "owner": {"login": "HyperBDR", "secret": "ignored"},
                "unexpected": "ignored",
            },
            request=request,
        )

    repository = _execute(
        "github_repository_get",
        {"repository": "HyperBDR/sourcelens"},
        repository_handler,
    )

    assert repository == {
        "ok": True,
        "repository": "HyperBDR/sourcelens",
        "description": "Source analysis",
        "private": True,
        "default_branch": "main",
        "archived": False,
        "fork": False,
        "language": "Python",
        "stars": 12,
        "forks": 3,
        "open_issues": 4,
        "updated_at": "2026-09-02T00:00:00Z",
        "url": "https://github.com/HyperBDR/sourcelens",
        "owner": "HyperBDR",
    }

    def branches_handler(request):
        assert request.url.path == "/repos/HyperBDR/sourcelens/branches"
        assert parse_qs(request.url.query.decode()) == {
            "page": ["2"],
            "per_page": ["20"],
        }
        return httpx.Response(
            200,
            json=[
                {
                    "name": "main",
                    "protected": True,
                    "commit": {"sha": "abc", "url": "ignored"},
                }
            ],
            request=request,
        )

    branches = _execute(
        "github_branch_list",
        {"repository": "HyperBDR/sourcelens", "page": 2, "per_page": 20},
        branches_handler,
    )

    assert branches["items"] == [
        {"name": "main", "protected": True, "sha": "abc"}
    ]
    assert branches["page"] == 2


def test_commit_list_and_get_return_stable_fields():
    def list_handler(request):
        assert request.url.path == "/repos/HyperBDR/sourcelens/commits"
        assert parse_qs(request.url.query.decode()) == {
            "page": ["1"],
            "path": ["backend/lens"],
            "per_page": ["20"],
            "sha": ["main"],
            "since": ["2026-09-01T16:00:00Z"],
            "until": ["2026-09-02T15:59:59Z"],
        }
        return httpx.Response(
            200,
            json=[
                {
                    "sha": "abc",
                    "html_url": "https://github.com/c/abc",
                    "commit": {
                        "message": "feat: expand GitHub tools\n\nbody",
                        "author": {
                            "name": "Developer",
                            "date": "2026-09-02T00:00:00Z",
                        },
                    },
                    "author": {"login": "developer"},
                }
            ],
            request=request,
        )

    commits = _execute(
        "github_commit_list",
        {
            "repository": "HyperBDR/sourcelens",
            "ref": "main",
            "path": "backend/lens",
            "page": 1,
            "per_page": 20,
            "since": "2026-09-01T16:00:00Z",
            "until": "2026-09-02T15:59:59Z",
        },
        list_handler,
    )

    assert commits["items"][0]["message"] == "feat: expand GitHub tools"
    assert commits["items"][0]["login"] == "developer"

    def get_handler(request):
        assert request.url.path == "/repos/HyperBDR/sourcelens/commits/abc"
        return httpx.Response(
            200,
            json={
                "sha": "abc",
                "html_url": "https://github.com/c/abc",
                "stats": {"additions": 10, "deletions": 2, "total": 12},
                "commit": {
                    "message": "feat: expand GitHub tools",
                    "author": {"name": "Developer", "date": "now"},
                    "committer": {"name": "Developer", "date": "now"},
                },
                "author": {"login": "developer"},
                "files": [
                    {
                        "filename": "runtime.py",
                        "status": "modified",
                        "additions": 10,
                        "deletions": 2,
                        "changes": 12,
                        "patch": "must not be returned",
                    }
                ],
            },
            request=request,
        )

    commit = _execute(
        "github_commit_get",
        {"repository": "HyperBDR/sourcelens", "ref": "abc"},
        get_handler,
    )

    assert commit["stats"] == {"additions": 10, "deletions": 2, "total": 12}
    assert commit["files"] == [
        {
            "path": "runtime.py",
            "status": "modified",
            "additions": 10,
            "deletions": 2,
            "changes": 12,
        }
    ]
    assert "patch" not in str(commit)


def test_activity_summary_batches_and_filters_repository_activity():
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/commits"):
            assert parse_qs(request.url.query.decode()) == {
                "page": ["1"],
                "per_page": ["2"],
                "since": ["2026-09-01T16:00:00Z"],
                "until": ["2026-09-02T15:59:59Z"],
            }
            payload = [
                {
                    "sha": "abc",
                    "html_url": "https://github.com/c/abc",
                    "commit": {
                        "message": "feat: useful work",
                        "author": {
                            "name": "Developer",
                            "date": "2026-09-02T08:00:00Z",
                        },
                    },
                    "author": {"login": "developer"},
                }
            ]
        elif request.url.path.endswith("/pulls"):
            assert parse_qs(request.url.query.decode()) == {
                "direction": ["desc"],
                "page": ["1"],
                "per_page": ["2"],
                "sort": ["updated"],
                "state": ["all"],
            }
            payload = [
                {
                    "number": 10,
                    "title": "In window",
                    "state": "closed",
                    "user": {"login": "developer"},
                    "created_at": "2026-09-01T10:00:00Z",
                    "updated_at": "2026-09-02T08:00:00Z",
                    "html_url": "https://github.com/pull/10",
                },
                {
                    "number": 9,
                    "title": "Too old",
                    "state": "closed",
                    "user": {"login": "developer"},
                    "created_at": "2026-08-01T10:00:00Z",
                    "updated_at": "2026-08-02T08:00:00Z",
                    "html_url": "https://github.com/pull/9",
                },
            ]
        else:
            assert parse_qs(request.url.query.decode()) == {
                "direction": ["desc"],
                "page": ["1"],
                "per_page": ["2"],
                "q": [
                    "repo:HyperBDR/sourcelens is:issue "
                    "updated:2026-09-01T16:00:00Z.."
                    "2026-09-02T15:59:59Z"
                ],
                "sort": ["updated"],
            }
            payload = {
                "total_count": 1,
                "incomplete_results": False,
                "items": [
                    {
                        "number": 20,
                        "title": "Issue in window",
                        "state": "open",
                        "user": {"login": "reporter"},
                        "created_at": "2026-09-02T07:00:00Z",
                        "updated_at": "2026-09-02T09:00:00Z",
                        "html_url": "https://github.com/issues/20",
                    }
                ],
            }
        return httpx.Response(200, json=payload, request=request)

    result = _execute(
        "github_activity_summary",
        {
            "repositories": ["HyperBDR/sourcelens"],
            "since": "2026-09-01T16:00:00Z",
            "until": "2026-09-02T15:59:59Z",
            "per_page": 2,
        },
        handler,
    )

    repository = result["repositories"][0]
    assert len(requests) == 3
    assert repository["commits"][0]["sha"] == "abc"
    assert [item["number"] for item in repository["pull_requests"]] == [10]
    assert [item["number"] for item in repository["issues"]] == [20]
    assert repository["possibly_truncated"] == {
        "commits": False,
        "pull_requests": False,
        "issues": False,
    }
    assert "body" not in json.dumps(result)


def test_activity_summary_applies_safe_resource_specific_limits():
    def handler(request):
        query = parse_qs(request.url.query.decode())
        expected = "50" if request.url.path.endswith("/commits") else "10"
        assert query["per_page"] == [expected]
        if request.url.path == "/search/issues":
            return httpx.Response(
                200,
                json={
                    "total_count": 0,
                    "incomplete_results": False,
                    "items": [],
                },
                request=request,
            )
        return httpx.Response(200, json=[], request=request)

    result = _execute(
        "github_activity_summary",
        {
            "repositories": ["HyperBDR/sourcelens"],
            "since": "2026-09-01T16:00:00Z",
            "until": "2026-09-02T15:59:59Z",
        },
        handler,
    )

    assert result["limits"] == {
        "commits": 50,
        "pull_requests": 10,
        "issues": 10,
    }


def test_activity_summary_uses_issue_only_search_before_limit():
    def handler(request):
        if request.url.path != "/search/issues":
            return httpx.Response(200, json=[], request=request)
        query = parse_qs(request.url.query.decode())
        assert query["per_page"] == ["10"]
        assert "is:issue" in query["q"][0]
        issue = {
            "number": 20,
            "title": "Issue after recent pull requests",
            "state": "open",
            "user": {"login": "reporter"},
            "created_at": "2026-09-02T07:00:00Z",
            "updated_at": "2026-09-02T09:00:00Z",
            "html_url": "https://github.com/issues/20",
        }
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "incomplete_results": False,
                "items": [issue],
            },
            request=request,
        )

    result = _execute(
        "github_activity_summary",
        {
            "repositories": ["HyperBDR/sourcelens"],
            "since": "2026-09-01T16:00:00Z",
            "until": "2026-09-02T15:59:59Z",
        },
        handler,
    )

    repository = result["repositories"][0]
    assert [item["number"] for item in repository["issues"]] == [20]
    assert repository["possibly_truncated"]["issues"] is False


def test_activity_summary_preserves_partial_results_when_one_request_fails():
    def handler(request):
        if request.url.path.endswith("/issues"):
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=[], request=request)

    result = _execute(
        "github_activity_summary",
        {
            "repositories": ["HyperBDR/sourcelens"],
            "since": "2026-09-01T16:00:00Z",
            "until": "2026-09-02T15:59:59Z",
        },
        handler,
    )

    repository = result["repositories"][0]
    assert result["ok"] is True
    assert repository["errors"] == {"issues": "GITHUB_REQUEST_FAILED"}
    assert repository["commits"] == []
    assert repository["pull_requests"] == []


def test_activity_summary_retries_transient_issue_search_failure():
    issue_attempts = 0

    def handler(request):
        nonlocal issue_attempts
        if request.url.path != "/search/issues":
            return httpx.Response(200, json=[], request=request)
        issue_attempts += 1
        if issue_attempts == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "incomplete_results": False,
                "items": [
                    {
                        "number": 20,
                        "title": "Recovered issue search",
                        "state": "open",
                        "user": {"login": "reporter"},
                        "created_at": "2026-09-02T07:00:00Z",
                        "updated_at": "2026-09-02T09:00:00Z",
                        "html_url": "https://github.com/issues/20",
                    }
                ],
            },
            request=request,
        )

    result = _execute(
        "github_activity_summary",
        {
            "repositories": ["HyperBDR/sourcelens"],
            "since": "2026-09-01T16:00:00Z",
            "until": "2026-09-02T15:59:59Z",
        },
        handler,
    )

    repository = result["repositories"][0]
    assert issue_attempts == 2
    assert [item["number"] for item in repository["issues"]] == [20]
    assert "errors" not in repository


@pytest.mark.parametrize(
    ("tool_key", "path", "payload", "expected"),
    [
        (
            "github_issue_get",
            "/repos/HyperBDR/sourcelens/issues/488",
            {
                "number": 488,
                "title": "Expand tools",
                "state": "open",
                "body": "Bounded body",
                "user": {"login": "author"},
                "labels": [{"name": "feature"}],
                "comments": 2,
                "created_at": "created",
                "updated_at": "updated",
                "closed_at": None,
                "html_url": "https://github.com/i/488",
            },
            {"number": 488, "title": "Expand tools", "author": "author"},
        ),
        (
            "github_pull_request_get",
            "/repos/HyperBDR/sourcelens/pulls/488",
            {
                "number": 488,
                "title": "Expand tools",
                "state": "open",
                "body": "Bounded body",
                "draft": False,
                "merged": False,
                "mergeable": True,
                "user": {"login": "author"},
                "head": {"ref": "feature", "sha": "abc"},
                "base": {"ref": "main", "sha": "def"},
                "commits": 3,
                "additions": 20,
                "deletions": 4,
                "changed_files": 2,
                "comments": 1,
                "review_comments": 2,
                "created_at": "created",
                "updated_at": "updated",
                "merged_at": None,
                "closed_at": None,
                "html_url": "https://github.com/p/488",
            },
            {"number": 488, "title": "Expand tools", "head_ref": "feature"},
        ),
        (
            "github_workflow_run_get",
            "/repos/HyperBDR/sourcelens/actions/runs/99",
            {
                "id": 99,
                "name": "CI",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "workflow_id": 7,
                "run_number": 8,
                "run_attempt": 1,
                "head_branch": "main",
                "head_sha": "abc",
                "actor": {"login": "author"},
                "created_at": "created",
                "updated_at": "updated",
                "html_url": "https://github.com/a/99",
                "jobs_url": "must not be returned",
            },
            {"id": 99, "name": "CI", "actor": "author"},
        ),
    ],
)
def test_detail_tools_use_fixed_endpoints_and_safe_projections(
    tool_key,
    path,
    payload,
    expected,
):
    def handler(request):
        assert request.url.path == path
        return httpx.Response(200, json=payload, request=request)

    identifier = 99 if tool_key == "github_workflow_run_get" else 488
    field = "run_id" if tool_key == "github_workflow_run_get" else "number"
    result = _execute(
        tool_key,
        {"repository": "HyperBDR/sourcelens", field: identifier},
        handler,
    )

    for key, value in expected.items():
        assert result[key] == value
    assert "jobs_url" not in result


def test_list_tools_bound_pages_and_filter_pull_requests_from_issue_results():
    def handler(request):
        assert request.url.path == "/repos/HyperBDR/sourcelens/issues"
        assert parse_qs(request.url.query.decode()) == {
            "labels": ["bug"],
            "page": ["1"],
            "per_page": ["20"],
            "state": ["open"],
        }
        return httpx.Response(
            200,
            json=[
                {
                    "number": 1,
                    "title": "Issue",
                    "state": "open",
                    "user": {"login": "author"},
                    "labels": [{"name": "bug"}],
                    "comments": 0,
                    "created_at": "created",
                    "updated_at": "updated",
                    "html_url": "https://github.com/i/1",
                },
                {
                    "number": 2,
                    "title": "PR returned by issues API",
                    "pull_request": {"url": "ignored"},
                },
            ],
            request=request,
        )

    result = _execute(
        "github_issue_list",
        {
            "repository": "HyperBDR/sourcelens",
            "state": "open",
            "labels": "bug",
            "page": 1,
            "per_page": 20,
        },
        handler,
    )

    assert [item["number"] for item in result["items"]] == [1]
    assert result["has_more"] is False


def test_list_tools_reject_oversized_pages():
    with pytest.raises(PluginRuntimeError, match="PLUGIN_ARGUMENTS_INVALID"):
        _execute(
            "github_commit_list",
            {
                "repository": "HyperBDR/sourcelens",
                "per_page": 21,
            },
            lambda request: httpx.Response(200, json=[], request=request),
        )


def test_comments_files_reviews_releases_and_runs_have_bounded_shapes():
    cases = [
        (
            "github_issue_comments",
            {"repository": "HyperBDR/sourcelens", "number": 1},
            "/repos/HyperBDR/sourcelens/issues/1/comments",
            [{"id": 1, "body": "comment", "user": {"login": "author"}}],
            "body",
        ),
        (
            "github_pull_request_files",
            {"repository": "HyperBDR/sourcelens", "number": 1},
            "/repos/HyperBDR/sourcelens/pulls/1/files",
            [{"filename": "a.py", "status": "modified", "patch": "ignored"}],
            "path",
        ),
        (
            "github_pull_request_reviews",
            {"repository": "HyperBDR/sourcelens", "number": 1},
            "/repos/HyperBDR/sourcelens/pulls/1/reviews",
            [{"id": 2, "state": "APPROVED", "user": {"login": "reviewer"}}],
            "state",
        ),
        (
            "github_release_list",
            {"repository": "HyperBDR/sourcelens"},
            "/repos/HyperBDR/sourcelens/releases",
            [{"id": 3, "tag_name": "v1.0.0", "name": "V1", "assets": [{}]}],
            "tag",
        ),
        (
            "github_workflow_run_list",
            {"repository": "HyperBDR/sourcelens"},
            "/repos/HyperBDR/sourcelens/actions/runs",
            {"workflow_runs": [{"id": 4, "name": "CI", "actor": {}}]},
            "id",
        ),
    ]

    for tool_key, arguments, path, payload, expected_field in cases:
        def handler(request, expected_path=path, response=payload):
            assert request.url.path == expected_path
            return httpx.Response(200, json=response, request=request)

        result = _execute(tool_key, arguments, handler)

        assert expected_field in result["items"][0], tool_key
        assert "patch" not in str(result), tool_key
        assert "assets" not in str(result), tool_key


def test_runtime_rejects_unknown_tools_and_redirects():
    def unknown_handler(request):
        raise AssertionError(request.url)

    with pytest.raises(PluginRuntimeError, match="PLUGIN_TOOL_UNSUPPORTED"):
        _execute(
            "github_api",
            {"repository": "HyperBDR/sourcelens"},
            unknown_handler,
        )

    def redirect_handler(request):
        return httpx.Response(
            302,
            headers={"Location": "https://evil.example"},
            request=request,
        )

    with pytest.raises(PluginRuntimeError, match="GITHUB_REDIRECT_REJECTED"):
        _execute(
            "github_issue_get",
            {"repository": "HyperBDR/sourcelens", "number": 1},
            redirect_handler,
        )


def test_runtime_rejects_scope_and_path_escape_even_with_a_valid_snapshot():
    """Runtime enforces repository and path boundaries independently."""

    def handler(request):
        raise AssertionError(request.url)

    with pytest.raises(PluginRuntimeError, match="PLUGIN_SCOPE_MISMATCH"):
        _execute(
            "github_issue_get",
            {"repository": "other/repository", "number": 1},
            handler,
        )

    with pytest.raises(PluginRuntimeError, match="PLUGIN_ARGUMENTS_INVALID"):
        _execute(
            "github_read_file",
            {
                "repository": "HyperBDR/sourcelens",
                "path": "../secrets.txt",
            },
            handler,
        )
