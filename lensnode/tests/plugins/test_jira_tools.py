import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx

from lensnode.plugin_package_loader import load_runtime_contract
from lensnode.plugin_tools import build_plugin_tools


JIRA_RUNTIME = load_runtime_contract("jira", "1.0.0")


def _command(tool_key):
    definitions = {
        "jira_get_issue": {
            "key": "jira_get_issue",
            "description": "Read one authorized Jira Issue.",
            "capability": "issue.read",
            "side_effect": "none",
            "input_schema": {
                "type": "object",
                "properties": {"issue_key": {"type": "string"}},
                "required": ["issue_key"],
            },
        },
        "jira_search_issues": {
            "key": "jira_search_issues",
            "description": "Search one authorized Jira project.",
            "capability": "jira.issue.search",
            "side_effect": "none",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["project", "query"],
            },
        },
        "jira_activity_summary": {
            "key": "jira_activity_summary",
            "description": "Summarize authorized Jira activity.",
            "capability": "jira.issue.search",
            "side_effect": "none",
            "input_schema": {
                "type": "object",
                "properties": {
                    "projects": {"type": "array"},
                    "since": {"type": "string"},
                    "until": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["projects", "since", "until"],
            },
        },
    }
    return {
        "run_uuid": "run-1",
        "loaded_plugins": [
            {
                "connection_uuid": "connection-1",
                "plugin_key": "jira",
                "plugin_version": "1.0.0",
                "protocol_version": 1,
                "tools": [definitions[tool_key]],
            }
        ],
    }


class JiraRuntimeClient:
    """Provide control-plane and Jira responses."""

    def __init__(self, allowed_projects=None):
        self.allowed_projects = allowed_projects or ["SL", "OPS"]
        self.provider_requests = []

    def post(self, url, **kwargs):
        if url.endswith("/plugin-runtime/tool-snapshots/"):
            payload = kwargs["json"]
            return httpx.Response(
                201,
                json={
                    "snapshot_uuid": f"snapshot-{payload['call_id']}",
                    "run_uuid": payload["run_uuid"],
                    "connection_uuid": payload["connection_uuid"],
                    "tool_key": payload["tool_key"],
                    "invocation_id": payload["call_id"],
                    "plugin_key": "jira",
                    "plugin_version": "1.0.0",
                },
            )
        if url.endswith("/plugin-runtime/leases/"):
            return httpx.Response(
                201,
                json={
                    "lease_uuid": "lease-1",
                    "snapshot_uuid": kwargs["json"]["snapshot_uuid"],
                },
            )
        if url.endswith("/material/"):
            return httpx.Response(
                200,
                json={
                    "plugin_key": "jira",
                    "endpoint": "https://company.atlassian.net",
                    "value": "jira-api-token",
                },
            )
        raise AssertionError(url)

    def get(self, url, **kwargs):
        if "/plugin-runtime/snapshots/" not in url:
            raise AssertionError(url)
        snapshot_uuid = url.rstrip("/").rsplit("/", 1)[-1]
        if "activity" in snapshot_uuid:
            tool_key = "jira_activity_summary"
            arguments = {
                "projects": ["SL", "OPS"],
                "since": "2026-09-01T00:00:00Z",
                "until": "2026-09-01T23:59:59Z",
                "max_results": 2,
            }
        elif "search" in snapshot_uuid:
            tool_key = "jira_search_issues"
            arguments = {
                "project": "SL",
                "query": "plugin",
                "max_results": 10,
            }
        else:
            tool_key = "jira_get_issue"
            arguments = {"issue_key": "SL-488"}
        return httpx.Response(
            200,
            json={
                "snapshot_uuid": snapshot_uuid,
                "run_uuid": "run-1",
                "tool_key": tool_key,
                "invocation_id": snapshot_uuid.removeprefix("snapshot-"),
                "plugin_key": "jira",
                "plugin_version": "1.0.0",
                "resolved_config": {
                    "endpoint": "https://company.atlassian.net",
                    "connection_config": {"email": "admin@example.com"},
                    "allowed_scope": {
                        "projects": self.allowed_projects,
                    },
                    "arguments": arguments,
                },
            },
        )

    @contextmanager
    def stream(self, method, url, **kwargs):
        assert method == "GET"
        authorization = kwargs["headers"]["Authorization"]
        assert authorization.startswith("Basic ")
        assert "jira-api-token" not in authorization
        self.provider_requests.append((url, kwargs.get("params") or {}))
        if url.endswith("/myself"):
            yield httpx.Response(
                200,
                json={"timeZone": "Asia/Shanghai"},
            )
            return
        if "/issue/SL-488" in url:
            yield httpx.Response(
                200,
                json={
                    "key": "SL-488",
                    "fields": {
                        "summary": "Plugin design",
                        "status": {"name": "In Progress"},
                    },
                },
            )
            return
        if url.endswith("/search/jql"):
            jql = kwargs["params"]["jql"]
            if 'project = "OPS"' in jql:
                yield httpx.Response(500)
                return
            assert jql.startswith('project = "SL"')
            if "updated >=" in jql:
                yield httpx.Response(
                    200,
                    json={
                        "issues": [
                            {
                                "key": "SL-488",
                                "fields": {
                                    "summary": "Plugin activity report",
                                    "status": {"name": "In Progress"},
                                    "assignee": {
                                        "displayName": "Zheng Wei",
                                    },
                                    "description": {
                                        "type": "doc",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [
                                                    {
                                                        "type": "text",
                                                        "text": "x" * 3000,
                                                    }
                                                ],
                                            }
                                        ],
                                    },
                                    "updated": "2026-09-01T10:00:00Z",
                                },
                            },
                            {
                                "key": "SL-400",
                                "fields": {
                                    "summary": "Outside window",
                                    "updated": "2026-09-02T00:00:00Z",
                                },
                            },
                        ]
                    },
                )
                return
            yield httpx.Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "SL-488",
                            "fields": {
                                "summary": "Plugin design",
                                "status": {"name": "In Progress"},
                            },
                        }
                    ]
                },
            )
            return
        raise AssertionError(url)


def test_jira_runtime_accepts_cloud_and_self_hosted_http_origins():
    assert JIRA_RUNTIME.http_origins(
        "https://company.atlassian.net/"
    ) == ("https://company.atlassian.net",)
    assert JIRA_RUNTIME.http_origins(
        "http://office.oneprocloud.com.cn:9005/"
    ) == ("http://office.oneprocloud.com.cn:9005",)


def test_jira_runtime_uses_v2_api_for_self_hosted_instance():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "key": "SL-488",
                "fields": {"summary": "Plugin design"},
            },
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = JIRA_RUNTIME.execute_tool(
            "jira_get_issue",
            client,
            {"issue_key": "SL-488"},
            "jira-api-token",
            "http://office.oneprocloud.com.cn:9005",
            {"email": "jira-admin"},
        )

    assert result["ok"] is True
    assert seen == [
        "http://office.oneprocloud.com.cn:9005/rest/api/2/issue/SL-488"
        "?fields=summary%2Cstatus%2Cassignee%2Cpriority%2Cupdated%2Cdescription"
    ]


def _config():
    return SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
    )


def test_jira_get_issue_uses_snapshot_connection_config_and_lease():
    tool = build_plugin_tools(
        _command("jira_get_issue"),
        _config(),
        JiraRuntimeClient(),
    )[0]

    result = json.loads(tool.func(
        issue_key="SL-488",
        runtime=SimpleNamespace(tool_call_id="read-1"),
    ))

    assert result["ok"] is True
    assert result["issue"]["key"] == "SL-488"
    assert "jira-api-token" not in json.dumps(result)
    assert tool.metadata["capability_family"] == "plugin"
    assert tool.metadata["plugin_key"] == "jira"
    assert tool.metadata["capability"] == "issue.read"


def test_jira_search_issues_builds_project_bounded_jql():
    tool = build_plugin_tools(
        _command("jira_search_issues"),
        _config(),
        JiraRuntimeClient(),
    )[0]

    result = json.loads(tool.func(
        project="SL",
        query="plugin",
        max_results=10,
        runtime=SimpleNamespace(tool_call_id="search-1"),
    ))

    assert result["ok"] is True
    assert result["items"][0]["key"] == "SL-488"


def test_jira_activity_summary_uses_python_http_for_all_projects():
    client = JiraRuntimeClient()
    tool = build_plugin_tools(
        _command("jira_activity_summary"),
        _config(),
        client,
    )[0]

    result = json.loads(tool.func(
        projects=["SL", "OPS"],
        since="2026-09-01T00:00:00Z",
        until="2026-09-01T23:59:59Z",
        max_results=2,
        runtime=SimpleNamespace(tool_call_id="activity-1"),
    ))

    assert result["ok"] is True
    assert len(client.provider_requests) == 3
    search_requests = [
        params
        for url, params in client.provider_requests
        if url.endswith("/search/jql")
    ]
    assert ('updated >= "2026-09-01 08:00" ' 'AND updated < "2026-09-02 08:00"') in search_requests[0]["jql"]
    assert 'resolved >= "2026-09-01 08:00"' in search_requests[0]["jql"]
    assert result["items"][0]["key"] == "SL-488"
    assert len(result["items"][0]["description"]) == 2000
    assert result["possibly_truncated"] is True
    assert result["errors"] == {"OPS": "JIRA_REQUEST_FAILED"}
    assert "jira-api-token" not in json.dumps(result)


def test_jira_activity_summary_rechecks_frozen_project_scope():
    client = JiraRuntimeClient(allowed_projects=["SL"])
    tool = build_plugin_tools(
        _command("jira_activity_summary"),
        _config(),
        client,
    )[0]

    result = json.loads(tool.func(
        projects=["SL", "OPS"],
        since="2026-09-01T00:00:00Z",
        until="2026-09-01T23:59:59Z",
        max_results=2,
        runtime=SimpleNamespace(tool_call_id="activity-scope-1"),
    ))

    assert result == {"ok": False, "error": "PLUGIN_SCOPE_MISMATCH"}
    assert client.provider_requests == []
