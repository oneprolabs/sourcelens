from contextlib import contextmanager

import httpx
import pytest

from lensnode.plugin_http import (
    PLUGIN_HTTP_JSON_MAX_BYTES,
    PluginHttpClientError,
    PluginHttpClientPool,
)


class FakeHttpClient:
    """Record requests made through one pooled origin client."""

    def __init__(self, **options):
        self.options = options
        self.requests = []
        self.is_closed = False

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        yield object()

    def close(self):
        self.is_closed = True


def test_pool_reuses_http2_client_for_same_connection_and_origin():
    clients = []

    def factory(**options):
        client = FakeHttpClient(**options)
        clients.append(client)
        return client

    pool = PluginHttpClientPool(
        timeout=30,
        verify=True,
        client_factory=factory,
    )
    first = pool.bind(
        "github",
        "connection-1",
        ["https://api.github.com"],
    )
    second = pool.bind(
        "github",
        "connection-1",
        ["https://api.github.com"],
    )

    with first.stream("GET", "https://api.github.com/repos/one/a"):
        pass
    with second.stream("GET", "https://api.github.com/repos/one/b"):
        pass

    assert len(clients) == 1
    assert clients[0].options["http2"] is True
    assert len(clients[0].requests) == 2


def test_pool_enables_negotiated_http2_without_requiring_plugin_branching():
    clients = []

    def factory(**options):
        client = FakeHttpClient(**options)
        clients.append(client)
        return client

    pool = PluginHttpClientPool(
        timeout=30,
        verify=True,
        client_factory=factory,
    )
    client = pool.bind("gitlab", "connection-1", ["https://gitlab.com"])

    with client.stream("GET", "https://gitlab.com/api/v4/projects"):
        pass

    assert clients[0].options["http2"] is True
    assert clients[0].options["follow_redirects"] is False


def test_pool_isolates_connections_even_when_origin_matches():
    clients = []

    def factory(**options):
        client = FakeHttpClient(**options)
        clients.append(client)
        return client

    pool = PluginHttpClientPool(
        timeout=30,
        verify=True,
        client_factory=factory,
    )
    first = pool.bind("github", "connection-1", ["https://api.github.com"])
    second = pool.bind("github", "connection-2", ["https://api.github.com"])

    with first.stream("GET", "https://api.github.com/repos/one/a"):
        pass
    with second.stream("GET", "https://api.github.com/repos/two/b"):
        pass

    assert len(clients) == 2


def test_bound_client_accepts_declared_http_origin_with_custom_port():
    clients = []

    def factory(**options):
        client = FakeHttpClient(**options)
        clients.append(client)
        return client

    pool = PluginHttpClientPool(
        timeout=30,
        verify=True,
        client_factory=factory,
    )
    client = pool.bind(
        "jira",
        "connection-1",
        ["http://office.oneprocloud.com.cn:9005"],
    )

    with client.stream(
        "GET",
        "http://office.oneprocloud.com.cn:9005/rest/api/2/myself",
    ):
        pass

    assert len(clients[0].requests) == 1


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        ("POST", "https://api.github.com/repos/one/a", {}),
        ("GET", "http://api.github.com/repos/one/a", {}),
        ("GET", "https://other.example/repos/one/a", {}),
        ("GET", "https://user:pass@api.github.com/repos/one/a", {}),
        (
            "GET",
            "https://api.github.com/repos/one/a",
            {"follow_redirects": True},
        ),
        ("GET", "https://api.github.com/repos/one/a", {"timeout": None}),
        ("GET", "https://api.github.com/repos/one/a", {"cookies": {}}),
        (
            "GET",
            "https://api.github.com/repos/one/a",
            {"headers": {"Host": "other.example"}},
        ),
    ],
)
def test_bound_client_rejects_requests_outside_policy(method, url, kwargs):
    pool = PluginHttpClientPool(timeout=30, verify=True)
    client = pool.bind(
        "github",
        "connection-1",
        ["https://api.github.com"],
    )

    with pytest.raises(PluginHttpClientError):
        with client.stream(method, url, **kwargs):
            pass


def test_pool_closes_every_origin_client_and_rejects_new_bindings():
    clients = []

    def factory(**options):
        client = FakeHttpClient(**options)
        clients.append(client)
        return client

    pool = PluginHttpClientPool(
        timeout=30,
        verify=True,
        client_factory=factory,
    )
    client = pool.bind(
        "github",
        "connection-1",
        ["https://api.github.com", "https://uploads.github.com"],
    )
    with client.stream("GET", "https://api.github.com/repos/one/a"):
        pass
    with client.stream("HEAD", "https://uploads.github.com/archive"):
        pass

    pool.close()

    assert len(clients) == 2
    assert all(item.is_closed for item in clients)
    with pytest.raises(PluginHttpClientError):
        pool.bind("github", "connection-1", ["https://api.github.com"])


@pytest.mark.parametrize("plugin,url,kwargs", [
    ("github", "https://decision.example/v1/systemone", {"json": {}}),
    ("typesafe", "https://decision.example/admin", {"json": {}}),
    ("typesafe", "https://decision.example/v1/systemone?x=1", {"json": {}}),
    ("typesafe", "https://decision.example/v1/systemone/extra", {"json": {}}),
    ("typesafe", "https://other.example/v1/systemone", {"json": {}}),
    ("typesafe", "https://decision.example/v1/systemone",
     {"json": {}, "params": {}}),
    ("typesafe", "https://decision.example/v1/systemone",
     {"json": {"x": "a" * PLUGIN_HTTP_JSON_MAX_BYTES}}),
])
def test_decision_post_permission_is_bounded(plugin, url, kwargs):
    pool = PluginHttpClientPool(timeout=15, verify=True)
    client = pool.bind(plugin, "connection-1", ["https://decision.example"])
    with pytest.raises(PluginHttpClientError):
        with client.stream("POST", url, **kwargs):
            pass


def test_decision_post_allows_body_within_the_bounded_size():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={}, request=request)

    pool = PluginHttpClientPool(
        timeout=15,
        verify=True,
        client_factory=lambda **options: httpx.Client(
            transport=httpx.MockTransport(handler), **options
        ),
    )
    try:
        client = pool.bind(
            "typesafe", "connection-1", ["https://decision.example"]
        )
        with client.stream(
            "POST",
            "https://decision.example/v1/systemone",
            json={"state": "x" * 200_000},
        ) as response:
            assert response.status_code == 200
    finally:
        pool.close()
    assert [request.url.path for request in seen] == ["/v1/systemone"]


def test_decision_post_allows_a_gateway_base_path():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={}, request=request)

    pool = PluginHttpClientPool(
        timeout=15,
        verify=True,
        client_factory=lambda **options: httpx.Client(
            transport=httpx.MockTransport(handler), **options
        ),
    )
    try:
        client = pool.bind(
            "typesafe", "connection-1", ["https://ai-gateway.vercel.sh"]
        )
        with client.stream(
            "POST",
            "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
            json={"state": "x"},
        ) as response:
            assert response.status_code == 200
    finally:
        pool.close()
    assert [request.url.path for request in seen] == [
        "/typesafe/v1/systemone"
    ]
