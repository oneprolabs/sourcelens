import asyncio

from lensnode.execution_queue import ExecutionClass, LensNodeExecutionQueue


def test_standard_work_runs_in_parallel_up_to_configured_limit():
    async def exercise():
        queue = LensNodeExecutionQueue(max_standard_concurrency=2)

        await queue.acquire(ExecutionClass.STANDARD)
        await queue.acquire(ExecutionClass.STANDARD)
        third_started = asyncio.Event()

        async def acquire_third():
            await queue.acquire(ExecutionClass.STANDARD)
            third_started.set()

        third = asyncio.create_task(acquire_third())
        await asyncio.sleep(0)
        assert not third_started.is_set()

        await queue.release(ExecutionClass.STANDARD)
        await asyncio.wait_for(third_started.wait(), timeout=1)

        await queue.release(ExecutionClass.STANDARD)
        await queue.release(ExecutionClass.STANDARD)
        await third

    asyncio.run(exercise())

def test_waiting_exclusive_work_blocks_later_standard_work():
    async def exercise():
        queue = LensNodeExecutionQueue(max_standard_concurrency=2)
        await queue.acquire(ExecutionClass.STANDARD)

        exclusive_started = asyncio.Event()
        later_standard_started = asyncio.Event()

        async def acquire_exclusive():
            await queue.acquire(ExecutionClass.EXCLUSIVE)
            exclusive_started.set()

        async def acquire_later_standard():
            await queue.acquire(ExecutionClass.STANDARD)
            later_standard_started.set()

        exclusive = asyncio.create_task(acquire_exclusive())
        await asyncio.sleep(0)
        later_standard = asyncio.create_task(acquire_later_standard())
        await asyncio.sleep(0)

        assert not exclusive_started.is_set()
        assert not later_standard_started.is_set()

        await queue.release(ExecutionClass.STANDARD)
        await asyncio.wait_for(exclusive_started.wait(), timeout=1)

        await queue.release(ExecutionClass.EXCLUSIVE)
        await asyncio.wait_for(later_standard_started.wait(), timeout=1)
        await queue.release(ExecutionClass.STANDARD)
        await asyncio.gather(exclusive, later_standard)

    asyncio.run(exercise())


def test_cancelling_queued_work_does_not_leak_capacity():
    async def exercise():
        queue = LensNodeExecutionQueue(max_standard_concurrency=1)
        await queue.acquire(ExecutionClass.STANDARD)

        queued = asyncio.create_task(queue.acquire(ExecutionClass.STANDARD))
        await asyncio.sleep(0)
        queued.cancel()
        await asyncio.gather(queued, return_exceptions=True)

        await queue.release(ExecutionClass.STANDARD)
        await asyncio.wait_for(
            queue.acquire(ExecutionClass.STANDARD),
            timeout=1,
        )
        await queue.release(ExecutionClass.STANDARD)

    asyncio.run(exercise())


def test_delegated_work_has_capacity_while_parent_is_running():
    async def exercise():
        queue = LensNodeExecutionQueue(max_standard_concurrency=1)
        await queue.acquire(ExecutionClass.STANDARD)
        await asyncio.wait_for(
            queue.acquire(ExecutionClass.DELEGATED),
            timeout=1,
        )
        await queue.release(ExecutionClass.DELEGATED)
        await queue.release(ExecutionClass.STANDARD)

    asyncio.run(exercise())
