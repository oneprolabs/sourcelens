import asyncio
import logging
import time

from lensnode.load_probe import LoopLagProbe
from lensnode.main import LensNodeClient


def _make_client():
    """Build a client with a throwaway config (executor is unused here)."""

    config = type(
        "Config",
        (),
        {
            "agent_version": "test-agent",
            "name": "test-node",
            "protocol_version": "v1",
            "request_timeout_s": 240,
            "max_concurrent_runs": 1,
            "workspace_path": "/missing-test-workspace",
        },
    )()
    return LensNodeClient(config)


async def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def test_collect_reports_worst_lag_and_stall_count():
    probe = LoopLagProbe(warn_threshold_s=2.0)
    probe._record(0.01)
    probe._record(3.5)
    probe._record(0.02)

    assert probe.collect() == {"loop_lag_ms": 3500.0, "loop_stall_count": 1}
    # A new window starts from the last sample so an idle loop does not
    # report a floor of zero.
    assert probe.collect() == {"loop_lag_ms": 20.0, "loop_stall_count": 0}


def test_stall_warning_is_threshold_gated_and_rate_limited(caplog):
    probe = LoopLagProbe(warn_threshold_s=2.0, warn_cooldown_s=60.0)

    with caplog.at_level(logging.WARNING, logger="lensnode"):
        probe._record(0.5)
        assert caplog.records == []
        probe._record(2.5)
        assert len(caplog.records) == 1
        assert "resumed after a stall" in caplog.records[0].getMessage()
        # Still stalled, still inside the cooldown: count it, do not re-log.
        probe._record(2.5)
        assert len(caplog.records) == 1

    assert probe.collect()["loop_stall_count"] == 2


def test_watchdog_dumps_stacks_while_loop_is_blocked(caplog):
    async def run():
        probe = LoopLagProbe(
            interval_s=0.1,
            warn_threshold_s=0.2,
            warn_cooldown_s=60.0,
        )
        probe.start()
        await asyncio.sleep(0.15)
        # Block the loop the way an in-process blocking call would: the
        # sampler cannot beat while this runs, so the watchdog thread has to
        # report the stalled loop from its own stack.
        time.sleep(0.6)
        await asyncio.sleep(0.1)
        await probe.stop()
        return probe

    with caplog.at_level(logging.ERROR, logger="lensnode"):
        probe = asyncio.run(run())

    messages = [record.getMessage() for record in caplog.records]
    assert any("stalled LensNode event loop" in message for message in messages)
    assert any("EventLoopStack:" in message for message in messages)
    assert probe.collect()["loop_lag_ms"] >= 200


def test_watchdog_does_not_dump_a_healthy_loop(caplog):
    async def run():
        probe = LoopLagProbe(
            interval_s=0.1,
            warn_threshold_s=0.2,
            warn_cooldown_s=60.0,
        )
        probe.start()
        await asyncio.sleep(0.6)
        await probe.stop()

    with caplog.at_level(logging.ERROR, logger="lensnode"):
        asyncio.run(run())

    assert caplog.records == []


def test_restart_after_stop_reports_each_stall_again(caplog):
    async def run():
        probe = LoopLagProbe(
            interval_s=0.1,
            warn_threshold_s=0.2,
            warn_cooldown_s=60.0,
        )
        probe.start()
        await asyncio.sleep(0.15)
        time.sleep(0.6)
        await asyncio.sleep(0.1)
        await probe.stop()
        probe.start()
        await asyncio.sleep(0.15)
        time.sleep(0.6)
        await asyncio.sleep(0.1)
        await probe.stop()

    with caplog.at_level(logging.ERROR, logger="lensnode"):
        asyncio.run(run())

    stalls = [
        record.getMessage()
        for record in caplog.records
        if "stalled LensNode event loop" in record.getMessage()
    ]
    # The second stall is only reported if the restart clears the dump
    # cooldown left behind by the first one.
    assert len(stalls) == 2


def test_resource_metrics_report_loop_lag_send_time_and_outbox_depth():
    client = _make_client()
    client._send_max_s = 0.25

    metrics = client._resource_metrics()

    assert metrics["outbox_depth"] == 0
    assert metrics["send_ms_max"] == 250.0
    assert metrics["loop_lag_ms"] == 0.0
    assert metrics["loop_stall_count"] == 0
    # The send window is per report, not cumulative.
    assert client._resource_metrics()["send_ms_max"] == 0.0


def test_disconnect_log_snapshots_control_channel_metrics(caplog):
    client = _make_client()
    client._send_max_s = 1.5
    client._enqueue({"type": "heartbeat"})

    with caplog.at_level(logging.INFO, logger="lensnode"):
        client._log_disconnected(1011, "keepalive ping timeout")

    message = caplog.records[0].getMessage()
    assert "CloseStatus: 1011" in message
    assert "outbox_depth: 1" in message
    assert "send_ms_max: 1500.0" in message
    assert "loop_lag_ms:" in message


def test_send_loop_records_slow_send_time():
    client = _make_client()

    class SlowWebSocket:
        """Stands in for a socket whose write is held back by backpressure."""

        async def send(self, data):
            del data
            await asyncio.sleep(0.05)

    async def run():
        client._enqueue({"type": "heartbeat"})
        task = asyncio.create_task(client._send_loop(SlowWebSocket()))
        await _wait_until(lambda: client._send_max_s >= 0.04)
        client.stopping.set()
        client._outbox_ready.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())

    assert client._resource_metrics()["send_ms_max"] >= 40
