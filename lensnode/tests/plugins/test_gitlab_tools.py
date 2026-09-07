import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx

from lensnode.plugin_tools import build_plugin_tools
from lensnode.plugin_package_loader import load_runtime_contract


GITLAB_RUNTIME = load_runtime_contract("gitlab", "1.0.0")


def test_builds_multi_project_datasource_command():
    command = GITLAB_RUNTIME.build_datasource_command(
        {
            "datasource_uuid": "ds-1",
            "resolved_config": {
                "endpoint": "https://gitlab.example.com",
                "connection_scope": {
                    "projects": ["platform/a", "platform/b"]
                },
                "datasource_config": {
                    "projects": ["platform/a", "platform/b"],
                    "branch": "main",
                },
                "target_path": "/workspace/repos",
                "sync_policy": {},
            },
        },
        {
            "plugin_key": "gitlab",
            "endpoint": "https://gitlab.example.com",
            "value": "secret",
        },
        "manual",
    )

    assert [item["repo_url"] for item in command["config"]["repositories"]] == [
        "https://gitlab.example.com/platform/a.git",
        "https://gitlab.example.com/platform/b.git",
    ]
    assert [
        item["target_subdir"] for item in command["config"]["repositories"]
    ] == ["platform/a", "platform/b"]
    assert command["config"]["allow_submodules"] is True
    assert callable(GITLAB_RUNTIME.sync_datasource)


def test_gitlab_datasource_handler_owns_submodule_sync():
    """GitLab injects its submodule behavior before executing Git sync."""

    command = {"config": {"gitlab_endpoint": "http://gitlab.example"}}
    seen = {}

    def execute(value, workspace_path, emit):
        seen.update(value)
        assert workspace_path == "/workspace"
        assert emit is None
        return {"status": "success"}

    result = GITLAB_RUNTIME.sync_datasource(
        command,
        "/workspace",
        None,
        execute,
    )

    assert result == {"status": "success"}
    assert callable(seen["config"]["submodule_url_resolver"])


def test_gitlab_submodule_resolver_rewrites_ssh_origins(tmp_path):
    """GitLab owns SSH-to-HTTP submodule origin translation."""

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitmodules").write_text(
        "[submodule \"api\"]\n"
        "\tpath = api\n"
        "\turl = ssh://git@office.internal:20022/team/api.git\n"
        "[submodule \"web\"]\n"
        "\tpath = web\n"
        "\turl = git@office.internal:team/web.git\n",
        encoding="utf-8",
    )

    resolver = GITLAB_RUNTIME.build_datasource_command.__globals__[
        "_gitlab_submodule_url_rewrites"
    ]

    assert resolver(repo, {"gitlab_endpoint": "http://gitlab.example"}) == [
        {
            "from": "ssh://git@office.internal:20022/",
            "to": "http://gitlab.example/",
        },
        {
            "from": "git@office.internal:",
            "to": "http://gitlab.example/",
        },
    ]


def test_builds_single_project_as_repository_collection():
    command = GITLAB_RUNTIME.build_datasource_command(
        {
            "datasource_uuid": "ds-1",
            "resolved_config": {
                "endpoint": "https://gitlab.example.com",
                "connection_scope": {"projects": ["platform/a"]},
                "datasource_config": {
                    "project": "platform/a",
                    "branch": "main",
                },
                "target_path": "/workspace/repos",
                "sync_policy": {},
            },
        },
        {
            "plugin_key": "gitlab",
            "endpoint": "https://gitlab.example.com",
            "value": "secret",
        },
        "manual",
    )

    assert "repo_url" not in command["config"]
    assert command["config"]["repositories"] == [
        {
            "repo_url": "https://gitlab.example.com/platform/a.git",
            "branch": "main",
            "directory": "",
            "target_subdir": "platform/a",
            "enabled": True,
        }
    ]


def test_multi_project_datasource_resolves_each_default_branch():
    command = GITLAB_RUNTIME.build_datasource_command(
        {
            "datasource_uuid": "ds-1",
            "resolved_config": {
                "endpoint": "https://gitlab.example.com",
                "connection_scope": {"projects": ["platform/a"]},
                "datasource_config": {"projects": ["platform/a"]},
                "target_path": "/workspace/repos",
                "sync_policy": {},
            },
        },
        {
            "plugin_key": "gitlab",
            "endpoint": "https://gitlab.example.com",
            "value": "secret",
        },
        "manual",
    )

    assert command["config"]["branch"] == ""
    assert command["config"]["repositories"][0]["branch"] == ""


def _command(tool_key):
    definitions = {
        "gitlab_read_file": {
            "key": "gitlab_read_file",
            "description": "Read one authorized GitLab project file.",
            "capability": "repository.read",
            "side_effect": "none",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "path": {"type": "string"},
                    "ref": {"type": "string"},
                },
                "required": ["project", "path"],
            },
        },
        "gitlab_search_code": {
            "key": "gitlab_search_code",
            "description": "Search one authorized GitLab project.",
            "capability": "repository.read",
            "side_effect": "none",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "ref": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["project", "query"],
            },
        },
        "gitlab_activity_summary": {
            "key": "gitlab_activity_summary",
            "description": "Summarize authorized GitLab activity.",
            "capability": "repository.read",
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
                "plugin_key": "gitlab",
                "plugin_version": "1.0.0",
                "protocol_version": 1,
                "tools": [definitions[tool_key]],
            }
        ],
    }


class GitLabRuntimeClient:
    """Provide control-plane and GitLab responses without real secrets."""

    def __init__(
        self,
        endpoint="https://gitlab.example",
        allowed_projects=None,
    ):
        self.endpoint = endpoint
        self.allowed_projects = allowed_projects or [
            "platform/sourcelens",
            "platform/ops",
        ]
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
                    "plugin_key": "gitlab",
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
                    "plugin_key": "gitlab",
                    "endpoint": self.endpoint,
                    "value": "gitlab-secret",
                },
            )
        raise AssertionError(url)

    def get(self, url, **kwargs):
        if "/plugin-runtime/snapshots/" in url:
            snapshot_uuid = url.rstrip("/").rsplit("/", 1)[-1]
            if "activity" in snapshot_uuid:
                tool_key = "gitlab_activity_summary"
                arguments = {
                    "projects": [
                        "platform/sourcelens",
                        "platform/ops",
                    ],
                    "since": "2026-09-01T00:00:00Z",
                    "until": "2026-09-01T23:59:59Z",
                    "max_results": 2,
                }
            elif "search" in snapshot_uuid:
                tool_key = "gitlab_search_code"
                arguments = {
                    "project": "platform/sourcelens",
                    "query": "PluginRuntime",
                    "max_results": 2,
                }
            else:
                tool_key = "gitlab_read_file"
                arguments = {
                    "project": "platform/sourcelens",
                    "path": "README.md",
                    "ref": "main",
                }
            return httpx.Response(
                200,
                json={
                    "snapshot_uuid": snapshot_uuid,
                    "run_uuid": "run-1",
                    "tool_key": tool_key,
                    "invocation_id": snapshot_uuid.removeprefix("snapshot-"),
                    "plugin_key": "gitlab",
                    "plugin_version": "1.0.0",
                    "resolved_config": {
                        "endpoint": self.endpoint,
                        "connection_config": {},
                        "allowed_scope": {
                            "projects": self.allowed_projects,
                        },
                        "arguments": arguments,
                    },
                },
            )
        raise AssertionError(url)

    @contextmanager
    def stream(self, method, url, **kwargs):
        assert method == "GET"
        assert kwargs["headers"]["PRIVATE-TOKEN"] == "gitlab-secret"
        self.provider_requests.append((url, kwargs.get("params") or {}))
        if url.endswith("/raw"):
            yield httpx.Response(200, text="# GitLab project\n")
            return
        if url.endswith("/search"):
            yield httpx.Response(
                200,
                json=[
                    {
                        "filename": "plugin_tools.py",
                        "path": "lensnode/plugin_tools.py",
                        "ref": "main",
                    }
                ],
            )
            return
        if "/repository/commits" in url:
            yield httpx.Response(
                200,
                json=[
                    {
                        "id": "a" * 40,
                        "title": "Implement activity summary",
                        "author_name": "Zheng Wei",
                        "authored_date": "2026-09-01T10:00:00Z",
                        "committed_date": "2026-09-01T10:01:00Z",
                        "web_url": f"{self.endpoint}/commit/one",
                    },
                    {
                        "id": "b" * 40,
                        "title": "Outside window",
                        "committed_date": "2026-09-02T00:00:00Z",
                    },
                ],
            )
            return
        if url.endswith("/merge_requests"):
            yield httpx.Response(
                200,
                json=[
                    {
                        "iid": 12,
                        "title": "Ship activity summary",
                        "description": "x" * 3000,
                        "state": "merged",
                        "author": {
                            "username": "zhengwei",
                            "name": "Zheng Wei",
                        },
                        "updated_at": "2026-09-01T12:00:00Z",
                        "merged_at": "2026-09-01T12:01:00Z",
                    }
                ],
            )
            return
        if url.endswith("/issues"):
            if "platform%2Fops" in url:
                yield httpx.Response(500)
                return
            yield httpx.Response(
                200,
                json=[
                    {
                        "iid": 99,
                        "title": "Track report coverage",
                        "state": "opened",
                        "updated_at": "2026-09-01T13:00:00Z",
                    }
                ],
            )
            return
        raise AssertionError(url)


def _config():
    return SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
    )


def test_gitlab_read_file_uses_snapshot_endpoint_and_lease():
    tool = build_plugin_tools(
        _command("gitlab_read_file"),
        _config(),
        GitLabRuntimeClient(),
    )[0]

    result = json.loads(tool.func(
        project="platform/sourcelens",
        path="README.md",
        ref="main",
        runtime=SimpleNamespace(tool_call_id="read-1"),
    ))

    assert result["ok"] is True
    assert result["content"] == "# GitLab project\n"
    assert "gitlab-secret" not in json.dumps(result)


def test_gitlab_search_code_returns_only_bounded_metadata():
    tool = build_plugin_tools(
        _command("gitlab_search_code"),
        _config(),
        GitLabRuntimeClient(),
    )[0]

    result = json.loads(tool.func(
        project="platform/sourcelens",
        query="PluginRuntime",
        path="",
        ref="",
        max_results=2,
        runtime=SimpleNamespace(tool_call_id="search-1"),
    ))

    assert result == {
        "ok": True,
        "project": "platform/sourcelens",
        "query": "PluginRuntime",
        "items": [
            {
                "name": "plugin_tools.py",
                "path": "lensnode/plugin_tools.py",
                "ref": "main",
            }
        ],
    }


def test_gitlab_activity_summary_uses_python_http_for_all_projects():
    client = GitLabRuntimeClient(
        endpoint="http://gitlab.internal.example:8080",
    )
    tool = build_plugin_tools(
        _command("gitlab_activity_summary"),
        _config(),
        client,
    )[0]

    result = json.loads(tool.func(
        projects=["platform/sourcelens", "platform/ops"],
        since="2026-09-01T00:00:00Z",
        until="2026-09-01T23:59:59Z",
        max_results=2,
        runtime=SimpleNamespace(tool_call_id="activity-1"),
    ))

    assert result["ok"] is True
    assert len(client.provider_requests) == 6
    assert all(
        url.startswith("http://gitlab.internal.example:8080/api/v4/")
        for url, _params in client.provider_requests
    )
    sourcelens = result["projects"][0]
    assert len(sourcelens["commits"]) == 1
    assert len(sourcelens["merge_requests"][0]["description"]) == 2000
    assert sourcelens["possibly_truncated"]["commits"] is True
    assert result["projects"][1]["errors"] == {
        "issues": "GITLAB_REQUEST_FAILED",
    }
    assert "gitlab-secret" not in json.dumps(result)


def test_gitlab_activity_summary_rechecks_frozen_project_scope():
    client = GitLabRuntimeClient(
        allowed_projects=["platform/sourcelens"],
    )
    tool = build_plugin_tools(
        _command("gitlab_activity_summary"),
        _config(),
        client,
    )[0]

    result = json.loads(tool.func(
        projects=["platform/sourcelens", "platform/ops"],
        since="2026-09-01T00:00:00Z",
        until="2026-09-01T23:59:59Z",
        max_results=2,
        runtime=SimpleNamespace(tool_call_id="activity-scope-1"),
    ))

    assert result == {"ok": False, "error": "PLUGIN_SCOPE_MISMATCH"}
    assert client.provider_requests == []
