import ssl
from types import SimpleNamespace

import httpx

from lensnode.runtime_resources import (
    _download_skill_package,
    _materialize_mcp,
)


def test_skill_package_download_uses_configured_tls_context(monkeypatch):
    captured = {}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"package")
    )
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        captured.update(kwargs)
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(
        "lensnode.runtime_resources.httpx.Client",
        fake_client,
    )
    config = SimpleNamespace(
        ai_gateway_url="https://server.example/api/lens/lensnode/ai-gateway/",
        token="token",
        request_timeout_s=30,
        tls_skip_verify=True,
        tls_ca_file=None,
    )

    package = _download_skill_package(
        config,
        {
            "skill_uuid": "11111111-1111-1111-1111-111111111111",
            "package_hash": "sha256:abc",
        },
    )

    assert package == b"package"
    assert captured["verify"].verify_mode == ssl.CERT_NONE


def test_legacy_oauth_values_are_not_added_to_runtime_config(tmp_path):
    token = "obsolete-user-token"
    mcp = {
        "mcp_uuid": "11111111-1111-1111-1111-111111111111",
        "content_hash": "sha256:abc",
        "mcp_name": "identity-mcp",
        "transport": "url",
        "endpoint": "https://mcp.example/tools",
        "config": {},
        "oauth_enabled": True,
        "oauth_access_token": token,
    }

    runtime_config = _materialize_mcp(tmp_path, mcp)

    assert "oauth_enabled" not in runtime_config
    assert "oauth_access_token" not in runtime_config
    assert token not in (tmp_path / "identity-mcp" / "mcp.json").read_text()


def test_bearer_token_is_only_in_ephemeral_runtime_config(tmp_path):
    token = "service-account-token"
    mcp = {
        "mcp_uuid": "22222222-2222-2222-2222-222222222222",
        "content_hash": "sha256:def",
        "mcp_name": "service-account-mcp",
        "transport": "url",
        "endpoint": "https://mcp.example/tools",
        "config": {"headers": {"Authorization": f"Bearer {token}"}},
    }

    runtime_config = _materialize_mcp(tmp_path, mcp)

    assert runtime_config["config"]["headers"]["Authorization"] == (
        f"Bearer {token}"
    )
    assert token not in (tmp_path / "service-account-mcp" / "mcp.json").read_text()
