import asyncio
import collections
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable


class ExecutionClass(StrEnum):
    """Resource class used by the LensNode-local execution queue."""

    STANDARD = "standard"
    DELEGATED = "delegated"
    EXCLUSIVE = "exclusive"


@dataclass
class _ExecutionRequest:
    """One queued request for LensNode execution capacity."""

    execution_class: ExecutionClass
    admitted: asyncio.Future


class LensNodeExecutionQueue:
    """Limit standard and exclusive work with independent capacities."""

    def __init__(
        self,
        max_standard_concurrency,
        max_exclusive_concurrency=1,
    ):
        self.max_standard_concurrency = max(
            1,
            int(max_standard_concurrency),
        )
        self.max_exclusive_concurrency = max(
            1,
            int(max_exclusive_concurrency),
        )
        self._lock = asyncio.Lock()
        self._waiting = collections.deque()
        self._active_standard = 0
        self._active_delegated = 0
        self.max_delegated_concurrency = self.max_standard_concurrency
        self._active_exclusive = 0

    async def acquire(
        self,
        execution_class,
        on_queued: Callable[[], None] | None = None,
    ):
        """Wait for capacity and return whether the request was queued."""

        execution_class = ExecutionClass(execution_class)
        request = _ExecutionRequest(
            execution_class=execution_class,
            admitted=asyncio.get_running_loop().create_future(),
        )
        async with self._lock:
            self._waiting.append(request)
            self._admit_waiters()
            queued = not request.admitted.done()
        if queued and on_queued is not None:
            on_queued()
        try:
            await asyncio.shield(request.admitted)
        except asyncio.CancelledError:
            async with self._lock:
                if request.admitted.done():
                    self._release_active(execution_class)
                else:
                    self._waiting.remove(request)
                self._admit_waiters()
            raise
        return queued

    async def release(self, execution_class):
        """Release capacity and admit the next eligible queued work."""

        execution_class = ExecutionClass(execution_class)
        async with self._lock:
            self._release_active(execution_class)
            self._admit_waiters()

    def _release_active(self, execution_class):
        """Release one active request while the queue lock is held."""

        if execution_class == ExecutionClass.EXCLUSIVE:
            if self._active_exclusive <= 0:
                raise RuntimeError("No exclusive LensNode work is active.")
            self._active_exclusive -= 1
            return
        if execution_class == ExecutionClass.DELEGATED:
            if self._active_delegated <= 0:
                raise RuntimeError("No delegated LensNode work is active.")
            self._active_delegated -= 1
            return
        if self._active_standard <= 0:
            raise RuntimeError("No standard LensNode work is active.")
        self._active_standard -= 1

    def _admit_waiters(self):
        """Admit standard work before starting the next exclusive request."""

        self._admit_standard_waiters()
        self._admit_delegated_waiters()
        self._admit_exclusive_waiters()

    def _admit_standard_waiters(self):
        """Fill free standard slots without an exclusive-work barrier."""

        waiting = collections.deque()
        while self._waiting:
            request = self._waiting.popleft()
            if (
                request.execution_class == ExecutionClass.STANDARD
                and self._active_standard < self.max_standard_concurrency
            ):
                self._active_standard += 1
                request.admitted.set_result(None)
            else:
                waiting.append(request)
        self._waiting = waiting

    def _admit_exclusive_waiters(self):
        """Fill free exclusive slots independently from standard work."""

        waiting = collections.deque()
        while self._waiting:
            request = self._waiting.popleft()
            if (
                request.execution_class == ExecutionClass.EXCLUSIVE
                and self._active_exclusive < self.max_exclusive_concurrency
            ):
                self._active_exclusive += 1
                request.admitted.set_result(None)
            else:
                waiting.append(request)
        self._waiting = waiting

    def _admit_delegated_waiters(self):
        """Admit delegated work independently of the parent Run slot."""

        waiting = collections.deque()
        while self._waiting:
            request = self._waiting.popleft()
            if (
                request.execution_class == ExecutionClass.DELEGATED
                and self._active_delegated < self.max_delegated_concurrency
            ):
                self._active_delegated += 1
                request.admitted.set_result(None)
            else:
                waiting.append(request)
        self._waiting = waiting
