import asyncio
import ssl

import pytest

from lensnode.main import LensNodeClient


class FakeWebSocketContext:
    """Minimal asynchronous WebSocket connection context."""

    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _make_client():
    """Build a LensNode client for transport option tests."""

    config = type(
        "Config",
        (),
        {
            "name": "test-node",
            "request_timeout_s": 30,
            "max_concurrent_runs": 1,
            "tls_skip_verify": True,
            "tls_ca_file": None,
        },
    )()
    return LensNodeClient(config)


@pytest.mark.parametrize(
    "url,uses_tls",
    [
        ("wss://server.example/ws/lens/lensnodes/", True),
        ("ws://server.example/ws/lens/lensnodes/", False),
    ],
)
def test_websocket_connection_uses_tls_context_only_for_wss(
    monkeypatch,
    url,
    uses_tls,
):
    captured = {}

    def fake_connect(target, **options):
        captured["target"] = target
        captured["options"] = options
        return FakeWebSocketContext()

    async def connected():
        return None

    async def loop(*args):
        return None

    client = _make_client()
    monkeypatch.setattr("lensnode.main.connect", fake_connect)
    monkeypatch.setattr(client, "_on_connected", connected)
    monkeypatch.setattr(client, "_send_loop", loop)
    monkeypatch.setattr(client, "_heartbeat_loop", loop)
    monkeypatch.setattr(client, "_receive_loop", loop)

    assert asyncio.run(client._run_connection(url)) is True
    assert captured["target"] == url
    if uses_tls:
        context = captured["options"]["ssl"]
        assert context.verify_mode == ssl.CERT_NONE
        assert context.check_hostname is False
    else:
        assert "ssl" not in captured["options"]


def test_runtime_cleanup_loop_repeats_until_stopped(monkeypatch):
    """The cleanup loop keeps sweeping while the LensNode is alive."""

    calls = []

    async def exercise():
        client = _make_client()
        client.config.workspace_path = "/test-workspace"

        async def no_wait(_delay):
            return None

        def cleanup(workspace_path, max_age_s):
            calls.append((workspace_path, max_age_s))
            if len(calls) == 2:
                client.stopping.set()

        monkeypatch.setattr("lensnode.main.asyncio.sleep", no_wait)
        monkeypatch.setattr(
            "lensnode.main.cleanup_stale_runtime_resources",
            cleanup,
        )
        monkeypatch.setattr(
            "lensnode.main.cleanup_expired_checkpoints",
            lambda *_args: None,
        )

        await client._runtime_cleanup_loop()

    asyncio.run(exercise())

    assert calls == [
        ("/test-workspace", 24 * 60 * 60),
        ("/test-workspace", 24 * 60 * 60),
    ]


def test_run_forever_starts_runtime_cleanup(monkeypatch):
    """The main client lifecycle owns the periodic cleanup task."""

    cleanup_started = False

    async def exercise():
        nonlocal cleanup_started
        client = _make_client()
        client.config.workspace_path = "/test-workspace"

        async def cleanup_loop():
            nonlocal cleanup_started
            cleanup_started = True
            await client.stopping.wait()

        async def run_connection(_url):
            while not cleanup_started:
                await asyncio.sleep(0)
            client.stopping.set()
            return True

        monkeypatch.setattr(client, "_ws_url", lambda: "ws://test")
        monkeypatch.setattr(client, "_run_connection", run_connection)
        monkeypatch.setattr(client, "_runtime_cleanup_loop", cleanup_loop)

        await client.run_forever()

    asyncio.run(exercise())

    assert cleanup_started is True


def test_thread_enqueue_ignores_closed_event_loop():
    """Background workers cannot crash when the client loop has closed."""

    client = _make_client()
    loop = asyncio.new_event_loop()
    loop.close()

    client._enqueue_from_thread(loop, {"type": "run_output"})

    assert not client._outbox
