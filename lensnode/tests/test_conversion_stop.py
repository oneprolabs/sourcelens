import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from lensnode.execution_queue import ExecutionClass
from lensnode.main import LensNodeClient


def make_client(tmp_path):
    """Create a client with persistent storage isolated to this test."""

    return LensNodeClient(
        SimpleNamespace(
            name="stop-test",
            request_timeout_s=10,
            max_concurrent_runs=1,
            workspace_path=str(tmp_path),
            ai_gateway_url="http://gateway",
            token="test",
        )
    )


async def command(client, kind, task_id="original", datasource_uuid="source"):
    """Deliver a task-scoped control command."""

    await client._handle_message(
        json.dumps(
            {
                "type": kind,
                "task_id": task_id,
                "datasource_uuid": datasource_uuid,
            }
        )
    )


def stopped_frames(client):
    """Return terminal conversion evidence from the outbound buffer."""

    return [
        frame
        for frame in client._outbox
        if frame["type"]
        in {
            "datasource_convert_stopped",
            "datasource_convert_done",
        }
    ]


def test_absent_cancellation_fences_delayed_dispatch_across_restart(tmp_path):
    async def exercise():
        client = make_client(tmp_path)
        await command(client, "datasource_convert_cancel")
        frame = stopped_frames(client)[-1]
        assert frame["executor_stopped"] is True
        assert frame["datasource_uuid"] == "source"
        assert frame["task_id"] == "original"
        assert frame["stop_confirmation_source"] == "lensnode_absent_fenced"
        assert frame["stop_confirmed_at"]
        assert frame["executor_instance_id"]

        restarted = make_client(tmp_path)
        with patch("lensnode.main.convert_managed_workspace") as convert:
            await command(restarted, "datasource_convert")
            assert "datasource-convert:original" not in restarted.running_tasks
            convert.assert_not_called()
        assert stopped_frames(restarted)[-1]["executor_stopped"] is True

    asyncio.run(exercise())


def test_running_cancellation_waits_for_real_thread_exit(tmp_path):
    started = threading.Event()
    release = threading.Event()
    exited = threading.Event()

    def convert(message, workspace, emit):
        """Keep running after cancellation until the test releases the worker."""

        started.set()
        assert release.wait(5)
        exited.set()
        return {"status": "cancelled", "conversion_summary": {"converted": 2}}

    async def exercise():
        client = make_client(tmp_path)
        with patch("lensnode.main.convert_managed_workspace", convert):
            await command(client, "datasource_convert")
            task = client.running_tasks["datasource-convert:original"]
            assert await asyncio.to_thread(started.wait, 2)
            try:
                await command(client, "datasource_convert_cancel")
                assert not stopped_frames(client)
                assert not exited.is_set()
            finally:
                release.set()
                await asyncio.wait_for(task, 2)
        frame = stopped_frames(client)[-1]
        assert frame["executor_stopped"] is True
        assert frame["conversion_summary"] == {"converted": 2}
        await command(client, "datasource_convert_cancel")
        assert stopped_frames(client)[-1] == frame
        restarted = make_client(tmp_path)
        await command(restarted, "datasource_convert_cancel")
        assert stopped_frames(restarted)[-1] == frame

    asyncio.run(exercise())


def test_queued_cancellation_never_enters_worker(tmp_path):
    async def exercise():
        client = make_client(tmp_path)
        await client.execution_queue.acquire(ExecutionClass.STANDARD)
        with patch("lensnode.main.convert_managed_workspace") as convert:
            await command(client, "datasource_convert")
            task = client.running_tasks["datasource-convert:original"]
            await asyncio.sleep(0)
            await command(client, "datasource_convert_cancel")
            await asyncio.wait_for(task, 2)
            await client.execution_queue.release(ExecutionClass.STANDARD)
            await command(client, "datasource_convert")
            convert.assert_not_called()
        assert stopped_frames(client)[-1]["executor_stopped"] is True

    asyncio.run(exercise())


def test_wrong_datasource_identity_does_not_cancel_running_work(tmp_path):
    async def exercise():
        client = make_client(tmp_path)
        await client.execution_queue.acquire(ExecutionClass.STANDARD)
        await command(client, "datasource_convert")
        task = client.running_tasks["datasource-convert:original"]
        await command(client, "datasource_convert_cancel", datasource_uuid="other")
        assert not client.datasource_conversion_cancels["original"].is_set()
        assert not stopped_frames(client)
        await command(client, "datasource_convert_cancel")
        await asyncio.wait_for(task, 2)
        await client.execution_queue.release(ExecutionClass.STANDARD)

    asyncio.run(exercise())


def test_absent_cancellation_without_identity_is_not_confirmed(tmp_path):
    async def exercise():
        client = make_client(tmp_path)
        await command(client, "datasource_convert_cancel", datasource_uuid="")
        assert not stopped_frames(client)

    asyncio.run(exercise())


def test_cancelling_async_wrapper_keeps_thread_ownership_until_exit(tmp_path):
    """An asyncio cancellation cannot establish that a Python worker stopped."""

    started = threading.Event()
    release = threading.Event()

    def convert(message, workspace, emit):
        """Hold the actual executor beyond cancellation of its wrapper."""

        started.set()
        assert release.wait(5)
        return {"status": "cancelled"}

    async def exercise():
        client = make_client(tmp_path)
        with patch("lensnode.main.convert_managed_workspace", convert):
            await command(client, "datasource_convert")
            task = client.running_tasks["datasource-convert:original"]
            assert await asyncio.to_thread(started.wait, 2)
            try:
                task.cancel()
                await asyncio.sleep(0)
                await command(client, "datasource_convert_cancel")
                assert not stopped_frames(client)
                assert "original" in client.datasource_conversion_cancels
                assert "datasource-convert:original" in client.running_tasks
            finally:
                release.set()
                await asyncio.wait_for(task, 2)
        assert stopped_frames(client)[-1]["executor_stopped"] is True

    asyncio.run(exercise())


def test_stop_frame_survives_lost_delivery_and_duplicate_requests(tmp_path):
    """A stop reply uses terminal replay and keeps its original provenance."""

    async def exercise():
        client = make_client(tmp_path)
        await command(client, "datasource_convert_cancel")
        frame = stopped_frames(client)[-1]
        client._outbox.clear()
        client._restore_pending_datasource_terminal_frames()
        assert stopped_frames(client) == [frame]
        await client._handle_message(json.dumps({"type": "datasource_terminal_ack", "task_id": "original"}))
        assert not stopped_frames(client)
        await command(client, "datasource_convert_cancel")
        assert stopped_frames(client) == [frame]
        restarted = make_client(tmp_path)
        await command(restarted, "datasource_convert_cancel")
        assert stopped_frames(restarted) == [frame]

    asyncio.run(exercise())


def test_cancellation_racing_completed_worker_preserves_original_result(tmp_path):
    """Cancellation during result delivery must not replace successful output."""

    async def exercise():
        client = make_client(tmp_path)
        loop = asyncio.get_running_loop()
        run_in_executor = loop.run_in_executor

        def cancel_before_delivery(*args):
            """Cancel the wrapper after its real worker has finished."""

            owner = asyncio.current_task()
            future = run_in_executor(*args)
            future.add_done_callback(lambda item: owner.cancel())
            return future

        with (
            patch(
                "lensnode.main.convert_managed_workspace",
                return_value={
                    "status": "success",
                    "conversion_summary": {"converted": 7},
                },
            ),
            patch.object(loop, "run_in_executor", cancel_before_delivery),
        ):
            await command(client, "datasource_convert")
            await asyncio.wait_for(client.running_tasks["datasource-convert:original"], 2)
        frame = stopped_frames(client)[-1]
        assert frame["status"] == "success"
        assert frame["conversion_summary"] == {"converted": 7}
        assert frame["executor_stopped"] is True

    asyncio.run(exercise())
