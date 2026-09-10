import asyncio
import json
from unittest.mock import patch

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


class FakeSendError(Exception):
    """Stand-in for a websocket send failing as the connection drops."""


class FakeWebSocket:
    """Records sent frames; optionally fails the send at a given index."""

    def __init__(self, fail_on_index=None):
        self.sent = []
        self._fail_on = fail_on_index
        self._count = 0

    async def send(self, data):
        if self._fail_on is not None and self._count == self._fail_on:
            raise FakeSendError()
        self.sent.append(data)
        self._count += 1


async def _wait_until(predicate, timeout=1.0):
    deadline = timeout
    while deadline > 0:
        if predicate():
            return
        await asyncio.sleep(0.01)
        deadline -= 0.01
    raise AssertionError("condition not met within timeout")


def test_outbox_flushes_frames_enqueued_while_disconnected():
    """Frames emitted while no send loop is active flush on the next one.

    Models a run that keeps executing across a reconnect: its frames land in
    the durable outbox with no live connection, then the next connection's
    send loop delivers them in order.
    """

    async def exercise():
        client = _make_client()
        client._enqueue({"type": "run_output", "content_delta": "a"})
        client._enqueue({"type": "run_output", "content_delta": "b"})
        client._enqueue({"type": "run_done", "status": "done"})

        ws = FakeWebSocket()
        loop_task = asyncio.create_task(client._send_loop(ws))
        await _wait_until(lambda: len(ws.sent) == 3)

        client.stopping.set()
        client._outbox_ready.set()
        await asyncio.wait_for(loop_task, timeout=1)

        frames = [json.loads(item) for item in ws.sent]
        assert [f.get("content_delta") for f in frames[:2]] == ["a", "b"]
        assert frames[2]["type"] == "run_done"
        assert not client._outbox

    asyncio.run(exercise())


def test_outbox_preserves_frame_and_order_on_send_failure():
    """A send failing mid-flush keeps that frame and the rest, in order.

    Peek-then-pop: the failing frame is never dequeued, so a drop mid-send
    loses nothing and the next connection resends from exactly where it left
    off.
    """

    async def exercise():
        client = _make_client()
        client._enqueue({"type": "run_output", "content_delta": "a"})
        client._enqueue({"type": "run_output", "content_delta": "b"})
        client._enqueue({"type": "run_output", "content_delta": "c"})

        ws = FakeWebSocket(fail_on_index=1)
        loop_task = asyncio.create_task(client._send_loop(ws))
        try:
            await asyncio.wait_for(loop_task, timeout=1)
        except FakeSendError:
            pass

        # "a" was sent and dequeued; "b" failed and stayed, "c" behind it.
        assert [json.loads(item)["content_delta"] for item in ws.sent] == ["a"]
        remaining = [frame["content_delta"] for frame in client._outbox]
        assert remaining == ["b", "c"]

    asyncio.run(exercise())


def test_trace_frames_remain_pending_until_cursor_ack_and_reconnect():
    async def exercise():
        client = _make_client()
        for sequence in (1, 2):
            client._enqueue(
                {
                    "type": "run_trace_events",
                    "run_uuid": "run-1",
                    "events": [{"sequence": sequence}],
                }
            )

        client._outbox.clear()
        client._restore_pending_trace_frames()
        assert [frame["events"][0]["sequence"] for frame in client._outbox] == [1, 2]

        await client._handle_message(
            json.dumps(
                {
                    "type": "run_trace_events_ack",
                    "run_uuid": "run-1",
                    "last_sequence": 1,
                }
            )
        )

        assert list(client._pending_trace_frames) == [("run-1", 2)]

    asyncio.run(exercise())


def test_run_done_remains_reported_and_retries_until_acknowledged():
    async def exercise():
        client = _make_client()
        client._enqueue(
            {
                "type": "run_done",
                "run_uuid": "completed-run",
                "status": "done",
            }
        )

        client._outbox.clear()
        assert client._reported_active_runs() == ["completed-run"]

        client._restore_pending_terminal_frames()
        assert client._outbox[0]["run_uuid"] == "completed-run"

        await client._handle_message(
            json.dumps(
                {
                    "type": "run_done_ack",
                    "run_uuid": "completed-run",
                }
            )
        )
        assert client._reported_active_runs() == []

    asyncio.run(exercise())


def test_resume_starts_after_unacknowledged_trace_cursor():
    async def exercise():
        client = _make_client()
        captured = []

        class FakeExecutor:
            async def execute(self, command, emit):
                del emit
                captured.append(command)

        client.executor = FakeExecutor()
        client._enqueue(
            {
                "type": "run_trace_events",
                "run_uuid": "run-1",
                "events": [{"sequence": 7}],
            }
        )

        await client._start_command(
            {
                "type": "run_start",
                "run_uuid": "run-1",
                "resume": True,
                "trace_cursor": 5,
            }
        )
        await client.running_tasks["run-1"]

        assert captured[0]["trace_cursor"] == 7

    asyncio.run(exercise())


def test_reconnect_replays_pending_trace_frames_before_newer_frames():
    async def exercise():
        client = _make_client()
        client._enqueue(
            {
                "type": "run_trace_events",
                "run_uuid": "run-1",
                "events": [{"sequence": 1}],
            }
        )
        client._enqueue(
            {
                "type": "run_trace_events",
                "run_uuid": "run-1",
                "events": [{"sequence": 2}],
            }
        )
        client._outbox.clear()
        client._enqueue(
            {
                "type": "run_trace_events",
                "run_uuid": "run-1",
                "events": [{"sequence": 3}],
            }
        )

        client._restore_pending_trace_frames()

        assert [
            frame["events"][0]["sequence"] for frame in client._outbox
        ] == [1, 2, 3]

    asyncio.run(exercise())


def test_reconnect_does_not_duplicate_pending_batch_frames():
    async def exercise():
        client = _make_client()
        frame = {
            "type": "run_trace_events",
            "run_uuid": "run-1",
            "events": [{"sequence": 1}, {"sequence": 2}],
        }
        client._enqueue(frame)
        client._outbox.clear()
        client._enqueue(
            {
                "type": "run_trace_events",
                "run_uuid": "run-1",
                "events": [{"sequence": 3}],
            }
        )

        client._restore_pending_trace_frames()

        assert [
            event["sequence"]
            for item in client._outbox
            for event in item["events"]
        ] == [1, 2, 3]

    asyncio.run(exercise())


def test_reconnect_replays_pending_trace_before_terminal_frame():
    async def exercise():
        client = _make_client()
        client._enqueue(
            {
                "type": "run_trace_events",
                "run_uuid": "run-1",
                "events": [{"sequence": 1}],
            }
        )
        client._outbox.clear()
        client._enqueue(
            {
                "type": "run_done",
                "run_uuid": "run-1",
                "status": "failed",
            }
        )

        client._restore_pending_trace_frames()

        assert [frame["type"] for frame in client._outbox] == [
            "run_trace_events",
            "run_done",
        ]

    asyncio.run(exercise())


def test_completed_run_hands_terminal_frame_to_outbox():
    """A completed run enters the outbox before leaving running_tasks."""

    async def exercise():
        client = _make_client()
        run_uuid = "completed-run"

        class FakeExecutor:
            """Emit a terminal frame without yielding to the event loop."""

            async def execute(self, command, emit):
                """Complete the requested run immediately."""

                emit(
                    {
                        "type": "run_done",
                        "run_uuid": command["run_uuid"],
                        "status": "done",
                    }
                )

        client.executor = FakeExecutor()
        client.running_tasks[run_uuid] = asyncio.current_task()

        await client._execute_command(run_uuid, {"run_uuid": run_uuid})

        assert run_uuid not in client.running_tasks
        assert list(client._outbox) == [
            {
                "type": "run_event",
                "run_uuid": run_uuid,
                "step_type": "retrieval",
                "status": "running",
                "detail": {"queue_state": "STARTED"},
            },
            {
                "type": "run_done",
                "run_uuid": run_uuid,
                "status": "done",
            },
        ]

    asyncio.run(exercise())


def test_reconnect_hello_claims_buffered_terminal_run_before_flush():
    """Hello protects a completed run whose terminal frame is buffered."""

    async def exercise():
        client = _make_client()
        client.running_tasks["running-run"] = asyncio.current_task()
        client.running_tasks["datasource:sync-1"] = asyncio.current_task()
        client._enqueue(
            {
                "type": "run_output",
                "run_uuid": "incomplete-run",
                "content_delta": "partial",
            }
        )
        client._enqueue(
            {
                "type": "run_output",
                "run_uuid": "completed-run",
                "final_content": "complete answer",
            }
        )
        client._enqueue(
            {
                "type": "run_done",
                "run_uuid": "completed-run",
                "status": "done",
            }
        )

        ws = FakeWebSocket()
        client.websocket = ws
        with patch("lensnode.main.get_checkpoint_saver"):
            await client._send_hello()

        loop_task = asyncio.create_task(client._send_loop(ws))
        await _wait_until(lambda: len(ws.sent) == 4)
        client.stopping.set()
        client._outbox_ready.set()
        await asyncio.wait_for(loop_task, timeout=1)

        frames = [json.loads(item) for item in ws.sent]
        assert frames[0]["type"] == "hello"
        assert frames[0]["active_runs"] == [
            "completed-run",
            "running-run",
        ]
        assert frames[0]["labels"]["run_document_attachments"] is True
        assert frames[0]["labels"]["run_checkpoint_resume"] is True
        assert frames[0]["labels"]["run_admission_checkpoint_v1"] is True
        assert [frame["type"] for frame in frames[1:]] == [
            "run_output",
            "run_output",
            "run_done",
        ]

    asyncio.run(exercise())


def test_datasource_sync_terminal_frame_replays_until_acknowledged():
    client = _make_client()
    payload = {
        "type": "datasource_sync_done",
        "task_id": "sync-1",
        "status": "success",
    }

    client._enqueue(payload)
    assert client._pending_datasource_terminal_frames["sync-1"] is payload

    client._acknowledge_datasource_terminal_frame(payload)

    assert "sync-1" not in client._pending_datasource_terminal_frames
    assert "sync-1" not in client.active_datasource_operations


def test_hello_does_not_advertise_resume_when_checkpointing_is_disabled(
    monkeypatch,
):
    async def exercise():
        client = _make_client()
        client.websocket = FakeWebSocket()

        await client._send_hello()

        frame = json.loads(client.websocket.sent[0])
        assert frame["labels"]["run_checkpoint_resume"] is False

    monkeypatch.setenv("LENSNODE_CHECKPOINT_ENABLED", "0")
    asyncio.run(exercise())


def test_hello_does_not_advertise_unusable_checkpoint_storage():
    async def exercise():
        client = _make_client()
        client.websocket = FakeWebSocket()

        with patch(
            "lensnode.main.get_checkpoint_saver",
            side_effect=OSError("read only"),
        ):
            await client._send_hello()

        frame = json.loads(client.websocket.sent[0])
        assert frame["labels"]["run_checkpoint_resume"] is False

    asyncio.run(exercise())


def test_run_done_ack_cleans_checkpoint_and_runtime_resources():
    async def exercise():
        client = _make_client()
        await client._handle_message(
            json.dumps(
                {
                    "type": "run_done_ack",
                    "run_uuid": "completed-run",
                }
            )
        )

    with (
        patch("lensnode.main.cleanup_run_checkpoint") as checkpoint_cleanup,
        patch("lensnode.main.cleanup_run_runtime_resources") as runtime_cleanup,
    ):
        asyncio.run(exercise())

    checkpoint_cleanup.assert_called_once_with(
        "completed-run",
        "/missing-test-workspace",
    )
    runtime_cleanup.assert_called_once_with(
        "/missing-test-workspace",
        "completed-run",
    )


def test_run_done_ack_waits_for_timed_out_worker_before_cleanup():
    async def exercise(checkpoint_cleanup, runtime_cleanup):
        client = _make_client()
        worker_release = asyncio.Event()

        async def worker():
            await worker_release.wait()

        worker_task = asyncio.create_task(worker())
        client.executor._pending_workers = {"timed-out-run": worker_task}
        await client._handle_message(
            json.dumps(
                {
                    "type": "run_done_ack",
                    "run_uuid": "timed-out-run",
                }
            )
        )

        checkpoint_cleanup.assert_not_called()
        runtime_cleanup.assert_not_called()

        worker_release.set()
        await _wait_until(lambda: checkpoint_cleanup.called)

    with (
        patch("lensnode.main.cleanup_run_checkpoint") as checkpoint_cleanup,
        patch("lensnode.main.cleanup_run_runtime_resources") as runtime_cleanup,
        patch(
            "lensnode.executor.cleanup_run_checkpoint",
            checkpoint_cleanup,
        ),
        patch(
            "lensnode.executor.cleanup_run_runtime_resources",
            runtime_cleanup,
        ),
    ):
        asyncio.run(exercise(checkpoint_cleanup, runtime_cleanup))

    checkpoint_cleanup.assert_called_once_with(
        "timed-out-run",
        "/missing-test-workspace",
    )
    runtime_cleanup.assert_called_once_with(
        "/missing-test-workspace",
        "timed-out-run",
    )
