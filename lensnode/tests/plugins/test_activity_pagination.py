"""Provider-owned pagination expands activity coverage within fixed budgets."""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from lensnode.plugin_package_loader import load_runtime_contract

SINCE = "2026-10-07T16:00:00Z"
UNTIL = "2026-10-08T16:00:00Z"
STAMP = "2026-10-08T08:00:00Z"


def _function(provider, name):
    return load_runtime_contract(provider, "1.0.0").execute_tool.__globals__[name]


@pytest.mark.parametrize("cloud", [False, True])
def test_jira_activity_collects_later_pages_and_honors_exact_window(cloud):
    seen = []

    def respond(request):
        query = dict(request.url.params)
        page = int(query.get("nextPageToken", query.get("startAt", "0")))
        seen.append(page)
        count = 100 if page == 0 else 21
        result = {
            "issues": [
                {
                    "key": f"SL-{page + index}",
                    "fields": {"summary": "Work", "updated": STAMP},
                }
                for index in range(count)
            ]
        }
        if cloud:
            result.update(isLast=page != 0)
            if page == 0:
                result["nextPageToken"] = "100"
        else:
            result.update(total=121, startAt=page)
        return httpx.Response(200, json=result)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        items, truncated = _function("jira", "_jira_project_activity")(
            client,
            "https://tenant.atlassian.net" if cloud else "https://jira.example.com",
            {},
            "SL",
            datetime.fromisoformat(SINCE.replace("Z", "+00:00")),
            datetime.fromisoformat(UNTIL.replace("Z", "+00:00")),
            500,
            ZoneInfo("UTC"),
        )
    assert len(items) == 121
    assert seen == [0, 100]
    assert truncated is False


def test_gitlab_all_projects_expands_beyond_the_old_first_fifty():
    def respond(request):
        page = int(request.url.params["page"])
        start = (page - 1) * 100
        count = 100 if page == 1 else 21
        return httpx.Response(200, json=[{"path_with_namespace": f"team/repo-{start + i}"} for i in range(count)])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        projects, truncated = _function("gitlab", "_all_activity_projects")(client, "https://gitlab.example", "secret")
    assert len(projects) == 121
    assert truncated is False


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_commit_activity_collects_multiple_pages(provider):
    def respond(request):
        page = int(request.url.params["page"])
        start = (page - 1) * 50
        count = 50 if page <= 2 else 21
        payload = []
        for i in range(count):
            identity = str(start + i)
            if provider == "github":
                payload.append({"sha": identity, "commit": {"message": "Work", "author": {"date": STAMP}}})
            else:
                payload.append({"id": identity, "committed_date": STAMP})
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if provider == "github":
            items, truncated = _function(provider, "_activity_resource")(
                client,
                "owner/repo",
                "commits",
                SINCE,
                UNTIL,
                50,
                "",
            )
        else:
            items, truncated = _function(provider, "_gitlab_activity_resource")(
                client,
                "https://gitlab.example",
                "secret",
                "team/repo",
                "commits",
                datetime.fromisoformat(SINCE.replace("Z", "+00:00")),
                datetime.fromisoformat(UNTIL.replace("Z", "+00:00")),
                500,
            )
    assert len(items) == 121
    assert truncated is False


def test_github_closed_issue_is_included_even_if_updated_after_the_window():
    queries = []

    def respond(request):
        query = request.url.params["q"]
        queries.append(query)
        items = (
            [
                {
                    "number": 7,
                    "title": "Work finished in window",
                    "updated_at": "2026-10-09T08:00:00Z",
                    "closed_at": STAMP,
                }
            ]
            if " closed:" in query
            else []
        )
        return httpx.Response(200, json={"total_count": len(items), "incomplete_results": False, "items": items})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        items, truncated = _function("github", "_activity_issues")(client, "owner/repo", SINCE, UNTIL, 50, "")
    assert [item["number"] for item in items] == [7]
    assert len(queries) == 3
    assert truncated is False


def test_repeated_jira_pages_are_incomplete_even_if_total_is_reached_by_duplicate_rows():
    requests = []

    def respond(request):
        requests.append(request.url)
        return httpx.Response(
            200, json={"total": 200, "issues": [{"key": f"SL-{i}", "fields": {"updated": STAMP}} for i in range(100)]}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        items, truncated = _function("jira", "_jira_project_activity")(
            client,
            "https://jira.example",
            {},
            "SL",
            datetime.fromisoformat(SINCE.replace("Z", "+00:00")),
            datetime.fromisoformat(UNTIL.replace("Z", "+00:00")),
            500,
            ZoneInfo("UTC"),
        )
    assert len(items) == 100
    assert len(requests) == 2
    assert truncated is True


def test_jira_cloud_continues_short_pages_when_a_cursor_is_present():
    requests = []

    def respond(request):
        token = request.url.params.get("nextPageToken")
        requests.append(token)
        payload = {
            "issues": [{"key": "SL-2" if token else "SL-1", "fields": {"updated": STAMP}}],
            "isLast": bool(token),
        }
        if not token:
            payload["nextPageToken"] = "next"
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        items, truncated = _function("jira", "_jira_project_activity")(
            client,
            "https://tenant.atlassian.net",
            {},
            "SL",
            datetime.fromisoformat(SINCE.replace("Z", "+00:00")),
            datetime.fromisoformat(UNTIL.replace("Z", "+00:00")),
            500,
            ZoneInfo("UTC"),
        )
    assert len(items) == 2
    assert requests == [None, "next"]
    assert truncated is False


@pytest.mark.parametrize("resource", ["projects", "commits"])
def test_gitlab_later_page_failure_keeps_collected_evidence(resource):
    def respond(request):
        if request.url.params["page"] == "2":
            raise httpx.ConnectError("Unavailable", request=request)
        if resource == "projects":
            payload = [{"path_with_namespace": f"team/repo-{i}"} for i in range(100)]
        else:
            payload = [{"id": str(i), "committed_date": STAMP} for i in range(50)]
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if resource == "projects":
            items, truncated = _function("gitlab", "_all_activity_projects")(client, "https://gitlab.example", "secret")
        else:
            items, truncated = _function("gitlab", "_gitlab_activity_resource")(
                client,
                "https://gitlab.example",
                "secret",
                "team/repo",
                "commits",
                datetime.fromisoformat(SINCE.replace("Z", "+00:00")),
                datetime.fromisoformat(UNTIL.replace("Z", "+00:00")),
                500,
            )
    assert len(items) == (100 if resource == "projects" else 50)
    assert truncated is True


@pytest.mark.parametrize("provider", ["jira", "github"])
def test_invalid_later_page_keeps_first_page_and_marks_partial(provider):
    def respond(request):
        query = request.url.params
        first = query.get("startAt", "0") == "0" if provider == "jira" else query["page"] == "1"
        if not first:
            return httpx.Response(200, json={"unexpected": "shape"})
        if provider == "jira":
            payload = {"total": 200, "issues": [{"key": f"SL-{i}", "fields": {"updated": STAMP}} for i in range(100)]}
        else:
            payload = {
                "total_count": 100,
                "incomplete_results": False,
                "items": [{"number": i, "updated_at": STAMP} for i in range(50)],
            }
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if provider == "jira":
            items, truncated = _function(provider, "_jira_project_activity")(
                client,
                "https://jira.example",
                {},
                "SL",
                datetime.fromisoformat(SINCE.replace("Z", "+00:00")),
                datetime.fromisoformat(UNTIL.replace("Z", "+00:00")),
                500,
                ZoneInfo("UTC"),
            )
        else:
            items, truncated = _function(provider, "_activity_issues")(client, "owner/repo", SINCE, UNTIL, 50, "")
    assert len(items) == (100 if provider == "jira" else 50)
    assert truncated is True
