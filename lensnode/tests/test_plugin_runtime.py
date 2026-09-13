from types import SimpleNamespace

import httpx
import pytest

from lensnode.datasource_sync import DataSourceSyncError
from lensnode.main import LensNodeClient
from lensnode.plugin_runtime import (
    PluginRuntimeError,
    acquire_plugin_lease,
    create_plugin_tool_snapshot,
    fetch_plugin_snapshot,
    lease_url,
    retrieve_plugin_material,
    tool_snapshot_url,
)


def test_lease_url_replaces_ai_gateway_path():
    assert lease_url(
        "http://gateway/api/lens/lensnode/ai-gateway/"
    ) == "http://gateway/api/lens/plugin-runtime/leases/"


def test_tool_snapshot_url_replaces_ai_gateway_path():
    assert tool_snapshot_url(
        "http://gateway/api/lens/lensnode/ai-gateway/"
    ) == "http://gateway/api/lens/plugin-runtime/tool-snapshots/"


def test_create_plugin_tool_snapshot_uses_model_call_identity():
    class Client:
        def post(self, url, **kwargs):
            assert url.endswith("/plugin-runtime/tool-snapshots/")
            assert kwargs["json"] == {
                "run_uuid": "run-1",
                "connection_uuid": "connection-1",
                "tool_key": "github_read_file",
                "call_id": "call-1",
                "arguments": {
                    "repository": "owner/repository",
                    "path": "README.md",
                },
            }
            assert kwargs["headers"]["Authorization"] == "Bearer node-token"
            return httpx.Response(
                201,
                json={
                    "snapshot_uuid": "snapshot-1",
                    "run_uuid": "run-1",
                    "connection_uuid": "connection-1",
                    "tool_key": "github_read_file",
                    "invocation_id": "call-1",
                    "plugin_key": "github",
                },
            )

    result = create_plugin_tool_snapshot(
        Client(),
        "http://gateway/api/lens/lensnode/ai-gateway/",
        "node-token",
        "run-1",
        "connection-1",
        "github_read_file",
        "call-1",
        {
            "repository": "owner/repository",
            "path": "README.md",
        },
    )

    assert result["snapshot_uuid"] == "snapshot-1"
    assert result["invocation_id"] == "call-1"


def test_create_plugin_tool_snapshot_rejects_malformed_control_response():
    class Client:
        def post(self, _url, **_kwargs):
            return httpx.Response(201, text="not-json")

    with pytest.raises(PluginRuntimeError) as exc_info:
        create_plugin_tool_snapshot(
            Client(),
            "http://gateway/api/lens/lensnode/ai-gateway/",
            "node-token",
            "run-1",
            "connection-1",
            "github_read_file",
            "call-1",
            {"repository": "owner/repository", "path": "README.md"},
        )

    assert str(exc_info.value) == "PLUGIN_TOOL_SNAPSHOT_INVALID_RESPONSE"


def test_acquire_plugin_lease_returns_opaque_metadata():
    request = httpx.Request(
        "POST",
        "http://gateway/api/lens/plugin-runtime/leases",
    )
    response = httpx.Response(
        200,
        json={
            "lease_uuid": "lease-1",
            "snapshot_uuid": "snapshot-1",
            "expires_at": "2099-01-01T00:00:00Z",
        },
        request=request,
    )

    class Client:
        def post(self, url, **kwargs):
            assert url.endswith("/plugin-runtime/leases/")
            assert kwargs["json"] == {"snapshot_uuid": "snapshot-1"}
            assert "secret" not in kwargs
            return response

    result = acquire_plugin_lease(
        Client(),
        "http://gateway/api/lens/lensnode/ai-gateway/",
        "node-token",
        "snapshot-1",
    )
    assert result["lease_uuid"] == "lease-1"
    assert "access_token" not in result


def test_retrieve_plugin_material_uses_snapshot_lease_path():
    class Client:
        def post(self, url, **kwargs):
            assert url.endswith("/plugin-runtime/leases/lease-1/material/")
            assert "json" not in kwargs
            return httpx.Response(200, json={"value": "secret"})

    result = retrieve_plugin_material(
        Client(),
        "http://gateway/api/lens/lensnode/ai-gateway/",
        "node-token",
        "lease-1",
    )
    assert result["value"] == "secret"


def test_fetch_plugin_snapshot_returns_non_sensitive_config():
    class Client:
        def get(self, url, **kwargs):
            assert url.endswith("/plugin-runtime/snapshots/snapshot-1/")
            assert kwargs["headers"]["Authorization"] == "Bearer node-token"
            return httpx.Response(
                200,
                json={
                    "snapshot_uuid": "snapshot-1",
                    "resolved_config": {"target_path": "/workspace/repo"},
                },
            )

    result = fetch_plugin_snapshot(
        Client(),
        "http://gateway/api/lens/lensnode/ai-gateway/",
        "node-token",
        "snapshot-1",
    )
    assert result["resolved_config"]["target_path"] == "/workspace/repo"


def test_plugin_runtime_unwraps_control_plane_response_envelope():
    class Client:
        def post(self, url, **kwargs):
            if url.endswith("/plugin-runtime/leases/"):
                return httpx.Response(
                    201,
                    json={
                        "code": 0,
                        "message": "success",
                        "data": {
                            "lease_uuid": "lease-1",
                            "snapshot_uuid": "snapshot-1",
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {
                        "value": "secret",
                        "plugin_key": "github",
                        "endpoint": "https://github.com",
                    },
                },
            )

        def get(self, _url, **_kwargs):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {
                        "resolved_config": {"target_path": "/workspace/repo"}
                    },
                },
            )

    client = Client()
    lease = acquire_plugin_lease(
        client,
        "http://gateway/api/lens/lensnode/ai-gateway/",
        "node-token",
        "snapshot-1",
    )
    material = retrieve_plugin_material(
        client,
        "http://gateway/api/lens/lensnode/ai-gateway/",
        "node-token",
        lease["lease_uuid"],
    )
    snapshot = fetch_plugin_snapshot(
        client,
        "http://gateway/api/lens/lensnode/ai-gateway/",
        "node-token",
        "snapshot-1",
    )

    assert lease["lease_uuid"] == "lease-1"
    assert material["value"] == "secret"
    assert snapshot["resolved_config"]["target_path"] == "/workspace/repo"


def test_plugin_sync_does_not_fallback_to_legacy_credentials(monkeypatch):
    client = LensNodeClient.__new__(LensNodeClient)
    client.config = SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
        workspace_path="/workspace",
    )
    client.gateway_http_client = object()
    monkeypatch.setattr(
        "lensnode.main.acquire_plugin_lease",
        lambda *args: {"lease_uuid": "lease-1"},
    )
    monkeypatch.setattr(
        "lensnode.main.fetch_plugin_snapshot",
        lambda *args: {
            "plugin_key": "github",
            "plugin_version": "1.0.0",
            "datasource_uuid": "datasource-1",
            "resolved_config": {
                "endpoint": "https://github.com",
                "connection_scope": {"repositories": ["owner/repo"]},
                "target_path": "/workspace/repo",
                "datasource_config": {
                    "repository": "owner/repo",
                    "directory": "docs",
                },
            },
        },
    )
    monkeypatch.setattr(
        "lensnode.main.retrieve_plugin_material",
        lambda *args: {
            "plugin_key": "github",
            "endpoint": "https://github.com",
            "value": "secret",
        },
    )
    seen = {}

    def fake_sync(command, workspace, emit):
        seen["token"] = command["config"].get("access_token")
        seen["directory"] = command["config"].get("directory")
        seen["target_path"] = command["target_path"]
        seen["allow_submodules"] = command["config"].get(
            "allow_submodules"
        )
        return {"status": "success"}

    monkeypatch.setattr("lensnode.main.sync_datasource", fake_sync)

    result = client._execute_plugin_datasource_sync(
        {"snapshot_uuid": "snapshot-1", "access_token": "must-not-use"}
    )
    assert result["status"] == "success"
    assert seen["token"] == "secret"
    assert seen["directory"] == "docs"
    assert seen["target_path"] == "/workspace/repo"
    assert seen["allow_submodules"] is False


def test_plugin_sync_rejects_material_for_another_endpoint(monkeypatch):
    client = LensNodeClient.__new__(LensNodeClient)
    client.config = SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
        workspace_path="/workspace",
    )
    client.gateway_http_client = object()
    monkeypatch.setattr(
        "lensnode.main.fetch_plugin_snapshot",
        lambda *args: {
            "plugin_key": "github",
            "plugin_version": "1.0.0",
            "resolved_config": {
                "endpoint": "https://github.com",
                "target_path": "/workspace/repo",
                "datasource_config": {"repository": "owner/repo"},
            },
        },
    )
    monkeypatch.setattr(
        "lensnode.main.acquire_plugin_lease",
        lambda *args: {"lease_uuid": "lease-1"},
    )
    material = {
        "plugin_key": "github",
        "endpoint": "https://evil.example",
        "value": "secret",
    }
    monkeypatch.setattr(
        "lensnode.main.retrieve_plugin_material",
        lambda *args: material,
    )

    result = client._execute_plugin_datasource_sync(
        {"snapshot_uuid": "snapshot-1"}
    )

    assert result["error"] == "PLUGIN_MATERIAL_MISMATCH"
    assert material["value"] == ""


def test_plugin_sync_releases_material_for_malformed_snapshot(monkeypatch):
    client = LensNodeClient.__new__(LensNodeClient)
    client.config = SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
        workspace_path="/workspace",
    )
    client.gateway_http_client = object()
    monkeypatch.setattr(
        "lensnode.main.fetch_plugin_snapshot",
        lambda *args: {
            "plugin_key": "github",
            "plugin_version": "1.0.0",
            "resolved_config": [],
        },
    )
    monkeypatch.setattr(
        "lensnode.main.acquire_plugin_lease",
        lambda *args: {"lease_uuid": "lease-1"},
    )
    material = {
        "plugin_key": "github",
        "endpoint": "https://github.com",
        "value": "secret",
    }
    monkeypatch.setattr(
        "lensnode.main.retrieve_plugin_material",
        lambda *args: material,
    )

    result = client._execute_plugin_datasource_sync(
        {"snapshot_uuid": "snapshot-1"}
    )

    assert result["error"] == "PLUGIN_CONFIG_INVALID"
    assert material["value"] == ""


def test_plugin_sync_reports_cancellation(monkeypatch):
    """Plugin datasource cancellation is exposed as a stable terminal state."""

    client = LensNodeClient.__new__(LensNodeClient)
    client.config = SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
        workspace_path="/workspace",
    )
    client.gateway_http_client = object()
    monkeypatch.setattr(
        "lensnode.main.fetch_plugin_snapshot",
        lambda *args: {
            "plugin_key": "github",
            "plugin_version": "1.0.0",
            "resolved_config": {
                "endpoint": "https://github.com",
                "connection_scope": {"repositories": ["owner/repo"]},
                "datasource_config": {"repository": "owner/repo"},
            },
        },
    )
    monkeypatch.setattr(
        "lensnode.main.acquire_plugin_lease",
        lambda *args: {"lease_uuid": "lease-1"},
    )
    material = {
        "plugin_key": "github",
        "endpoint": "https://github.com",
        "value": "secret",
    }
    monkeypatch.setattr(
        "lensnode.main.retrieve_plugin_material",
        lambda *args: material,
    )
    monkeypatch.setattr(
        "lensnode.main.sync_datasource",
        lambda *args: (_ for _ in ()).throw(
            DataSourceSyncError("LENS_SOURCE_SYNC_CANCELLED")
        ),
    )

    result = client._execute_plugin_datasource_sync(
        {"snapshot_uuid": "snapshot-1"}
    )

    assert result["status"] == "cancelled"
    assert result["error"] == "DATASOURCE_SYNC_CANCELLED"
    assert material["value"] == ""


def test_plugin_sync_rejects_material_for_another_plugin(monkeypatch):
    client = LensNodeClient.__new__(LensNodeClient)
    client.config = SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
    )
    client.gateway_http_client = object()
    monkeypatch.setattr(
        "lensnode.main.fetch_plugin_snapshot",
        lambda *args: {
            "plugin_key": "github",
            "plugin_version": "1.0.0",
            "resolved_config": {
                "endpoint": "https://github.com",
                "datasource_config": {},
            },
        },
    )
    monkeypatch.setattr(
        "lensnode.main.acquire_plugin_lease",
        lambda *args: {"lease_uuid": "lease-1"},
    )
    material = {
        "plugin_key": "gitlab",
        "endpoint": "https://github.com",
        "value": "secret",
    }
    monkeypatch.setattr(
        "lensnode.main.retrieve_plugin_material",
        lambda *args: material,
    )

    result = client._execute_plugin_datasource_sync(
        {"snapshot_uuid": "snapshot-1"}
    )

    assert result["error"] == "PLUGIN_MATERIAL_MISMATCH"
    assert material["value"] == ""


def test_gitlab_plugin_sync_builds_git_command_from_snapshot(monkeypatch):
    client = LensNodeClient.__new__(LensNodeClient)
    client.config = SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
        workspace_path="/workspace",
    )
    client.gateway_http_client = object()
    monkeypatch.setattr(
        "lensnode.main.fetch_plugin_snapshot",
        lambda *args: {
            "plugin_key": "gitlab",
            "plugin_version": "1.0.0",
            "datasource_uuid": "datasource-1",
            "resolved_config": {
                "endpoint": "https://gitlab.internal.example",
                "target_path": "/workspace/repo",
                "connection_scope": {
                    "projects": ["platform/backend/sourcelens"]
                },
                "datasource_config": {
                    "project": "platform/backend/sourcelens",
                    "branch": "main",
                    "directory": "docs",
                },
            },
        },
    )
    monkeypatch.setattr(
        "lensnode.main.acquire_plugin_lease",
        lambda *args: {"lease_uuid": "lease-1"},
    )
    material = {
        "plugin_key": "gitlab",
        "endpoint": "https://gitlab.internal.example",
        "value": "gitlab-secret",
    }
    monkeypatch.setattr(
        "lensnode.main.retrieve_plugin_material",
        lambda *args: material,
    )
    seen = {}

    def fake_sync(command, workspace, emit):
        seen.update(command)
        return {"status": "success"}

    monkeypatch.setattr("lensnode.main.sync_datasource", fake_sync)

    result = client._execute_plugin_datasource_sync(
        {"snapshot_uuid": "snapshot-1"}
    )

    assert result["status"] == "success"
    assert seen["config"]["repositories"] == [
        {
            "repo_url": (
                "https://gitlab.internal.example/"
                "platform/backend/sourcelens.git"
            ),
            "branch": "main",
            "directory": "docs",
            "target_subdir": "platform/backend/sourcelens",
            "enabled": True,
        }
    ]
    assert seen["config"]["access_token"] == "gitlab-secret"
    assert material["value"] == ""


def test_tool_only_plugin_rejects_datasource_sync(monkeypatch):
    client = LensNodeClient.__new__(LensNodeClient)
    client.config = SimpleNamespace(
        ai_gateway_url="http://gateway/api/lens/lensnode/ai-gateway/",
        token="node-token",
        workspace_path="/workspace",
    )
    client.gateway_http_client = object()
    monkeypatch.setattr(
        "lensnode.main.fetch_plugin_snapshot",
        lambda *args: {
            "plugin_key": "jira",
            "plugin_version": "1.0.0",
        },
    )
    monkeypatch.setattr(
        "lensnode.main.acquire_plugin_lease",
        lambda *args: pytest.fail("tool-only Plugin must not acquire a lease"),
    )
    monkeypatch.setattr(
        "lensnode.main.retrieve_plugin_material",
        lambda *args: pytest.fail(
            "tool-only Plugin must not retrieve secret material"
        ),
    )
    monkeypatch.setattr(
        "lensnode.main.load_runtime_contract",
        lambda *args: SimpleNamespace(build_datasource_command=None),
    )

    result = client._execute_plugin_datasource_sync(
        {"snapshot_uuid": "snapshot-1"}
    )

    assert result == {
        "status": "failed",
        "error": "PLUGIN_DATASOURCE_UNSUPPORTED",
    }
